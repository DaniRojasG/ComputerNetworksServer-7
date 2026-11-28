# Computer Networks Server-Client Architecture - Group 7: Nia Crusan, Dani Rojas Gonzalez, Hosevana Lindsey

This project will implement a client-server architecure for a distributed file sharing system.
The system will be under certain core constraints; employing multi-threading for quick access
and support for a myriad of file types.


## Objectives
- Have a client & server application that communicate to each other.
- Allow users to upload a variety of files & have said files categorized.
- Clients will be able to interact with the system via commands; Connect, Upload, etc.
- The system will gather metrics throughout its runtime that can be later analyzed.
- The client & server will have approriate forms of error handling for smooth operation.
- Development of the system will be monitored via Github Remote VCS.
- ### Server updates:
   - 2026-11-15 added barebones test code.
   - 2026-11-24 undid everything and restart. build things from ground up, starting with connecting server.py (uploaded to google cloud vm) and client.py (running on a personal computer)
   - 2026-11-24 server creates a storage directory server.py
   - 2026-11-25 client can upload files to the storage directory via server.py
   - 2026-11-25 client can download files from the storage directory

## Requirements
- Text files must be under 25 MB
- Audio files must be under 0.5 GB
- Video files must be under 2 GB

## To run

```bash
python3 server.py
```
The server stores files in `/mnt/disks/file-storage` by default. Set
`FILE_STORAGE_DIR` to use another directory:

```bash
FILE_STORAGE_DIR=/directory/path python3 server.py
```
Set the full directory to work properly, not just the directory the command is run in.

Enter `DIR` in the client to list server files. Use `UPLOAD <local-file-path>`
to send a file. The server assigns a unique type-based name and preserves the
file extension: `TS` for text, `AS` for audio, `VS` for video, `IS` for images,
and `OS` for other extensions (for example, `TS001.txt`). Uploading the same
source again creates a new numbered server filename. Upload filenames that
contain Windows-invalid characters, are reserved device names, end in a space
or period, or exceed 255 UTF-8 bytes are rejected; rename the local file and
retry. Both client and server validate the original name. Use
`DOWNLOAD <filename>` to save a server file to the client's Downloads folder;
the client asks before replacing an existing local file. Use `DELETE <filename>`
to remove a file from the current server directory; the client confirms first.

Use `CD <directory>` to move through server folders, `CD ..` to move up, and
`CD /` to return to the storage root. `DIR`, `UPLOAD`, `DOWNLOAD`, and `DELETE`
operate on the current server directory. The current directory is tracked by
the client and resets to the root when the client restarts. Manage folders
relative to the current directory with `SUBFOLDER CREATE <path>` and
`SUBFOLDER DELETE <path>` (for example, `SUBFOLDER CREATE reports/weekly`).
Folder deletion only removes empty folders; remove their contents first.

The client connects to the server's external IP on TCP port `3300`.

## Metrics

All relevant information is captured by the metrics process as soon
as the server is ran. Once the server finishes running it will store
all the data metrics picked into a JSON & CSV file. Both can then
be view and hold info on relevant infromation such as:
- Timestamp
- Client IP
- Command Used
- Filename
- Latency
- etc.

Enter `q` in the client to download `metrics_report.json` and
`metrics_report.csv` from the server to the client's `Downloads` folder.
The client asks before overwriting either report. The server keeps its
original copies. Keep the server running while the client requests them.

## Process Overview
![Process Overview](images/processoverview.png)
