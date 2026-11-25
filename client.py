# Client Code
# initially commited 2026-11-04 by dani/Androtis, created 2026-11-23 by hosevana
# last modified 2026-11-24 by hosevana
# client driver code, ran on pc
# untested as of 2026-11-24
import json
import os
import socket
import tempfile
from pathlib import Path

# client and server should match port
host = '34.16.58.20'
port = 3300
CHUNK_SIZE = 64 * 1024

# setup connection to server, send command, print response
def setup_connection(command):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        client_tcp.sendall((command + "\n").encode("utf-8"))
        with client_tcp.makefile("rb") as response_file:
            response = json.loads(response_file.readline().decode("utf-8"))

    if not response["ok"]:
        print(f"Server error: {response['error']}")
    elif not response.get("entries"):
        print("Server storage is empty.")
    else:
        for entry in response["entries"]:
            suffix = "/" if entry["type"] == "directory" else ""
            size = f" ({entry['size_bytes']} bytes)" if "size_bytes" in entry else ""
            print(f"{entry['name']}{suffix}{size}")

# transfer upload function, send file to server, return response
def transfer_upload(file_path, overwrite=False):
    source = Path(file_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Not a file: {source}")

    size = source.stat().st_size
    request = {
        "command": "UPLOAD",
        "filename": source.name,
        "size": size,
        "overwrite": overwrite,
    }

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        with client_tcp.makefile("rb") as response_file:
            client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = json.loads(response_file.readline().decode("utf-8"))
            if not response.get("ok"):
                return response

            remaining = size
            with source.open("rb") as source_file:
                while remaining:
                    chunk = source_file.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise OSError(f"{source} changed while it was being uploaded.")
                    client_tcp.sendall(chunk)
                    remaining -= len(chunk)

            return json.loads(response_file.readline().decode("utf-8"))

# upload file function, confirm overwrite if file already exists, call transfer_upload
def upload_file(file_path):
    response = transfer_upload(file_path)
    if response.get("exists"):
        confirmation = input(
            f"{Path(file_path).name} already exists on the server. Overwrite? [y/N]: "
        ).strip().lower()
        if confirmation not in {"y", "yes"}:
            print("Upload cancelled.")
            return
        response = transfer_upload(file_path, overwrite=True)

    if response.get("ok"):
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")


def download_file(filename):
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\0" in filename
    ):
        print("Enter a filename from the server directory, not a path.")
        return

    destination = Path.cwd() / filename
    if destination.is_dir():
        print(f"A directory named {filename} already exists here.")
        return
    if destination.exists():
        confirmation = input(
            f"{destination} already exists. Overwrite? [y/N]: "
        ).strip().lower()
        if confirmation not in {"y", "yes"}:
            print("Download cancelled.")
            return

    request = {"command": "DOWNLOAD", "filename": filename}
    temporary_path = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
            client_tcp.connect((host, port))
            with client_tcp.makefile("rb") as response_file:
                client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
                response = json.loads(response_file.readline().decode("utf-8"))
                if not response.get("ok"):
                    print(f"Server error: {response['error']}")
                    return

                size = response.get("size")
                if type(size) is not int or size < 0:
                    raise OSError("Server returned an invalid file size.")

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=f".{filename}.",
                    suffix=".part",
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    remaining = size
                    while remaining:
                        chunk = response_file.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            raise OSError("Connection ended before the download completed.")
                        temporary_file.write(chunk)
                        remaining -= len(chunk)

        os.replace(temporary_path, destination)
        temporary_path = None
        print(f"Downloaded {filename} to {destination}")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                print(f"Could not remove temporary download {temporary_path}: {error}")


# driver code
if __name__ == '__main__':
    while True:
        command = input(
            'Enter DIR, UPLOAD <local-file-path>, DOWNLOAD <filename>, or q to quit: '
        ).strip()
        if command.lower() == 'q':
            break
        try:
            operation, separator, argument = command.partition(" ")
            if operation.upper() == "DIR":
                setup_connection("DIR")
            elif operation.upper() == "UPLOAD" and separator and argument.strip():
                file_path = argument.strip()
                if len(file_path) >= 2 and file_path[0] == file_path[-1] and file_path[0] in {"'", '"'}:
                    file_path = file_path[1:-1]
                upload_file(file_path)
            elif operation.upper() == "DOWNLOAD" and separator and argument.strip():
                filename = argument.strip()
                if (
                    len(filename) >= 2
                    and filename[0] == filename[-1]
                    and filename[0] in {"'", '"'}
                ):
                    filename = filename[1:-1]
                download_file(filename)
            else:
                print("Use DIR, UPLOAD <local-file-path>, or DOWNLOAD <filename>.")
        except OSError as error:
            print(f"Request failed: {error}")