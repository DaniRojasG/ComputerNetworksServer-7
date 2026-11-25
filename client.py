# Client Code
import json
import socket
from pathlib import Path

# client and server should match port
host = '34.71.48.46'
port = 3300
CHUNK_SIZE = 64 * 1024


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


if __name__ == '__main__':
    while True:
        command = input('Enter DIR, UPLOAD <local-file-path>, or q to quit: ').strip()
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
            else:
                print("Use DIR or UPLOAD <local-file-path>.")
        except OSError as error:
            print(f"Request failed: {error}")