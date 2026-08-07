"""Writable app data paths for desktop + Android/iOS (Flet packaged apps)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def app_data_dir() -> Path:
    """
    Return a writable directory for app data.

    On Flet Android/iOS builds, Path.home() resolves to a non-writable path
    like /data. Prefer Flet storage env vars instead.
    """
    for key in ("FLET_APP_STORAGE_DATA", "FLET_APP_STORAGE_CACHE"):
        value = (os.environ.get(key) or "").strip()
        if value:
            p = Path(value)
            try:
                p.mkdir(parents=True, exist_ok=True)
                return p
            except OSError:
                continue

    # Desktop / local fallback
    p = Path.home() / ".qr_vault"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except OSError:
        p = Path(tempfile.gettempdir()) / "qr_vault"
        p.mkdir(parents=True, exist_ok=True)
        return p


def offline_dir() -> Path:
    p = app_data_dir() / "offline"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_path() -> Path:
    return app_data_dir() / "session.json"


def downloads_dir() -> Path:
    p = app_data_dir() / "downloads"
    p.mkdir(parents=True, exist_ok=True)
    return p
