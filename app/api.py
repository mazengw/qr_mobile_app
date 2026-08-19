"""HTTP client for QR Vault Django APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .state import Session


class ApiError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        details: Any = None,
        *,
        code: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details
        self.code = code


class VaultAPI:
    def __init__(self, session: Session):
        self.session = session

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.session.access:
            headers["Authorization"] = f"Bearer {self.session.access}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.session.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _handle(self, response: httpx.Response) -> Any:
        if response.status_code == 204:
            return None
        try:
            data = response.json()
        except Exception:
            data = {"detail": response.text}
        if response.is_success:
            return data
        code, message = self._parse_error(data)
        raise ApiError(message, response.status_code, data, code=code)

    @staticmethod
    def _parse_error(data: Any) -> tuple[str | None, str]:
        """Return (error_code, human message) from a DRF error payload."""

        def fmt_val(v: Any) -> str:
            if isinstance(v, list):
                return "; ".join(fmt_val(x) for x in v)
            if isinstance(v, dict):
                return "; ".join(f"{k}: {fmt_val(x)}" for k, x in v.items())
            return str(v)

        known = {
            "INVALID_PHONE",
            "PHONE_EXISTS",
            "DISPLAY_NAME_REQUIRED",
            "PASSWORD_TOO_SHORT",
            "PASSWORD_MISMATCH",
            "PASSWORD_REQUIRED",
            "INVALID_CREDENTIALS",
            "ACCOUNT_DISABLED",
            "INVALID_OTP",
        }

        def find_code(v: Any) -> str | None:
            if isinstance(v, str):
                token = v.strip()
                # DRF may wrap: "ErrorDetail(...)" already stringified as code
                if token in known:
                    return token
                for k in known:
                    if k in token:
                        return k
                return None
            if isinstance(v, list):
                for item in v:
                    c = find_code(item)
                    if c:
                        return c
                return None
            if isinstance(v, dict):
                for item in v.values():
                    c = find_code(item)
                    if c:
                        return c
                return None
            return None

        if not isinstance(data, dict):
            return None, str(data or "Request failed")

        code = find_code(data)
        if "detail" in data:
            return code, fmt_val(data["detail"]) or "Request failed"

        parts: list[str] = []
        if "non_field_errors" in data:
            parts.append(fmt_val(data["non_field_errors"]))
        for key, val in data.items():
            if key in ("status_code", "non_field_errors"):
                continue
            msg = fmt_val(val)
            if msg:
                parts.append(msg if code else f"{key}: {msg}")
        return code, ("; ".join(parts) or "Request failed")

    def request_otp(self, phone: str) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(self._url("/api/auth/otp/request/"), json={"phone": phone})
            return self._handle(r)

    def verify_otp(self, phone: str, code: str, full_name: str = "") -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url("/api/auth/otp/verify/"),
                json={"phone": phone, "code": code, "full_name": full_name},
            )
            data = self._handle(r)
            self.session.set_auth(data["access"], data["refresh"], data["user"])
            return data

    def login(self, phone: str, password: str) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url("/api/auth/login/"),
                json={"phone": phone, "password": password},
            )
            data = self._handle(r)
            self.session.set_auth(data["access"], data["refresh"], data["user"])
            return data

    def register(
        self,
        phone: str,
        password: str,
        password_confirm: str,
        display_name: str,
    ) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url("/api/auth/register/"),
                json={
                    "phone": phone,
                    "password": password,
                    "password_confirm": password_confirm,
                    "display_name": display_name,
                },
            )
            data = self._handle(r)
            self.session.set_auth(data["access"], data["refresh"], data["user"])
            return data

    def me(self) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.get(self._url("/api/auth/me/"), headers=self._headers())
            return self._handle(r)

    def update_me(self, *, display_name: str | None = None) -> dict:
        payload: dict[str, Any] = {}
        if display_name is not None:
            payload["display_name"] = display_name
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url("/api/auth/me/"),
                headers=self._headers(),
                json=payload,
            )
            data = self._handle(r)
            if isinstance(data, dict):
                self.session.user = data
                self.session.save()
            return data

    def upload_avatar(self, path: Path) -> dict:
        with httpx.Client(timeout=60) as client:
            with path.open("rb") as f:
                r = client.post(
                    self._url("/api/auth/me/avatar/"),
                    headers=self._headers(),
                    files={"avatar": (path.name, f)},
                )
            data = self._handle(r)
            if isinstance(data, dict):
                self.session.user = data
                self.session.save()
            return data

    def delete_avatar(self) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.delete(self._url("/api/auth/me/avatar/"), headers=self._headers())
            data = self._handle(r)
            if isinstance(data, dict):
                self.session.user = data
                self.session.save()
            return data

    def list_storages(self) -> list[dict]:
        with httpx.Client(timeout=30) as client:
            r = client.get(self._url("/api/storages/"), headers=self._headers())
            data = self._handle(r)
            return data.get("results", data if isinstance(data, list) else [])

    def reorder_storages(self, storage_ids: list[int]) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url("/api/storages/reorder/"),
                headers=self._headers(),
                json={"storage_ids": storage_ids},
            )
            return self._handle(r)

    def reorder_items(self, storage_id: int, items: list[dict]) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url(f"/api/storages/{storage_id}/items/reorder/"),
                headers=self._headers(),
                json={"items": items},
            )
            return self._handle(r)

    def scan_qr(self, qr_code: str) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url("/api/storages/scan/"),
                headers=self._headers(),
                json={"qr_code": qr_code},
            )
            return self._handle(r)

    def get_storage(self, storage_id: int, archived: bool = False) -> dict:
        q = "?archived=1" if archived else ""
        with httpx.Client(timeout=30) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/{q}"),
                headers=self._headers(),
            )
            return self._handle(r)

    def rename_storage(self, storage_id: int, title: str) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/"),
                headers=self._headers(),
                json={"title": title},
            )
            return self._handle(r)

    def set_storage_public(self, storage_id: int, is_public: bool) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/"),
                headers=self._headers(),
                json={"is_public": is_public},
            )
            return self._handle(r)

    def set_storage_kind(self, storage_id: int, kind: str) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/"),
                headers=self._headers(),
                json={"kind": kind},
            )
            return self._handle(r)

    def update_menu_data(self, storage_id: int, menu_data: dict) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/"),
                headers=self._headers(),
                json={"menu_data": menu_data},
            )
            return self._handle(r)

    def update_storage_note(self, storage_id: int, note: str) -> dict:
        """Deprecated — use create_note / update_note."""
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/"),
                headers=self._headers(),
                json={"note": note},
            )
            return self._handle(r)

    def list_notes(self, storage_id: int) -> list:
        with httpx.Client(timeout=30) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/notes/"),
                headers=self._headers(),
            )
            data = self._handle(r)
            return data if isinstance(data, list) else data.get("results", [])

    def create_note(self, storage_id: int, body: str, title: str = "") -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url(f"/api/storages/{storage_id}/notes/"),
                headers=self._headers(),
                json={"title": title, "body": body},
            )
            return self._handle(r)

    def update_note(self, storage_id: int, note_id: int, body: str, title: str = "") -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/notes/{note_id}/"),
                headers=self._headers(),
                json={"title": title, "body": body},
            )
            return self._handle(r)

    def delete_note(self, storage_id: int, note_id: int) -> None:
        with httpx.Client(timeout=30) as client:
            r = client.delete(
                self._url(f"/api/storages/{storage_id}/notes/{note_id}/"),
                headers=self._headers(),
            )
            self._handle(r)

    def move_file(self, storage_id: int, file_id: int, target_storage_id: int) -> dict:
        with httpx.Client(timeout=60) as client:
            r = client.post(
                self._url(f"/api/storages/{storage_id}/files/{file_id}/move/"),
                headers=self._headers(),
                json={"target_storage_id": target_storage_id},
            )
            return self._handle(r)

    def list_files(self, storage_id: int, kind: str | None = None, archived: bool = False) -> list:
        params = []
        if kind:
            params.append(f"kind={kind}")
        if archived:
            params.append("archived=1")
        q = ("?" + "&".join(params)) if params else ""
        with httpx.Client(timeout=30) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/files/{q}"),
                headers=self._headers(),
            )
            return self._handle(r)

    def upload_file(self, storage_id: int, path: str) -> dict:
        p = Path(path)
        with httpx.Client(timeout=120) as client:
            with p.open("rb") as f:
                r = client.post(
                    self._url(f"/api/storages/{storage_id}/files/"),
                    headers=self._headers(),
                    files={"file": (p.name, f)},
                )
            return self._handle(r)

    def download_file(self, storage_id: int, file_id: int, dest: Path) -> Path:
        with httpx.Client(timeout=120) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/files/{file_id}/"),
                headers=self._headers(),
            )
            if not r.is_success:
                self._handle(r)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return dest

    def pdf_preview_meta(self, storage_id: int, file_id: int) -> dict:
        with httpx.Client(timeout=60) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/files/{file_id}/preview/"),
                headers=self._headers(),
            )
            return self._handle(r)

    def pdf_preview_page(self, storage_id: int, file_id: int, page: int, dest: Path) -> Path:
        with httpx.Client(timeout=90) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/files/{file_id}/preview/{page}/"),
                headers=self._headers(),
            )
            if not r.is_success:
                self._handle(r)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return dest

    def archive_file(self, storage_id: int, file_id: int, archived: bool = True) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.patch(
                self._url(f"/api/storages/{storage_id}/files/{file_id}/"),
                headers=self._headers(),
                json={"is_archived": archived},
            )
            return self._handle(r)

    def delete_file(self, storage_id: int, file_id: int) -> None:
        with httpx.Client(timeout=30) as client:
            r = client.delete(
                self._url(f"/api/storages/{storage_id}/files/{file_id}/"),
                headers=self._headers(),
            )
            self._handle(r)

    def list_shares(self, storage_id: int) -> list:
        with httpx.Client(timeout=30) as client:
            r = client.get(
                self._url(f"/api/storages/{storage_id}/shares/"),
                headers=self._headers(),
            )
            return self._handle(r)

    def share_storage(self, storage_id: int, phone: str, permission: str) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url(f"/api/storages/{storage_id}/shares/"),
                headers=self._headers(),
                json={"phone": phone, "permission": permission},
            )
            return self._handle(r)

    def list_incoming_shares(self) -> list:
        with httpx.Client(timeout=30) as client:
            r = client.get(
                self._url("/api/shares/incoming/"),
                headers=self._headers(),
            )
            data = self._handle(r)
            return data.get("results", data if isinstance(data, list) else [])

    def accept_share(self, share_id: int) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url(f"/api/shares/{share_id}/accept/"),
                headers=self._headers(),
            )
            return self._handle(r)

    def reject_share(self, share_id: int) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                self._url(f"/api/shares/{share_id}/reject/"),
                headers=self._headers(),
            )
            return self._handle(r)

    def merge_files(
        self,
        storage_id: int,
        file_ids: list[int],
        output_name: str = "merged.pdf",
        archive_sources: bool = False,
    ) -> dict:
        with httpx.Client(timeout=180) as client:
            r = client.post(
                self._url(f"/api/storages/{storage_id}/files/merge/"),
                headers=self._headers(),
                json={
                    "file_ids": file_ids,
                    "output_name": output_name,
                    "archive_sources": archive_sources,
                },
            )
            return self._handle(r)

    def revoke_share(self, storage_id: int, share_id: int) -> None:
        with httpx.Client(timeout=30) as client:
            r = client.request(
                "DELETE",
                self._url(f"/api/storages/{storage_id}/shares/"),
                headers=self._headers(),
                json={"share_id": share_id},
            )
            self._handle(r)
