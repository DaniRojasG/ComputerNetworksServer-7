import json
import os
import socket
import threading
from pathlib import Path

HOST = "0.0.0.0"
PORT = 3300
STORAGE_DIR = Path(
    os.environ.get("FILE_STORAGE_DIR", Path(__file__).resolve().parent / "storage")
).expanduser().resolve()


def handle_client(connection, address):
    print(f"Connected by {address}")
    with connection:
        with connection.makefile("rb") as reader:
            request = reader.readline(4097)
            if not request.endswith(b"\n") or len(request) > 4096:
                response = {
                    "ok": False,
                    "error": "Request must be a command of at most 4095 bytes.",
                }
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
                    response = {"ok": True, "entries": entries}
                except OSError as error:
                    print(f"Could not list storage directory {STORAGE_DIR}: {error}")
                    response = {"ok": False, "error": "Could not list server storage."}
            else:
                response = {"ok": False, "error": "Unsupported command. Try DIR."}

            connection.sendall((json.dumps(response) + "\n").encode("utf-8"))


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