# Client Code
# initially commited 2026-11-04 by dani/Androtis, created 2026-11-23 by hosevana
# last modified 2026-11-26 by nia
# client driver code, ran on pc
# tested as of 2026-11-25
import json
import getpass
import os
import socket
import ssl
import tempfile
from contextlib import contextmanager
from pathlib import Path

import ui

# client and server should match port
host = os.environ.get("FILESERVER_HOST", "35.239.196.153")
port = int(os.environ.get("FILESERVER_PORT", "3300"))
CHUNK_SIZE = 64 * 1024
current_remote_directory = ""
username = None
password = None
tls_server_name = os.environ.get("FILESERVER_TLS_NAME", host)
ca_certificate = os.environ.get("FILESERVER_CA_CERT") or None
tls_context = ssl.create_default_context(cafile=ca_certificate)
tls_context.minimum_version = ssl.TLSVersion.TLSv1_2

# check login
@contextmanager
def authenticated_connection():
    # must have a user and pass
    if not username or password is None:
        raise PermissionError("Log in before sending server commands.")

    with socket.create_connection((host, port)) as raw_socket:
        with tls_context.wrap_socket(
            raw_socket,
            server_hostname=tls_server_name,
        ) as client_tcp:
            with client_tcp.makefile("rb") as response_file:
                auth_request = {
                    "command": "AUTH",
                    "username": username,
                    "password": password,
                }
                # send the auth request with the user and pass
                client_tcp.sendall((json.dumps(auth_request) + "\n").encode("utf-8"))
                auth_response = json.loads(response_file.readline().decode("utf-8"))
                if not auth_response.get("ok"):
                    raise PermissionError(auth_response.get("error", "Authentication failed."))
                yield client_tcp, response_file

# prompt user for login and authenticate connection
def prompt_for_login():
    global username, password
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    if not username or not password:
        raise ValueError("Username and password must not be empty.")
    with authenticated_connection():
        pass
    print("Login successful.")

# setup connection to server, send command, print response
def setup_connection(command, directory=""):
    request = {"command": command, "directory": directory}
    with authenticated_connection() as (client_tcp, response_file):
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
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

# transfer upload function, send file to server, return response
def transfer_upload(file_path, overwrite=False, directory=""):
    # ensure the file and a path to it exists
    source = Path(file_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Not a file: {source}")
    
    size = source.stat().st_size
    request = {
        "command": "UPLOAD",
        "filename": source.name,
        "size": size,
        "overwrite": overwrite,
        "directory": directory,
    }

    # send the upload request, send the file in chunks
    with authenticated_connection() as (client_tcp, response_file):
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = json.loads(response_file.readline().decode("utf-8"))
        if not response.get("ok"):
            return response

        # send in chunks properly, make sure file doesn't change during upload
        remaining = size
        with source.open("rb") as source_file:
            while remaining:
                chunk = source_file.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise OSError(f"{source} changed while it was being uploaded.")
                client_tcp.sendall(chunk)
                remaining -= len(chunk)
        # return response
        return json.loads(response_file.readline().decode("utf-8"))

# upload file function, confirm overwrite if file already exists, call transfer_upload
def upload_file(file_path, directory=""):
    # check if file exists and confirm overwrit if so
    response = transfer_upload(file_path, directory=directory)
    if response.get("exists"):
        confirmation = input(
            f"{Path(file_path).name} already exists on the server. Overwrite? [y/N]: "
        ).strip().lower()
        if confirmation not in {"y", "yes"}:
            print("Upload cancelled.")
            return
        response = transfer_upload(file_path, overwrite=True, directory=directory)
    # print server response
    if response.get("ok"):
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")

# download file, confirm overwrite if file exists, save to Downloads
def download_file(filename, directory=""):
    # check for invalid filenames or paths
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\0" in filename
    ):
        print("Enter a filename from the server directory, not a path.")
        return

    # ensure the file is downloaded to Downloads folder, creates one if it doesn't exist
    download_dir = Path.home() / "Downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    destination = download_dir / filename
    # exit if a directory with the filename already exists
    if destination.is_dir():
        print(f"A directory named {filename} already exists here.")
        return
    # confirm overwrite if the file already exists
    if destination.exists():
        confirmation = input(
            f"{destination} already exists. Overwrite? [y/N]: "
        ).strip().lower()
        if confirmation not in {"y", "yes"}:
            print("Download cancelled.")
            return

    # request to send to the server
    request = {
        "command": "DOWNLOAD",
        "filename": filename,
        "directory": directory,
    }
    temporary_path = None
    try:
        # send the request to the server and read response if not ok
        with authenticated_connection() as (client_tcp, response_file):
            client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = json.loads(response_file.readline().decode("utf-8"))
            if not response.get("ok"):
                print(f"Server error: {response['error']}")
                return

            # send the file in chunks, check for valid size
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
        # handle errors with temp files
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                print(f"Could not remove temporary download {temporary_path}: {error}")

# subfolder command, creates or deletes a subfolder and prints response
def subfolder_command(action, folder_path, directory=""):
    # request sent to server
    request = {
        "command": "SUBFOLDER",
        "action": action.upper(), # either CREATE or DELETE
        "path": folder_path,
        "directory": directory,
    }
    # send request to server
    with authenticated_connection() as (client_tcp, response_file):
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = json.loads(response_file.readline().decode("utf-8"))

    if response["ok"]:
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")

# delete file, send delete command to server
def delete_file(filename, directory=""):
    # ensure filename is valid and not a directory
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\0" in filename
    ):
        print("Enter a filename from the current server directory.")
        return

    # confirm deletion
    confirmation = input(f"Delete {filename} from the server? [y/N]: ").strip().lower()
    if confirmation not in {"y", "yes"}:
        print("Delete cancelled.")
        return

    # request sent to server
    request = {
        "command": "DELETE",
        "filename": filename,
        "directory": directory,
    }
    # send request and have server confirm
    with authenticated_connection() as (client_tcp, response_file):
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = json.loads(response_file.readline().decode("utf-8"))

    if response["ok"]:
        print(response["message"])
    else:
        print(f"Server error: {response['error']}")

# send the change directory command
def change_directory(path):
    global current_remote_directory
    # request sent to server
    request = {
        "command": "CD",
        "directory": current_remote_directory,
        "path": path,
    }
    # send request to server
    with authenticated_connection() as (client_tcp, response_file):
        client_tcp.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = json.loads(response_file.readline().decode("utf-8"))

    if response["ok"]:
        current_remote_directory = response["path"]
        shown_path = f"/{current_remote_directory}" if current_remote_directory else "/"
        print(f"Changed server directory to {shown_path}")
    else:
        print(f"Server error: {response['error']}")

# driver code
if __name__ == '__main__':
    try:
        # prompt user for login, confirm, and connect
        prompt_for_login()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Login failed: {error}")
        raise SystemExit(1)

    ui.question_line()
    while True:
        # display the current path where the prompts are directed
        shown_path = f"/{current_remote_directory}" if current_remote_directory else "/"
        command = input(f"Server:{shown_path}> ").strip()
        if command.lower() == 'q':
            # exit program
            ui.quit_message()
            break
        try:
            # parse user's command and call the appropriate function
            operation, separator, argument = command.partition(" ")
            if operation.upper() == "DIR":
                # display all items in the current server directory
                setup_connection("DIR", current_remote_directory)
            elif operation.upper() == "UPLOAD" and separator and argument.strip():
                # upload a file to server
                file_path = argument.strip()
                if len(file_path) >= 2 and file_path[0] == file_path[-1] and file_path[0] in {"'", '"'}:
                    file_path = file_path[1:-1]
                upload_file(file_path, current_remote_directory)
            elif operation.upper() == "DOWNLOAD" and separator and argument.strip():
                # download a file from server
                filename = argument.strip()
                if (
                    len(filename) >= 2
                    and filename[0] == filename[-1]
                    and filename[0] in {"'", '"'}
                ):
                    filename = filename[1:-1]
                download_file(filename, current_remote_directory)
            elif operation.upper() == "DELETE" and separator and argument.strip():
                # delete a file from the server
                filename = argument.strip()
                if len(filename) >= 2 and filename[0] == filename[-1] and filename[0] in {"'", '"'}:
                    filename = filename[1:-1]
                delete_file(filename, current_remote_directory)
            elif operation.upper() == "CD" and separator and argument.strip():
                # change the current server directory
                path = argument.strip()
                if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"'}:
                    path = path[1:-1]
                change_directory(path)
            elif operation.upper() == "SUBFOLDER" and separator and argument.strip():
                # create or delete a subfolder in the current server directory
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
        except OSError as error:
            print(f"Request failed: {error}")
            