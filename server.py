# barebones code, untested and does not do anything we need it to do yet
# uses fastapi and uvicorn
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse

app = FastAPI()
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

EXTENSIONS = {".txt": "TS", ".mp4": "VS"}


@app.get("/health")
def health():
    return {"status": "ok"}

# uploading files barebones test
@app.post("/files", status_code=201)
async def upload_file(file: UploadFile):
    extension = Path(file.filename or "").suffix.lower()
    prefix = EXTENSIONS.get(extension)
    if prefix is None:
        raise HTTPException(status_code=400, detail="Only .txt and .mp4 files are supported")

    # uploads a unique filename by using prefixes to denote filetype and a hex uuid
    file_id = f"{prefix}-{uuid4().hex}"
    destination = UPLOAD_DIR / f"{file_id}{extension}"

    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                output.write(chunk)
    finally:
        await file.close()

    return {"id": file_id, "name": destination.name}

# barebones code of downloading files
@app.get("/files/{file_id}")
def download_file(file_id: str):
    if not re.fullmatch(r"(?:TS|VS)-[0-9a-f]{32}", file_id):
        raise HTTPException(status_code=404, detail="File not found")
    # this code woudl only work with .txt and .mp4, we will need to add more
    # i just started with the two bc if we can get two working we can get more
    prefix = file_id[:2]
    extension = ".txt" if prefix == "TS" else ".mp4"
    path = UPLOAD_DIR / f"{file_id}{extension}"

    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path, filename=path.name)
