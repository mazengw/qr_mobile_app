"""Prepare Mozilla's official PDF.js viewer and serve it over localhost."""

from __future__ import annotations

import shutil
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PDFJS_ROOT = Path(__file__).resolve().parent / "pdfjs"
PDFJS_DIST = PDFJS_ROOT / "viewer_dist"
MAX_PDF_BYTES = 80 * 1024 * 1024

_active_servers: list[ThreadingHTTPServer] = []
_server_lock = threading.Lock()


def can_serve_pdf(pdf_path: Path) -> bool:
    try:
        return pdf_path.is_file() and pdf_path.stat().st_size <= MAX_PDF_BYTES
    except OSError:
        return False


def _dist_ready() -> bool:
    return (
        (PDFJS_DIST / "web" / "viewer.html").is_file()
        and (PDFJS_DIST / "build" / "pdf.js").is_file()
    )


def prepare_pdf_viewer_dir(pdf_path: Path, *, work_root: Path) -> Path:
    """Copy official PDF.js viewer + PDF into a session folder."""
    if not can_serve_pdf(pdf_path):
        raise ValueError("PDF missing or too large")
    if not _dist_ready():
        raise FileNotFoundError(
            "Official PDF.js viewer missing under app/pdfjs/viewer_dist/"
        )

    session = work_root / f"pdfjs_official_{int(time.time() * 1000)}"
    session.mkdir(parents=True, exist_ok=True)

    # Slim copy: skip source maps and sample PDF.
    def _ignore(directory: str, names: list[str]) -> set[str]:
        skip = set()
        for n in names:
            low = n.lower()
            if low.endswith(".map") or low.endswith(".pdf") or low.startswith("debugger"):
                skip.add(n)
        return skip

    shutil.copytree(PDFJS_DIST / "build", session / "build", ignore=_ignore)
    shutil.copytree(PDFJS_DIST / "web", session / "web", ignore=_ignore)
    shutil.copy2(pdf_path, session / "document.pdf")
    return session


def start_pdf_viewer_server(session_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    """Serve session_dir and return (server, viewer_url)."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(session_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with _server_lock:
        _active_servers.append(server)
    port = server.server_address[1]
    # Official viewer reads ?file= relative to viewer.html location (web/).
    url = f"http://127.0.0.1:{port}/web/viewer.html?file=../document.pdf"
    return server, url


def stop_pdf_viewer_servers() -> None:
    with _server_lock:
        servers = list(_active_servers)
        _active_servers.clear()
    for srv in servers:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass


# Back-compat aliases used by older main.py imports
def can_embed_pdf(pdf_path: Path) -> bool:
    return can_serve_pdf(pdf_path)
