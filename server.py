# created 2026-11-04 by dani/Androtis, remade 2026-11-23 by hosevana
# last modified: 2026-11-26 by nia
# server driver code, ran on gcp compute engine
# working of 2026-11-25
import errno
import getpass
import hashlib
import hmac
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import secrets
from pathlib import Path

HOST = "0.0.0.0"
PORT = 3300
CHUNK_SIZE = 64 * 1024
PASSWORD_HASH_ITERATIONS = 600_000
# where files are stored, this is a seperate mounted disk on gcp
STORAGE_DIR = Path(
    os.environ.get("FILE_STORAGE_DIR", "/mnt/disks/file-storage")
).expanduser().resolve()

# send response function
def send_response(connection, response):
    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))

# hash password with salt using PBKDF2-HMAC-SHA256
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )

# create a password hash and salt for the server, print them to stdout (only run once!)
# salt and hash is like magic to me idk how to explain it
def create_password_hash():
    password = getpass.getpass("Create server password: ")
    confirmation = getpass.getpass("Confirm server password: ")
    if not password or password != confirmation:
        raise ValueError("Passwords must be non-empty and match.")

    salt = secrets.token_bytes(16)
    password_hash = hash_password(password, salt)
    print(f"FILESERVER_PASSWORD_SALT={salt.hex()}")
    print(f"FILESERVER_PASSWORD_HASH={password_hash.hex()}")

# load authentication credentials from environment variables, raise error if missing or malformed
def load_auth_credentials():
    username = os.environ.get("FILESERVER_USERNAME")
    salt_hex = os.environ.get("FILESERVER_PASSWORD_SALT")
    password_hash_hex = os.environ.get("FILESERVER_PASSWORD_HASH")
    if not username or not salt_hex or not password_hash_hex:
        raise RuntimeError(
            "Set FILESERVER_USERNAME, FILESERVER_PASSWORD_SALT, and "
            "FILESERVER_PASSWORD_HASH before starting the server."
        )

    try:
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(password_hash_hex)
    except ValueError as error:
        raise RuntimeError("Password salt and hash must be hexadecimal.") from error
    if len(salt) < 16 or len(expected_hash) != 32:
        raise RuntimeError("Password salt/hash has an invalid length.")
    return username, salt, expected_hash

# verify user and pass
def verify_credentials(username, password, credentials):
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    expected_username, salt, expected_hash = credentials
    try:
        candidate_hash = hash_password(password, salt)
    except UnicodeEncodeError:
        return False
    username_matches = hmac.compare_digest(
        username.encode("utf-8"),
        expected_username.encode("utf-8"),
    )
    password_matches = hmac.compare_digest(candidate_hash, expected_hash)
    return username_matches and password_matches

# create a tls context for secure connections, raise error if missing cert/key
def create_tls_context():
    certificate = os.environ.get("FILESERVER_TLS_CERT")
    private_key = os.environ.get("FILESERVER_TLS_KEY")
    if not certificate or not private_key:
        raise RuntimeError("Set FILESERVER_TLS_CERT and FILESERVER_TLS_KEY.")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    return context

# validate filename
def valid_filename(filename):
    return (
        isinstance(filename, str)
        and filename not in {"", ".", ".."}
        and "/" not in filename
        and "\\" not in filename
        and "\0" not in filename
    )

# ensure filepath is valid and inside storage, return resolved path
def resolve_storage_path(relative_path, allow_root=False):
    storage_root = STORAGE_DIR.resolve() # storage path
    if not isinstance(relative_path, str):
        raise ValueError("Folder path must be text.")
    normalized = relative_path.strip().replace("\\", "/") # change from windows to unix paths
    if allow_root and normalized in {"", "."}:
        return storage_root # return the storage path
    if normalized.startswith("/"):
        raise ValueError("Folder paths must be relative to server storage.")
    normalized = normalized.rstrip("/")
    parts = normalized.split("/")
    if (
        not normalized
        or any(part in {"", ".", ".."} for part in parts)
        or (len(parts[0]) >= 2 and parts[0][1] == ":")
    ):
        raise ValueError("Invalid folder path.")

    # paths cannot pass through symbolic links, and must remain inside storage
    candidate = storage_root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("Folder paths cannot pass through symbolic links.")

    resolved = candidate.resolve()
    try:
        resolved.relative_to(storage_root)
    except ValueError as error:
        raise ValueError("Folder path must remain inside server storage.") from error
    return resolved

# check if the current directory exists
def resolve_current_directory(relative_path):
    directory = resolve_storage_path(relative_path, allow_root=True)
    if not directory.is_dir():
        raise FileNotFoundError("Server directory not found.")
    return directory

# return the relative path of a directory
def relative_storage_path(directory):
    relative = directory.relative_to(STORAGE_DIR.resolve())
    return "" if relative == Path(".") else relative.as_posix()

# create or delete a subfolder in the server storage
def handle_subfolder(connection, request):
    action = request.get("action")
    if not isinstance(action, str) or action.upper() not in {"CREATE", "DELETE"}:
        send_response(connection, {"ok": False, "error": "Use CREATE or DELETE."})
        return
    action = action.upper()
    # invaid action was passed

    try:
        # ensure path exists and is in the correct format
        current_directory = resolve_current_directory(request.get("directory", ""))
        base_path = relative_storage_path(current_directory)
        requested_path = request.get("path")
        if not isinstance(requested_path, str):
            raise ValueError("Folder path must be text.")
        combined_path = (
            f"{base_path}/{requested_path}" if base_path else requested_path
        )
        folder = resolve_storage_path(combined_path)
    except ValueError as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return
    except FileNotFoundError as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    try:
        # create folder
        if action == "CREATE":
            folder.mkdir(parents=True, exist_ok=False)
            send_response(connection, {"ok": True, "message": f"Created {request['path']}."})
        else:
            if not folder.is_dir():
                send_response(connection, {"ok": False, "error": "Subfolder not found."})
                return
            # delete folder otherwise
            folder.rmdir()
            send_response(connection, {"ok": True, "message": f"Deleted empty folder {request['path']}."})
    except FileExistsError:
        send_response(connection, {"ok": False, "error": "That path already exists."})
    except FileNotFoundError:
        send_response(connection, {"ok": False, "error": "Subfolder not found."})
    except OSError as error:
        print(f"Could not {action.lower()} subfolder {request.get('path')}: {error}")
        # make sure folder is empty and do not delete if not empty
        if action == "DELETE" and (
            error.errno in {errno.ENOTEMPTY, errno.EEXIST}
            or getattr(error, "winerror", None) == 145
        ):
            message = "Folder is not empty; remove its contents first."
        else:
            message = f"Could not {action.lower()} subfolder. Check server permissions."
        send_response(connection, {"ok": False, "error": message})

# list the current directory
def list_directory(connection, request):
    try:
        directory = resolve_current_directory(request.get("directory", ""))
        entries = []
        # put all directory items into a list, skip symlinks, and display
        for item in directory.iterdir():
            if item.is_symlink():
                continue
            entry = {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
            }
            if item.is_file():
                entry["size_bytes"] = item.stat().st_size
            entries.append(entry)
        entries.sort(key=lambda entry: entry["name"].casefold())
        send_response(
            connection,
            {
                "ok": True,
                "path": relative_storage_path(directory),
                "entries": entries,
            },
        )
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
    except OSError as error:
        print(f"Could not list server directory: {error}")
        send_response(connection, {"ok": False, "error": "Could not list server directory."})

# change the current directory, upwards or downwards
def change_directory(connection, request):
    try:
        # make sure directory exists and is in the correct format
        current_directory = resolve_current_directory(request.get("directory", ""))
        target = request.get("path")
        if not isinstance(target, str):
            raise ValueError("Directory path must be text.")

        # replace windows paths with unix paths and check if path is valid
        normalized_target = target.strip().replace("\\", "/")
        if normalized_target == "/":
            parts = []
        elif normalized_target.startswith("/"):
            raise ValueError("Use a path relative to the current server directory.")
        else:
            parts = list(
                relative_storage_path(current_directory).split("/")
                if relative_storage_path(current_directory)
                else []
            )
            for part in normalized_target.split("/"):
                if part in {"", "."}:
                    continue
                if part == "..":
                    if parts:
                        parts.pop() # go back a step in the directory on `CD ..`
                elif len(part) >= 2 and part[1] == ":":
                    raise ValueError("Use a path relative to the current server directory.")
                else:
                    parts.append(part) # go forward a step in the directory on `CD <subfolder>`
                    # this rebuilds the whole path from scratch, going up or down 

        target_path = resolve_storage_path("/".join(parts), allow_root=True)
        if not target_path.is_dir():
            # make sure directory exists
            send_response(connection, {"ok": False, "error": "Directory not found."})
            return
        path = relative_storage_path(target_path)
        send_response(connection, {"ok": True, "path": path})
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})

# delete a file
def delete_file(connection, request):
    filename = request.get("filename")
    # validat filename
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return

    # make sure filename exists in the directory
    try:
        directory = resolve_current_directory(request.get("directory", ""))
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    # make sure directory is not a symlink
    target = directory / filename
    if target.is_symlink() or not target.is_file():
        send_response(connection, {"ok": False, "error": "File not found."})
        return
    try:
        # delete file
        target.unlink()
        send_response(connection, {"ok": True, "message": f"Deleted {filename}."})
    except FileNotFoundError:
        send_response(connection, {"ok": False, "error": "File not found."})
    except OSError as error:
        print(f"Could not delete file {target}: {error}")
        send_response(connection, {"ok": False, "error": "Could not delete server file."})

# receive the upload, write to temp file, move to storage, send response
def receive_upload(connection, reader, request):
    filename = request.get("filename")
    size = request.get("size")
    overwrite = request.get("overwrite", False)
    # validate filename, size, and overwrite option
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return
    if type(size) is not int or size < 0:
        send_response(connection, {"ok": False, "error": "Invalid file size."})
        return
    if not isinstance(overwrite, bool):
        send_response(connection, {"ok": False, "error": "Invalid overwrite option."})
        return

    # make sure the directory exists and is valid
    try:
        directory = resolve_current_directory(request.get("directory", ""))
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    destination = directory / filename
    destination_exists = destination.exists() or destination.is_symlink()
    if destination.is_dir():
        # make sure a directory with that name doesnt already exist
        send_response(connection, {"ok": False, "error": "A directory with that name exists."})
        return
    if destination_exists and not overwrite:
        # if overwrite is false, do not upload the file
        send_response(connection, {"ok": False, "exists": True, "error": "File already exists."})
        return

    # write to temp file, move to storage
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=".upload-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            # recieve the file in chunks
            temporary_path = Path(temporary_file.name)
            send_response(connection, {"ok": True, "ready": True})

            remaining = size
            while remaining:
                chunk = reader.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Client disconnected before upload completed.")
                temporary_file.write(chunk)
                remaining -= len(chunk)

        if overwrite:
            os.replace(temporary_path, destination) # replace file if overwriting
        else:
            # linking fails atomically if another client created the name meanwhile.
            os.link(temporary_path, destination)
            temporary_path.unlink()
        temporary_path = None
        send_response(connection, {"ok": True, "message": f"Uploaded {filename} ({size} bytes)."})
    except FileExistsError:
        send_response(connection, {"ok": False, "error": "File already exists."})
    except OSError as error:
        print(f"Upload of {filename} failed: {error}")
        try:
            send_response(connection, {"ok": False, "error": "Upload failed on the server."})
        except OSError as send_error:
            print(f"Could not send upload error to {filename}: {send_error}")
    finally:
        # make sure temp file is deleted
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                print(f"Could not remove temporary upload {temporary_path}: {error}")

# send requested file to client
def send_download(connection, filename, relative_directory=""):
    # validate filename
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return

    # make sure directory exists and is valid
    try:
        directory = resolve_current_directory(relative_directory)
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    # make sure file exists and is not a symlink
    source = directory / filename
    if source.is_symlink() or not source.is_file():
        send_response(connection, {"ok": False, "error": "File not found."})
        return

    try:
        # open the file and get its size
        source_file = source.open("rb")
        size = source.stat().st_size
    except FileNotFoundError:
        send_response(connection, {"ok": False, "error": "File not found."})
        return
    except OSError as error:
        print(f"Download of {filename} failed: {error}")
        send_response(connection, {"ok": False, "error": "Could not read server file."})
        return

    # send file to server in chunks, ensure file is not modified during download
    with source_file:
        send_response(connection, {"ok": True, "size": size})
        remaining = size
        while remaining:
            chunk = source_file.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                print(f"Download of {filename} stopped because the source file changed.")
                return
            try:
                connection.sendall(chunk)
            except OSError as error:
                print(f"Sending {filename} to the client failed: {error}")
                return
            remaining -= len(chunk)

# handle a single command from the client, send response
def handle_command(connection, reader, request):
    if not request.endswith(b"\n") or len(request) > 4096:
        send_response(
            connection,
            {"ok": False, "error": "Request must be a command of at most 4095 bytes."},
        )
        return

    # list all items in the current directory
    if request.strip().upper() == b"DIR":
        list_directory(connection, {})
        return

    try:
        # load the request as json
        command = json.loads(request.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        send_response(connection, {"ok": False, "error": "Invalid JSON request."})
        return
    # handle invalid requests
    if not isinstance(command, dict):
        send_response(connection, {"ok": False, "error": "Invalid JSON request."})
    elif command.get("command") == "DIR":
        # list the directory contents
        list_directory(connection, command)
    elif command.get("command") == "CD":
        # change the current directory upward or downwards
        change_directory(connection, command)
    elif command.get("command") == "UPLOAD":
        # revieve the sent file from client and store in server storage
        receive_upload(connection, reader, command)
    elif command.get("command") == "DOWNLOAD":
        # send the requested file to the client
        send_download(
            connection,
            command.get("filename"),
            command.get("directory", ""),
        )
    elif command.get("command") == "DELETE":
        # delete the requested file from the server storage
        delete_file(connection, command)
    elif command.get("command") == "SUBFOLDER":
        # create or delete a subfolder in the current directory
        handle_subfolder(connection, command)
    else:
        # offer a list of supported commands if the command is not recognized
        send_response(
            connection,
            {
                "ok": False,
                "error": "Unsupported command. Try DIR, CD, UPLOAD, DOWNLOAD, DELETE, or SUBFOLDER.",
            },
        )


# connect securely and authenticate before handling one client command
def handle_client(connection, address, tls_context, credentials):
    print(f"Connected by {address}")
    try:
        # handle secure connection and user/pass authentication
        with tls_context.wrap_socket(connection, server_side=True) as secure_connection:
            with secure_connection.makefile("rb") as reader:
                auth_line = reader.readline(4097)
                if not auth_line.endswith(b"\n") or len(auth_line) > 4096:
                    send_response(secure_connection, {"ok": False, "error": "Invalid authentication request."})
                    return
                try:
                    auth_request = json.loads(auth_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    send_response(secure_connection, {"ok": False, "error": "Invalid authentication request."})
                    return

                # attempt to authenticate
                if (
                    not isinstance(auth_request, dict)
                    or auth_request.get("command") != "AUTH"
                    or not verify_credentials(
                        auth_request.get("username"),
                        auth_request.get("password"),
                        credentials,
                    )
                ):
                    send_response(
                        secure_connection,
                        {"ok": False, "error": "Authentication failed."},
                    )
                    return

                # authentication success!!! yayyyyy
                send_response(secure_connection, {"ok": True, "message": "Authenticated."})
                request = reader.readline(4097)
                if request:
                    handle_command(secure_connection, reader, request)
    except (ssl.SSLError, OSError) as error:
        print(f"Secure connection from {address} failed: {error}")

# driver code
def main():
    credentials = load_auth_credentials()
    tls_context = create_tls_context() # handle user and pass and tls
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"File storage: {STORAGE_DIR}")
    print("TLS and password authentication enabled.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((HOST, PORT))
        server.listen()
        print(f"Listening on {HOST}:{PORT}")

        while True:
            connection, address = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(connection, address, tls_context, credentials),
                daemon=True,
            )
            thread.start()
            # new thread for each client

if __name__ == "__main__":
    if sys.argv[1:] == ["--create-password-hash"]:
        # create a password hash if the function is requested, otherwise start the server normally
        try:
            create_password_hash()
        except ValueError as error:
            raise SystemExit(str(error)) from error
    else:
        main()
