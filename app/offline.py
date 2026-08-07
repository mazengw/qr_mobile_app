"""Offline note queue + durable vault/file cache for QR Vault mobile."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_network_error(exc: BaseException) -> bool:
    """True for transport / connectivity failures (not HTTP 4xx/5xx)."""
    name = type(exc).__name__
    if name in (
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "NetworkError",
        "TimeoutException",
        "ProxyError",
    ):
        return True
    msg = str(exc).lower()
    needles = (
        "connection",
        "timed out",
        "timeout",
        "network",
        "unreachable",
        "name or service not known",
        "failed to establish",
        "temporarily unavailable",
        "winerror 10061",
        "actively refused",
    )
    return any(n in msg for n in needles)


class OfflineStore:
    """
    Disk layout under ~/.qr_vault_offline/:
      queue.json                  — pending note + file upload ops
      home_storages.json          — last known home vault list
      snapshots/{storage_id}.json — last known storage + files + notes meta
      files/{storage_id}/{file_id}__{safe_name} — cached file bytes
      pending_uploads/{queue_id}__{safe_name} — files waiting to upload
    """

    def __init__(self, root: Path | None = None):
        self.root = root or (Path.home() / ".qr_vault_offline")
        self.queue_path = self.root / "queue.json"
        self.home_path = self.root / "home_storages.json"
        self.snapshots_dir = self.root / "snapshots"
        self.files_dir = self.root / "files"
        self.pending_uploads_dir = self.root / "pending_uploads"
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.pending_uploads_dir.mkdir(parents=True, exist_ok=True)

    # ── JSON helpers ────────────────────────────────────────────
    def _read_json(self, path: Path, default: Any):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ── Note sync queue ─────────────────────────────────────────
    def load_queue(self) -> list[dict]:
        data = self._read_json(self.queue_path, [])
        return data if isinstance(data, list) else []

    def save_queue(self, items: list[dict]) -> None:
        self._write_json(self.queue_path, items)

    def pending_count(self, storage_id: int | None = None) -> int:
        q = self.load_queue()
        if storage_id is None:
            return len(q)
        return sum(1 for x in q if x.get("storage_id") == storage_id)

    def enqueue_note_create(self, storage_id: int, title: str, body: str) -> dict:
        local_id = f"local-{uuid.uuid4().hex[:12]}"
        item = {
            "id": uuid.uuid4().hex,
            "op": "create",
            "storage_id": storage_id,
            "note_id": None,
            "local_id": local_id,
            "title": title or "",
            "body": body or "",
            "created_at": _now_iso(),
            "status": "pending",
            "error": None,
        }
        q = self.load_queue()
        q.append(item)
        self.save_queue(q)
        # Also mirror into snapshot so UI can show it immediately
        self._upsert_local_note(
            storage_id,
            {
                "id": local_id,
                "title": title or "",
                "body": body or "",
                "sort_order": 10**9,
                "created_at": item["created_at"],
                "updated_at": item["created_at"],
                "pending": True,
                "local_id": local_id,
            },
        )
        return item

    def enqueue_note_update(self, storage_id: int, note_id: int | str, title: str, body: str) -> dict:
        # If updating a still-local create, patch the pending create instead
        q = self.load_queue()
        if isinstance(note_id, str) and str(note_id).startswith("local-"):
            for row in q:
                if row.get("local_id") == note_id and row.get("op") == "create":
                    row["title"] = title or ""
                    row["body"] = body or ""
                    row["status"] = "pending"
                    row["error"] = None
                    self.save_queue(q)
                    self._upsert_local_note(
                        storage_id,
                        {
                            "id": note_id,
                            "title": title or "",
                            "body": body or "",
                            "pending": True,
                            "local_id": note_id,
                        },
                    )
                    return row
        # Coalesce with an existing pending update for same note
        for row in q:
            if (
                row.get("op") == "update"
                and row.get("storage_id") == storage_id
                and row.get("note_id") == note_id
            ):
                row["title"] = title or ""
                row["body"] = body or ""
                row["status"] = "pending"
                row["error"] = None
                self.save_queue(q)
                self._upsert_local_note(
                    storage_id,
                    {
                        "id": note_id,
                        "title": title or "",
                        "body": body or "",
                        "pending": True,
                    },
                )
                return row
        item = {
            "id": uuid.uuid4().hex,
            "op": "update",
            "storage_id": storage_id,
            "note_id": note_id,
            "local_id": None,
            "title": title or "",
            "body": body or "",
            "created_at": _now_iso(),
            "status": "pending",
            "error": None,
        }
        q.append(item)
        self.save_queue(q)
        self._upsert_local_note(
            storage_id,
            {
                "id": note_id,
                "title": title or "",
                "body": body or "",
                "pending": True,
            },
        )
        return item

    def enqueue_note_delete(self, storage_id: int, note_id: int | str) -> dict | None:
        q = self.load_queue()
        # Cancel a pending local create
        if isinstance(note_id, str) and str(note_id).startswith("local-"):
            q = [r for r in q if not (r.get("local_id") == note_id and r.get("op") == "create")]
            self.save_queue(q)
            self._remove_local_note(storage_id, note_id)
            return None
        # Drop pending updates for this note
        q = [
            r
            for r in q
            if not (
                r.get("storage_id") == storage_id
                and r.get("note_id") == note_id
                and r.get("op") == "update"
            )
        ]
        item = {
            "id": uuid.uuid4().hex,
            "op": "delete",
            "storage_id": storage_id,
            "note_id": note_id,
            "local_id": None,
            "title": "",
            "body": "",
            "created_at": _now_iso(),
            "status": "pending",
            "error": None,
        }
        q.append(item)
        self.save_queue(q)
        self._remove_local_note(storage_id, note_id)
        return item

    def flush_notes(self, api) -> dict:
        """Alias — flushes notes and pending file uploads."""
        return self.flush_queue(api)

    def flush_queue(self, api) -> dict:
        """
        Push pending note ops + file uploads to the API.
        Returns {"synced": n, "failed": n, "errors": [...]}.
        """
        q = self.load_queue()
        if not q:
            return {"synced": 0, "failed": 0, "errors": []}

        remaining: list[dict] = []
        synced = 0
        failed = 0
        errors: list[str] = []

        for idx, row in enumerate(q):
            op = row.get("op")
            sid = row.get("storage_id")
            try:
                if op == "create":
                    created = api.create_note(sid, row.get("body") or "", row.get("title") or "")
                    local_id = row.get("local_id")
                    if local_id:
                        self._remove_local_note(sid, local_id)
                    if isinstance(created, dict):
                        self._upsert_local_note(sid, {**created, "pending": False})
                    synced += 1
                elif op == "update":
                    nid = row.get("note_id")
                    if nid is None:
                        raise ValueError("update missing note_id")
                    updated = api.update_note(sid, int(nid), row.get("body") or "", row.get("title") or "")
                    if isinstance(updated, dict):
                        self._upsert_local_note(sid, {**updated, "pending": False})
                    synced += 1
                elif op == "delete":
                    nid = row.get("note_id")
                    if nid is None:
                        raise ValueError("delete missing note_id")
                    api.delete_note(sid, int(nid))
                    self._remove_local_note(sid, nid)
                    synced += 1
                elif op == "upload":
                    local_path = row.get("local_path")
                    if not local_path or not Path(local_path).exists():
                        raise FileNotFoundError(f"Pending upload missing: {local_path}")
                    uploaded = api.upload_file(sid, local_path)
                    local_id = row.get("local_id")
                    if local_id:
                        self._remove_local_file(sid, local_id)
                    if isinstance(uploaded, dict):
                        self._upsert_local_file(sid, {**uploaded, "pending": False})
                        # Promote blob into durable cache under server file id
                        try:
                            fid = uploaded.get("id")
                            name = uploaded.get("original_name") or Path(local_path).name
                            if fid is not None:
                                self.store_cached_file(sid, int(fid), name, Path(local_path))
                        except Exception:
                            pass
                    # Clean pending copy
                    try:
                        Path(local_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    synced += 1
                else:
                    remaining.append(row)
            except Exception as exc:
                if is_network_error(exc):
                    remaining.append(row)
                    remaining.extend(q[idx + 1 :])
                    failed += 1
                    errors.append(str(exc))
                    break
                row = {**row, "status": "failed", "error": str(exc)}
                remaining.append(row)
                failed += 1
                errors.append(str(exc))

        self.save_queue(remaining)
        return {"synced": synced, "failed": failed, "errors": errors}

    def enqueue_file_upload(self, storage_id: int, source_path: str | Path) -> dict:
        """Copy file into pending_uploads and enqueue an upload op."""
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        local_id = f"local-file-{uuid.uuid4().hex[:12]}"
        queue_id = uuid.uuid4().hex
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in src.name)[:80] or "file"
        dest = self.pending_uploads_dir / f"{queue_id}__{safe}"
        shutil.copy2(src, dest)

        import mimetypes

        content_type = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        size = dest.stat().st_size
        item = {
            "id": queue_id,
            "op": "upload",
            "storage_id": storage_id,
            "local_id": local_id,
            "local_path": str(dest),
            "original_name": src.name,
            "content_type": content_type,
            "size_original": size,
            "created_at": _now_iso(),
            "status": "pending",
            "error": None,
        }
        q = self.load_queue()
        q.append(item)
        self.save_queue(q)
        self._upsert_local_file(
            storage_id,
            {
                "id": local_id,
                "original_name": src.name,
                "content_type": content_type,
                "size_original": size,
                "size_compressed": size,
                "is_archived": False,
                "sort_order": 10**9,
                "pending": True,
                "local_id": local_id,
                "local_path": str(dest),
                "created_at": item["created_at"],
            },
        )
        # Bump home file_count hint
        home = self.load_home_storages() or []
        for i, s in enumerate(home):
            if s.get("id") == storage_id:
                try:
                    home[i] = {**s, "file_count": int(s.get("file_count") or 0) + 1}
                except Exception:
                    pass
                self.save_home_storages(home)
                break
        return item

    def merge_files_for_display(self, storage_id: int, server_files: list[dict]) -> list[dict]:
        files = [dict(f) for f in server_files]
        by_id = {f.get("id"): i for i, f in enumerate(files)}
        for row in self.load_queue():
            if row.get("storage_id") != storage_id or row.get("op") != "upload":
                continue
            lid = row.get("local_id")
            if not lid or lid in by_id:
                continue
            files.append(
                {
                    "id": lid,
                    "original_name": row.get("original_name") or "file",
                    "content_type": row.get("content_type") or "application/octet-stream",
                    "size_original": row.get("size_original") or 0,
                    "size_compressed": row.get("size_original") or 0,
                    "is_archived": False,
                    "sort_order": 10**9,
                    "pending": True,
                    "local_id": lid,
                    "local_path": row.get("local_path"),
                    "created_at": row.get("created_at"),
                }
            )
            by_id[lid] = len(files) - 1
        return files

    def _upsert_local_file(self, storage_id: int, file_row: dict) -> None:
        snap = self.load_snapshot(storage_id) or {
            "storage_id": storage_id,
            "storage": {},
            "files": [],
            "notes": [],
        }
        files = list(snap.get("files") or [])
        fid = file_row.get("id")
        found = False
        for i, f in enumerate(files):
            if f.get("id") == fid:
                files[i] = {**f, **file_row}
                found = True
                break
        if not found:
            files.append(file_row)
        snap["files"] = files
        snap["saved_at"] = _now_iso()
        # Keep storage.file_count roughly in sync for UI
        storage = dict(snap.get("storage") or {})
        if storage:
            real = [f for f in files if not f.get("is_archived")]
            storage["file_count"] = len(real)
            snap["storage"] = storage
        self._write_json(self.snapshot_path(storage_id), snap)

    def _remove_local_file(self, storage_id: int, file_id: int | str) -> None:
        snap = self.load_snapshot(storage_id)
        if not snap:
            return
        snap["files"] = [f for f in (snap.get("files") or []) if f.get("id") != file_id]
        snap["saved_at"] = _now_iso()
        self._write_json(self.snapshot_path(storage_id), snap)

    def home_storage_as_detail(self, storage_id: int) -> dict | None:
        """Build a minimal storage detail dict from the cached home list."""
        items = self.load_home_storages() or []
        for s in items:
            if s.get("id") == storage_id:
                return {
                    "id": s.get("id"),
                    "qr_code": s.get("qr_code"),
                    "title": s.get("title") or "",
                    "owner_phone": s.get("owner_phone"),
                    "is_archived": s.get("is_archived", False),
                    "is_public": s.get("is_public", False),
                    "file_count": s.get("file_count") or 0,
                    "my_permission": s.get("my_permission") or (
                        "owner" if (s.get("source") or "owned") == "owned" else "read"
                    ),
                    "files": [],
                    "payload": {},
                    "updated_at": s.get("updated_at"),
                }
        return None

    def ensure_snapshot(self, storage_id: int, storage: dict | None = None) -> dict:
        """Return existing snapshot or create an empty one (for empty vaults)."""
        snap = self.load_snapshot(storage_id)
        if snap and snap.get("storage"):
            return snap
        detail = storage or self.home_storage_as_detail(storage_id) or {
            "id": storage_id,
            "title": f"Storage {storage_id}",
            "my_permission": "owner",
            "file_count": 0,
            "qr_code": str(storage_id),
        }
        data = {
            "storage_id": storage_id,
            "saved_at": _now_iso(),
            "storage": detail,
            "files": [],
            "notes": [],
        }
        self._write_json(self.snapshot_path(storage_id), data)
        return data

    # ── Home storages list ──────────────────────────────────────
    def save_home_storages(self, items: list[dict]) -> None:
        self._write_json(
            self.home_path,
            {
                "saved_at": _now_iso(),
                "results": items or [],
            },
        )
        # Seed empty snapshots so empty vaults can be opened offline
        for s in items or []:
            sid = s.get("id")
            if sid is None:
                continue
            existing = self.load_snapshot(sid)
            if existing and existing.get("storage"):
                continue
            self.ensure_snapshot(sid, self.home_storage_as_detail(sid))

    def load_home_storages(self) -> list[dict] | None:
        data = self._read_json(self.home_path, None)
        if not isinstance(data, dict):
            return None
        results = data.get("results")
        return results if isinstance(results, list) else None

    def upsert_home_storage(self, storage: dict) -> None:
        """Keep home cache in sync when a vault is opened/updated offline-capable."""
        if not storage or not storage.get("id"):
            return
        items = self.load_home_storages() or []
        sid = storage.get("id")
        row = {
            "id": sid,
            "qr_code": storage.get("qr_code"),
            "title": storage.get("title") or "",
            "owner_phone": storage.get("owner_phone"),
            "is_archived": storage.get("is_archived", False),
            "is_public": storage.get("is_public", False),
            "file_count": storage.get("file_count"),
            "my_permission": storage.get("my_permission"),
            "source": "owned" if storage.get("my_permission") == "owner" else "shared",
            "updated_at": storage.get("updated_at"),
        }
        # Prefer existing source if present
        found = False
        for i, s in enumerate(items):
            if s.get("id") == sid:
                row["source"] = s.get("source") or row["source"]
                if row.get("file_count") is None:
                    row["file_count"] = s.get("file_count", 0)
                items[i] = {**s, **row}
                found = True
                break
        if not found:
            if row.get("file_count") is None:
                row["file_count"] = 0
            items.insert(0, row)
        self.save_home_storages(items)
        self.ensure_snapshot(sid, row if row.get("id") else storage)

    # ── Snapshots (browse offline) ──────────────────────────────
    def snapshot_path(self, storage_id: int) -> Path:
        return self.snapshots_dir / f"{storage_id}.json"

    def save_snapshot(
        self,
        storage_id: int,
        *,
        storage: dict | None,
        files: list[dict],
        notes: list[dict],
    ) -> None:
        # Persist server (or last-known) state only. Pending queue is overlaid at display time.
        data = {
            "storage_id": storage_id,
            "saved_at": _now_iso(),
            "storage": storage or {},
            "files": files,
            "notes": notes,
        }
        self._write_json(self.snapshot_path(storage_id), data)

    def load_snapshot(self, storage_id: int) -> dict | None:
        data = self._read_json(self.snapshot_path(storage_id), None)
        return data if isinstance(data, dict) else None

    def _upsert_local_note(self, storage_id: int, note: dict) -> None:
        snap = self.load_snapshot(storage_id) or {
            "storage_id": storage_id,
            "storage": {},
            "files": [],
            "notes": [],
        }
        notes = list(snap.get("notes") or [])
        nid = note.get("id")
        found = False
        for i, n in enumerate(notes):
            if n.get("id") == nid:
                notes[i] = {**n, **note}
                found = True
                break
        if not found:
            notes.append(note)
        snap["notes"] = notes
        snap["saved_at"] = _now_iso()
        self._write_json(self.snapshot_path(storage_id), snap)

    def _remove_local_note(self, storage_id: int, note_id: int | str) -> None:
        snap = self.load_snapshot(storage_id)
        if not snap:
            return
        snap["notes"] = [n for n in (snap.get("notes") or []) if n.get("id") != note_id]
        snap["saved_at"] = _now_iso()
        self._write_json(self.snapshot_path(storage_id), snap)

    def merge_notes_for_display(self, storage_id: int, server_notes: list[dict]) -> list[dict]:
        """Overlay pending queue changes onto server notes for UI."""
        notes = [dict(n) for n in server_notes]
        by_id = {n.get("id"): i for i, n in enumerate(notes)}
        for row in self.load_queue():
            if row.get("storage_id") != storage_id:
                continue
            op = row.get("op")
            if op == "create":
                lid = row.get("local_id")
                if lid and lid not in by_id:
                    notes.append(
                        {
                            "id": lid,
                            "title": row.get("title") or "",
                            "body": row.get("body") or "",
                            "sort_order": 10**9,
                            "pending": True,
                            "local_id": lid,
                            "created_at": row.get("created_at"),
                        }
                    )
                    by_id[lid] = len(notes) - 1
            elif op == "update":
                nid = row.get("note_id")
                if nid in by_id:
                    notes[by_id[nid]].update(
                        {
                            "title": row.get("title") or "",
                            "body": row.get("body") or "",
                            "pending": True,
                        }
                    )
            elif op == "delete":
                nid = row.get("note_id")
                if nid in by_id:
                    notes.pop(by_id[nid])
                    by_id = {n.get("id"): i for i, n in enumerate(notes)}
        return notes

    # ── File blob cache ─────────────────────────────────────────
    def cached_file_path(self, storage_id: int, file_id: int, name: str = "") -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (name or "file"))[:80] or "file"
        folder = self.files_dir / str(storage_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{file_id}__{safe}"

    def has_cached_file(self, storage_id: int, file_id: int, name: str = "") -> bool:
        # Prefer exact path; also accept any {file_id}__* in folder
        p = self.cached_file_path(storage_id, file_id, name)
        if p.exists() and p.stat().st_size > 0:
            return True
        folder = self.files_dir / str(storage_id)
        if not folder.exists():
            return False
        for f in folder.glob(f"{file_id}__*"):
            if f.is_file() and f.stat().st_size > 0:
                return True
        return False

    def find_cached_file(self, storage_id: int, file_id: int, name: str = "") -> Path | None:
        p = self.cached_file_path(storage_id, file_id, name)
        if p.exists() and p.stat().st_size > 0:
            return p
        folder = self.files_dir / str(storage_id)
        if not folder.exists():
            return None
        for f in folder.glob(f"{file_id}__*"):
            if f.is_file() and f.stat().st_size > 0:
                return f
        return None

    def store_cached_file(self, storage_id: int, file_id: int, name: str, source: Path) -> Path:
        dest = self.cached_file_path(storage_id, file_id, name)
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        return dest
