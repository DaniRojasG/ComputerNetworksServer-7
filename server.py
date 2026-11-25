# created 2026-11-04 by dani/Androtis, remade 2026-11-23 by hosevana
# last modified: 2026-11-24 by hosevana
# server driver code, ran on gcp compute engine
# untested as of 2026-11-24
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


def valid_filename(filename):
    return (
        isinstance(filename, str)
        and filename not in {"", ".", ".."}
        and "/" not in filename
        and "\\" not in filename
        and "\0" not in filename
    )


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

    destination = STORAGE_DIR / filename
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
            dir=STORAGE_DIR,
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


def send_download(connection, filename):
    if not valid_filename(filename):
        send_response(connection, {"ok": False, "error": "Invalid filename."})
        return

    source = STORAGE_DIR / filename
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
                else:
                    send_response(connection, {"ok": False, "error": "Unsupported command."})
            else:
                send_response(
                    connection,
                    {"ok": False, "error": "Unsupported command. Try DIR, UPLOAD, or DOWNLOAD."},
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
