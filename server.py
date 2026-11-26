# created 2026-11-04 by dani/Androtis, remade 2026-11-23 by hosevana
# last modified: 2026-11-25 by hosevana
# server driver code, ran on gcp compute engine
# working of 2026-11-25
import errno
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

HOST = "0.0.0.0"
PORT = 3300
CHUNK_SIZE = 64 * 1024
STORAGE_DIR = Path(
    os.environ.get("FILE_STORAGE_DIR", "/mnt/disks/file-storage")
).expanduser().resolve()

# send response function
def send_response(connection, response):
    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))

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
    overwrite = request.get("overwrite", False)
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return
    if type(size) is not int or size < 0:
        send_response(connection, {"ok": False, "error": "Invalid file size."})
        return
    if not isinstance(overwrite, bool):
        send_response(connection, {"ok": False, "error": "Invalid overwrite option."})
        return

    try:
        directory = resolve_current_directory(request.get("directory", ""))
    except (ValueError, FileNotFoundError) as error:
        send_response(connection, {"ok": False, "error": str(error)})
        return

    destination = directory / filename
    destination_exists = destination.exists() or destination.is_symlink()
    if destination.is_dir():
        send_response(connection, {"ok": False, "error": "A directory with that name exists."})
        return
    if destination_exists and not overwrite:
        send_response(connection, {"ok": False, "exists": True, "error": "File already exists."})
        return

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
            send_response(connection, {"ok": True, "ready": True})

            remaining = size
            while remaining:
                chunk = reader.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Client disconnected before upload completed.")
                temporary_file.write(chunk)
                remaining -= len(chunk)

        if overwrite:
            os.replace(temporary_path, destination)
        else:
            # Linking fails atomically if another client created the name meanwhile.
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


# connect and handle client requests, new thread per client
def handle_client(connection, address):
    print(f"Connected by {address}")
    with connection:
        with connection.makefile("rb") as reader:
            request = reader.readline(4097)
            if not request.endswith(b"\n") or len(request) > 4096:
                send_response(
                    connection,
                    {
                        "ok": False,
                        "error": "Request must be a command of at most 4095 bytes.",
                    },
                )
            elif request.strip().upper() == b"DIR":
                try:
                    entries = []
                    for item in STORAGE_DIR.iterdir():
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
                    send_response(connection, {"ok": True, "entries": entries})
                except OSError as error:
                    print(f"Could not list storage directory {STORAGE_DIR}: {error}")
                    send_response(
                        connection,
                        {"ok": False, "error": "Could not list server storage."},
                    )
            elif request.startswith(b"{"):
                try:
                    upload_request = json.loads(request.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    send_response(connection, {"ok": False, "error": "Invalid JSON request."})
                    return
                if not isinstance(upload_request, dict):
                    send_response(connection, {"ok": False, "error": "Invalid JSON request."})
                elif upload_request.get("command") == "UPLOAD":
                    receive_upload(connection, reader, upload_request)
                elif upload_request.get("command") == "DOWNLOAD":
                    send_download(connection, upload_request.get("filename"))
                elif upload_request.get("command") == "SUBFOLDER":
                    handle_subfolder(connection, upload_request)
                else:
                    send_response(connection, {"ok": False, "error": "Unsupported command."})
            else:
                send_response(
                    connection,
                    {
                        "ok": False,
                        "error": "Unsupported command. Try DIR, UPLOAD, DOWNLOAD, or SUBFOLDER.",
                    },
                )

# driver code
def main():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"File storage: {STORAGE_DIR}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((HOST, PORT))
        server.listen()
        print(f"Listening on {HOST}:{PORT}")

        while True:
            connection, address = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(connection, address),
                daemon=True,
            )
            thread.start()


if __name__ == "__main__":
    main()
