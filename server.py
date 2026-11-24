# undo everything and start over
import socket
import threading

HOST = "0.0.0.0"
PORT = 3300
STORAGE_DIR = "storage"


def handle_client(connection, address):
    print(f"Connected by {address}")
    with connection:
        connection.sendall(b"Welcome\n")
        # Receive commands and process files here.


def main():
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