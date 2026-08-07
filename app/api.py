"""HTTP client for QR Vault Django APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .state import Session


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


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
        detail = data.get("detail") if isinstance(data, dict) else data
        if isinstance(detail, list):
            detail = "; ".join(str(x) for x in detail)
        elif isinstance(detail, dict):
            detail = "; ".join(f"{k}: {v}" for k, v in detail.items())
        raise ApiError(str(detail or "Request failed"), response.status_code, data)

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

    def me(self) -> dict:
        with httpx.Client(timeout=30) as client:
            r = client.get(self._url("/api/auth/me/"), headers=self._headers())
            return self._handle(r)

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
