# created 2026-11-04 by dani/Androtis, remade 2026-11-23 by hosevana
# last modified: 2026-11-27 by nia
# server driver code, ran on gcp compute engine
# working of 2026-11-25
import errno
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import ssl
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path
import metrics

HOST = "0.0.0.0"
PORT = 3300
CHUNK_SIZE = 64 * 1024
PASSWORD_HASH_ITERATIONS = 600_000
TEXT_EXTENSIONS = {
    ".txt", ".text", ".csv", ".json", ".xml", ".html",
}
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".mpg", ".mpeg", ".mp4",
}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
}
STORAGE_DIR = Path(
    os.environ.get("FILE_STORAGE_DIR", "/mnt/disks/file-storage")
).expanduser().resolve()
UPLOAD_NAME_LOCK = threading.Lock()
RESERVED_UPLOAD_NAMES = set()

# send response function
def send_response(connection, response):
    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )


def create_password_hash():
    password = getpass.getpass("Create server password: ")
    confirmation = getpass.getpass("Confirm server password: ")
    if not password or password != confirmation:
        raise ValueError("Passwords must be non-empty and match.")

    salt = secrets.token_bytes(16)
    password_hash = hash_password(password, salt)
    print(f"FILESERVER_PASSWORD_SALT={salt.hex()}")
    print(f"FILESERVER_PASSWORD_HASH={password_hash.hex()}")


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


def verify_credentials(username, password, credentials):
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    expected_username, salt, expected_hash = credentials
    try:
        candidate_hash = hash_password(password, salt)
        username_bytes = username.encode("utf-8")
        expected_username_bytes = expected_username.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(
        username_bytes,
        expected_username_bytes,
    ) and hmac.compare_digest(candidate_hash, expected_hash)


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
    if (
        not isinstance(filename, str)
        or filename in {"", ".", ".."}
        or filename.endswith((" ", "."))
        or any(char in '<>:"/\\|?*' for char in filename)
        or any(unicodedata.category(char) == "Cc" for char in filename)
    ):
        return False

    try:
        if len(filename.encode("utf-8")) > 255:
            return False
    except UnicodeEncodeError:
        return False

    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        "CLOCK$",
    }
    reserved_names.update(f"COM{number}" for number in range(1, 10))
    reserved_names.update(f"LPT{number}" for number in range(1, 10))
    reserved_names.update(f"COM{number}" for number in "¹²³")
    reserved_names.update(f"LPT{number}" for number in "¹²³")
    stem = filename.split(".", 1)[0].rstrip(" .").upper()
    return stem not in reserved_names


def allocate_upload_filename(original_filename):
    extension = Path(original_filename).suffix
    extension_type = extension.lower()
    if extension_type in TEXT_EXTENSIONS:
        prefix = "TS"
    elif extension_type in AUDIO_EXTENSIONS:
        prefix = "AS"
    elif extension_type in VIDEO_EXTENSIONS:
        prefix = "VS"
    elif extension_type in IMAGE_EXTENSIONS:
        prefix = "IS"
    else:
        prefix = "OS"

    sequence_pattern = re.compile(rf"^{prefix}(\d+)(?:\.|$)", re.IGNORECASE)
    with UPLOAD_NAME_LOCK:
        existing_names = set()
        highest_number = 0
        for path in STORAGE_DIR.rglob("*"):
            existing_names.add(path.name.casefold())
            match = sequence_pattern.match(path.name)
            if match:
                highest_number = max(highest_number, int(match.group(1)))

        for name in RESERVED_UPLOAD_NAMES:
            match = sequence_pattern.match(name)
            if match:
                highest_number = max(highest_number, int(match.group(1)))

        number = highest_number + 1
        while True:
            generated_name = f"{prefix}{number:03d}{extension}"
            if not valid_filename(generated_name):
                raise ValueError("The uploaded file extension cannot be used in a server filename.")
            name_key = generated_name.casefold()
            if name_key not in existing_names and name_key not in RESERVED_UPLOAD_NAMES:
                RESERVED_UPLOAD_NAMES.add(name_key)
                return generated_name
            number += 1


def release_upload_filename(filename):
    with UPLOAD_NAME_LOCK:
        RESERVED_UPLOAD_NAMES.discard(filename.casefold())


# ensure filepath is valid and inside storage, return resolved path
def resolve_storage_path(relative_path, allow_root=False):
    storage_root = STORAGE_DIR.resolve()
    if not isinstance(relative_path, str):
        raise ValueError("Folder path must be text.")
    normalized = relative_path.strip().replace("\\", "/")
    if allow_root and normalized in {"", "."}:
        return storage_root
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

    try:
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
        if action == "CREATE":
            folder.mkdir(parents=True, exist_ok=False)
            send_response(connection, {"ok": True, "message": f"Created {request['path']}."})
        else:
            if not folder.is_dir():
                send_response(connection, {"ok": False, "error": "Subfolder not found."})
                return
            folder.rmdir()
            send_response(connection, {"ok": True, "message": f"Deleted empty folder {request['path']}."})
    except FileExistsError:
        send_response(connection, {"ok": False, "error": "That path already exists."})
    except FileNotFoundError:
        send_response(connection, {"ok": False, "error": "Subfolder not found."})
    except OSError as error:
        print(f"Could not {action.lower()} subfolder {request.get('path')}: {error}")
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
        current_directory = resolve_current_directory(request.get("directory", ""))
        target = request.get("path")
        if not isinstance(target, str):
            raise ValueError("Directory path must be text.")

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
                        parts.pop()
                elif len(part) >= 2 and part[1] == ":":
                    raise ValueError("Use a path relative to the current server directory.")
                else:
                    parts.append(part)

        target_path = resolve_storage_path("/".join(parts), allow_root=True)
        if not target_path.is_dir():
            send_response(connection, {"ok": False, "error": "Directory not found."})
            return
        path = relative_storage_path(target_path)
        send_response(connection, {"ok": True, "path": path})
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})

# delete a file
def delete_file(connection, request):
    filename = request.get("filename")
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return

    try:
        directory = resolve_current_directory(request.get("directory", ""))
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    target = directory / filename
    if target.is_symlink() or not target.is_file():
        send_response(connection, {"ok": False, "error": "File not found."})
        return
    try:
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
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return
    if type(size) is not int or size < 0:
        send_response(connection, {"ok": False, "error": "Invalid file size."})
        return

    try:
        directory = resolve_current_directory(request.get("directory", ""))
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    try:
        stored_filename = allocate_upload_filename(filename)
    except (OSError, ValueError) as error:
        print(f"Could not assign a server filename to {filename}: {error}")
        send_response(connection, {"ok": False, "error": "Could not assign a server filename."})
        return

    destination = directory / stored_filename
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=".upload-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            send_response(
                connection,
                {"ok": True, "ready": True, "filename": stored_filename},
            )

            remaining = size
            while remaining:
                chunk = reader.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Client disconnected before upload completed.")
                temporary_file.write(chunk)
                remaining -= len(chunk)

        os.link(temporary_path, destination)
        temporary_path.unlink()
        temporary_path = None
        send_response(
            connection,
            {
                "ok": True,
                "filename": stored_filename,
                "message": f"Uploaded as {stored_filename} ({size} bytes).",
            },
        )
    except FileExistsError:
        send_response(
            connection,
            {"ok": False, "error": "Generated filename collision; please retry the upload."},
        )
    except OSError as error:
        print(f"Upload of {filename} failed: {error}")
        try:
            send_response(connection, {"ok": False, "error": "Upload failed on the server."})
        except OSError as send_error:
            print(f"Could not send upload error to {filename}: {send_error}")
    finally:
        release_upload_filename(stored_filename)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                print(f"Could not remove temporary upload {temporary_path}: {error}")

# send requested file to client
def send_download(connection, filename, relative_directory=""):
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return

    try:
        directory = resolve_current_directory(relative_directory)
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    source = directory / filename
    if source.is_symlink() or not source.is_file():
        send_response(connection, {"ok": False, "error": "File not found."})
        return

    try:
        source_file = source.open("rb")
        size = source.stat().st_size
    except FileNotFoundError:
        send_response(connection, {"ok": False, "error": "File not found."})
        return
    except OSError as error:
        print(f"Download of {filename} failed: {error}")
        send_response(connection, {"ok": False, "error": "Could not read server file."})
        return

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


def send_metrics_reports(connection):
    try:
        with metrics.metrics_lock:
            metrics.save_report()
    except OSError as error:
        print(f"Could not save metrics reports: {error}")
        send_response(connection, {"ok": False, "error": "Could not save metrics reports."})
        return

    report_paths = (
        Path(metrics.JSON_REPORT_PATH),
        Path(metrics.CSV_REPORT_PATH),
    )
    opened_reports = []
    try:
        reports = []
        for path in report_paths:
            report_file = path.open("rb")
            opened_reports.append(report_file)
            reports.append(
                {
                    "filename": path.name,
                    "size": os.fstat(report_file.fileno()).st_size,
                }
            )
    except OSError as error:
        for report_file in opened_reports:
            report_file.close()
        print(f"Could not open metrics reports: {error}")
        send_response(connection, {"ok": False, "error": "Metrics reports are unavailable."})
        return

    try:
        send_response(connection, {"ok": True, "files": reports})
        for report_file, report in zip(opened_reports, reports):
            remaining = report["size"]
            while remaining:
                chunk = report_file.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise OSError(f"{report['filename']} changed while being sent.")
                connection.sendall(chunk)
                remaining -= len(chunk)
    except OSError as error:
        print(f"Sending metrics reports to the client failed: {error}")
    finally:
        for report_file in opened_reports:
            report_file.close()


def handle_command(connection, reader, request, address):
    if not request.endswith(b"\n") or len(request) > 4096:
        send_response(
            connection,
            {"ok": False, "error": "Request must be a command of at most 4095 bytes."},
        )
        return
    if request.strip().upper() == b"DIR":
        metrics.record_request_metadata((address[0], address[1]), "DIR")
        list_directory(connection, {})
        return

    try:
        command = json.loads(request.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        send_response(connection, {"ok": False, "error": "Invalid JSON request."})
        return

    if not isinstance(command, dict):
        send_response(connection, {"ok": False, "error": "Invalid JSON request."})
        return

    metrics.record_request_metadata(
        (address[0], address[1]),
        command.get("command"),
        command.get("filename") or command.get("path") or "",
    )
    if command.get("command") == "DIR":
        list_directory(connection, command)
    elif command.get("command") == "CD":
        change_directory(connection, command)
    elif command.get("command") == "UPLOAD":
        receive_upload(connection, reader, command)
    elif command.get("command") == "DOWNLOAD":
        send_download(
            connection,
            command.get("filename"),
            command.get("directory", ""),
        )
    elif command.get("command") == "DELETE":
        delete_file(connection, command)
    elif command.get("command") == "SUBFOLDER":
        handle_subfolder(connection, command)
    elif command.get("command") == "METRICS":
        send_metrics_reports(connection)
    else:
        send_response(
            connection,
            {
                "ok": False,
                "error": "Unsupported command. Try DIR, CD, UPLOAD, DOWNLOAD, DELETE, SUBFOLDER, or METRICS.",
            },
        )


# Wrap each connection in TLS and authenticate before dispatching a command.
def handle_client(connection, address, tls_context, credentials):
    print(f"Connected by {address}")
    try:
        with tls_context.wrap_socket(connection, server_side=True) as secure_connection:
            with secure_connection.makefile("rb") as reader:
                auth_line = reader.readline(4097)
                if not auth_line.endswith(b"\n") or len(auth_line) > 4096:
                    send_response(
                        secure_connection,
                        {"ok": False, "error": "Invalid authentication request."},
                    )
                    return
                try:
                    auth_request = json.loads(auth_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    send_response(
                        secure_connection,
                        {"ok": False, "error": "Invalid authentication request."},
                    )
                    return

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

                send_response(
                    secure_connection,
                    {"ok": True, "message": "Authenticated."},
                )
                request = reader.readline(4097)
                if request:
                    handle_command(secure_connection, reader, request, address)
    except (ssl.SSLError, OSError) as error:
        print(f"Secure connection from {address} failed: {error}")

# driver code
def main():
    credentials = load_auth_credentials()
    tls_context = create_tls_context()
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"File storage: {STORAGE_DIR}")
    print("TLS and password authentication enabled.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((HOST, PORT))
        server.listen()
        print(f"Listening on {HOST}:{PORT}")

        metrics.start_metrics(PORT)

        while True:
            connection, address = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(connection, address, tls_context, credentials),
                daemon=True,
            )
            thread.start()


if __name__ == "__main__":
    if sys.argv[1:] == ["--create-password-hash"]:
        try:
            create_password_hash()
        except ValueError as error:
            raise SystemExit(str(error)) from error
    else:
        main()
