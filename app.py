"""HTTP front end for lldgen: paste a repository URL, get PlantUML diagrams."""

from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lldgen import clone, generate

ALLOWED_HOSTS = {"github.com", "gitlab.com"}
CLONE_TIMEOUT_SECONDS = 90
MAX_JAVA_FILES = 5000
# The free Render instance has 512 MB; keep concurrent clones bounded.
WORKERS = threading.BoundedSemaphore(2)

app = FastAPI(title="Repository LLD Generator")


class GenerateRequest(BaseModel):
    url: str = Field(max_length=500)
    package_depth: int = Field(default=2, ge=1, le=6)
    single_diagram: bool = False


STATIC_DIR = (Path(__file__).parent / "static").resolve()


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


@app.post("/api/generate")
def generate_diagrams(request: GenerateRequest) -> dict:
    parsed = urlparse(request.url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username:
        raise HTTPException(400, "Only public https://github.com or https://gitlab.com URLs are supported.")
    if not WORKERS.acquire(timeout=30):
        raise HTTPException(503, "The server is busy. Try again in a minute.")
    try:
        with tempfile.TemporaryDirectory(prefix="lldgen-") as temp:
            try:
                scan_root = clone(request.url.strip(), Path(temp) / "repo", timeout=CLONE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                raise HTTPException(504, "Cloning took too long; try a subdirectory (tree) URL.")
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode(errors="replace").strip()
                if "could not read Username" in stderr or "not found" in stderr:
                    raise HTTPException(422, "Repository or branch not found, or it is private.")
                raise HTTPException(422, f"git clone failed: {stderr.splitlines()[-1] if stderr else 'unknown error'}")
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            if sum(1 for _ in scan_root.rglob("*.java")) > MAX_JAVA_FILES:
                raise HTTPException(413, f"More than {MAX_JAVA_FILES} Java files; try a subdirectory URL.")
            diagrams = generate(scan_root, request.package_depth, request.single_diagram)
    finally:
        WORKERS.release()
    return {"diagrams": [
        {"filename": d.filename, "source": d.source, "types": d.type_count, "path": d.relative}
        for d in diagrams
    ]}


@app.api_route("/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def static_file(name: str) -> FileResponse:
    """Favicons, the social preview image, robots.txt, and the web manifest."""
    path = (STATIC_DIR / name).resolve()
    if path.parent != STATIC_DIR or not path.is_file() or name == "index.html":
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)
