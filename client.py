# Client Code
import json
import socket

# client and server should match port
host = '34.71.61.218'
port = 3300


def setup_connection(command):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_tcp:
        client_tcp.connect((host, port))
        client_tcp.sendall((command + "\n").encode("utf-8"))
        with client_tcp.makefile("rb") as response_file:
            response = json.loads(response_file.readline().decode("utf-8"))

    if not response["ok"]:
        print(f"Server error: {response['error']}")
    elif not response["entries"]:
        print("Server storage is empty.")
    else:
        for entry in response["entries"]:
            suffix = "/" if entry["type"] == "directory" else ""
            size = f" ({entry['size_bytes']} bytes)" if "size_bytes" in entry else ""
            print(f"{entry['name']}{suffix}{size}")


if __name__ == '__main__':
    while True:
        command = input('Enter DIR to list server files, or q to quit: ').strip()
        if command.lower() == 'q':
            break
        try:
            setup_connection(command)
        except OSError as error:
            print(f"Could not connect to server: {error}")