# Client Code
# initially commited 2026-11-04 by dani/Androtis, created 2026-11-23 by hosevana
# last modified 2026-11-27 by nia
# client driver code, ran on pc
# tested as of 2026-11-26
import json
import os
import socket
import tempfile
import unicodedata
from pathlib import Path

import ui

# client and server should match port
host = '34.172.235.4' # server has static ip now
port = 3300
CHUNK_SIZE = 64 * 1024
current_remote_directory = ""

# setup connection to server, send command, print response
def setup_connection(command, directory=""):
    request = {"command": command, "directory": directory}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with client_tcp.makefile("rb") as response_file:
            response = json.loads(response_file.readline().decode("utf-8"))

    if not response["ok"]:
        print(f"Server error: {response['error']}")
    else:
        remote_path = response.get("path", "")
        print(f"Server directory: /{remote_path}" if remote_path else "Server directory: /")
        if not response["entries"]:
            print("This server directory is empty.")
        else:
            for entry in response["entries"]:
                suffix = "/" if entry["type"] == "directory" else ""
                size = f" ({entry['size_bytes']} bytes)" if "size_bytes" in entry else ""
                print(f"{entry['name']}{suffix}{size}")


def valid_upload_filename(filename):
    if (
        not filename
        or filename in {".", ".."}
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


# transfer upload function, send file to server, return response
def transfer_upload(file_path, directory=""):
    source = Path(file_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Not a file: {source}")

    filename = source.name
    if not valid_upload_filename(filename):
        raise ValueError(
            "Unsafe filename. Use a name without Windows-invalid characters, "
            "reserved device names, trailing spaces/dots, or more than 255 UTF-8 bytes."
        )

    size = source.stat().st_size
    request = {
        "command": "UPLOAD",
        "filename": filename,
        "size": size,
        "directory": directory,
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

# upload file and display the server-assigned name
def upload_file(file_path, directory=""):
    response = transfer_upload(file_path, directory=directory)
    if response.get("ok"):
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")

# download file, confirm overwrite if file exists, save to Downloads
def download_file(filename, directory=""):
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\0" in filename
    ):
        print("Enter a filename from the server directory, not a path.")
        return

    download_dir = Path.home() / "Downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    destination = download_dir / filename
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

    request = {
        "command": "DOWNLOAD",
        "filename": filename,
        "directory": directory,
    }
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

# subfolder, creates or deletes a subfolder and prints response
def subfolder_command(action, folder_path, directory=""):
    request = {
        "command": "SUBFOLDER",
        "action": action.upper(),
        "path": folder_path,
        "directory": directory,
    }
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with client_tcp.makefile("rb") as response_file:
            response = json.loads(response_file.readline().decode("utf-8"))

    if response["ok"]:
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")

# delete file, send delete command to server
def delete_file(filename, directory=""):
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\0" in filename
    ):
        print("Enter a filename from the current server directory.")
        return

    confirmation = input(f"Delete {filename} from the server? [y/N]: ").strip().lower()
    if confirmation not in {"y", "yes"}:
        print("Delete cancelled.")
        return

    request = {
        "command": "DELETE",
        "filename": filename,
        "directory": directory,
    }
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with client_tcp.makefile("rb") as response_file:
            response = json.loads(response_file.readline().decode("utf-8"))

    if response["ok"]:
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")

# send the change directory command
def change_directory(path):
    global current_remote_directory
    request = {
        "command": "CD",
        "directory": current_remote_directory,
        "path": path,
    }
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with client_tcp.makefile("rb") as response_file:
            response = json.loads(response_file.readline().decode("utf-8"))

    if response["ok"]:
        current_remote_directory = response["path"]
        shown_path = f"/{current_remote_directory}" if current_remote_directory else "/"
        print(f"Changed server directory to {shown_path}")
    else:
        print(f"Server error: {response['error']}")


# driver code
if __name__ == '__main__':
    ui.question_line()
    while True:
        shown_path = f"/{current_remote_directory}" if current_remote_directory else "/"
        command = input(f"Server:{shown_path}> ").strip()
        if command.lower() == 'q':
            ui.quit_message()
            break
        try:
            operation, separator, argument = command.partition(" ")
            if operation.upper() == "DIR":
                setup_connection("DIR", current_remote_directory)
            elif operation.upper() == "UPLOAD" and separator and argument.strip():
                file_path = argument.strip()
                if len(file_path) >= 2 and file_path[0] == file_path[-1] and file_path[0] in {"'", '"'}:
                    file_path = file_path[1:-1]
                upload_file(file_path, current_remote_directory)
            elif operation.upper() == "DOWNLOAD" and separator and argument.strip():
                filename = argument.strip()
                if (
                    len(filename) >= 2
                    and filename[0] == filename[-1]
                    and filename[0] in {"'", '"'}
                ):
                    filename = filename[1:-1]
                download_file(filename, current_remote_directory)
            elif operation.upper() == "DELETE" and separator and argument.strip():
                filename = argument.strip()
                if len(filename) >= 2 and filename[0] == filename[-1] and filename[0] in {"'", '"'}:
                    filename = filename[1:-1]
                delete_file(filename, current_remote_directory)
            elif operation.upper() == "CD" and separator and argument.strip():
                path = argument.strip()
                if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"'}:
                    path = path[1:-1]
                change_directory(path)
            elif operation.upper() == "SUBFOLDER" and separator and argument.strip():
                action, action_separator, folder_path = argument.strip().partition(" ")
                if action_separator and folder_path.strip():
                    folder_path = folder_path.strip()
                    if (
                        len(folder_path) >= 2
                        and folder_path[0] == folder_path[-1]
                        and folder_path[0] in {"'", '"'}
                    ):
                        folder_path = folder_path[1:-1]
                    subfolder_command(action, folder_path, current_remote_directory)
                else:
                    print("Use SUBFOLDER CREATE <path> or SUBFOLDER DELETE <path>.")
            else:
                ui.question_line()
        except (OSError, ValueError) as error:
            print(f"Request failed: {error}")