"""Simple in-memory + disk session for JWT tokens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import session_path


class Session:
    def __init__(self, path: Path | None = None):
        self.path = path or session_path()
        self.access: str | None = None
        self.refresh: str | None = None
        self.user: dict[str, Any] | None = None
        self.base_url: str = "http://127.0.0.1:8000"
        self.lang: str = "en"
        self.theme: str = "dark"
        self.ai_fab_right: float = 10.0
        self.ai_fab_bottom: float = 18.0
        self.load()

    @property
    def is_authenticated(self) -> bool:
        return bool(self.access)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.access = data.get("access")
            self.refresh = data.get("refresh")
            self.user = data.get("user")
            self.base_url = data.get("base_url") or self.base_url
            self.lang = data.get("lang") or self.lang
            raw_theme = str(data.get("theme") or self.theme).strip().lower()
            self.theme = "light" if raw_theme == "light" else "dark"
            try:
                self.ai_fab_right = float(data.get("ai_fab_right", self.ai_fab_right))
            except (TypeError, ValueError):
                pass
            try:
                self.ai_fab_bottom = float(data.get("ai_fab_bottom", self.ai_fab_bottom))
            except (TypeError, ValueError):
                pass
        except Exception:
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "access": self.access,
                    "refresh": self.refresh,
                    "user": self.user,
                    "base_url": self.base_url,
                    "lang": self.lang,
                    "theme": "light" if self.theme == "light" else "dark",
                    "ai_fab_right": self.ai_fab_right,
                    "ai_fab_bottom": self.ai_fab_bottom,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def set_auth(self, access: str, refresh: str, user: dict) -> None:
        self.access = access
        self.refresh = refresh
        self.user = user
        self.save()

    def clear(self) -> None:
        self.access = None
        self.refresh = None
        self.user = None
        self.save()
