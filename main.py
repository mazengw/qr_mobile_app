"""
QR Vault — Flet mobile client
Professional phone-first UI talking to the Django QR Vault APIs.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import flet as ft
import flet_video as ftv

from app.api import ApiError, VaultAPI
from app.note_html import NOTE_COLORS, NOTE_SIZES, note_plain_preview, note_to_text_control, wrap_selection
from app.offline import OfflineStore, is_network_error
from app.paths import downloads_dir
from app.qr_decode import decode_qr_payload
from app.state import Session
from app.theme import C, card, chip, ghost_button, muted, page_theme, primary_button, section_title

try:
    import flet_camera as fc
except ImportError:  # pragma: no cover - desktop without extension
    fc = None

try:
    import flet_permission_handler as fph
except ImportError:  # pragma: no cover
    fph = None


PREVIEW_DIR = Path(tempfile.gettempdir()) / "qr_vault_preview"
TEXT_PREVIEW_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
AUDIO_EXTS = {".mp3", ".wav", ".wave", ".m4a", ".aac", ".ogg"}


def is_image(content_type: str, name: str = "") -> bool:
    ct = (content_type or "").lower()
    return ct.startswith("image/") or Path(name).suffix.lower() in IMAGE_EXTS


def is_video(content_type: str, name: str = "") -> bool:
    ct = (content_type or "").lower()
    return ct.startswith("video/") or Path(name).suffix.lower() in VIDEO_EXTS


def is_audio(content_type: str, name: str = "") -> bool:
    ct = (content_type or "").lower()
    ext = Path(name).suffix.lower()
    return ext in AUDIO_EXTS or ct.startswith("audio/")


def is_playable(content_type: str, name: str = "") -> bool:
    return is_video(content_type, name) or is_audio(content_type, name)


def is_pdf(content_type: str, name: str = "") -> bool:
    ct = (content_type or "").lower()
    return ct == "application/pdf" or Path(name).suffix.lower() == ".pdf"


def render_pdf_pages(pdf_path: Path, max_pages: int = 40) -> list[Path]:
    import pypdfium2 as pdfium

    out_dir = PREVIEW_DIR / f"pdf_{pdf_path.stem}_{pdf_path.stat().st_size}"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    paths: list[Path] = []
    try:
        n = min(len(doc), max_pages)
        for i in range(n):
            out = out_dir / f"page_{i + 1:03d}.png"
            if not out.exists():
                page = doc[i]
                bitmap = page.render(scale=1.8)
                bitmap.to_pil().save(out, format="PNG")
            paths.append(out)
    finally:
        doc.close()
    return paths


def fmt_size(n: int | None) -> str:
    n = n or 0
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 ** 2):.1f} MB"


def file_icon(content_type: str) -> str:
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return ft.Icons.IMAGE_OUTLINED
    if "pdf" in ct:
        return ft.Icons.PICTURE_AS_PDF_OUTLINED
    if "video" in ct:
        return ft.Icons.MOVIE_OUTLINED
    if "audio" in ct:
        return ft.Icons.AUDIOTRACK_OUTLINED
    if "zip" in ct or "gzip" in ct:
        return ft.Icons.FOLDER_ZIP_OUTLINED
    return ft.Icons.INSERT_DRIVE_FILE_OUTLINED


class QRVaultApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.session = Session()
        self.api = VaultAPI(self.session)
        self.offline = OfflineStore()
        self.current_storage: dict | None = None
        self.file_filter = "all"
        self.file_search = ""
        self.show_archived = False
        self.browse_mode = "list"  # list | icons
        self.home_storage_filter = "all"  # all | owned | shared
        self._offline_mode = False
        self._sync_banner: ft.Container | None = None
        self._expanded_fid: int | None = None
        self._preview_panels: dict[int, ft.Container] = {}
        self._files_host: ft.Container | None = None
        self._filter_row: ft.Row | None = None
        self._view_toggle: ft.Row | None = None
        self._home_list = None
        self._home_filter_row: ft.Row | None = None
        self._home_items: list[dict] = []
        self._home_visible_ids: list[int] = []
        self._home_invites: ft.Column | None = None
        self._home_offline_banner: ft.Container | None = None
        self._storage_files_cache: list[dict] = []
        self._storage_notes_cache: list[dict] = []
        self._vault_visible_items: list[dict] = []
        self._scan_camera = None
        self._scan_busy = False
        self._scan_decode_pending = False
        self._scan_last_decode = 0.0
        self._scan_status: ft.Text | None = None
        self._scan_qr_field: ft.TextField | None = None

        self.snack = ft.SnackBar(content=ft.Text(""), bgcolor=C.surface_alt)
        self.page.overlay.append(self.snack)

        # FilePicker is a Service in Flet >=0.80 — do not add to page.overlay
        self.file_picker = ft.FilePicker()

        self._configure_page()
        self.root = ft.Container(expand=True)
        self.page.add(self.root)
        self.go_boot()

    def _configure_page(self):
        self.page.title = "QR Vault"
        self.page.theme = page_theme()
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = C.bg
        self.page.padding = 0
        self.page.window.width = 420
        self.page.window.height = 860
        self.page.window.min_width = 360
        self.page.window.min_height = 700

    def toast(self, message: str, error: bool = False):
        self.snack.content = ft.Text(message, color=C.text)
        self.snack.bgcolor = C.danger if error else C.surface_alt
        self.snack.open = True
        self.page.update()

    def set_view(self, body: ft.Control):
        # SafeArea keeps content below the Android/iOS status bar & above home indicator.
        self.root.content = ft.Container(
            content=ft.SafeArea(
                content=ft.Container(
                    content=body,
                    expand=True,
                    padding=ft.Padding.only(left=18, right=18, top=12, bottom=8),
                ),
                expand=True,
                maintain_bottom_view_padding=True,
            ),
            expand=True,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_LEFT,
                end=ft.Alignment.BOTTOM_RIGHT,
                colors=["#0B1220", "#0F172A", "#042F2E"],
            ),
        )
        self.page.update()

    # ── Boot / Auth ─────────────────────────────────────────────
    def go_boot(self):
        self.set_view(
            ft.Column(
                [
                    ft.Container(expand=True),
                    ft.Icon(ft.Icons.QR_CODE_2, size=64, color=C.primary),
                    ft.Text("QR Vault", size=28, weight=ft.FontWeight.BOLD, color=C.text),
                    muted("Secure storages unlocked by QR"),
                    ft.ProgressRing(color=C.primary, width=28, height=28),
                    ft.Container(expand=True),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                expand=True,
            )
        )
        self.page.run_task(self._boot_async)

    async def _boot_async(self):
        await asyncio.sleep(0.4)
        if self.session.is_authenticated:
            try:
                self.session.user = await asyncio.to_thread(self.api.me)
                self.session.save()
                await self._flush_offline_notes(silent=True)
                self.go_home()
                return
            except Exception as exc:
                if is_network_error(exc) and self.session.access:
                    self.toast("Offline — opening with cached data")
                    self.go_home()
                    return
                self.session.clear()
        self.go_login()

    async def _flush_offline_notes(self, silent: bool = False):
        pending = self.offline.pending_count()
        if pending <= 0:
            return
        try:
            result = await asyncio.to_thread(self.offline.flush_queue, self.api)
        except Exception as exc:
            if not silent:
                self.toast(f"Sync failed: {exc}", error=True)
            return
        synced = result.get("synced", 0)
        failed = result.get("failed", 0)
        if silent:
            return
        if synced and not failed:
            self.toast(f"Synced {synced} offline item(s)")
        elif synced and failed:
            self.toast(f"Synced {synced}, {failed} failed", error=True)
        elif failed:
            self.toast(f"Could not sync {failed} item(s)", error=True)

    def go_login(self):
        phone = ft.TextField(
            label="Phone number",
            hint_text="+9715...",
            prefix_icon=ft.Icons.PHONE_IPHONE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            cursor_color=C.primary,
            value=self.session.user.get("phone") if self.session.user else "",
        )
        name = ft.TextField(
            label="Full name (optional)",
            prefix_icon=ft.Icons.PERSON_OUTLINE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        base_url = ft.TextField(
            label="API base URL",
            value=self.session.base_url,
            hint_text="http://192.168.x.x:8000  (not 127.0.0.1 on phone)",
            prefix_icon=ft.Icons.CLOUD_OUTLINED,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            text_size=13,
        )

        def next_click(_):
            if not phone.value or len(phone.value.strip()) < 8:
                self.toast("Enter a valid phone number", error=True)
                return
            url = (base_url.value or self.session.base_url).rstrip("/")
            if "127.0.0.1" in url or "localhost" in url.lower():
                self.toast(
                    "On a real phone use your PC LAN IP, e.g. http://192.168.1.4:8000",
                    error=True,
                )
                # still allow desktop testing with localhost
            self.session.base_url = url
            self.session.save()
            self.page.run_task(self._request_otp, phone.value.strip(), name.value or "")

        self.set_view(
            ft.Column(
                [
                    ft.Container(height=24),
                    ft.Icon(ft.Icons.LOCK_PERSON_OUTLINED, size=48, color=C.primary),
                    section_title("Welcome back"),
                    muted("Sign in with your phone. We'll send a one-time code."),
                    muted("Phone APK: set API URL to your PC Wi‑Fi IP (Django must listen on 0.0.0.0:8000)."),
                    ft.Container(height=8),
                    card(
                        ft.Column(
                            [phone, name, base_url, primary_button("Sign in", next_click, ft.Icons.LOGIN)],
                            spacing=14,
                        )
                    ),
                    muted("Dev OTP is always 123456 when Django DEBUG=True"),
                ],
                spacing=14,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )
        )

    async def _request_otp(self, phone: str, full_name: str):
        try:
            data = await asyncio.to_thread(self.api.request_otp, phone)
            hint = data.get("dev_otp")
            self.go_otp(phone, full_name, hint)
        except ApiError as e:
            self.toast(e.message, error=True)
        except Exception as e:
            self.toast(f"Login error: {e}", error=True)

    def go_otp(self, phone: str, full_name: str, hint: str | None):
        code = ft.TextField(
            label="OTP code",
            hint_text="123456",
            password=True,
            can_reveal_password=True,
            max_length=8,
            text_align=ft.TextAlign.CENTER,
            text_size=22,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            value=hint or "",
        )

        def verify(_):
            if not code.value:
                self.toast("Enter OTP", error=True)
                return
            self.page.run_task(self._verify_otp, phone, code.value.strip(), full_name)

        self.set_view(
            ft.Column(
                [
                    ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=lambda e: self.go_login()),
                    section_title("Verify phone"),
                    muted(f"Code sent to {phone}"),
                    card(
                        ft.Column(
                            [
                                code,
                                primary_button("Verify & enter vault", verify, ft.Icons.VERIFIED_USER_OUTLINED),
                                ghost_button("Resend code", lambda e: self.page.run_task(self._request_otp, phone, full_name)),
                            ],
                            spacing=14,
                        )
                    ),
                    muted(f"Dev hint: {hint}" if hint else "Check SMS / server logs for OTP"),
                ],
                spacing=14,
                expand=True,
            )
        )

    async def _verify_otp(self, phone: str, code: str, full_name: str):
        try:
            await asyncio.to_thread(self.api.verify_otp, phone, code, full_name)
            self.toast("Signed in successfully")
            self.go_home()
        except ApiError as e:
            self.toast(e.message, error=True)

    # ── Home ────────────────────────────────────────────────────
    def go_home(self):
        user = self.session.user or {}
        list_view = ft.ReorderableListView(
            expand=True,
            spacing=0,
            padding=0,
            show_default_drag_handles=False,
            on_reorder=self._on_home_reorder,
        )
        self._home_list = list_view
        filter_row = ft.Row(spacing=8, visible=False)
        self._home_filter_row = filter_row

        header = ft.Row(
            [
                ft.Column(
                    [
                        muted("Signed in as"),
                        ft.Text(
                            user.get("first_name") or user.get("phone") or "User",
                            size=22,
                            weight=ft.FontWeight.BOLD,
                            color=C.text,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ghost_button("Sign out", self._logout, ft.Icons.LOGOUT),
            ]
        )

        actions = ft.Row(
            [
                primary_button("Scan QR", lambda e: self.go_scan(), ft.Icons.QR_CODE_SCANNER, expand=True),
                ghost_button("Refresh", lambda e: self.page.run_task(self._refresh_home, list_view)),
                ghost_button("Help", lambda e: self._show_help(), ft.Icons.HELP_OUTLINE),
            ],
            spacing=10,
        )

        invites = ft.Column(spacing=8, visible=False)
        self._home_invites = invites
        offline_banner = ft.Container(visible=False)
        self._home_offline_banner = offline_banner

        self.set_view(
            ft.Column(
                [
                    header,
                    muted("Your vaults — drag the left handle to reorder"),
                    actions,
                    offline_banner,
                    invites,
                    filter_row,
                    ft.Container(content=list_view, expand=True),
                ],
                spacing=14,
                expand=True,
            )
        )
        self.page.run_task(self._refresh_home, list_view)

    def _logout(self, _):
        self.session.clear()
        self.go_login()

    def _rebuild_home_filter(self, has_owned: bool, has_shared: bool):
        row = self._home_filter_row
        if row is None:
            return
        if not (has_owned and has_shared):
            row.visible = False
            row.controls = []
            try:
                row.update()
            except Exception:
                pass
            return

        def set_filter(kind: str):
            self.home_storage_filter = kind
            self._rebuild_home_filter(True, True)
            self._render_home_storages()

        def chip_btn(label: str, kind: str):
            active = self.home_storage_filter == kind
            return ft.Container(
                content=ft.Text(label, size=12, weight=ft.FontWeight.W_700, color=C.bg if active else C.text),
                bgcolor=C.primary if active else C.surface_alt,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=999,
                border=ft.Border.all(1, C.primary if active else C.border),
                on_click=lambda e, k=kind: set_filter(k),
                ink=True,
            )

        row.visible = True
        row.controls = [
            chip_btn("All", "all"),
            chip_btn("Owned", "owned"),
            chip_btn("Shared", "shared"),
        ]
        try:
            row.update()
        except Exception:
            pass

    def _drag_handle(self) -> ft.Control:
        return ft.ReorderableDragHandle(
            content=ft.Container(
                content=ft.Icon(ft.Icons.DRAG_INDICATOR, color=C.text_muted, size=22),
                width=36,
                height=44,
                bgcolor=C.surface_alt,
                border=ft.Border.all(1, C.border),
                border_radius=10,
                alignment=ft.Alignment.CENTER,
            ),
            tooltip="Drag to reorder",
        )

    def _wrap_list_item(self, content: ft.Control, *, reorder: bool = False) -> ft.Control:
        """Add bottom gap between items; optional left drag handle (no overlap with actions)."""
        if reorder:
            row = ft.Row(
                [
                    self._drag_handle(),
                    ft.Container(content=content, expand=True),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            return ft.Container(content=row, margin=ft.Margin.only(bottom=10))
        return ft.Container(content=content, margin=ft.Margin.only(bottom=10))

    def _storage_card(self, s: dict) -> ft.Control:
        source = s.get("source") or "owned"
        is_shared = source != "owned"
        is_public = bool(s.get("is_public"))
        color = C.shared if is_shared else C.owned
        # Thin elegant frame for shared vaults
        border = (
            ft.Border.all(1.5, "#7DD3FC")
            if is_shared
            else ft.Border.all(1, C.border)
        )
        badges = [chip(source.upper(), color)]
        if is_public:
            badges.insert(0, chip("PUBLIC", C.accent))
        return ft.Container(
            content=ft.ListTile(
                leading=ft.Container(
                    content=ft.Icon(ft.Icons.QR_CODE_2, color=C.bg),
                    bgcolor=color,
                    padding=10,
                    border_radius=12,
                ),
                title=ft.Text(s.get("title") or f"Storage {s.get('qr_code')}", color=C.text, weight=ft.FontWeight.W_600),
                subtitle=ft.Text(
                    f"QR: {s.get('qr_code')}  ·  {s.get('file_count', 0)} files  ·  {s.get('my_permission')}",
                    color=C.text_muted,
                    size=12,
                ),
                trailing=ft.Row(badges, spacing=6, tight=True) if len(badges) > 1 else badges[0],
                on_click=lambda e, sid=s["id"]: self.page.run_task(self._open_storage, sid),
            ),
            bgcolor=C.surface,
            border=border,
            border_radius=16,
            padding=ft.Padding.only(left=2, right=8, top=1, bottom=1) if is_shared else ft.Padding.only(right=6),
        )

    def _render_home_storages(self):
        list_view = self._home_list
        if list_view is None:
            return
        items = self._home_items or []
        filt = self.home_storage_filter
        if filt == "owned":
            items = [s for s in items if (s.get("source") or "owned") == "owned"]
        elif filt == "shared":
            items = [s for s in items if (s.get("source") or "owned") != "owned"]

        self._home_visible_ids = [s["id"] for s in items if s.get("id")]
        can_reorder = self.home_storage_filter == "all" and len(items) > 1
        list_view.show_default_drag_handles = False

        if not items:
            list_view.controls = [muted("No storages in this filter.")]
        else:
            list_view.controls = [
                self._wrap_list_item(self._storage_card(s), reorder=can_reorder) for s in items
            ]
        self.page.update()

    def _on_home_reorder(self, e: ft.OnReorderEvent):
        if self.home_storage_filter != "all":
            self.toast("Switch to All to reorder storages", error=True)
            self._render_home_storages()
            return
        old = e.old_index
        new = e.new_index
        if old is None or new is None:
            return
        if new > old:
            new -= 1
        ids = list(self._home_visible_ids)
        if not (0 <= old < len(ids)) or not (0 <= new <= len(ids)):
            return
        item = ids.pop(old)
        ids.insert(new, item)
        self._home_visible_ids = ids
        # Keep local cache order in sync
        by_id = {s["id"]: s for s in self._home_items}
        self._home_items = [by_id[i] for i in ids if i in by_id]
        self._render_home_storages()
        self.page.run_task(self._persist_home_order, ids)

    async def _persist_home_order(self, storage_ids: list[int]):
        try:
            await asyncio.to_thread(self.api.reorder_storages, storage_ids)
        except ApiError as err:
            self.toast(err.message, error=True)
            await self._load_storages(self._home_list)

    async def _refresh_home(self, list_view):
        await self._flush_offline_notes(silent=True)
        await self._load_incoming_shares()
        await self._load_storages(list_view)

    def _invite_card(self, share: dict) -> ft.Control:
        title = share.get("storage_title") or f"Storage {share.get('storage_qr_code')}"
        owner = share.get("owner_phone") or "unknown"
        perm = share.get("permission") or "read"
        share_id = share.get("id")

        return card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.MARK_EMAIL_UNREAD_OUTLINED, color=C.accent, size=22),
                            ft.Column(
                                [
                                    ft.Text(title, color=C.text, weight=ft.FontWeight.W_700, size=15),
                                    muted(f"From {owner} · {perm} access · QR {share.get('storage_qr_code')}"),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            primary_button(
                                "Accept",
                                lambda e, sid=share_id: self.page.run_task(self._accept_share, sid),
                                ft.Icons.CHECK,
                                expand=True,
                            ),
                            ghost_button(
                                "Reject",
                                lambda e, sid=share_id: self.page.run_task(self._reject_share, sid),
                                ft.Icons.CLOSE,
                            ),
                        ],
                        spacing=10,
                    ),
                ],
                spacing=10,
            ),
            padding=12,
        )

    async def _load_incoming_shares(self):
        host = self._home_invites
        if host is None:
            return
        try:
            shares = await asyncio.to_thread(self.api.list_incoming_shares)
        except (ApiError, Exception):
            host.visible = False
            host.controls = []
            try:
                host.update()
            except Exception:
                pass
            return

        if not shares:
            host.visible = False
            host.controls = []
        else:
            host.visible = True
            host.controls = [
                ft.Text("Share requests", weight=ft.FontWeight.W_700, color=C.text, size=14),
                muted("Accept to see this vault under Shared."),
                *[self._invite_card(sh) for sh in shares],
            ]
        try:
            host.update()
        except Exception:
            self.page.update()

    async def _accept_share(self, share_id: int):
        try:
            data = await asyncio.to_thread(self.api.accept_share, share_id)
            self.toast("Share accepted")
            storage = data.get("storage") or {}
            if storage.get("id"):
                await self._open_storage(storage["id"])
            else:
                self.go_home()
        except ApiError as e:
            self.toast(e.message, error=True)

    async def _reject_share(self, share_id: int):
        try:
            await asyncio.to_thread(self.api.reject_share, share_id)
            self.toast("Share rejected")
            await self._load_incoming_shares()
            if self._home_list is not None:
                await self._load_storages(self._home_list)
        except ApiError as e:
            self.toast(e.message, error=True)

    async def _load_storages(self, list_view):
        list_view.controls = [
            ft.Row([ft.ProgressRing(width=22, height=22, color=C.primary)], alignment=ft.MainAxisAlignment.CENTER)
        ]
        self.page.update()
        offline = False
        try:
            items = await asyncio.to_thread(self.api.list_storages)
            self.offline.save_home_storages(items or [])
            self._offline_mode = False
        except (ApiError, Exception) as e:
            cached = self.offline.load_home_storages()
            if cached is not None:
                items = cached
                offline = True
                self._offline_mode = True
                if isinstance(e, ApiError) or is_network_error(e):
                    self.toast("Offline — showing cached vault list")
                else:
                    self.toast(str(e), error=True)
            else:
                msg = e.message if isinstance(e, ApiError) else str(e)
                self.toast(msg, error=True)
                list_view.controls = [
                    muted("Failed to load storages"),
                    muted("Open a vault while online once, then try again offline."),
                ]
                self.page.update()
                return

        if not items:
            self._home_items = []
            self._rebuild_home_filter(False, False)
            empty_msg = (
                "No cached vaults yet. Connect once and open Home to save the list."
                if offline
                else "Scan a QR code to open or create your first vault."
            )
            list_view.controls = [
                card(
                    ft.Column(
                        [
                            ft.Icon(
                                ft.Icons.CLOUD_OFF if offline else ft.Icons.INVENTORY_2_OUTLINED,
                                size=40,
                                color=C.text_muted,
                            ),
                            section_title("No storages yet" if not offline else "Offline"),
                            muted(empty_msg),
                            primary_button("Scan QR", lambda e: self.go_scan(), ft.Icons.QR_CODE_SCANNER)
                            if not offline
                            else ft.Container(),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                    )
                )
            ]
            self.page.update()
            return

        self._home_items = items
        has_owned = any((s.get("source") or "owned") == "owned" for s in items)
        has_shared = any((s.get("source") or "owned") != "owned" for s in items)
        if not (has_owned and has_shared):
            self.home_storage_filter = "all"
        self._rebuild_home_filter(has_owned, has_shared)
        self._render_home_storages()
        banner = getattr(self, "_home_offline_banner", None)
        if banner is not None:
            if offline:
                banner.visible = True
                banner.content = card(
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.CLOUD_OFF, color=C.warning, size=18),
                            muted("Offline — cached vault list. Open a vault to browse saved files/notes."),
                        ],
                        spacing=8,
                    ),
                    padding=10,
                )
            else:
                banner.visible = False
                banner.content = None
            try:
                banner.update()
            except Exception:
                self.page.update()

    # ── Scan ────────────────────────────────────────────────────
    def _camera_platform_ok(self) -> bool:
        if fc is None:
            return False
        try:
            p = self.page.platform
            return p in (
                ft.PagePlatform.ANDROID,
                ft.PagePlatform.ANDROID_TV,
                ft.PagePlatform.IOS,
            ) or bool(self.page.web)
        except Exception:
            return False

    def go_scan(self):
        self.page.run_task(self._stop_scan_camera)
        self._scan_busy = False
        self._scan_decode_pending = False
        self._scan_last_decode = 0.0

        qr = ft.TextField(
            label="QR code value",
            hint_text='e.g. 1   or   share:<uuid>',
            prefix_icon=ft.Icons.QR_CODE_2,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            autofocus=not self._camera_platform_ok(),
        )
        self._scan_qr_field = qr
        status = muted(
            "Point camera at a vault QR — or paste/type below"
            if self._camera_platform_ok()
            else "Camera works on the mobile APK. On desktop, paste/type the QR payload."
        )
        self._scan_status = status

        camera_host = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.QR_CODE_SCANNER, size=64, color=C.primary),
                    muted("Preparing camera…"),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            height=280,
            bgcolor=C.surface_alt,
            border_radius=16,
            padding=12,
            border=ft.Border.all(1, C.border),
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        def submit(_):
            if not qr.value or not qr.value.strip():
                self.toast("Enter QR value", error=True)
                return
            self.page.run_task(self._scan, qr.value.strip())

        def leave(_):
            self.page.run_task(self._stop_scan_camera)
            self.go_home()

        def capture(_):
            self.page.run_task(self._capture_scan_frame)

        actions = [
            primary_button("Open storage", submit, ft.Icons.LOCK_OPEN_OUTLINED),
        ]
        if self._camera_platform_ok():
            actions.insert(
                0,
                ghost_button("Capture frame", capture, ft.Icons.CAMERA_ALT),
            )

        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=leave),
                            section_title("Scan QR"),
                        ]
                    ),
                    card(
                        ft.Column(
                            [
                                camera_host,
                                status,
                                qr,
                                *actions,
                            ],
                            spacing=14,
                        )
                    ),
                    muted("Empty storage → you can upload files. Existing storage → browse contents."),
                ],
                spacing=14,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )
        )
        if self._camera_platform_ok():
            self.page.run_task(self._start_scan_camera, camera_host)
        else:
            camera_host.content = ft.Column(
                [
                    ft.Icon(ft.Icons.QR_CODE_SCANNER, size=64, color=C.primary),
                    muted("Paste or type the QR payload"),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            )
            try:
                camera_host.update()
            except Exception:
                self.page.update()

    async def _stop_scan_camera(self):
        cam = self._scan_camera
        self._scan_camera = None
        if not cam:
            return
        try:
            await cam.stop_image_stream()
        except Exception:
            pass
        try:
            await cam.pause_preview()
        except Exception:
            pass

    async def _request_camera_permission(self) -> bool:
        if fph is None:
            return True
        try:
            ph = fph.PermissionHandler()
            status = await ph.request(fph.Permission.CAMERA)
            if status in (
                fph.PermissionStatus.GRANTED,
                fph.PermissionStatus.LIMITED,
                fph.PermissionStatus.PROVISIONAL,
            ):
                return True
            if status == fph.PermissionStatus.PERMANENTLY_DENIED:
                self.toast("Camera permission blocked — enable it in Settings", error=True)
                try:
                    await ph.open_app_settings()
                except Exception:
                    pass
                return False
            self.toast("Camera permission is required to scan", error=True)
            return False
        except Exception as exc:
            # Desktop / unsupported platforms — continue and let camera init fail soft.
            if "Unsupported" in type(exc).__name__ or "unsupported" in str(exc).lower():
                return True
            self.toast(f"Permission error: {exc}", error=True)
            return False

    def _set_scan_status(self, message: str):
        if self._scan_status is None:
            return
        self._scan_status.value = message
        try:
            self._scan_status.update()
        except Exception:
            try:
                self.page.update()
            except Exception:
                pass

    async def _start_scan_camera(self, camera_host: ft.Container):
        if fc is None:
            self._set_scan_status("Camera package missing — paste QR value below")
            return
        if not await self._request_camera_permission():
            camera_host.content = ft.Column(
                [
                    ft.Icon(ft.Icons.NO_PHOTOGRAPHY, size=56, color=C.warning),
                    muted("Camera permission denied — paste QR value below"),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            )
            try:
                camera_host.update()
            except Exception:
                self.page.update()
            return

        async def on_frame(e):
            await self._on_scan_frame(getattr(e, "bytes", None))

        camera = fc.Camera(
            expand=True,
            preview_enabled=True,
            on_stream_image=on_frame,
        )
        camera_host.content = camera
        camera_host.padding = 0
        try:
            camera_host.update()
        except Exception:
            self.page.update()

        try:
            cameras = await camera.get_available_cameras()
            if not cameras:
                raise RuntimeError("No camera found on this device")
            selected = next(
                (c for c in cameras if c.lens_direction == fc.CameraLensDirection.BACK),
                cameras[0],
            )
            await camera.initialize(
                description=selected,
                resolution_preset=fc.ResolutionPreset.MEDIUM,
                enable_audio=False,
                image_format_group=fc.ImageFormatGroup.JPEG,
            )
            self._scan_camera = camera
            streaming = False
            try:
                streaming = bool(await camera.supports_image_streaming())
            except Exception:
                streaming = False
            if streaming:
                await camera.start_image_stream()
                self._set_scan_status("Point camera at a QR code")
            else:
                self._set_scan_status("Tap Capture frame, or paste QR value below")
                self.page.run_task(self._poll_scan_frames)
        except Exception as exc:
            self._scan_camera = None
            camera_host.content = ft.Column(
                [
                    ft.Icon(ft.Icons.NO_PHOTOGRAPHY, size=56, color=C.danger),
                    muted(f"Camera unavailable: {exc}"),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            )
            camera_host.padding = 12
            try:
                camera_host.update()
            except Exception:
                self.page.update()
            self._set_scan_status("Paste or type the QR payload below")

    async def _poll_scan_frames(self):
        """Fallback when live image streaming is unavailable."""
        while self._scan_camera is not None and not self._scan_busy:
            await asyncio.sleep(0.85)
            cam = self._scan_camera
            if cam is None or self._scan_busy:
                break
            try:
                data = await cam.take_picture()
            except Exception:
                continue
            await self._on_scan_frame(data)

    async def _capture_scan_frame(self):
        cam = self._scan_camera
        if cam is None:
            self.toast("Camera not ready", error=True)
            return
        try:
            self._set_scan_status("Capturing…")
            data = await cam.take_picture()
            payload = await asyncio.to_thread(decode_qr_payload, data)
            if not payload:
                self._set_scan_status("No QR found — try again or paste value")
                self.toast("No QR found in frame", error=True)
                return
            await self._handle_scanned_payload(payload)
        except Exception as exc:
            self.toast(str(exc), error=True)

    async def _on_scan_frame(self, image_bytes: bytes | None):
        if self._scan_busy or self._scan_decode_pending or not image_bytes:
            return
        now = time.monotonic()
        if now - self._scan_last_decode < 0.4:
            return
        self._scan_last_decode = now
        self._scan_decode_pending = True
        try:
            payload = await asyncio.to_thread(decode_qr_payload, image_bytes)
            if payload:
                await self._handle_scanned_payload(payload)
        finally:
            self._scan_decode_pending = False

    async def _handle_scanned_payload(self, payload: str):
        if self._scan_busy:
            return
        self._scan_busy = True
        try:
            await self._stop_scan_camera()
            if self._scan_qr_field is not None:
                self._scan_qr_field.value = payload
                try:
                    self._scan_qr_field.update()
                except Exception:
                    pass
            self._set_scan_status("QR detected — opening…")
            self.toast(f"Scanned: {payload[:48]}")
            await self._scan(payload)
        finally:
            self._scan_busy = False

    async def _scan(self, qr_code: str):
        try:
            data = await asyncio.to_thread(self.api.scan_qr, qr_code)
            status = data.get("status")
            if status == "share_pending":
                share = data.get("share") or {}
                self.toast(data.get("message") or "Share request pending")
                self.go_home()
                # Highlight by refreshing invites; user can Accept from home.
                if share.get("id"):
                    await self._load_incoming_shares()
                return
            storage = data.get("storage") or {}
            msg = data.get("message") or status
            if data.get("public_access") or storage.get("is_public"):
                if (storage.get("my_permission") or "") == "read" and storage.get("is_public"):
                    msg = data.get("message") or "Public vault — view only"
            self.toast(str(msg))
            if storage.get("id"):
                await self._open_storage(storage["id"])
        except ApiError as e:
            self.toast(e.message, error=True)
        except Exception as e:
            if is_network_error(e):
                self.toast("Offline — cannot scan right now", error=True)
            else:
                self.toast(str(e), error=True)

    async def _open_storage(self, storage_id: int):
        try:
            await self._flush_offline_notes(silent=True)
            storage = await asyncio.to_thread(self.api.get_storage, storage_id, self.show_archived)
            self.current_storage = storage
            self._offline_mode = False
            self.file_search = ""
            self.file_filter = "all"
            self.offline.upsert_home_storage(storage)
            self.go_storage()
        except (ApiError, Exception) as e:
            networkish = isinstance(e, ApiError) or is_network_error(e)
            snap = self.offline.load_snapshot(storage_id)
            storage = (snap or {}).get("storage") if snap else None
            if not storage:
                # Empty vault never opened before — build from home list cache
                seeded = self.offline.ensure_snapshot(storage_id)
                storage = seeded.get("storage")
            if networkish and storage:
                self.current_storage = storage
                self._offline_mode = True
                self.file_search = ""
                self.file_filter = "all"
                self.toast("Offline — showing cached vault")
                self.go_storage()
                return
            self.toast(e.message if isinstance(e, ApiError) else str(e), error=True)

    # ── Storage detail ──────────────────────────────────────────
    def _perm(self) -> str:
        return (self.current_storage or {}).get("my_permission") or "read"

    def _can_write(self) -> bool:
        return self._perm() in ("owner", "manage", "write")

    def _can_manage(self) -> bool:
        return self._perm() in ("owner", "manage")

    def _is_owner(self) -> bool:
        return self._perm() == "owner"

    def go_storage(self):
        s = self.current_storage or {}
        sid = s.get("id")
        files_host = ft.Container(
            expand=True,
            bgcolor=C.bg,
            border_radius=12,
            alignment=ft.Alignment.TOP_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self._files_host = files_host
        filter_row = ft.Row(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self._filter_row = filter_row

        def set_filter(kind: str):
            self.file_filter = kind
            self._rebuild_filter_chips(filter_row, sid, on_select=set_filter)
            self.page.run_task(self._load_files, sid)

        def set_mode(mode: str):
            if self.browse_mode == mode:
                return
            self.browse_mode = mode
            self._rebuild_view_toggle(view_toggle, sid, on_select=set_mode)
            self.go_storage()

        def on_search_change(e):
            self.file_search = (e.control.value or "").strip()
            self._render_storage_files(sid)

        view_toggle = ft.Row(spacing=8)
        self._view_toggle = view_toggle

        search_field = ft.TextField(
            hint_text="Search files…",
            value=self.file_search,
            prefix_icon=ft.Icons.SEARCH,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            cursor_color=C.primary,
            text_size=14,
            on_change=on_search_change,
        )

        can_write = self._can_write()
        can_manage = self._can_manage()

        top_actions = []
        if can_write:
            top_actions.append(
                primary_button(
                    "Upload",
                    lambda e: self.page.run_task(self._pick_and_upload),
                    ft.Icons.UPLOAD_FILE,
                    expand=False,
                )
            )
            top_actions.append(
                ghost_button(
                    "Add Note",
                    lambda e: self._show_note_editor(sid, note_id=None, note_html="", title=""),
                    ft.Icons.NOTE_ADD_OUTLINED,
                )
            )
            top_actions.append(ghost_button("Merge PDF", lambda e: self.go_merge(), ft.Icons.PICTURE_AS_PDF_OUTLINED))
        if can_manage:
            top_actions.append(ghost_button("Share", lambda e: self.go_share()))
        top_actions.append(
            ghost_button(
                "Save offline",
                lambda e, st=sid: self.page.run_task(self._prefetch_offline_files, st),
                ft.Icons.DOWNLOAD_FOR_OFFLINE_OUTLINED,
            )
        )
        top_actions.append(ghost_button("Archived", lambda e: self.go_archive(), ft.Icons.ARCHIVE_OUTLINED))
        top_actions.append(ghost_button("Help", lambda e: self._show_help(), ft.Icons.HELP_OUTLINE))
        top_actions.append(ghost_button("Sign out", self._logout, ft.Icons.LOGOUT))

        title_controls = [
            ft.Text(
                s.get("title") or f"Storage {s.get('qr_code')}",
                size=20,
                weight=ft.FontWeight.BOLD,
                color=C.text,
            ),
        ]
        if self._is_owner():
            title_controls.append(
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    icon_color=C.primary,
                    tooltip="Rename storage",
                    icon_size=20,
                    on_click=lambda e: self._show_rename_dialog(sid, s.get("title") or f"Storage {s.get('qr_code')}"),
                )
            )

        perm_label = self._perm()
        is_public = bool(s.get("is_public"))
        pending_n = self.offline.pending_count(sid) if sid else 0

        status_chips = []
        if is_public:
            status_chips.append(chip("PUBLIC", C.accent))
        if self._offline_mode:
            status_chips.append(chip("OFFLINE", C.warning))
        elif pending_n:
            status_chips.append(chip(f"SYNC {pending_n}", C.warning))
        if not can_write:
            status_chips.append(chip("READ ONLY", C.warning))
        else:
            status_chips.append(chip(perm_label.upper(), C.owned))

        sync_banner = ft.Container(visible=False)
        self._sync_banner = sync_banner
        if self._offline_mode or pending_n:
            sync_banner.visible = True
            sync_banner.content = card(
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.CLOUD_OFF if self._offline_mode else ft.Icons.CLOUD_SYNC,
                            color=C.warning,
                            size=20,
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    "Offline mode" if self._offline_mode else f"{pending_n} item(s) waiting to sync",
                                    color=C.text,
                                    weight=ft.FontWeight.W_700,
                                    size=13,
                                ),
                                muted(
                                    "Showing cached files & notes. New files/notes queue until you're back online."
                                    if self._offline_mode
                                    else "Files and notes will upload automatically when the connection returns."
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ghost_button(
                            "Sync",
                            lambda e, st=sid: self.page.run_task(self._manual_sync, st),
                            ft.Icons.SYNC,
                        )
                        if pending_n and not self._offline_mode
                        else ft.Container(),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=12,
            )

        public_card = ft.Container()
        if self._is_owner() and sid:
            public_switch = ft.Switch(
                value=is_public,
                active_color=C.primary,
                on_change=lambda e, st=sid: self.page.run_task(
                    self._toggle_public, st, bool(e.control.value)
                ),
            )
            public_card = card(
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text("Public vault", weight=ft.FontWeight.W_700, color=C.text, size=13),
                                muted(
                                    "Anyone who scans this QR (while signed in) can view files & notes. "
                                    "Only you can edit."
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        public_switch,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=12,
            )

        body_controls = [
            ft.Row(
                [
                    ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=lambda e: self.go_home()),
                    ft.Column(
                        [
                            ft.Row(title_controls, spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            muted(f"QR {s.get('qr_code')} · {perm_label} · owner {s.get('owner_phone')}"),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Row(status_chips, spacing=6, wrap=True) if status_chips else ft.Container(),
                ]
            ),
            sync_banner,
            public_card,
            card(
                ft.Column(
                    [
                        ft.Text("Retention policy", weight=ft.FontWeight.W_700, color=C.text, size=13),
                        muted(
                            "Files are permanently deleted 30 days after upload. "
                            "Archive hides a file from this list — restore or delete it from Archived."
                        ),
                    ],
                    spacing=4,
                ),
                padding=12,
            ),
            ft.Row(top_actions, spacing=10, scroll=ft.ScrollMode.AUTO) if top_actions else ft.Container(),
            ft.Row(
                [
                    filter_row,
                    view_toggle,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Column(
                [search_field, files_host],
                spacing=6,
                expand=True,
                tight=True,
            ),
        ]

        self.set_view(ft.Column(body_controls, spacing=12, expand=True, tight=True))
        self._rebuild_filter_chips(filter_row, sid, on_select=set_filter)
        self._rebuild_view_toggle(view_toggle, sid, on_select=set_mode)
        self.page.run_task(self._load_files, sid)

    def _show_note_editor(
        self,
        storage_id: int,
        note_id: int | None = None,
        note_html: str = "",
        title: str = "",
    ):
        title_field = ft.TextField(
            label="Title (optional)",
            value=title or "",
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        editor = ft.TextField(
            value=note_html or "",
            multiline=True,
            min_lines=6,
            max_lines=12,
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            cursor_color=C.primary,
            text_size=14,
            hint_text="Write a note… use toolbar for bold, color, size",
        )
        preview = ft.Container(
            content=note_to_text_control(note_html),
            bgcolor=C.bg,
            border=ft.Border.all(1, C.border),
            border_radius=12,
            padding=10,
            width=340,
        )

        def refresh_preview(_=None):
            preview.content = note_to_text_control(editor.value or "")
            try:
                preview.update()
            except Exception:
                pass

        editor.on_change = refresh_preview

        def sel_range():
            sel = editor.selection
            if sel is None:
                pos = len(editor.value or "")
                return pos, pos
            a = int(getattr(sel, "base_offset", 0) or 0)
            b = int(getattr(sel, "extent_offset", a) or a)
            return (a, b) if a <= b else (b, a)

        def apply_wrap(open_tag: str, close_tag: str):
            start, end = sel_range()
            editor.value = wrap_selection(editor.value or "", start, end, open_tag, close_tag)
            refresh_preview()
            try:
                editor.update()
            except Exception:
                pass

        def tool_btn(label: str, on_click, bgcolor=None):
            return ft.Container(
                content=ft.Text(label, size=12, weight=ft.FontWeight.W_700, color=C.bg if bgcolor else C.text),
                bgcolor=bgcolor or C.surface_alt,
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                border_radius=10,
                border=ft.Border.all(1, C.border if not bgcolor else bgcolor),
                on_click=on_click,
                ink=True,
            )

        toolbar = ft.Row(
            [
                tool_btn("B", lambda e: apply_wrap("<b>", "</b>"), C.primary),
                tool_btn("I", lambda e: apply_wrap("<i>", "</i>")),
                tool_btn("U", lambda e: apply_wrap("<u>", "</u>")),
                *[
                    tool_btn(
                        str(sz),
                        lambda e, s=sz: apply_wrap(f'<span style="font-size:{s}px">', "</span>"),
                    )
                    for sz in NOTE_SIZES
                ],
            ],
            spacing=6,
            wrap=True,
        )
        color_row = ft.Row(
            [
                ft.Container(
                    width=28,
                    height=28,
                    bgcolor=hex_color,
                    border_radius=999,
                    border=ft.Border.all(1, C.border),
                    tooltip=name,
                    on_click=lambda e, c=hex_color: apply_wrap(
                        f'<span style="color:{c}">', "</span>"
                    ),
                    ink=True,
                )
                for hex_color, name in NOTE_COLORS
            ],
            spacing=8,
        )

        def close(_=None):
            self.page.pop_dialog()

        def save(_=None):
            body = editor.value or ""
            ttl = (title_field.value or "").strip()
            if not body.strip() and not ttl:
                self.toast("Write something first", error=True)
                return
            self.page.pop_dialog()
            self.page.run_task(self._save_note, storage_id, note_id, body, ttl)

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text(
                "Edit note" if note_id else "Add note",
                color=C.text,
                weight=ft.FontWeight.W_700,
            ),
            content=ft.Container(
                width=360,
                content=ft.Column(
                    [
                        muted("Select text, then tap Bold / size / color (HTML-style)."),
                        title_field,
                        toolbar,
                        color_row,
                        editor,
                        muted("Preview"),
                        preview,
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                    tight=True,
                ),
                height=520,
            ),
            actions=[
                ft.Button(content="Cancel", on_click=close),
                ft.Button(content="Save", on_click=save, bgcolor=C.primary, color=C.bg),
            ],
        )
        self.page.show_dialog(dialog)

    async def _save_note(self, storage_id: int, note_id: int | str | None, body: str, title: str = ""):
        try:
            if note_id is not None and not (isinstance(note_id, str) and str(note_id).startswith("local-")):
                await asyncio.to_thread(self.api.update_note, storage_id, int(note_id), body, title)
                self.toast("Note updated")
            elif note_id is not None and isinstance(note_id, str) and str(note_id).startswith("local-"):
                # Still offline-local — update queued create
                self.offline.enqueue_note_update(storage_id, note_id, title, body)
                self.toast("Note saved offline — will sync later")
            else:
                await asyncio.to_thread(self.api.create_note, storage_id, body, title)
                self.toast("Note added")
            await self._load_files(storage_id)
        except ApiError as e:
            # Queue for later if we have write access
            if self._can_write():
                if note_id is None:
                    self.offline.enqueue_note_create(storage_id, title, body)
                else:
                    self.offline.enqueue_note_update(storage_id, note_id, title, body)
                self.toast("Saved offline — will sync when online")
                await self._load_files(storage_id)
            else:
                self.toast(e.message, error=True)
        except Exception as e:
            if is_network_error(e) and self._can_write():
                if note_id is None or (isinstance(note_id, str) and str(note_id).startswith("local-")):
                    if note_id is None:
                        self.offline.enqueue_note_create(storage_id, title, body)
                    else:
                        self.offline.enqueue_note_update(storage_id, note_id, title, body)
                else:
                    self.offline.enqueue_note_update(storage_id, note_id, title, body)
                self.toast("Saved offline — will sync when online")
                self._offline_mode = True
                await self._load_files(storage_id)
            else:
                self.toast(str(e), error=True)

    async def _delete_note(self, storage_id: int, note_id: int | str):
        try:
            if isinstance(note_id, str) and str(note_id).startswith("local-"):
                self.offline.enqueue_note_delete(storage_id, note_id)
                self.toast("Queued note removed")
                await self._load_files(storage_id)
                return
            await asyncio.to_thread(self.api.delete_note, storage_id, int(note_id))
            self.toast("Note deleted")
            await self._load_files(storage_id)
        except ApiError as e:
            if self._can_write():
                self.offline.enqueue_note_delete(storage_id, note_id)
                self.toast("Delete queued — will sync when online")
                await self._load_files(storage_id)
            else:
                self.toast(e.message, error=True)
        except Exception as e:
            if is_network_error(e) and self._can_write():
                self.offline.enqueue_note_delete(storage_id, note_id)
                self.toast("Delete queued — will sync when online")
                await self._load_files(storage_id)
            else:
                self.toast(str(e), error=True)

    async def _toggle_public(self, storage_id: int, is_public: bool):
        try:
            storage = await asyncio.to_thread(self.api.set_storage_public, storage_id, is_public)
            self.current_storage = storage
            self.toast("Public access enabled" if is_public else "Vault is private again")
            self.go_storage()
        except ApiError as e:
            self.toast(e.message, error=True)
            self.go_storage()
        except Exception as e:
            self.toast(str(e), error=True)
            self.go_storage()

    async def _manual_sync(self, storage_id: int):
        await self._flush_offline_notes(silent=False)
        self._offline_mode = False
        await self._load_files(storage_id)
        self.go_storage()

    async def _prefetch_offline_files(self, storage_id: int):
        files = list(self._storage_files_cache or [])
        if not files:
            snap = self.offline.load_snapshot(storage_id)
            files = list((snap or {}).get("files") or [])
        if not files:
            self.toast("No files to cache")
            return
        self.toast(f"Caching {len(files)} file(s) for offline…")
        ok = 0
        fail = 0
        for f in files:
            fid = f.get("id")
            name = f.get("original_name") or "file"
            if fid is None:
                continue
            try:
                await self._cache_file(storage_id, int(fid), name)
                ok += 1
            except Exception:
                fail += 1
        if fail:
            self.toast(f"Cached {ok}, failed {fail}", error=True)
        else:
            self.toast(f"Cached {ok} file(s) for offline browse")

    def _rebuild_filter_chips(self, filter_row: ft.Row, storage_id: int, on_select=None):
        def default_select(kind: str):
            self.file_filter = kind
            self._rebuild_filter_chips(filter_row, storage_id, on_select=default_select)
            self.page.run_task(self._load_files, storage_id)

        select = on_select or default_select
        filter_row.controls.clear()
        for kind, label in [("all", "All"), ("images", "Images"), ("docs", "Docs"), ("notes", "Notes")]:
            active = self.file_filter == kind
            filter_row.controls.append(
                ft.Container(
                    content=ft.Text(label, size=12, weight=ft.FontWeight.W_600, color=C.bg if active else C.text),
                    bgcolor=C.primary if active else C.surface_alt,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    border_radius=999,
                    border=ft.Border.all(1, C.border if not active else C.primary),
                    on_click=lambda e, k=kind: select(k),
                )
            )
        try:
            filter_row.update()
        except Exception:
            pass

    def _rebuild_view_toggle(self, view_toggle: ft.Row, storage_id: int, on_select=None):
        def default_select(mode: str):
            if self.browse_mode == mode:
                return
            self.browse_mode = mode
            self._rebuild_view_toggle(view_toggle, storage_id, on_select=default_select)
            self.page.run_task(self._load_files, storage_id)

        select = on_select or default_select
        list_active = self.browse_mode == "list"
        icons_active = self.browse_mode == "icons"

        def mode_chip(icon, mode: str, active: bool, tooltip: str):
            return ft.Container(
                content=ft.Icon(icon, size=20, color=C.bg if active else C.text_muted),
                bgcolor=C.primary if active else C.surface_alt,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=12,
                border=ft.Border.all(1, C.primary if active else C.border),
                on_click=lambda e, m=mode: select(m),
                ink=True,
                tooltip=tooltip,
            )

        view_toggle.controls = [
            mode_chip(ft.Icons.VIEW_LIST, "list", list_active, "List view"),
            mode_chip(ft.Icons.GRID_VIEW, "icons", icons_active, "Icons view"),
        ]
        try:
            view_toggle.update()
        except Exception:
            pass

    def _plain_note_text(self, note: dict) -> str:
        import re

        raw = f"{note.get('title') or ''} {note.get('body') or ''}"
        return re.sub(r"<[^>]+>", " ", raw).lower()

    def _files_matching_search(self, files: list[dict]) -> list[dict]:
        q = (self.file_search or "").strip().lower()
        if not q:
            return files
        return [f for f in files if q in (f.get("original_name") or f.get("name") or "").lower()]

    def _notes_matching_search(self, notes: list[dict]) -> list[dict]:
        q = (self.file_search or "").strip().lower()
        if not q:
            return notes
        return [n for n in notes if q in self._plain_note_text(n)]

    def _note_tile(self, storage_id: int, note: dict) -> ft.Control:
        title = (note.get("title") or "").strip() or "Note"
        can_write = self._can_write()
        preview = note_plain_preview(note.get("body") or "", limit=90)
        pending = bool(note.get("pending")) or (
            isinstance(note.get("id"), str) and str(note.get("id")).startswith("local-")
        )

        def open_note(e=None, n=note):
            self._show_note_viewer(storage_id, n)

        actions = []
        if pending:
            actions.append(
                ft.Icon(ft.Icons.CLOUD_UPLOAD_OUTLINED, color=C.warning, size=18),
            )
        if can_write:
            actions.extend(
                [
                    ft.IconButton(
                        ft.Icons.EDIT_OUTLINED,
                        icon_color=C.primary,
                        icon_size=20,
                        tooltip="Edit",
                        on_click=lambda e, n=note: self._show_note_editor(
                            storage_id,
                            note_id=n.get("id"),
                            note_html=n.get("body") or "",
                            title=n.get("title") or "",
                        ),
                    ),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        icon_color=C.danger,
                        icon_size=20,
                        tooltip="Delete",
                        on_click=lambda e, nid=note.get("id"): self.page.run_task(
                            self._delete_note, storage_id, nid
                        ),
                    ),
                ]
            )
        return ft.Container(
            bgcolor=C.surface,
            border=ft.Border.all(1, C.warning if pending else C.border),
            border_radius=14,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            ink=True,
            on_click=open_note,
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(ft.Icons.STICKY_NOTE_2_OUTLINED, color=C.accent, size=22),
                        bgcolor=C.surface_alt,
                        padding=10,
                        border_radius=12,
                    ),
                    ft.Column(
                        [
                            ft.Text(title, color=C.text, weight=ft.FontWeight.W_700, size=14, max_lines=1),
                            ft.Text(
                                ("⏳ Pending sync · " if pending else "") + preview,
                                color=C.text_muted,
                                size=12,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                        tight=True,
                    ),
                    *actions,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
        )

    def _note_icon_tile(self, storage_id: int, note: dict) -> ft.Control:
        title = (note.get("title") or "").strip() or "Note"
        preview = note_plain_preview(note.get("body") or "", limit=40)

        def open_note(_=None, n=note):
            self._show_note_viewer(storage_id, n)

        return ft.Container(
            bgcolor=C.surface,
            border=ft.Border.all(1, C.border),
            border_radius=14,
            padding=12,
            on_click=open_note,
            ink=True,
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.STICKY_NOTE_2_OUTLINED, color=C.accent, size=36),
                    ft.Text(title, color=C.text, size=12, weight=ft.FontWeight.W_600, max_lines=1),
                    ft.Text(
                        preview,
                        color=C.text_muted,
                        size=11,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
                tight=True,
            ),
            alignment=ft.Alignment.CENTER,
        )

    def _show_note_viewer(self, storage_id: int, note: dict):
        title = (note.get("title") or "").strip() or "Note"
        can_write = self._can_write()

        def close(_=None):
            self.page.pop_dialog()

        def edit(_=None):
            self.page.pop_dialog()
            self._show_note_editor(
                storage_id,
                note_id=note.get("id"),
                note_html=note.get("body") or "",
                title=note.get("title") or "",
            )

        actions = [ft.Button(content="Close", on_click=close)]
        if can_write:
            actions.insert(0, ft.Button(content="Edit", on_click=edit, bgcolor=C.primary, color=C.bg))

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text(title, color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Container(
                width=360,
                content=ft.Column(
                    [
                        muted("Note"),
                        note_to_text_control(note.get("body") or ""),
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                    tight=True,
                ),
                height=420,
            ),
            actions=actions,
        )
        self.page.show_dialog(dialog)

    def _merged_vault_items(self, files: list[dict], notes: list[dict]) -> list[dict]:
        items = []
        for n in notes:
            items.append(
                {
                    "kind": "note",
                    "id": n.get("id"),
                    "sort_order": n.get("sort_order", 0),
                    "data": n,
                }
            )
        for f in files:
            items.append(
                {
                    "kind": "file",
                    "id": f.get("id"),
                    "sort_order": f.get("sort_order", 0),
                    "data": f,
                }
            )
        items.sort(key=lambda x: (x.get("sort_order") if x.get("sort_order") is not None else 10**9, x["kind"], x["id"] or 0))
        return items

    def _can_reorder_vault_items(self) -> bool:
        return (
            self._can_write()
            and self.browse_mode == "list"
            and self.file_filter == "all"
            and not (self.file_search or "").strip()
        )

    def _render_storage_files(self, storage_id: int):
        host = self._files_host
        if host is None:
            return

        show_notes = self.file_filter in ("all", "notes")
        show_files = self.file_filter != "notes"
        files = self._files_matching_search(self._storage_files_cache) if show_files else []
        notes = self._notes_matching_search(self._storage_notes_cache) if show_notes else []
        merged = self._merged_vault_items(files, notes)
        self._vault_visible_items = merged

        if not merged:
            if not self._storage_files_cache and not self._storage_notes_cache:
                host.content = muted(
                    "No items yet — upload a file or add a note."
                    if self._can_write()
                    else "Nothing here yet."
                )
            else:
                host.content = muted("No items match your search/filter.")
            self.page.update()
            return

        self._expanded_fid = None
        self._preview_panels = {}
        leadings = []
        can_reorder = self._can_reorder_vault_items() and len(merged) > 1

        if self.browse_mode == "icons":
            grid = ft.GridView(
                expand=True,
                runs_count=2,
                max_extent=170,
                child_aspect_ratio=0.85,
                spacing=10,
                run_spacing=10,
                padding=4,
            )
            for item in merged:
                if item["kind"] == "note":
                    grid.controls.append(self._note_icon_tile(storage_id, item["data"]))
                else:
                    tile, leading = self._file_icon_tile(storage_id, item["data"])
                    grid.controls.append(tile)
                    leadings.append((item["data"], leading))
            host.content = grid
        else:
            if can_reorder:
                list_view = ft.ReorderableListView(
                    expand=True,
                    spacing=0,
                    padding=0,
                    show_default_drag_handles=False,
                    on_reorder=lambda e, sid=storage_id: self._on_vault_reorder(e, sid),
                )
            else:
                list_view = ft.ListView(expand=True, spacing=0, padding=0)
            for item in merged:
                if item["kind"] == "note":
                    tile = self._note_tile(storage_id, item["data"])
                else:
                    tile, leading = self._file_tile(storage_id, item["data"], list_view)
                    leadings.append((item["data"], leading))
                list_view.controls.append(self._wrap_list_item(tile, reorder=can_reorder))
            host.content = list_view

        self.page.update()
        if leadings:
            self.page.run_task(self._hydrate_image_thumbs, storage_id, leadings)

    def _on_vault_reorder(self, e: ft.OnReorderEvent, storage_id: int):
        if not self._can_reorder_vault_items():
            self.toast("Reorder in All list view without search", error=True)
            self._render_storage_files(storage_id)
            return
        old = e.old_index
        new = e.new_index
        if old is None or new is None:
            return
        if new > old:
            new -= 1
        items = list(self._vault_visible_items)
        if not (0 <= old < len(items)) or not (0 <= new <= len(items)):
            return
        row = items.pop(old)
        items.insert(new, row)
        self._vault_visible_items = items
        # Reflect order into caches
        for i, it in enumerate(items):
            it["sort_order"] = i
            it["data"]["sort_order"] = i
        self._storage_files_cache = [it["data"] for it in items if it["kind"] == "file"]
        self._storage_notes_cache = [it["data"] for it in items if it["kind"] == "note"]
        self._render_storage_files(storage_id)
        payload = [{"kind": it["kind"], "id": it["id"]} for it in items]
        self.page.run_task(self._persist_vault_order, storage_id, payload)

    async def _persist_vault_order(self, storage_id: int, items: list[dict]):
        try:
            await asyncio.to_thread(self.api.reorder_items, storage_id, items)
        except ApiError as err:
            self.toast(err.message, error=True)
            await self._load_files(storage_id)

    async def _load_files(self, storage_id: int, files_col=None, filter_row=None):
        host = self._files_host
        if host is None:
            return
        host.content = ft.Row(
            [ft.ProgressRing(width=22, height=22, color=C.primary)],
            alignment=ft.MainAxisAlignment.CENTER,
        )
        self.page.update()
        kind = None if self.file_filter in ("all", "notes") else self.file_filter

        # Try syncing pending notes first when online
        if self.offline.pending_count(storage_id):
            await self._flush_offline_notes(silent=True)

        try:
            # Always fetch full file list for offline snapshot (ignore UI filter for cache)
            all_files = await asyncio.to_thread(self.api.list_files, storage_id, None, self.show_archived)
            notes = await asyncio.to_thread(self.api.list_notes, storage_id)
            self.current_storage = await asyncio.to_thread(self.api.get_storage, storage_id, self.show_archived)
            server_notes = notes or []
            self.offline.save_snapshot(
                storage_id,
                storage=self.current_storage,
                files=all_files or [],
                notes=server_notes,
            )
            notes = self.offline.merge_notes_for_display(storage_id, server_notes)
            files = self.offline.merge_files_for_display(storage_id, all_files or [])
            if kind == "images":
                files = [f for f in files if str(f.get("content_type") or "").startswith("image/")]
            elif kind == "docs":
                files = [f for f in files if not str(f.get("content_type") or "").startswith("image/")]
            if self.file_filter == "notes":
                files = []
            self._offline_mode = False
            if self.current_storage:
                self.offline.upsert_home_storage(self.current_storage)
        except (ApiError, Exception) as e:
            snap = self.offline.load_snapshot(storage_id) or self.offline.ensure_snapshot(storage_id)
            if snap:
                self._offline_mode = True
                files = self.offline.merge_files_for_display(storage_id, snap.get("files") or [])
                if kind == "images":
                    files = [f for f in files if str(f.get("content_type") or "").startswith("image/")]
                elif kind == "docs":
                    files = [f for f in files if not str(f.get("content_type") or "").startswith("image/")]
                notes = self.offline.merge_notes_for_display(storage_id, snap.get("notes") or [])
                if snap.get("storage"):
                    self.current_storage = snap["storage"]
                if isinstance(e, ApiError) or is_network_error(e):
                    self.toast("Offline — cached vault contents")
                else:
                    self.toast(str(e), error=True)
            else:
                self.toast(e.message if isinstance(e, ApiError) else str(e), error=True)
                self._storage_files_cache = []
                self._storage_notes_cache = []
                host.content = muted("Could not load items")
                self.page.update()
                return

        self._storage_files_cache = files or []
        self._storage_notes_cache = notes or []
        self._render_storage_files(storage_id)

    def _show_rename_dialog(self, storage_id: int, current_title: str):
        name_field = ft.TextField(
            label="Storage name",
            value=current_title,
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )

        def close(_=None):
            self.page.pop_dialog()

        def save(_=None):
            title = (name_field.value or "").strip()
            if not title:
                self.toast("Name required", error=True)
                return
            self.page.pop_dialog()
            self.page.run_task(self._rename_storage, storage_id, title)

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text("Rename storage", color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Container(content=name_field, width=320),
            actions=[
                ft.Button(content="Cancel", on_click=close),
                ft.Button(content="Save", on_click=save, bgcolor=C.primary, color=C.bg),
            ],
        )
        self.page.show_dialog(dialog)

    async def _rename_storage(self, storage_id: int, title: str):
        try:
            data = await asyncio.to_thread(self.api.rename_storage, storage_id, title)
            self.current_storage = data
            self.toast("Storage renamed")
            self.go_storage()
        except ApiError as e:
            self.toast(e.message, error=True)

    async def _show_move_dialog(self, storage_id: int, file_id: int, file_name: str):
        try:
            storages = await asyncio.to_thread(self.api.list_storages)
        except ApiError as e:
            self.toast(e.message, error=True)
            return

        targets = [s for s in storages if s.get("id") != storage_id and s.get("my_permission") in ("owner", "manage", "write")]
        if not targets:
            self.toast("No other writable storage available", error=True)
            return

        dd = ft.Dropdown(
            label="Move to storage",
            value=str(targets[0]["id"]),
            options=[
                ft.dropdown.Option(
                    str(s["id"]),
                    f"{s.get('title') or 'Storage'} (QR {s.get('qr_code')})",
                )
                for s in targets
            ],
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            color=C.text,
            width=320,
        )

        def close(_=None):
            self.page.pop_dialog()

        def confirm(_=None):
            if not dd.value:
                self.toast("Select a storage", error=True)
                return
            self.page.pop_dialog()
            self.page.run_task(self._move_file, storage_id, file_id, int(dd.value))

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text("Move file", color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Column(
                [
                    muted(file_name),
                    muted("Choose destination storage (write access required)."),
                    dd,
                ],
                spacing=10,
                tight=True,
                width=320,
            ),
            actions=[
                ft.Button(content="Cancel", on_click=close),
                ft.Button(content="Move", on_click=confirm, bgcolor=C.primary, color=C.bg),
            ],
        )
        self.page.show_dialog(dialog)

    async def _move_file(self, storage_id: int, file_id: int, target_storage_id: int):
        try:
            data = await asyncio.to_thread(self.api.move_file, storage_id, file_id, target_storage_id)
            self.toast(data.get("message") or "File moved")
            await self._open_storage(storage_id)
        except ApiError as e:
            self.toast(e.message, error=True)

    def _leading_placeholder(self, content_type: str) -> ft.Container:
        return ft.Container(
            content=ft.Icon(file_icon(content_type), color=C.accent, size=26),
            width=48,
            height=48,
            bgcolor=C.surface_alt,
            border_radius=10,
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            border=ft.Border.all(1, C.border),
        )

    def _collapse_previews(self):
        for panel in self._preview_panels.values():
            panel.visible = False
            panel.content = None
            try:
                panel.update()
            except Exception:
                pass
        self._expanded_fid = None

    def _file_tile(self, storage_id: int, f: dict, files_col: ft.ListView) -> tuple[ft.Control, ft.Container]:
        name = f.get("original_name") or "file"
        archived = f.get("is_archived")
        fid = f["id"]
        content_type = f.get("content_type") or ""
        can_write = self._can_write()
        playable = is_playable(content_type, name)
        image = is_image(content_type, name)

        open_label = "Play" if playable else "Open"
        open_icon = ft.Icons.PLAY_CIRCLE_OUTLINE if playable else ft.Icons.VISIBILITY_OUTLINED
        pending = bool(f.get("pending")) or (
            isinstance(fid, str) and str(fid).startswith("local-file-")
        )
        cached = pending or self.offline.has_cached_file(
            storage_id, fid if isinstance(fid, int) else -1, name
        )

        preview = ft.Container(
            visible=False,
            content=None,
            padding=ft.Padding.only(left=4, right=4, top=8, bottom=4),
        )
        self._preview_panels[fid] = preview

        def toggle(_=None, i=fid, n=name, ct=content_type, panel=preview):
            self.page.run_task(self._toggle_inline_preview, storage_id, i, n, ct, panel)

        menu_items = [
            ft.PopupMenuItem(content=open_label, icon=open_icon, on_click=toggle),
            ft.PopupMenuItem(
                content="Download",
                icon=ft.Icons.DOWNLOAD,
                on_click=lambda e, i=fid, n=name: self.page.run_task(self._download, storage_id, i, n),
            ),
        ]
        if self._is_owner():
            menu_items.append(
                ft.PopupMenuItem(
                    content="Move to…",
                    icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINE,
                    on_click=lambda e, i=fid, n=name: self.page.run_task(self._show_move_dialog, storage_id, i, n),
                )
            )
        if can_write:
            menu_items.extend(
                [
                    ft.PopupMenuItem(
                        content="Unarchive" if archived else "Archive",
                        icon=ft.Icons.ARCHIVE_OUTLINED,
                        on_click=lambda e, i=fid, a=not archived: self.page.run_task(
                            self._archive, storage_id, i, a
                        ),
                    ),
                    ft.PopupMenuItem(
                        content="Delete",
                        icon=ft.Icons.DELETE_OUTLINE,
                        on_click=lambda e, i=fid: self.page.run_task(self._delete_file, storage_id, i),
                    ),
                ]
            )

        leading = self._leading_placeholder(content_type)
        trailing: list[ft.Control] = []
        if pending:
            trailing.append(chip("PENDING", C.warning))
        elif cached:
            trailing.append(chip("CACHED", C.success))
        elif self._offline_mode:
            trailing.append(chip("ONLINE ONLY", C.warning))
        if playable:
            trailing.append(
                ft.IconButton(
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    icon_color=C.primary,
                    tooltip="Play",
                    on_click=toggle,
                )
            )
        else:
            trailing.append(ft.Icon(ft.Icons.EXPAND_MORE, color=C.text_muted, size=22))
        trailing.append(
            ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                icon_color=C.text_muted,
                items=menu_items,
            )
        )

        days = f.get("days_remaining")
        days_txt = f" · {days}d left" if days is not None else ""
        hint = "tap to play below" if playable else (
            "tap to view below" if image or is_pdf(content_type, name) else "tap to open below"
        )
        header = ft.Container(
            ink=True,
            border_radius=14,
            padding=10,
            on_click=toggle,
            content=ft.Row(
                [
                    leading,
                    ft.Column(
                        [
                            ft.Text(
                                name,
                                color=C.text,
                                weight=ft.FontWeight.W_600,
                                size=14,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            muted(
                                f"{fmt_size(f.get('size_original'))} → {fmt_size(f.get('size_compressed'))} compressed"
                                + (" · archived" if archived else "")
                                + days_txt
                                + f" · {hint}"
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    *trailing,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        tile = ft.Container(
            bgcolor=C.surface,
            border=ft.Border.all(1, C.border),
            border_radius=14,
            content=ft.Column([header, preview], spacing=0),
        )
        return tile, leading

    def _file_icon_tile(self, storage_id: int, f: dict) -> tuple[ft.Control, ft.Container]:
        name = f.get("original_name") or "file"
        fid = f["id"]
        content_type = f.get("content_type") or ""
        can_write = self._can_write()
        playable = is_playable(content_type, name)

        leading = ft.Container(
            content=ft.Icon(file_icon(content_type), color=C.accent, size=40),
            width=120,
            height=90,
            bgcolor=C.surface_alt,
            border_radius=12,
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            border=ft.Border.all(1, C.border),
        )

        def open_full(_=None, i=fid, n=name, ct=content_type):
            self.page.run_task(self._open_full_preview, storage_id, i, n, ct)

        menu_items = [
            ft.PopupMenuItem(
                content="Open",
                icon=ft.Icons.OPEN_IN_FULL,
                on_click=open_full,
            ),
            ft.PopupMenuItem(
                content="Download",
                icon=ft.Icons.DOWNLOAD,
                on_click=lambda e, i=fid, n=name: self.page.run_task(self._download, storage_id, i, n),
            ),
        ]
        if self._is_owner():
            menu_items.append(
                ft.PopupMenuItem(
                    content="Move to…",
                    icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINE,
                    on_click=lambda e, i=fid, n=name: self.page.run_task(
                        self._show_move_dialog, storage_id, i, n
                    ),
                )
            )
        if can_write:
            menu_items.extend(
                [
                    ft.PopupMenuItem(
                        content="Archive",
                        icon=ft.Icons.ARCHIVE_OUTLINED,
                        on_click=lambda e, i=fid: self.page.run_task(self._archive, storage_id, i, True),
                    ),
                    ft.PopupMenuItem(
                        content="Delete",
                        icon=ft.Icons.DELETE_OUTLINE,
                        on_click=lambda e, i=fid: self.page.run_task(self._delete_file, storage_id, i),
                    ),
                ]
            )

        days = f.get("days_remaining")
        days_txt = f"{days}d left" if days is not None else ""
        tile = ft.Container(
            bgcolor=C.surface,
            border=ft.Border.all(1, C.border),
            border_radius=14,
            padding=10,
            ink=True,
            on_click=open_full,
            content=ft.Column(
                [
                    ft.Stack(
                        [
                            leading,
                            ft.Container(
                                content=ft.Icon(
                                    ft.Icons.PLAY_ARROW_ROUNDED if playable else ft.Icons.OPEN_IN_FULL,
                                    color=C.primary,
                                    size=18,
                                ),
                                right=4,
                                bottom=4,
                                bgcolor="#00000088",
                                border_radius=999,
                                padding=4,
                            ),
                            ft.Container(
                                content=ft.PopupMenuButton(
                                    icon=ft.Icons.MORE_VERT,
                                    icon_color=C.text,
                                    icon_size=18,
                                    items=menu_items,
                                ),
                                right=0,
                                top=0,
                            ),
                        ],
                        width=120,
                        height=90,
                    ),
                    ft.Text(
                        name,
                        color=C.text,
                        size=12,
                        weight=ft.FontWeight.W_600,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    muted(days_txt or "tap to open"),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        return tile, leading

    async def _hydrate_image_thumbs(self, storage_id: int, leadings: list[tuple[dict, ft.Container]]):
        for f, leading in leadings:
            name = f.get("original_name") or "file"
            content_type = f.get("content_type") or ""
            if not is_image(content_type, name):
                continue
            try:
                path = await self._cache_file(storage_id, f["id"], name)
                w = int(leading.width or 48)
                h = int(leading.height or 48)
                leading.content = ft.Image(
                    src=str(path),
                    width=w,
                    height=h,
                    fit=ft.BoxFit.COVER,
                )
                leading.bgcolor = "#000000"
                leading.update()
            except Exception:
                continue

    async def _open_full_preview(self, storage_id: int, file_id: int, name: str, content_type: str):
        self.toast("Opening…")
        try:
            dest = await self._cache_file(storage_id, file_id, name)
        except ApiError as e:
            self.toast(e.message, error=True)
            return
        except Exception as e:
            self.toast(str(e), error=True)
            return
        self.go_full_viewer(storage_id, name, dest, content_type or "")

    def go_full_viewer(self, storage_id: int, name: str, path: Path, content_type: str):
        media = self._build_full_media(name, path, content_type)
        frame = ft.Container(
            content=media,
            expand=True,
            bgcolor=C.surface,
            border=ft.Border.all(1, C.border),
            border_radius=16,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            padding=8,
        )
        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(
                                ft.Icons.ARROW_BACK,
                                icon_color=C.text,
                                on_click=lambda e: self.page.run_task(self._open_storage, storage_id),
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        name,
                                        size=16,
                                        weight=ft.FontWeight.W_700,
                                        color=C.text,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    muted("Full screen preview"),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                        ]
                    ),
                    frame,
                ],
                spacing=12,
                expand=True,
            )
        )

    def _build_full_media(self, name: str, path: Path, content_type: str) -> ft.Control:
        ct = (content_type or "").lower()
        ext = path.suffix.lower()

        if is_audio(ct, name):
            player = ftv.Video(
                expand=True,
                playlist=[ftv.VideoMedia(str(path))],
                autoplay=True,
                show_controls=True,
                fill_color="#0B1220",
                fit=ft.BoxFit.CONTAIN,
                volume=100,
            )
            return ft.Column(
                [
                    ft.Icon(ft.Icons.AUDIOTRACK, size=72, color=C.primary),
                    ft.Text(name, color=C.text, weight=ft.FontWeight.W_600, text_align=ft.TextAlign.CENTER),
                    ft.Container(content=player, height=90, width=320, border_radius=12, clip_behavior=ft.ClipBehavior.HARD_EDGE),
                ],
                expand=True,
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            )

        if is_video(ct, name):
            return ftv.Video(
                expand=True,
                playlist=[ftv.VideoMedia(str(path))],
                autoplay=True,
                show_controls=True,
                aspect_ratio=16 / 9,
                fill_color="#000000",
                fit=ft.BoxFit.CONTAIN,
                volume=100,
            )

        if is_image(ct, name):
            return ft.Container(
                content=ft.Image(src=str(path), fit=ft.BoxFit.CONTAIN, expand=True),
                expand=True,
                alignment=ft.Alignment.CENTER,
                bgcolor="#000000",
            )

        if is_pdf(ct, name):
            try:
                pages = render_pdf_pages(path)
            except Exception as e:
                return muted(f"PDF render error: {e}")
            if not pages:
                return muted("Could not render PDF pages.")
            return ft.ListView(
                expand=True,
                spacing=10,
                padding=8,
                controls=[
                    ft.Container(
                        content=ft.Image(src=str(p), fit=ft.BoxFit.CONTAIN, width=380),
                        bgcolor="#111827",
                        border_radius=8,
                        padding=4,
                        alignment=ft.Alignment.CENTER,
                    )
                    for p in pages
                ],
            )

        if ext in TEXT_PREVIEW_EXTS or ct.startswith("text/"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 20000:
                    text = text[:20000] + "\n\n… truncated …"
            except Exception as e:
                text = f"Could not read file: {e}"
            return ft.ListView(
                expand=True,
                padding=12,
                controls=[ft.Text(text, color=C.text, size=13, selectable=True)],
            )

        return ft.Column(
            [
                ft.Icon(file_icon(ct), size=64, color=C.accent),
                muted("Preview not available for this type."),
                primary_button(
                    "Download copy",
                    lambda e: self._save_cached_copy(path, name),
                    ft.Icons.DOWNLOAD,
                    expand=False,
                ),
            ],
            expand=True,
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        )

    async def _cache_file(self, storage_id: int, file_id: int | str, name: str) -> Path:
        # Pending offline upload — use local copy directly
        if isinstance(file_id, str) and str(file_id).startswith("local-file-"):
            for f in self._storage_files_cache:
                if f.get("id") == file_id and f.get("local_path"):
                    p = Path(f["local_path"])
                    if p.exists():
                        return p
            raise FileNotFoundError("Pending offline file is missing on disk")

        # Prefer durable offline cache
        cached = self.offline.find_cached_file(storage_id, int(file_id), name)
        if cached:
            return cached

        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name) or "file"
        dest = PREVIEW_DIR / f"{storage_id}_{file_id}_{safe}"
        if dest.exists() and dest.stat().st_size > 0:
            try:
                return self.offline.store_cached_file(storage_id, int(file_id), name, dest)
            except Exception:
                return dest
        try:
            await asyncio.to_thread(self.api.download_file, storage_id, int(file_id), dest)
            try:
                return self.offline.store_cached_file(storage_id, int(file_id), name, dest)
            except Exception:
                return dest
        except Exception as e:
            # Last chance: any durable cache hit
            cached = self.offline.find_cached_file(storage_id, int(file_id), name)
            if cached:
                return cached
            raise e

    async def _toggle_inline_preview(
        self,
        storage_id: int,
        file_id: int,
        name: str,
        content_type: str,
        panel: ft.Container,
    ):
        # Collapse if already open
        if self._expanded_fid == file_id and panel.visible:
            self._collapse_previews()
            self.page.update()
            return

        self._collapse_previews()
        panel.visible = True
        panel.content = ft.Row(
            [ft.ProgressRing(width=22, height=22, color=C.primary), muted("Loading…")],
            spacing=10,
        )
        self._expanded_fid = file_id
        self.page.update()

        try:
            dest = await self._cache_file(storage_id, file_id, name)
            media = self._build_inline_media(name, dest, content_type or "")
            panel.content = media
            self.page.update()
        except ApiError as e:
            panel.content = muted(e.message)
            self.page.update()
            self.toast(e.message, error=True)
        except Exception as e:
            panel.content = muted(str(e))
            self.page.update()
            self.toast(str(e), error=True)

    def _build_inline_media(self, name: str, path: Path, content_type: str) -> ft.Control:
        ct = (content_type or "").lower()
        ext = path.suffix.lower()

        if is_audio(ct, name):
            player = ftv.Video(
                width=260,
                height=70,
                playlist=[ftv.VideoMedia(str(path))],
                autoplay=True,
                show_controls=True,
                fill_color="#0B1220",
                fit=ft.BoxFit.CONTAIN,
                volume=100,
                on_error=lambda e: self.toast(f"Playback error: {getattr(e, 'data', e)}", error=True),
            )
            return ft.Container(
                content=ft.Column(
                    [
                        muted("Audio player"),
                        ft.Container(
                            content=player,
                            width=260,
                            height=72,
                            bgcolor="#0B1220",
                            border_radius=12,
                            border=ft.Border.all(1, C.border),
                            clip_behavior=ft.ClipBehavior.HARD_EDGE,
                            padding=4,
                        ),
                    ],
                    spacing=6,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                ),
                alignment=ft.Alignment.CENTER_LEFT,
            )

        if is_video(ct, name):
            player = ftv.Video(
                width=360,
                height=200,
                playlist=[ftv.VideoMedia(str(path))],
                autoplay=True,
                show_controls=True,
                aspect_ratio=16 / 9,
                fill_color="#000000",
                fit=ft.BoxFit.CONTAIN,
                volume=100,
                on_error=lambda e: self.toast(f"Playback error: {getattr(e, 'data', e)}", error=True),
            )
            return ft.Container(
                content=player,
                height=210,
                bgcolor="#000000",
                border_radius=12,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                alignment=ft.Alignment.CENTER,
            )

        if is_image(ct, name):
            return ft.Container(
                content=ft.Image(src=str(path), fit=ft.BoxFit.CONTAIN, height=220, width=360),
                height=230,
                bgcolor="#000000",
                border_radius=12,
                alignment=ft.Alignment.CENTER,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            )

        if is_pdf(ct, name):
            try:
                pages = render_pdf_pages(path)
            except Exception as e:
                return muted(f"PDF render error: {e}")
            if not pages:
                return muted("Could not render PDF pages.")
            return ft.Container(
                content=ft.ListView(
                    height=280,
                    spacing=8,
                    padding=6,
                    controls=[
                        ft.Container(
                            content=ft.Image(src=str(p), fit=ft.BoxFit.CONTAIN, width=340),
                            bgcolor="#111827",
                            border_radius=8,
                            padding=4,
                            alignment=ft.Alignment.CENTER,
                        )
                        for p in pages
                    ],
                ),
                height=290,
                border_radius=12,
                border=ft.Border.all(1, C.border),
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            )

        if ext in TEXT_PREVIEW_EXTS or ct.startswith("text/"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 12000:
                    text = text[:12000] + "\n\n… truncated …"
            except Exception as e:
                text = f"Could not read file: {e}"
            return ft.Container(
                content=ft.ListView(
                    height=180,
                    padding=8,
                    controls=[ft.Text(text, color=C.text, size=12, selectable=True)],
                ),
                height=190,
                border_radius=12,
                border=ft.Border.all(1, C.border),
            )

        return ft.Column(
            [
                muted(f"{ct or 'unknown type'} · preview not available inline"),
                primary_button(
                    "Download copy",
                    lambda e: self._save_cached_copy(path, name),
                    ft.Icons.DOWNLOAD,
                    expand=False,
                ),
            ],
            spacing=8,
        )

    def _save_cached_copy(self, path: Path, name: str):
        dest = downloads_dir() / name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())
            self.toast(f"Saved: {dest}")
        except Exception as e:
            self.toast(str(e), error=True)

    async def _pick_and_upload(self):
        if not self.current_storage:
            return
        try:
            files = await self.file_picker.pick_files(allow_multiple=True)
        except Exception as e:
            self.toast(f"File picker error: {e}", error=True)
            return
        if not files:
            return
        paths = [f.path for f in files if getattr(f, "path", None)]
        if not paths:
            # Fallback: write temp files from bytes (web / no path)
            tmp_paths = []
            for f in files:
                data = getattr(f, "bytes", None)
                if data is None:
                    continue
                tmp = Path(tempfile.gettempdir()) / f"qr_vault_{f.name}"
                tmp.write_bytes(data)
                tmp_paths.append(str(tmp))
            paths = tmp_paths
        if not paths:
            self.toast("Could not read selected files", error=True)
            return
        await self._upload_many(self.current_storage["id"], paths)

    async def _upload_many(self, storage_id: int, paths: list[str]):
        if not self._can_write():
            self.toast("Read-only vault", error=True)
            return
        ok = 0
        queued = 0
        for p in paths:
            if self._offline_mode:
                try:
                    await asyncio.to_thread(self.offline.enqueue_file_upload, storage_id, p)
                    queued += 1
                except Exception as qe:
                    self.toast(f"Could not queue {Path(p).name}: {qe}", error=True)
                continue
            try:
                await asyncio.to_thread(self.api.upload_file, storage_id, p)
                ok += 1
            except ApiError as err:
                self.toast(err.message, error=True)
            except Exception as err:
                if is_network_error(err):
                    try:
                        await asyncio.to_thread(self.offline.enqueue_file_upload, storage_id, p)
                        queued += 1
                        self._offline_mode = True
                    except Exception as qe:
                        self.toast(f"Could not queue {Path(p).name}: {qe}", error=True)
                else:
                    self.toast(str(err), error=True)

        if ok and not queued:
            self.toast(f"Uploaded {ok} file(s)")
            await self._load_files(storage_id)
        elif queued and not ok:
            self._offline_mode = True
            self.toast(f"Saved {queued} file(s) offline — will sync when online")
            await self._load_files(storage_id)
            self.go_storage()
        elif ok and queued:
            self.toast(f"Uploaded {ok}, queued {queued} offline")
            await self._load_files(storage_id)
            self.go_storage()

    async def _download(self, storage_id: int, file_id: int, name: str):
        dest = downloads_dir() / name
        try:
            # Pending local file
            if isinstance(file_id, str) and str(file_id).startswith("local-file-"):
                for f in self._storage_files_cache:
                    if f.get("id") == file_id and f.get("local_path"):
                        Path(dest).parent.mkdir(parents=True, exist_ok=True)
                        shutil_copy = __import__("shutil").copy2
                        shutil_copy(f["local_path"], dest)
                        self.toast(f"Saved: {dest}")
                        return
            await asyncio.to_thread(self.api.download_file, storage_id, file_id, dest)
            self.toast(f"Saved: {dest}")
        except ApiError as e:
            # Try durable cache
            cached = self.offline.find_cached_file(storage_id, int(file_id) if not isinstance(file_id, str) else -1, name)
            if cached:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(cached.read_bytes())
                self.toast(f"Saved from cache: {dest}")
            else:
                self.toast(e.message, error=True)
        except Exception as e:
            self.toast(str(e), error=True)

    async def _archive(self, storage_id: int, file_id: int, archived: bool):
        try:
            await asyncio.to_thread(self.api.archive_file, storage_id, file_id, archived)
            self.toast("Archived" if archived else "Restored")
            await self._open_storage(storage_id)
        except ApiError as e:
            self.toast(e.message, error=True)

    async def _delete_file(self, storage_id: int, file_id: int | str):
        # Cancel a pending offline upload
        if isinstance(file_id, str) and str(file_id).startswith("local-file-"):
            q = self.offline.load_queue()
            kept = []
            removed_path = None
            for row in q:
                if row.get("op") == "upload" and row.get("local_id") == file_id:
                    removed_path = row.get("local_path")
                    continue
                kept.append(row)
            self.offline.save_queue(kept)
            self.offline._remove_local_file(storage_id, file_id)
            if removed_path:
                try:
                    Path(removed_path).unlink(missing_ok=True)
                except Exception:
                    pass
            self.toast("Queued upload cancelled")
            await self._load_files(storage_id)
            return
        try:
            await asyncio.to_thread(self.api.delete_file, storage_id, int(file_id))
            self.toast("File deleted")
            await self._open_storage(storage_id)
        except ApiError as e:
            self.toast(e.message, error=True)

    # ── Merge PDFs / images ─────────────────────────────────────
    def go_merge(self):
        s = self.current_storage or {}
        sid = s.get("id")
        files_col = ft.ListView(expand=True, spacing=6, padding=0)
        selected: dict[int, bool] = {}
        order: list[int] = []

        output_name = ft.TextField(
            label="Output PDF name",
            value="merged.pdf",
            prefix_icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        archive_sources = ft.Checkbox(
            label="Archive source files after merge",
            value=False,
            fill_color=C.primary,
            check_color=C.bg,
        )
        status = muted("Select PDFs and/or images (order = merge order).")

        def toggle_file(fid: int, checked: bool):
            selected[fid] = checked
            if checked and fid not in order:
                order.append(fid)
            if not checked and fid in order:
                order.remove(fid)
            n = sum(1 for v in selected.values() if v)
            status.value = f"{n} selected · merge order follows selection order"
            status.update()

        def do_merge(_):
            ids = [fid for fid in order if selected.get(fid)]
            if len(ids) < 1:
                self.toast("Select at least one PDF or image", error=True)
                return
            name = (output_name.value or "merged.pdf").strip()
            self.page.run_task(
                self._merge_files,
                sid,
                ids,
                name,
                bool(archive_sources.value),
            )

        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(
                                ft.Icons.ARROW_BACK,
                                icon_color=C.text,
                                on_click=lambda e: self.page.run_task(self._open_storage, sid),
                            ),
                            section_title("Merge to PDF"),
                        ]
                    ),
                    card(
                        ft.Column(
                            [
                                muted(
                                    "Combine PDFs together, or turn images into one PDF. "
                                    "Optional: archive the files used after a successful merge."
                                ),
                                output_name,
                                archive_sources,
                                primary_button("Merge now", do_merge, ft.Icons.MERGE_TYPE, expand=False),
                                status,
                            ],
                            spacing=10,
                        )
                    ),
                    muted("Eligible files (PDF + images)"),
                    ft.Container(content=files_col, expand=True),
                ],
                spacing=12,
                expand=True,
            )
        )
        self.page.run_task(self._load_merge_candidates, sid, files_col, toggle_file)

    async def _load_merge_candidates(self, storage_id: int, files_col: ft.ListView, toggle_file):
        files_col.controls = [ft.ProgressRing(width=22, height=22, color=C.primary)]
        self.page.update()
        try:
            files = await asyncio.to_thread(self.api.list_files, storage_id, None, False)
        except ApiError as e:
            self.toast(e.message, error=True)
            files_col.controls = [muted("Could not load files")]
            self.page.update()
            return

        eligible = []
        for f in files:
            name = f.get("original_name") or ""
            ct = f.get("content_type") or ""
            if is_pdf(ct, name) or is_image(ct, name):
                eligible.append(f)

        if not eligible:
            files_col.controls = [muted("No PDFs or images available to merge.")]
            self.page.update()
            return

        controls = []
        for f in eligible:
            fid = f["id"]
            name = f.get("original_name") or "file"
            cb = ft.Checkbox(
                value=False,
                fill_color=C.primary,
                check_color=C.bg,
                on_change=lambda e, i=fid: toggle_file(i, bool(e.control.value)),
            )
            controls.append(
                ft.Container(
                    bgcolor=C.surface,
                    border=ft.Border.all(1, C.border),
                    border_radius=12,
                    padding=10,
                    content=ft.Row(
                        [
                            cb,
                            ft.Icon(file_icon(f.get("content_type") or ""), color=C.accent, size=24),
                            ft.Column(
                                [
                                    ft.Text(name, color=C.text, weight=ft.FontWeight.W_600, size=13, expand=True),
                                    muted(f"{fmt_size(f.get('size_compressed'))} · {f.get('content_type') or ''}"),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
        files_col.controls = controls
        self.page.update()

    async def _merge_files(self, storage_id: int, file_ids: list[int], output_name: str, archive_sources: bool):
        self.toast("Merging…")
        try:
            data = await asyncio.to_thread(
                self.api.merge_files, storage_id, file_ids, output_name, archive_sources
            )
            msg = data.get("message") or "Merge complete"
            archived = data.get("archived_source_ids") or []
            if archived:
                msg += f" · archived {len(archived)} source(s)"
            self.toast(msg)
            await self._open_storage(storage_id)
        except ApiError as e:
            self.toast(e.message, error=True)
        except Exception as e:
            self.toast(str(e), error=True)

    # ── Archive management ──────────────────────────────────────
    def _show_help(self, _=None):
        def close(_e=None):
            self.page.pop_dialog()

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text("How QR Vault works", color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Container(
                width=340,
                content=ft.Column(
                    [
                        muted("Scan a QR code to open or create a storage vault."),
                        muted("Permissions: read (view/download), write (upload/archive/delete), manage (share)."),
                        muted(
                            "Sharing sends a request. The other user must Accept on Home "
                            "before the vault appears under Shared."
                        ),
                        muted(
                            "Drag the handles to reorder vaults on Home, and files/notes "
                            "inside a storage (All + list view)."
                        ),
                        muted(
                            "Add Note places a rich note in the list with files. "
                            "Edit bold/colors/sizes; filter with the Notes chip."
                        ),
                        muted("Archive hides a file from the main list. Open Archived to restore or permanently delete it."),
                        muted(
                            "Retention: every file is permanently deleted 30 days after upload "
                            "(even if archived). Download anything you need to keep."
                        ),
                        muted("Tap a file to preview/play it inline under the row."),
                        muted(
                            "Browse modes: List (preview under the row) or Icons (full-screen open)."
                        ),
                        muted(
                            "Merge PDF: combine selected PDFs and/or images into one PDF. "
                            "Optionally archive the source files after merge."
                        ),
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                height=340,
            ),
            actions=[ft.Button(content="Got it", on_click=close, bgcolor=C.primary, color=C.bg)],
        )
        self.page.show_dialog(dialog)

    def go_archive(self):
        s = self.current_storage or {}
        sid = s.get("id")
        can_write = self._can_write()
        files_col = ft.ListView(expand=True, spacing=8, padding=0)

        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(
                                ft.Icons.ARROW_BACK,
                                icon_color=C.text,
                                on_click=lambda e: self.page.run_task(self._open_storage, sid),
                            ),
                            section_title("Archived files"),
                        ]
                    ),
                    card(
                        ft.Column(
                            [
                                ft.Text("Manage archive", weight=ft.FontWeight.W_700, color=C.text),
                                muted(
                                    "Restore brings a file back to the main list. Delete removes it forever. "
                                    "Remember: all files (active or archived) are auto-deleted 30 days after upload."
                                ),
                            ],
                            spacing=6,
                        )
                    ),
                    ft.Container(content=files_col, expand=True, bgcolor=C.bg),
                ],
                spacing=12,
                expand=True,
            )
        )
        self.page.run_task(self._load_archived, sid, files_col, can_write)

    async def _load_archived(self, storage_id: int, files_col: ft.ListView, can_write: bool):
        files_col.controls = [ft.ProgressRing(width=22, height=22, color=C.primary)]
        self.page.update()
        try:
            files = await asyncio.to_thread(self.api.list_files, storage_id, None, True)
            files = [f for f in files if f.get("is_archived")]
        except ApiError as e:
            self.toast(e.message, error=True)
            files_col.controls = [muted("Could not load archived files")]
            self.page.update()
            return

        if not files:
            files_col.controls = [
                card(
                    ft.Column(
                        [
                            ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, color=C.text_muted, size=40),
                            muted("No archived files."),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    )
                )
            ]
            self.page.update()
            return

        controls = []
        for f in files:
            name = f.get("original_name") or "file"
            fid = f["id"]
            days = f.get("days_remaining")
            days_txt = f"{days} days left until auto-delete" if days is not None else "expiry unknown"
            actions = []
            if can_write:
                actions.extend(
                    [
                        ghost_button(
                            "Restore",
                            lambda e, i=fid: self.page.run_task(self._archive_from_mgr, storage_id, i, False),
                            ft.Icons.UNARCHIVE_OUTLINED,
                        ),
                        ghost_button(
                            "Delete",
                            lambda e, i=fid: self.page.run_task(self._delete_from_archive, storage_id, i),
                            ft.Icons.DELETE_OUTLINE,
                        ),
                    ]
                )
            controls.append(
                ft.Container(
                    bgcolor=C.surface,
                    border=ft.Border.all(1, C.border),
                    border_radius=14,
                    padding=12,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(file_icon(f.get("content_type") or ""), color=C.accent, size=28),
                                    ft.Column(
                                        [
                                            ft.Text(name, color=C.text, weight=ft.FontWeight.W_600, expand=True),
                                            muted(
                                                f"{fmt_size(f.get('size_compressed'))} · {days_txt}"
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(actions, spacing=8, scroll=ft.ScrollMode.AUTO) if actions else muted("Read-only — ask owner to restore/delete."),
                        ],
                        spacing=10,
                    ),
                )
            )
        files_col.controls = controls
        self.page.update()

    async def _archive_from_mgr(self, storage_id: int, file_id: int, archived: bool):
        try:
            await asyncio.to_thread(self.api.archive_file, storage_id, file_id, archived)
            self.toast("Restored" if not archived else "Archived")
            self.go_archive()
        except ApiError as e:
            self.toast(e.message, error=True)

    async def _delete_from_archive(self, storage_id: int, file_id: int):
        try:
            await asyncio.to_thread(self.api.delete_file, storage_id, file_id)
            self.toast("File permanently deleted")
            self.go_archive()
        except ApiError as e:
            self.toast(e.message, error=True)

    # ── Share ───────────────────────────────────────────────────
    def go_share(self):
        s = self.current_storage or {}
        sid = s.get("id")
        phone = ft.TextField(
            label="Phone to share with",
            hint_text="+9715...",
            prefix_icon=ft.Icons.PERSON_ADD_ALT,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        permission = ft.Dropdown(
            label="Permission",
            value="read",
            options=[
                ft.dropdown.Option("read", "Read"),
                ft.dropdown.Option("write", "Write"),
                ft.dropdown.Option("manage", "Manage"),
            ],
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            color=C.text,
        )
        shares_list = ft.ListView(expand=True, spacing=8)

        def do_share(_):
            if not phone.value:
                self.toast("Phone required", error=True)
                return
            self.page.run_task(self._share, sid, phone.value.strip(), permission.value, shares_list)

        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(
                                ft.Icons.ARROW_BACK,
                                icon_color=C.text,
                                on_click=lambda e: self.page.run_task(self._open_storage, sid),
                            ),
                            section_title("Share storage"),
                        ]
                    ),
                    muted(f"QR {s.get('qr_code')} — send a request; they must accept before seeing this vault"),
                    card(
                        ft.Column(
                            [
                                phone,
                                permission,
                                primary_button("Send share request", do_share, ft.Icons.IOS_SHARE),
                            ],
                            spacing=12,
                        )
                    ),
                    muted("Share requests & access"),
                    ft.Container(content=shares_list, expand=True),
                ],
                spacing=12,
                expand=True,
            )
        )
        self.page.run_task(self._load_shares, sid, shares_list)

    async def _load_shares(self, storage_id: int, shares_list: ft.ListView):
        try:
            shares = await asyncio.to_thread(self.api.list_shares, storage_id)
        except ApiError as e:
            self.toast(e.message, error=True)
            return
        if not shares:
            shares_list.controls = [muted("No share requests yet")]
            self.page.update()
            return
        controls = []
        for sh in shares:
            phone = sh.get("user_phone") or ""
            current_perm = sh.get("permission") or "read"
            status = sh.get("status") or "pending"
            status_color = {
                "accepted": C.success,
                "pending": C.warning,
                "rejected": C.danger,
            }.get(status, C.text_muted)
            perm_dd = ft.Dropdown(
                value=current_perm,
                options=[
                    ft.dropdown.Option("read", "Read"),
                    ft.dropdown.Option("write", "Write"),
                    ft.dropdown.Option("manage", "Manage"),
                ],
                width=140,
                border_radius=12,
                bgcolor=C.surface_alt,
                border_color=C.border,
                color=C.text,
                text_size=13,
                on_select=lambda e, p=phone, prev=current_perm: self.page.run_task(
                    self._update_share_permission,
                    storage_id,
                    p,
                    (e.control.value if e.control.value else prev),
                    shares_list,
                ),
            )
            controls.append(
                ft.Container(
                    bgcolor=C.surface,
                    border=ft.Border.all(1, C.border),
                    border_radius=14,
                    padding=12,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Text(phone, color=C.text, weight=ft.FontWeight.W_600),
                                            muted(f"Status: {status}"),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                    chip(status.upper(), status_color),
                                    perm_dd,
                                    ft.IconButton(
                                        ft.Icons.DELETE_OUTLINE,
                                        icon_color=C.danger,
                                        tooltip="Revoke",
                                        on_click=lambda e, share_id=sh["id"]: self.page.run_task(
                                            self._revoke, storage_id, share_id, shares_list
                                        ),
                                    ),
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            muted(f"Share QR: {sh.get('share_qr_payload')}"),
                        ],
                        spacing=6,
                    ),
                )
            )
        shares_list.controls = controls
        self.page.update()

    async def _update_share_permission(
        self, storage_id: int, phone: str, permission: str, shares_list: ft.ListView
    ):
        if not phone or not permission:
            return
        try:
            await asyncio.to_thread(self.api.share_storage, storage_id, phone, permission)
            self.toast(f"Permission set to {permission}")
            await self._load_shares(storage_id, shares_list)
        except ApiError as e:
            self.toast(e.message, error=True)
            await self._load_shares(storage_id, shares_list)

    async def _share(self, storage_id: int, phone: str, permission: str, shares_list: ft.ListView):
        try:
            data = await asyncio.to_thread(self.api.share_storage, storage_id, phone, permission)
            self.toast(f"Request sent · waiting for accept · {data.get('share_qr_payload')}")
            await self._load_shares(storage_id, shares_list)
        except ApiError as e:
            self.toast(e.message, error=True)

    async def _revoke(self, storage_id: int, share_id: int, shares_list: ft.ListView):
        try:
            await asyncio.to_thread(self.api.revoke_share, storage_id, share_id)
            self.toast("Share revoked")
            await self._load_shares(storage_id, shares_list)
        except ApiError as e:
            self.toast(e.message, error=True)


def main(page: ft.Page):
    QRVaultApp(page)


if __name__ == "__main__":
    ft.run(main)
