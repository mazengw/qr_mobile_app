"""Serve DOCX/XLSX HTML previews over localhost (APK WebView-friendly).

Separate from PDF.js — do not mix with app.pdf_viewer_html.
"""

from __future__ import annotations

import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.office_preview import can_preview_office, office_to_html

_active_servers: list[ThreadingHTTPServer] = []
_server_lock = threading.Lock()


def prepare_office_viewer_dir(path: Path, name: str, *, work_root: Path) -> Path:
    if not can_preview_office(path, name):
        raise ValueError("Office file missing, too large, or unsupported")
    html = office_to_html(path, name)
    session = work_root / f"office_preview_{int(time.time() * 1000)}"
    session.mkdir(parents=True, exist_ok=True)
    (session / "index.html").write_text(html, encoding="utf-8")
    return session


def start_office_viewer_server(session_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(session_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with _server_lock:
        _active_servers.append(server)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/index.html"
    return server, url


def stop_office_viewer_servers() -> None:
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
