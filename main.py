"""
QR Vault — Flet mobile client
Professional phone-first UI talking to the Django QR Vault APIs.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import webbrowser
from pathlib import Path

import flet as ft
import flet_video as ftv

from app.api import ApiError, VaultAPI
from app.i18n import LANG_AR, LANG_EN, normalize_lang, t
from app.menu_data import (
    ALLERGENS,
    COLOR_PRESETS,
    CURRENCY_OPTIONS,
    SOCIAL_NETWORKS,
    clone_menu,
    empty_product,
    empty_section,
    format_hours_range,
    format_menu_price,
    mailto_href,
    maps_href,
    normalize_ampm_time,
    normalize_menu_data,
    social_href,
    tel_href,
    whatsapp_href,
)
from app.note_html import NOTE_COLORS, NOTE_SIZES, note_plain_preview, note_to_text_control, wrap_selection
from app.offline import OfflineStore, is_network_error
from app.paths import downloads_dir
from app.pdf_viewer_html import can_serve_pdf, prepare_pdf_viewer_dir, start_pdf_viewer_server
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

try:
    import flet_webview as fwv
except ImportError:  # pragma: no cover - optional until requirements install
    fwv = None

try:
    import flet_geolocator as fgeo
except ImportError:  # pragma: no cover
    fgeo = None


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


def render_pdf_pages(pdf_path: Path, max_pages: int = 40, *, scale: float = 1.8) -> list[Path]:
    """Render PDF pages to PNGs when pypdfium2 is available (desktop).

    APK builds cannot ship pypdfium2 — it has no Android wheel on pypi.flet.dev
    and Flet installs mobile deps with --only-binary. Callers must handle ImportError.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ImportError(
            "PDF preview needs pypdfium2 (desktop only). "
            "Install: pip install -r requirements-desktop.txt"
        ) from exc

    scale_tag = f"s{int(round(scale * 100))}"
    out_dir = PREVIEW_DIR / f"pdf_{pdf_path.stem}_{pdf_path.stat().st_size}_{scale_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    paths: list[Path] = []
    try:
        n = min(len(doc), max_pages)
        for i in range(n):
            out = out_dir / f"page_{i + 1:03d}.png"
            if not out.exists():
                page = doc[i]
                bitmap = page.render(scale=scale)
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
        self._pdf_server = None  # local HTTP server for official PDF.js viewer
        self._pending_register_avatar: Path | None = None
        self._auth_error_text: ft.Text | None = None
        self._auth_error_banner_box: ft.Container | None = None
        self._back_handler = None  # callable for system / gesture back
        self._drawer_open = False
        self._menu_image_cache: dict[int, str] = {}
        self._menu_draft: dict | None = None
        self._menu_category_id: str | None = None
        self._geolocator = None

        # FilePicker is a Service in Flet >=0.80 — do not add to page.overlay
        self.file_picker = ft.FilePicker()

        self._configure_page()
        self.root = ft.Container(expand=True)
        self.page.add(self.root)
        self._apply_locale()
        self.go_boot()

    def _(self, key: str, **kwargs) -> str:
        return t(self.session.lang, key, **kwargs)

    def _apply_locale(self):
        self.session.lang = normalize_lang(self.session.lang)
        self.page.rtl = self.session.lang == LANG_AR
        try:
            self.page.update()
        except Exception:
            pass

    def _toggle_language(self, _=None):
        self.session.lang = LANG_AR if normalize_lang(self.session.lang) != LANG_AR else LANG_EN
        self.session.save()
        self._apply_locale()
        if self.session.is_authenticated:
            self.go_home()
        else:
            self.go_login()

    def _lang_button(self) -> ft.Control:
        label = self._("lang_switch_to_en") if normalize_lang(self.session.lang) == LANG_AR else self._("lang_switch_to_ar")
        return ghost_button(label, self._toggle_language, ft.Icons.TRANSLATE)

    def _auth_error_message(self, exc: BaseException) -> str:
        """Map auth/network failures to clear localized user messages."""
        if isinstance(exc, ApiError):
            code = (exc.code or "").upper()
            code_map = {
                "INVALID_PHONE": "enter_phone",
                "PHONE_EXISTS": "phone_exists",
                "DISPLAY_NAME_REQUIRED": "enter_display_name",
                "PASSWORD_TOO_SHORT": "password_too_short",
                "PASSWORD_MISMATCH": "passwords_mismatch",
                "PASSWORD_REQUIRED": "enter_password",
                "INVALID_CREDENTIALS": "invalid_credentials",
                "ACCOUNT_DISABLED": "account_disabled",
                "INVALID_OTP": "invalid_otp",
            }
            if code in code_map:
                return self._(code_map[code])
            raw = (exc.message or "").lower()
            if "already exists" in raw or "phone_exists" in raw:
                return self._("phone_exists")
            if "invalid_credentials" in raw or "wrong phone" in raw or "invalid phone or password" in raw:
                return self._("invalid_credentials")
            if "password" in raw and ("match" in raw or "mismatch" in raw):
                return self._("passwords_mismatch")
            if "at least 4" in raw or "too short" in raw or "password_too_short" in raw:
                return self._("password_too_short")
            if "display" in raw and "name" in raw:
                return self._("enter_display_name")
            if "disabled" in raw:
                return self._("account_disabled")
            if "invalid phone" in raw:
                return self._("enter_phone")
            if exc.status_code and exc.status_code >= 500:
                return self._("auth_server_error")
            if exc.message:
                return exc.message
            return self._("auth_generic_error")

        if is_network_error(exc):
            msg = str(exc).lower()
            if "timeout" in msg or "timed out" in msg:
                return self._("auth_timeout")
            return self._("auth_network_error")
        return self._("auth_generic_error")

    def _toast_auth_error(self, exc: BaseException):
        # Auth screens: show only the top inline banner (no bottom SnackBar).
        self.toast(self._auth_error_message(exc), error=True, snack=False)

    def _auth_error_banner(self) -> ft.Control:
        """Persistent inline error line on auth forms (SnackBar alone is easy to miss)."""
        text = ft.Text("", color="#FECDD3", size=13, weight=ft.FontWeight.W_600)
        self._auth_error_text = text
        banner = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.ERROR_OUTLINE, color=C.danger, size=18),
                    text,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            visible=False,
            bgcolor="#4C0519",
            border=ft.Border.all(1, C.danger),
            border_radius=12,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        )
        self._auth_error_banner_box = banner
        return banner

    def _set_auth_form_error(self, message: str | None):
        banner = getattr(self, "_auth_error_banner_box", None)
        text = self._auth_error_text
        msg = (message or "").strip()
        if text is not None:
            text.value = msg
        if isinstance(banner, ft.Container):
            banner.visible = bool(msg)
            try:
                banner.update()
                return
            except Exception:
                pass
        try:
            self.page.update()
        except Exception:
            pass

    def _show_info_dialog(self, title: str, paragraphs: list[str]):
        """Mobile-friendly info panel (replaces unreliable tooltips)."""

        def close(_e=None):
            self.page.pop_dialog()

        body = [p for p in paragraphs if p]
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text(title or self._("info"), color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Container(
                width=340,
                content=ft.Column(
                    [muted(p) for p in body],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                height=min(420, max(140, 48 + 42 * len(body))),
            ),
            actions=[
                ft.Button(content=self._("got_it"), on_click=close, bgcolor=C.primary, color=C.bg),
            ],
        )
        self.page.show_dialog(dialog)

    def _info_button(self, tip_key: str, title_key: str = "info") -> ft.Control:
        """Info icon that looks like the old tooltip control, but opens a Help-style dialog."""
        tip = self._(tip_key)
        title = self._(title_key)
        return ft.IconButton(
            icon=ft.Icons.INFO_OUTLINE,
            icon_color=C.text_muted,
            icon_size=18,
            on_click=lambda e, t=title, b=tip: self._show_info_dialog(t, [b]),
        )

    async def _open_local_file(self, path: Path, name: str = "", mime: str = ""):
        """Open a local file with an external app (needed for PDF on Android APK)."""
        try:
            from flet.controls.services.share import Share, ShareFile

            await Share().share_files(
                [ShareFile.from_path(str(path), name=name or path.name)],
                title=name or path.name,
                text=name or path.name,
            )
            return
        except Exception:
            pass
        try:
            from flet.controls.services.url_launcher import LaunchMode, UrlLauncher

            await UrlLauncher().launch_url(
                path.resolve().as_uri(),
                mode=LaunchMode.EXTERNAL_APPLICATION,
            )
        except Exception as exc:
            self.toast(self._("pdf_open_failed"), error=True)
            self.toast(str(exc), error=True)

    def _pdf_fallback_panel(self, path: Path, name: str) -> ft.Control:
        return ft.Column(
            [
                ft.Icon(ft.Icons.PICTURE_AS_PDF_OUTLINED, size=48, color=C.primary),
                muted(self._("pdf_mobile_hint")),
                primary_button(
                    self._("open_with_app"),
                    lambda e, p=path, n=name: self.page.run_task(
                        self._open_local_file, p, n, "application/pdf"
                    ),
                    ft.Icons.OPEN_IN_NEW,
                    expand=False,
                ),
                ghost_button(
                    self._("download"),
                    lambda e, p=path, n=name: self._save_cached_copy(p, n),
                    ft.Icons.DOWNLOAD,
                ),
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        )

    def _supports_pdf_webview(self) -> bool:
        """In-app WebView: Android / iOS / macOS / web. Not Windows/Linux."""
        if fwv is None:
            return False
        try:
            p = self.page.platform
            return p in (
                ft.PagePlatform.ANDROID,
                ft.PagePlatform.ANDROID_TV,
                ft.PagePlatform.IOS,
                ft.PagePlatform.MACOS,
            ) or bool(self.page.web)
        except Exception:
            return False

    def _stop_pdf_server(self):
        srv = self._pdf_server
        self._pdf_server = None
        if srv is None:
            return
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

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
        # Android system back / iOS pop: close dialogs first, then screen stack.
        try:
            root = self.page.views[0]
            root.can_pop = False
            root.on_confirm_pop = self._on_confirm_pop
        except Exception:
            pass
        self.page.on_keyboard_event = self._on_keyboard_event

    def _set_back(self, handler=None):
        """Register what system/gesture back should do on this screen (None = app root)."""
        self._back_handler = handler

    def _request_back(self, _e=None):
        self.page.run_task(self._navigate_back)

    def _on_keyboard_event(self, e: ft.KeyboardEvent):
        if (e.key or "").lower() in ("escape", "esc"):
            self.page.run_task(self._navigate_back)

    async def _on_confirm_pop(self, e):
        handled = await self._navigate_back()
        try:
            # If nothing to go back to, allow leaving the app.
            await e.control.confirm_pop(not handled)
        except Exception:
            pass

    def _top_open_dialog(self):
        try:
            dialogs = getattr(self.page, "_dialogs", None)
            controls = getattr(dialogs, "controls", None) or []
            for dlg in reversed(controls):
                if getattr(dlg, "open", False):
                    return dlg
        except Exception:
            pass
        return None

    async def _navigate_back(self) -> bool:
        """Dismiss overlay or go to previous screen. True if something was handled."""
        dialog = self._top_open_dialog()
        if dialog is not None:
            # Prefer closing real dialogs; snackbars count too (top of stack).
            try:
                self.page.pop_dialog()
            except Exception:
                pass
            return True

        if self._drawer_open:
            self._drawer_open = False
            try:
                await self.page.close_drawer()
            except Exception:
                pass
            return True

        handler = self._back_handler
        if handler is None:
            return False
        try:
            result = handler()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            self.toast(str(exc), error=True)
        return True

    def toast(self, message: str, error: bool = False, *, snack: bool = True):
        msg = (message or "").strip() or self._("auth_generic_error")
        if error:
            self._set_auth_form_error(msg)
        if not snack:
            return
        # Flet 0.86+: SnackBar is a dialog — overlay.open no longer shows reliably.
        bar = ft.SnackBar(
            content=ft.Text(msg, color=C.text, size=13),
            bgcolor=C.danger if error else C.surface_alt,
            show_close_icon=True,
            behavior=ft.SnackBarBehavior.FLOATING,
            duration=ft.Duration(milliseconds=5000),
        )
        try:
            self.page.show_dialog(bar)
        except Exception:
            try:
                self.page.update()
            except Exception:
                pass

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
        self._set_back(None)
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
        self._set_back(None)
        phone = ft.TextField(
            label=self._("phone_number"),
            hint_text="+9715...",
            prefix_icon=ft.Icons.PHONE_IPHONE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            cursor_color=C.primary,
            value=(self.session.user or {}).get("phone") or "",
        )
        password = ft.TextField(
            label=self._("password"),
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.LOCK_OUTLINE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        base_url = ft.TextField(
            label=self._("api_base_url"),
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

        def save_base():
            url = (base_url.value or self.session.base_url).rstrip("/")
            if not url:
                self.toast(self._("enter_api_url"), error=True, snack=False)
                return False
            # 127.0.0.1 is fine on desktop; phone_apk_hint already explains LAN IP for APK.
            self.session.base_url = url
            self.session.save()
            return True

        def sign_in(_):
            self._set_auth_form_error(None)
            if not phone.value or len(phone.value.strip()) < 8:
                self.toast(self._("enter_phone"), error=True, snack=False)
                return
            if not (password.value or "").strip():
                self.toast(self._("enter_password"), error=True, snack=False)
                return
            if len((password.value or "").strip()) < 4:
                self.toast(self._("password_too_short"), error=True, snack=False)
                return
            if not save_base():
                return
            self.page.run_task(self._login, phone.value.strip(), password.value)

        error_banner = self._auth_error_banner()
        self.set_view(
            ft.Column(
                [
                    ft.Row([ft.Container(expand=True), self._lang_button()]),
                    ft.Container(height=8),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.LOCK_PERSON_OUTLINED, size=44, color=C.primary),
                            ft.Column(
                                [
                                    section_title(self._("welcome_back")),
                                    muted(self._("sign_in_hint")),
                                ],
                                spacing=4,
                                expand=True,
                            ),
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    muted(self._("phone_apk_hint")),
                    error_banner,
                    ft.Container(height=4),
                    card(
                        ft.Column(
                            [
                                phone,
                                password,
                                base_url,
                                primary_button(self._("sign_in"), sign_in, ft.Icons.LOGIN),
                                ghost_button(
                                    self._("no_account"),
                                    lambda e: self.go_register(),
                                    ft.Icons.PERSON_ADD_ALT_1,
                                ),
                            ],
                            spacing=14,
                        )
                    ),
                ],
                spacing=14,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )
        )

    def go_register(self):
        self._set_back(self.go_login)
        self._pending_register_avatar = None
        phone = ft.TextField(
            label=self._("phone_number"),
            hint_text="+9715...",
            prefix_icon=ft.Icons.PHONE_IPHONE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        display_name = ft.TextField(
            label=self._("display_name"),
            prefix_icon=ft.Icons.BADGE_OUTLINED,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        password = ft.TextField(
            label=self._("password"),
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.LOCK_OUTLINE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        password2 = ft.TextField(
            label=self._("password_confirm"),
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.LOCK_OUTLINE,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        base_url = ft.TextField(
            label=self._("api_base_url"),
            value=self.session.base_url,
            hint_text="http://192.168.x.x:8000",
            prefix_icon=ft.Icons.CLOUD_OUTLINED,
            border_radius=14,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            text_size=13,
        )

        avatar_box = ft.Container(
            content=ft.Icon(ft.Icons.ADD_A_PHOTO_OUTLINED, size=30, color=C.primary),
            width=72,
            height=72,
            bgcolor=C.surface_alt,
            border=ft.Border.all(2, C.primary),
            border_radius=36,
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ink=True,
            tooltip=self._("add_photo"),
        )

        async def pick_register_photo(_e=None):
            files = await self.file_picker.pick_files(
                allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["jpg", "jpeg", "png", "webp", "gif"],
            )
            if not files:
                return
            path = Path(files[0].path)
            self._pending_register_avatar = path
            avatar_box.content = ft.Image(
                src=str(path),
                width=72,
                height=72,
                fit=ft.BoxFit.COVER,
                border_radius=36,
            )
            try:
                avatar_box.update()
            except Exception:
                self.page.update()
            self.toast(self._("photo_selected"))

        avatar_box.on_click = lambda e: self.page.run_task(pick_register_photo)

        def create(_):
            self._set_auth_form_error(None)
            if not phone.value or len(phone.value.strip()) < 8:
                self.toast(self._("enter_phone"), error=True, snack=False)
                return
            name = (display_name.value or "").strip()
            if len(name) < 2:
                self.toast(self._("enter_display_name"), error=True, snack=False)
                return
            if not (password.value or "").strip():
                self.toast(self._("enter_password"), error=True, snack=False)
                return
            if len((password.value or "").strip()) < 4:
                self.toast(self._("password_too_short"), error=True, snack=False)
                return
            if (password.value or "") != (password2.value or ""):
                self.toast(self._("passwords_mismatch"), error=True, snack=False)
                return
            url = (base_url.value or self.session.base_url).rstrip("/")
            if not url:
                self.toast(self._("enter_api_url"), error=True, snack=False)
                return
            self.session.base_url = url
            self.session.save()
            self.page.run_task(
                self._register,
                phone.value.strip(),
                password.value,
                password2.value,
                name,
            )

        error_banner = self._auth_error_banner()
        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(
                                ft.Icons.ARROW_BACK,
                                icon_color=C.text,
                                on_click=self._request_back,
                            ),
                            ft.Container(expand=True),
                            self._lang_button(),
                        ]
                    ),
                    ft.Row(
                        [
                            avatar_box,
                            ft.Column(
                                [
                                    section_title(self._("create_account_title")),
                                    muted(self._("create_account_hint")),
                                    muted(self._("tap_to_add_photo"), size=12),
                                ],
                                spacing=4,
                                expand=True,
                            ),
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    error_banner,
                    card(
                        ft.Column(
                            [
                                phone,
                                display_name,
                                password,
                                password2,
                                base_url,
                                primary_button(self._("sign_up"), create, ft.Icons.PERSON_ADD),
                                ghost_button(
                                    self._("have_account"),
                                    lambda e: self.go_login(),
                                    ft.Icons.LOGIN,
                                ),
                            ],
                            spacing=14,
                        )
                    ),
                ],
                spacing=14,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )
        )

    async def _login(self, phone: str, password: str):
        try:
            await asyncio.to_thread(self.api.login, phone, password)
            self._set_auth_form_error(None)
            self.toast(self._("signed_in_ok"))
            self.go_home()
        except Exception as e:
            self._toast_auth_error(e)

    async def _register(self, phone: str, password: str, password_confirm: str, display_name: str):
        try:
            await asyncio.to_thread(
                self.api.register, phone, password, password_confirm, display_name
            )
            avatar_path = self._pending_register_avatar
            self._pending_register_avatar = None
            if avatar_path and avatar_path.is_file():
                try:
                    await asyncio.to_thread(self.api.upload_avatar, avatar_path)
                except Exception:
                    # Account exists; photo can still be set later from the profile drawer.
                    pass
            self._set_auth_form_error(None)
            self.toast(self._("account_created"))
            self.go_home()
        except ApiError as e:
            msg = self._auth_error_message(e)
            if (e.code or "") == "PHONE_EXISTS" or "PHONE_EXISTS" in (e.message or ""):
                self.go_login()
                self.toast(msg, error=True, snack=False)
            else:
                self.toast(msg, error=True, snack=False)
        except Exception as e:
            self._toast_auth_error(e)

    def _user_display_name(self) -> str:
        user = self.session.user or {}
        return (
            (user.get("display_name") or user.get("first_name") or "").strip()
            or user.get("phone")
            or "User"
        )

    def _avatar_image(self, *, size: float = 48, radius: float | None = None) -> ft.Control:
        r = size / 2 if radius is None else radius
        url = (self.session.user or {}).get("avatar_url")
        if url:
            inner: ft.Control = ft.Image(
                src=str(url),
                width=size,
                height=size,
                fit=ft.BoxFit.COVER,
                border_radius=r,
            )
        else:
            inner = ft.Icon(ft.Icons.PERSON, size=size * 0.55, color=C.primary)
        return ft.Container(
            content=inner,
            width=size,
            height=size,
            bgcolor=C.surface_alt,
            border=ft.Border.all(2, C.primary),
            border_radius=r,
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    def _drawer_profile_avatar(self) -> ft.Control:
        """Avatar with camera badge — tap opens viewer + edit actions."""
        size = 96.0
        badge = ft.Container(
            content=ft.Icon(ft.Icons.CAMERA_ALT, size=14, color="#FFFFFF"),
            width=30,
            height=30,
            bgcolor=C.primary,
            border_radius=15,
            alignment=ft.Alignment.CENTER,
            border=ft.Border.all(2, C.surface),
        )
        return ft.Container(
            content=ft.Stack(
                [
                    self._avatar_image(size=size),
                    ft.Container(content=badge, right=0, bottom=0),
                ],
                width=size,
                height=size,
            ),
            ink=True,
            border_radius=size / 2,
            tooltip=self._("profile_photo"),
            on_click=lambda e: self.page.run_task(self._open_avatar_viewer),
        )

    def _build_profile_drawer(self) -> ft.NavigationDrawer:
        user = self.session.user or {}
        phone = user.get("phone") or ""

        def tile(icon, label, on_click):
            return ft.Container(
                content=ft.ListTile(
                    leading=ft.Icon(icon, color=C.primary),
                    title=ft.Text(label, color=C.text, size=14),
                    on_click=on_click,
                ),
                border=ft.Border(bottom=ft.BorderSide(1, C.border)),
            )

        async def close_drawer():
            try:
                await self.page.close_drawer()
            except Exception:
                pass

        def after_close(fn):
            async def _run(_e=None):
                await close_drawer()
                fn()

            return lambda e: self.page.run_task(_run, e)

        return ft.NavigationDrawer(
            bgcolor=C.surface,
            indicator_color=C.primary_dim,
            on_dismiss=lambda e: setattr(self, "_drawer_open", False),
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            self._drawer_profile_avatar(),
                            ft.Text(
                                self._user_display_name(),
                                size=18,
                                weight=ft.FontWeight.W_700,
                                color=C.text,
                                text_align=ft.TextAlign.CENTER,
                            ),
                            muted(phone),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    padding=ft.Padding.only(top=28, bottom=16, left=16, right=16),
                ),
                tile(
                    ft.Icons.TRANSLATE,
                    self._("language"),
                    after_close(lambda: self._toggle_language()),
                ),
                tile(
                    ft.Icons.HELP_OUTLINE,
                    self._("help"),
                    after_close(lambda: self._show_help()),
                ),
                tile(
                    ft.Icons.LOGOUT,
                    self._("sign_out"),
                    after_close(lambda: self._logout(None)),
                ),
            ],
        )

    def _ensure_profile_drawer(self):
        self.page.drawer = self._build_profile_drawer()

    async def _open_profile_drawer(self, _e=None):
        self._ensure_profile_drawer()
        self.page.update()
        try:
            self._drawer_open = True
            await self.page.show_drawer()
        except Exception as exc:
            self._drawer_open = False
            self.toast(str(exc), error=True)

    async def _dismiss_overlays(self):
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        try:
            self._drawer_open = False
            await self.page.close_drawer()
        except Exception:
            pass

    async def _open_avatar_viewer(self, _e=None):
        try:
            self._drawer_open = False
            await self.page.close_drawer()
        except Exception:
            pass

        url = (self.session.user or {}).get("avatar_url")
        has_photo = bool(url)
        preview_size = 220.0

        if has_photo:
            preview: ft.Control = ft.Container(
                content=ft.Image(
                    src=str(url),
                    width=preview_size,
                    height=preview_size,
                    fit=ft.BoxFit.COVER,
                    border_radius=preview_size / 2,
                ),
                width=preview_size,
                height=preview_size,
                border_radius=preview_size / 2,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border=ft.Border.all(2, C.border),
                alignment=ft.Alignment.CENTER,
            )
        else:
            preview = ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.PERSON, size=72, color=C.primary),
                        muted(self._("no_profile_photo")),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10,
                ),
                width=preview_size,
                height=preview_size,
                bgcolor=C.surface_alt,
                border_radius=preview_size / 2,
                border=ft.Border.all(2, C.border),
                alignment=ft.Alignment.CENTER,
            )

        photo_actions = [
            ft.IconButton(
                icon=ft.Icons.PHOTO_CAMERA_OUTLINED,
                icon_color=C.bg,
                bgcolor=C.primary,
                tooltip=self._("change_photo") if has_photo else self._("add_photo"),
                on_click=lambda e: self.page.run_task(self._change_avatar),
            ),
        ]
        if has_photo:
            photo_actions.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_color=C.text,
                    bgcolor=C.surface_alt,
                    tooltip=self._("remove_photo"),
                    on_click=lambda e: self.page.run_task(self._remove_avatar),
                )
            )

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text(
                self._("profile_photo"),
                color=C.text,
                weight=ft.FontWeight.W_700,
            ),
            content=ft.Container(
                width=260,
                content=ft.Column(
                    [
                        preview,
                        ft.Row(
                            photo_actions,
                            spacing=8,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=12,
                    tight=True,
                ),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.only(top=4, bottom=4),
            ),
            actions=[],
        )
        self.page.show_dialog(dialog)

    async def _change_avatar(self):
        await self._dismiss_overlays()
        files = await self.file_picker.pick_files(
            allow_multiple=False,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["jpg", "jpeg", "png", "webp", "gif"],
        )
        if not files:
            return
        path = Path(files[0].path)
        try:
            await asyncio.to_thread(self.api.upload_avatar, path)
            self.toast(self._("photo_updated"))
            self.go_home()
        except ApiError as e:
            self.toast(e.message, error=True)
        except Exception as e:
            self.toast(str(e), error=True)

    async def _remove_avatar(self):
        await self._dismiss_overlays()
        try:
            await asyncio.to_thread(self.api.delete_avatar)
            self.toast(self._("photo_removed"))
            self.go_home()
        except ApiError as e:
            self.toast(e.message, error=True)
        except Exception as e:
            self.toast(str(e), error=True)

    # ── Home ────────────────────────────────────────────────────
    def go_home(self):
        self._set_back(None)
        self._ensure_profile_drawer()
        list_view = ft.ReorderableListView(
            expand=True,
            spacing=0,
            padding=0,
            # Mobile: long-press item to drag. Desktop: small overlay handle (no layout width).
            show_default_drag_handles=True,
            on_reorder=self._on_home_reorder,
        )
        self._home_list = list_view
        filter_row = ft.Row(spacing=8, visible=False)
        self._home_filter_row = filter_row

        avatar_btn = ft.Container(
            content=self._avatar_image(size=48),
            on_click=lambda e: self.page.run_task(self._open_profile_drawer),
            ink=True,
            border_radius=24,
            tooltip=self._("profile"),
        )
        header = ft.Row(
            [
                avatar_btn,
                ft.Container(expand=True),
                ghost_button(self._("help"), lambda e: self._show_help(), ft.Icons.HELP_OUTLINE),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        actions = ft.Row(
            [
                primary_button(self._("scan_qr"), lambda e: self.go_scan(), ft.Icons.QR_CODE_SCANNER, expand=True),
                ghost_button(self._("refresh"), lambda e: self.page.run_task(self._refresh_home, list_view)),
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
                    muted(self._("your_vaults")),
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
        try:
            self.page.drawer = None
        except Exception:
            pass
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
            chip_btn(self._("filter_all"), "all"),
            chip_btn(self._("owned_filter"), "owned"),
            chip_btn(self._("shared_filter"), "shared"),
        ]
        try:
            row.update()
        except Exception:
            pass

    def _wrap_list_item(self, content: ft.Control, *, reorder: bool = False) -> ft.Control:
        """Full-width list row with bottom gap. Reorder via long-press (see ReorderableListView)."""
        _ = reorder  # kept for call-site compatibility
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
        badges = [chip(self._("shared") if is_shared else self._("owned"), color)]
        if is_public:
            badges.insert(0, chip(self._("public"), C.accent))
        if (s.get("kind") or "vault") == "menu":
            badges.insert(0, chip(self._("menu_badge"), C.primary))
        leading_icon = ft.Icons.RESTAURANT_MENU if (s.get("kind") or "vault") == "menu" else ft.Icons.QR_CODE_2
        return ft.Container(
            content=ft.ListTile(
                leading=ft.Container(
                    content=ft.Icon(leading_icon, color=C.bg),
                    bgcolor=color,
                    padding=10,
                    border_radius=12,
                ),
                title=ft.Text(s.get("title") or self._("storage_fallback", qr=s.get("qr_code")), color=C.text, weight=ft.FontWeight.W_600),
                subtitle=ft.Text(
                    f"QR: {s.get('qr_code')}  ·  {self._('files_count', n=s.get('file_count', 0))}  ·  {self._perm_text(s.get('my_permission'))}"
                    if (s.get("kind") or "vault") != "menu"
                    else f"QR: {s.get('qr_code')}  ·  {self._('menu_badge')}  ·  {self._perm_text(s.get('my_permission'))}",
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
        # Long-press to reorder on mobile; disable when filter prevents reordering.
        list_view.show_default_drag_handles = can_reorder

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
                            ft.Text(
                                self._("offline_home_banner"),
                                size=13,
                                color=C.text_muted,
                                expand=True,
                                soft_wrap=True,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
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
        def leave_scan(_e=None):
            self.page.run_task(self._stop_scan_camera)
            self.go_home()

        self._set_back(leave_scan)
        self.page.run_task(self._stop_scan_camera)
        self._scan_busy = False
        self._scan_decode_pending = False
        self._scan_last_decode = 0.0

        qr = ft.TextField(
            label=self._("qr_value"),
            hint_text=self._("qr_hint"),
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
            self._("scan_status_ready")
            if self._camera_platform_ok()
            else self._("scan_status_desktop")
        )
        self._scan_status = status

        camera_host = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.QR_CODE_SCANNER, size=64, color=C.primary),
                    muted(self._("preparing_camera")),
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
                self.toast(self._("enter_qr"), error=True)
                return
            self.page.run_task(self._scan, qr.value.strip())

        def capture(_):
            self.page.run_task(self._capture_scan_frame)

        actions = [
            primary_button(self._("open_storage"), submit, ft.Icons.LOCK_OPEN_OUTLINED),
        ]
        if self._camera_platform_ok():
            actions.insert(
                0,
                ghost_button(self._("capture_frame"), capture, ft.Icons.CAMERA_ALT),
            )

        self.set_view(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=self._request_back),
                            section_title(self._("scan_title")),
                            ft.Container(expand=True),
                            self._info_button("scan_help_tooltip", "scan_title"),
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
                    muted(self._("paste_qr")),
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
            self._set_scan_status(self._("qr_detected"))
            self.toast(self._("scanned", value=payload[:48]))
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
            # Some transports wrap connection errors; still try offline resolve.
            offline_hit = self.offline.find_storage_by_qr(qr_code)
            if offline_hit and offline_hit.get("id"):
                self.toast(self._("offline_scan_open"))
                await self._open_storage(int(offline_hit["id"]))
                return
            self.toast(e.message, error=True)
        except Exception as e:
            if is_network_error(e):
                offline_hit = self.offline.find_storage_by_qr(qr_code)
                if offline_hit and offline_hit.get("id"):
                    self.toast(self._("offline_scan_open"))
                    await self._open_storage(int(offline_hit["id"]))
                    return
                self.toast(self._("offline_scan_miss"), error=True)
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
            self._menu_category_id = None
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
                self._menu_category_id = None
                self.toast(self._("offline_cached_vault"))
                self.go_storage()
                return
            self.toast(e.message if isinstance(e, ApiError) else str(e), error=True)

    # ── Storage detail ──────────────────────────────────────────
    def _perm(self) -> str:
        return (self.current_storage or {}).get("my_permission") or "read"

    def _perm_text(self, perm: str | None = None) -> str:
        p = (perm or self._perm() or "read").lower()
        key = {
            "owner": "perm_owner",
            "manage": "perm_manage",
            "write": "perm_write",
            "read": "perm_read",
        }.get(p, "perm_read")
        return self._(key)

    def _perm_badge(self, perm: str | None = None) -> str:
        p = (perm or self._perm() or "read").lower()
        key = {
            "owner": "badge_owner",
            "manage": "badge_manage",
            "write": "badge_write",
            "read": "badge_read",
        }.get(p)
        return self._(key) if key else p.upper()

    def _can_write(self) -> bool:
        return self._perm() in ("owner", "manage", "write")

    def _can_manage(self) -> bool:
        return self._perm() in ("owner", "manage")

    def _is_owner(self) -> bool:
        return self._perm() == "owner"

    def go_storage(self):
        self._set_back(self.go_home)
        s = self.current_storage or {}
        if self._is_menu_storage(s):
            self.go_menu()
            return
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
            hint_text=self._("search_files"),
            value=self.file_search,
            prefix_icon=ft.Icons.SEARCH,
            border_radius=10,
            bgcolor=C.surface,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            cursor_color=C.primary,
            text_size=13,
            dense=True,
            filled=True,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            expand=True,
            on_change=on_search_change,
        )

        can_write = self._can_write()
        can_manage = self._can_manage()

        top_actions = []
        if can_write:
            top_actions.append(
                primary_button(
                    self._("upload"),
                    lambda e: self.page.run_task(self._pick_and_upload),
                    ft.Icons.UPLOAD_FILE,
                    expand=False,
                )
            )
            top_actions.append(
                ghost_button(
                    self._("add_note"),
                    lambda e: self._show_note_editor(sid, note_id=None, note_html="", title=""),
                    ft.Icons.NOTE_ADD_OUTLINED,
                )
            )
            top_actions.append(
                ghost_button(self._("merge_pdf"), lambda e: self.go_merge(), ft.Icons.PICTURE_AS_PDF_OUTLINED)
            )
        if can_manage:
            top_actions.append(ghost_button(self._("share"), lambda e: self.go_share()))
        top_actions.append(
            ghost_button(
                self._("save_offline"),
                lambda e, st=sid: self.page.run_task(self._prefetch_offline_files, st),
                ft.Icons.DOWNLOAD_FOR_OFFLINE_OUTLINED,
            )
        )
        top_actions.append(ghost_button(self._("archived"), lambda e: self.go_archive(), ft.Icons.ARCHIVE_OUTLINED))
        top_actions.append(ghost_button(self._("help"), lambda e: self._show_help(), ft.Icons.HELP_OUTLINE))
        top_actions.append(ghost_button(self._("sign_out"), self._logout, ft.Icons.LOGOUT))

        title_controls = [
            ft.Text(
                s.get("title") or self._("storage_fallback", qr=s.get("qr_code")),
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
                    tooltip=self._("rename_storage"),
                    icon_size=20,
                    on_click=lambda e: self._show_rename_dialog(
                        sid, s.get("title") or self._("storage_fallback", qr=s.get("qr_code"))
                    ),
                )
            )

        perm_label = self._perm_text()
        is_public = bool(s.get("is_public"))
        pending_n = self.offline.pending_count(sid) if sid else 0

        status_chips = []
        if is_public:
            status_chips.append(chip(self._("public"), C.accent))
        if self._offline_mode:
            status_chips.append(chip(self._("offline"), C.warning))
        elif pending_n:
            status_chips.append(chip(self._("sync_badge", n=pending_n), C.warning))
        if not can_write:
            status_chips.append(chip(self._("read_only"), C.warning))
        else:
            status_chips.append(chip(self._perm_badge(), C.owned))

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
                                    self._("offline_mode")
                                    if self._offline_mode
                                    else self._("sync_pending", n=pending_n),
                                    color=C.text,
                                    weight=ft.FontWeight.W_700,
                                    size=13,
                                ),
                                muted(
                                    self._("offline_mode_body")
                                    if self._offline_mode
                                    else self._("sync_queue_body")
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ghost_button(
                            self._("sync"),
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
        menu_card = ft.Container()
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
                        ft.Text(
                            self._("public_vault"),
                            weight=ft.FontWeight.W_700,
                            color=C.text,
                            size=13,
                            expand=True,
                        ),
                        self._info_button("public_vault_tip", "public_vault"),
                        public_switch,
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            )
            menu_switch = ft.Switch(
                value=False,
                active_color=C.primary,
                on_change=lambda e, st=sid: self.page.run_task(
                    self._toggle_menu_kind, st, bool(e.control.value)
                ),
            )
            menu_card = card(
                ft.Row(
                    [
                        ft.Text(
                            self._("digital_menu"),
                            weight=ft.FontWeight.W_700,
                            color=C.text,
                            size=13,
                            expand=True,
                        ),
                        self._info_button("digital_menu_tip", "digital_menu"),
                        menu_switch,
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            )

        body_controls = [
            ft.Row(
                [
                    ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=self._request_back),
                    ft.Column(
                        [
                            ft.Row(title_controls, spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            muted(
                                self._(
                                    "storage_meta",
                                    qr=s.get("qr_code"),
                                    perm=perm_label,
                                    phone=s.get("owner_phone") or "",
                                )
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Row(status_chips, spacing=6, wrap=True) if status_chips else ft.Container(),
                ]
            ),
            sync_banner,
            public_card,
            menu_card,
            ft.Row(top_actions, spacing=10, scroll=ft.ScrollMode.AUTO) if top_actions else ft.Container(),
            ft.Row(
                [
                    filter_row,
                    view_toggle,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row([search_field], expand=False),
            ft.Column(
                [files_host],
                spacing=6,
                expand=True,
                tight=True,
            ),
        ]

        self.set_view(ft.Column(body_controls, spacing=10, expand=True, tight=True))
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

    async def _toggle_menu_kind(self, storage_id: int, enabled: bool):
        try:
            storage = await asyncio.to_thread(
                self.api.set_storage_kind,
                storage_id,
                "menu" if enabled else "vault",
            )
            self.current_storage = storage
            self.offline.upsert_home_storage(storage)
            self.toast(self._("menu_enabled") if enabled else self._("menu_disabled"))
            self.go_storage()
        except ApiError as e:
            self.toast(e.message, error=True)
            self.go_storage()
        except Exception as e:
            self.toast(str(e), error=True)
            self.go_storage()

    def _is_menu_storage(self, storage: dict | None = None) -> bool:
        s = storage if storage is not None else self.current_storage
        return ((s or {}).get("kind") or "vault") == "menu"

    def _menu_file_id(self, value) -> int | None:
        if value in (None, "", False, 0, "0"):
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    def _menu_file_name(self, file_id: int) -> str:
        sources = []
        if self.current_storage:
            sources.extend(self.current_storage.get("files") or [])
        sources.extend(self._storage_files_cache or [])
        for row in sources:
            if self._menu_file_id(row.get("id")) == file_id:
                return row.get("original_name") or f"menu_{file_id}.jpg"
        return f"menu_{file_id}.jpg"

    def _menu_photo_box(
        self,
        file_id,
        *,
        width: int | None = 88,
        height: int = 88,
        radius: int = 16,
        fallback_icon=ft.Icons.FASTFOOD_OUTLINED,
    ) -> tuple[ft.Container, int | None]:
        fid = self._menu_file_id(file_id)
        cached = self._menu_image_cache.get(fid) if fid else None
        if cached:
            img_kwargs: dict = {"src": cached, "height": height, "fit": ft.BoxFit.COVER}
            if width:
                img_kwargs["width"] = width
            content: ft.Control = ft.Image(**img_kwargs)
        else:
            content = ft.Icon(fallback_icon, size=min(34, max(18, height // 3)), color="#FFFFFF88")
        box = ft.Container(
            content=content,
            width=width,
            height=height,
            bgcolor="#0B1220",
            border_radius=radius,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            alignment=ft.Alignment.CENTER,
        )
        return box, fid

    async def _hydrate_menu_images(self, storage_id: int, jobs: list[tuple[int, ft.Container]]):
        for file_id, box in jobs:
            if file_id in self._menu_image_cache:
                src = self._menu_image_cache[file_id]
            else:
                try:
                    path = await self._cache_file(
                        storage_id, file_id, self._menu_file_name(file_id)
                    )
                    src = str(path)
                    self._menu_image_cache[file_id] = src
                except Exception:
                    continue
            w = box.width
            h = int(box.height or 88)
            img_kwargs: dict = {"src": src, "height": h, "fit": ft.BoxFit.COVER}
            if w:
                img_kwargs["width"] = int(w)
            box.content = ft.Image(**img_kwargs)
            try:
                box.update()
            except Exception:
                pass

    async def _pick_image_paths(self, *, multiple: bool = False) -> list[Path]:
        try:
            files = await self.file_picker.pick_files(
                allow_multiple=multiple,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["jpg", "jpeg", "png", "webp", "gif", "bmp"],
            )
        except Exception as e:
            self.toast(f"File picker error: {e}", error=True)
            return []
        if not files:
            return []
        paths: list[Path] = []
        for f in files:
            path = getattr(f, "path", None)
            if path:
                paths.append(Path(path))
                continue
            data = getattr(f, "bytes", None)
            if data is None:
                continue
            tmp = Path(tempfile.gettempdir()) / f"qr_vault_menu_{f.name}"
            tmp.write_bytes(data)
            paths.append(tmp)
        if not paths:
            self.toast("Could not read selected image", error=True)
        return paths

    async def _pick_image_path(self) -> Path | None:
        paths = await self._pick_image_paths(multiple=False)
        return paths[0] if paths else None

    async def _upload_menu_photos(self, *, multiple: bool = False) -> list[dict]:
        if not self.current_storage:
            return []
        if not self._can_write():
            self.toast(self._("read_only"), error=True)
            return []
        paths = await self._pick_image_paths(multiple=multiple)
        uploaded: list[dict] = []
        sid = self.current_storage["id"]
        for path in paths:
            try:
                row = await asyncio.to_thread(self.api.upload_file, sid, str(path))
            except ApiError as e:
                self.toast(e.message, error=True)
                continue
            except Exception as e:
                self.toast(str(e), error=True)
                continue
            if not isinstance(row, dict):
                continue
            fid = self._menu_file_id(row.get("id"))
            if fid:
                self._menu_image_cache[fid] = str(path)
                files = list((self.current_storage or {}).get("files") or [])
                files.append(row)
                self.current_storage["files"] = files
            uploaded.append(row)
        return uploaded

    async def _upload_menu_photo(self) -> dict | None:
        rows = await self._upload_menu_photos(multiple=False)
        return rows[0] if rows else None

    def _open_external(self, url: str):
        target = (url or "").strip()
        if not target:
            return
        try:
            webbrowser.open(target)
        except Exception as e:
            self.toast(str(e), error=True)

    def _action_chip(self, label: str, icon, url: str, color: str) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=16, color="#0B1220"),
                    ft.Text(label, size=12, weight=ft.FontWeight.W_700, color="#0B1220"),
                ],
                spacing=6,
                tight=True,
            ),
            bgcolor=color,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=999,
            ink=True,
            on_click=lambda e, u=url: self._open_external(u),
        )

    def _social_button(self, kind: str, value: str, color: str) -> ft.Control | None:
        href = social_href(kind, value)
        if not href:
            return None
        icons = {
            "instagram": ft.Icons.CAMERA_ALT,
            "facebook": ft.Icons.FACEBOOK,
            "tiktok": ft.Icons.MUSIC_NOTE,
            "twitter": ft.Icons.ALTERNATE_EMAIL,
            "snapchat": ft.Icons.CHAT_BUBBLE,
            "website": ft.Icons.LANGUAGE,
        }
        return ft.Container(
            content=ft.Icon(icons.get(kind, ft.Icons.LINK), size=20, color="#FFFFFF"),
            width=44,
            height=44,
            bgcolor=color,
            border_radius=22,
            alignment=ft.Alignment.CENTER,
            ink=True,
            tooltip=self._(f"social_{kind}"),
            on_click=lambda e, u=href: self._open_external(u),
        )

    def _editor_text_field(self, label: str, value: str, *, multiline: bool = False) -> ft.TextField:
        return ft.TextField(
            label=label,
            value=value or "",
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            multiline=multiline,
            min_lines=2 if multiline else 1,
            max_lines=3 if multiline else 1,
            expand=True,
            text_size=14,
        )

    def _ampm_time_dropdowns(self, value: dict | None, *, side: str) -> tuple[ft.Dropdown, ft.Dropdown, ft.Dropdown]:
        t = normalize_ampm_time(value, side=side)
        hour = str(t.get("h") or (10 if side == "from" else 9))
        minute = f"{int(t.get('m') or 0):02d}"
        ampm = "PM" if str(t.get("ampm") or ("AM" if side == "from" else "PM")).upper() == "PM" else "AM"
        style = dict(
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            color=C.text,
            dense=True,
        )
        hour_dd = ft.Dropdown(
            label=self._("hour"),
            value=hour,
            options=[ft.dropdown.Option(str(i), str(i)) for i in range(1, 13)],
            expand=True,
            **style,
        )
        minute_dd = ft.Dropdown(
            label=self._("minute"),
            value=minute,
            options=[ft.dropdown.Option(f"{i:02d}", f"{i:02d}") for i in range(0, 60)],
            expand=True,
            **style,
        )
        ampm_dd = ft.Dropdown(
            label=self._("ampm"),
            value=ampm,
            options=[
                ft.dropdown.Option("AM", self._("am")),
                ft.dropdown.Option("PM", self._("pm")),
            ],
            expand=True,
            **style,
        )
        return hour_dd, minute_dd, ampm_dd

    def _read_ampm_dropdowns(self, hour_dd, minute_dd, ampm_dd) -> dict:
        try:
            hour = int(hour_dd.value or 0)
            minute = int(minute_dd.value or 0)
        except (TypeError, ValueError):
            hour, minute = 10, 0
        return {
            "h": min(12, max(1, hour or 1)),
            "m": min(59, max(0, minute)),
            "ampm": "PM" if str(ampm_dd.value or "AM").upper() == "PM" else "AM",
        }

    async def _capture_gps(self) -> tuple[float, float] | None:
        if fph is not None:
            try:
                ph = fph.PermissionHandler()
                loc_perm = getattr(fph.Permission, "LOCATION_WHEN_IN_USE", None) or getattr(
                    fph.Permission, "LOCATION", None
                )
                if loc_perm:
                    status = await ph.request(loc_perm)
                    allowed = (
                        fph.PermissionStatus.GRANTED,
                        fph.PermissionStatus.LIMITED,
                        fph.PermissionStatus.PROVISIONAL,
                    )
                    if status not in allowed:
                        self.toast(self._("gps_fail"), error=True)
                        return None
            except Exception:
                pass
        geo = self._geolocator
        if geo is None and fgeo is not None:
            try:
                geo = fgeo.Geolocator()
                self._geolocator = geo
                services = getattr(self.page, "services", None)
                if services is not None and geo not in services:
                    services.append(geo)
            except Exception as exc:
                self.toast(str(exc), error=True)
                return None
        if geo is None:
            self.toast(self._("gps_fail"), error=True)
            return None
        try:
            if hasattr(geo, "request_permission"):
                await geo.request_permission()
            pos = await geo.get_current_position()
            lat = float(getattr(pos, "latitude", None))
            lng = float(getattr(pos, "longitude", None))
            return lat, lng
        except Exception as exc:
            self.toast(f"{self._('gps_fail')} {exc}", error=True)
            return None

    def _menu_payload(self) -> dict:
        s = self.current_storage or {}
        return normalize_menu_data(
            s.get("menu_data") or {},
            restaurant_name=s.get("title") or "",
        )

    def go_menu(self):
        """Public restaurant menu view for digital menu storages."""
        self._set_back(self.go_home)
        s = self.current_storage or {}
        sid = s.get("id")
        menu = self._menu_payload()
        primary = menu.get("primary_color") or C.primary
        can_write = self._can_write()
        image_jobs: list[tuple[int, ft.Container]] = []

        restaurant = menu.get("restaurant_name") or s.get("title") or self._("menu_badge")
        description = (menu.get("description") or "").strip()
        gallery_ids = [
            fid
            for fid in (self._menu_file_id(x) for x in (menu.get("gallery_file_ids") or []))
            if fid
        ]
        cover_id = self._menu_file_id(menu.get("cover_file_id")) or (gallery_ids[0] if gallery_ids else None)
        extra_ids = [fid for fid in gallery_ids if fid != cover_id]

        cover_box, cover_box_id = self._menu_photo_box(
            cover_id,
            width=None,
            height=210,
            radius=0,
            fallback_icon=ft.Icons.RESTAURANT,
        )
        cover_box.expand = True
        cover_box.bgcolor = primary
        if cover_box_id:
            image_jobs.append((cover_box_id, cover_box))

        logo_box, logo_id = self._menu_photo_box(
            menu.get("logo_file_id"),
            width=72,
            height=72,
            radius=36,
            fallback_icon=ft.Icons.STOREFRONT,
        )
        logo_box.border = ft.Border.all(3, "#FFFFFF")
        logo_box.shadow = ft.BoxShadow(blur_radius=16, color="#00000066", offset=ft.Offset(0, 4))
        if logo_id:
            image_jobs.append((logo_id, logo_box))

        hero = ft.Container(
            content=ft.Stack(
                [
                    cover_box,
                    ft.Container(
                        expand=True,
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment.TOP_CENTER,
                            end=ft.Alignment.BOTTOM_CENTER,
                            colors=["#33000000", "#E6000000"],
                        ),
                    ),
                    ft.Container(
                        padding=ft.Padding.only(left=16, right=16, bottom=16, top=12),
                        alignment=ft.Alignment.BOTTOM_LEFT,
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        logo_box,
                                        ft.Column(
                                            [
                                                ft.Text(
                                                    restaurant,
                                                    size=26,
                                                    weight=ft.FontWeight.BOLD,
                                                    color="#FFFFFF",
                                                ),
                                                ft.Text(
                                                    description or self._("our_menu"),
                                                    size=13,
                                                    color="#E2E8F0",
                                                ),
                                            ],
                                            spacing=2,
                                            expand=True,
                                        ),
                                    ],
                                    spacing=12,
                                    vertical_alignment=ft.CrossAxisAlignment.END,
                                ),
                            ],
                            spacing=0,
                        ),
                    ),
                ]
            ),
            height=220,
            border_radius=22,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        gallery_row: ft.Control = ft.Container()
        if extra_ids:
            thumbs: list[ft.Control] = []
            for gid in extra_ids:
                thumb, tid = self._menu_photo_box(gid, width=150, height=110, radius=16)
                if tid:
                    image_jobs.append((tid, thumb))
                thumbs.append(thumb)
            gallery_row = ft.Column(
                [
                    ft.Text(self._("gallery_title"), size=14, weight=ft.FontWeight.W_700, color=C.text),
                    ft.Row(thumbs, spacing=10, scroll=ft.ScrollMode.AUTO),
                ],
                spacing=8,
            )

        contact_actions: list[ft.Control] = []
        phone = (menu.get("phone") or "").strip()
        whatsapp = (menu.get("whatsapp") or "").strip()
        email = (menu.get("email") or "").strip()
        address = (menu.get("address") or "").strip()
        hours = format_hours_range(menu.get("hours_from"), menu.get("hours_to"), fallback=menu.get("hours") or "")
        maps_url = maps_href(
            address=address,
            maps_url=menu.get("maps_url") or "",
            lat=menu.get("lat"),
            lng=menu.get("lng"),
        )
        if tel_href(phone):
            contact_actions.append(self._action_chip(self._("call_now"), ft.Icons.CALL, tel_href(phone), primary))
        if whatsapp_href(whatsapp):
            contact_actions.append(
                self._action_chip(self._("contact_whatsapp"), ft.Icons.CHAT, whatsapp_href(whatsapp), "#22C55E")
            )
        if maps_url:
            contact_actions.append(self._action_chip(self._("open_maps"), ft.Icons.NEAR_ME, maps_url, C.accent))
        if mailto_href(email):
            contact_actions.append(self._action_chip(self._("contact_email"), ft.Icons.MAIL_OUTLINE, mailto_href(email), "#A855F7"))

        contact_details: list[ft.Control] = []
        if address:
            contact_details.append(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.LOCATION_ON_OUTLINED, size=18, color=primary),
                        ft.Text(address, color=C.text, size=13, expand=True),
                    ],
                    spacing=8,
                )
            )
        if hours:
            contact_details.append(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.SCHEDULE, size=18, color=primary),
                        ft.Text(hours, color=C.text, size=13, expand=True),
                    ],
                    spacing=8,
                )
            )
        if phone:
            contact_details.append(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.PHONE_OUTLINED, size=18, color=primary),
                        ft.Text(phone, color=C.text, size=13, expand=True),
                    ],
                    spacing=8,
                )
            )
        if email:
            contact_details.append(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ALTERNATE_EMAIL, size=18, color=primary),
                        ft.Text(email, color=C.text, size=13, expand=True),
                    ],
                    spacing=8,
                )
            )

        contact_card: ft.Control = ft.Container()
        if contact_actions or contact_details:
            contact_card = card(
                ft.Column(
                    [
                        ft.Text(self._("contact_title"), size=16, weight=ft.FontWeight.BOLD, color=C.text),
                        ft.Row(contact_actions, spacing=8, wrap=True) if contact_actions else ft.Container(),
                        *contact_details,
                    ],
                    spacing=10,
                ),
                padding=14,
            )

        social = menu.get("social") if isinstance(menu.get("social"), dict) else {}
        social_buttons = [
            btn
            for key, _label, _prefix in SOCIAL_NETWORKS
            if (btn := self._social_button(key, social.get(key) or "", primary))
        ]
        footer: ft.Control = ft.Container()
        if social_buttons:
            footer = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(self._("social_title"), size=14, weight=ft.FontWeight.W_700, color=C.text),
                        ft.Row(social_buttons, spacing=10, alignment=ft.MainAxisAlignment.CENTER),
                    ],
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=C.surface,
                border=ft.Border.all(1, C.border),
                border_radius=18,
                padding=16,
                margin=ft.Margin.only(top=8, bottom=20),
            )

        visible_sections = [sec for sec in menu.get("sections") or [] if sec.get("visible", True)]
        selected_category = self._menu_category_id
        if selected_category and not any(sec.get("id") == selected_category for sec in visible_sections):
            selected_category = None
            self._menu_category_id = None
        shown_sections = [
            sec for sec in visible_sections if not selected_category or sec.get("id") == selected_category
        ]

        def set_category(section_id: str | None):
            self._menu_category_id = section_id
            self.go_menu()

        category_chips: list[ft.Control] = []
        all_active = not selected_category
        category_chips.append(
            ft.Container(
                content=ft.Text(
                    self._("category_all"),
                    size=12,
                    weight=ft.FontWeight.W_700,
                    color="#0B1220" if all_active else C.text,
                ),
                bgcolor=primary if all_active else C.surface_alt,
                padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                border_radius=999,
                border=ft.Border.all(1, primary if all_active else C.border),
                ink=True,
                on_click=lambda e: set_category(None),
            )
        )
        for sec in visible_sections:
            active = selected_category == sec.get("id")
            category_chips.append(
                ft.Container(
                    content=ft.Text(
                        sec.get("name") or "Section",
                        size=12,
                        weight=ft.FontWeight.W_700,
                        color="#0B1220" if active else C.text,
                    ),
                    bgcolor=primary if active else C.surface_alt,
                    padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                    border_radius=999,
                    border=ft.Border.all(1, primary if active else C.border),
                    ink=True,
                    on_click=lambda e, sid=sec.get("id"): set_category(sid),
                )
            )

        section_controls: list[ft.Control] = []
        if not shown_sections:
            section_controls.append(
                card(
                    ft.Column(
                        [
                            ft.Icon(ft.Icons.RESTAURANT_MENU, size=36, color=primary),
                            muted(self._("menu_empty")),
                        ],
                        spacing=8,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                )
            )

        def allergen_row(codes: list) -> ft.Control:
            labels = [label for code, label in ALLERGENS if code in (codes or [])]
            if not labels:
                return ft.Container()
            chips = [
                ft.Container(
                    content=ft.Text(label, size=10, color=C.text_muted),
                    bgcolor=C.surface_alt,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    border_radius=999,
                )
                for label in labels
            ]
            return ft.Row(chips, spacing=6, wrap=True)

        for sec in shown_sections:
            products = [p for p in (sec.get("products") or []) if p.get("visible", True)]
            product_cards: list[ft.Control] = []
            currency = menu.get("currency") or "SYP"
            for prod in products:
                photo, photo_id = self._menu_photo_box(
                    prod.get("image_file_id"),
                    width=None,
                    height=112,
                    radius=14,
                    fallback_icon=ft.Icons.FASTFOOD_OUTLINED,
                )
                photo.expand = True
                if photo_id:
                    image_jobs.append((photo_id, photo))
                price = format_menu_price(prod.get("price") or "", currency) or self._("no_price")
                desc = (prod.get("description") or "").strip()
                product_cards.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                photo,
                                ft.Text(
                                    prod.get("name") or "—",
                                    weight=ft.FontWeight.W_800,
                                    color=C.text,
                                    size=13,
                                    max_lines=2,
                                ),
                                ft.Text(desc, color=C.text_muted, size=11, max_lines=2)
                                if desc
                                else ft.Container(),
                                allergen_row(prod.get("allergens") or []),
                                ft.Container(
                                    content=ft.Text(
                                        price,
                                        color="#0B1220",
                                        weight=ft.FontWeight.W_800,
                                        size=12,
                                    ),
                                    bgcolor=primary,
                                    padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                                    border_radius=10,
                                    alignment=ft.Alignment.CENTER,
                                ),
                            ],
                            spacing=6,
                        ),
                        bgcolor=C.surface,
                        border=ft.Border.all(1, C.border),
                        border_radius=18,
                        padding=8,
                        shadow=ft.BoxShadow(
                            blur_radius=12,
                            color="#00000040",
                            offset=ft.Offset(0, 4),
                        ),
                        expand=True,
                    )
                )
            grid_rows: list[ft.Control] = []
            for i in range(0, len(product_cards), 2):
                left = product_cards[i]
                right = product_cards[i + 1] if i + 1 < len(product_cards) else ft.Container(expand=True)
                grid_rows.append(
                    ft.Row(
                        [
                            ft.Container(content=left, expand=True),
                            ft.Container(content=right, expand=True),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                )
            section_controls.append(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Container(width=4, height=22, bgcolor=primary, border_radius=4),
                                ft.Text(
                                    sec.get("name") or "Section",
                                    size=20,
                                    weight=ft.FontWeight.BOLD,
                                    color=C.text,
                                    expand=True,
                                ),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Text(sec.get("description") or "", color=C.text_muted, size=12)
                        if (sec.get("description") or "").strip()
                        else ft.Container(),
                        *grid_rows,
                    ],
                    spacing=10,
                )
            )

        header_actions: list[ft.Control] = []
        if can_write:
            header_actions.append(
                ft.IconButton(
                    icon=ft.Icons.EDIT_NOTE,
                    icon_color=primary,
                    tooltip=self._("customize_menu"),
                    on_click=lambda e: self.go_menu_editor(),
                )
            )
        if self._is_owner():
            header_actions.append(
                ft.IconButton(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    icon_color=C.text_muted,
                    tooltip=self._("menu_manage"),
                    on_click=lambda e: self.go_menu_editor(),
                )
            )
        header_actions.append(
            ft.IconButton(
                icon=ft.Icons.HELP_OUTLINE,
                icon_color=C.text_muted,
                tooltip=self._("help"),
                on_click=lambda e: self._show_help(),
            )
        )

        body = ft.Column(
            [
                ft.Row(
                    [
                        ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=self._request_back),
                        ft.Column(
                            [
                                ft.Text(restaurant, size=16, weight=ft.FontWeight.BOLD, color=C.text),
                                muted(self._("menu_badge") if can_write else self._("our_menu")),
                            ],
                            spacing=0,
                            expand=True,
                        ),
                        *header_actions,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                hero,
                gallery_row,
                contact_card,
                ft.Row(category_chips, spacing=8, scroll=ft.ScrollMode.AUTO)
                if category_chips
                else ft.Container(),
                *section_controls,
                footer,
            ],
            spacing=14,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )
        self.set_view(body)
        if sid and image_jobs:
            self.page.run_task(self._hydrate_menu_images, sid, image_jobs)

    def go_menu_editor(self):
        """Owner/writer customization for digital menu."""
        if not self._can_write():
            self.toast(self._("read_only"), error=True)
            return
        self._set_back(self.go_menu)
        s = self.current_storage or {}
        sid = s.get("id")
        draft = clone_menu(self._menu_payload())
        self._menu_draft = draft

        name_field = ft.TextField(
            label=self._("restaurant_name"),
            value=draft.get("restaurant_name") or "",
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
        )
        desc_field = ft.TextField(
            label=self._("menu_description"),
            value=draft.get("description") or "",
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            focused_border_color=C.primary,
            color=C.text,
            multiline=True,
            min_lines=2,
            max_lines=4,
        )
        address_field = self._editor_text_field(self._("contact_address"), draft.get("address") or "", multiline=True)
        phone_field = self._editor_text_field(self._("contact_phone"), draft.get("phone") or "")
        whatsapp_field = self._editor_text_field(self._("contact_whatsapp"), draft.get("whatsapp") or "")
        email_field = self._editor_text_field(self._("contact_email"), draft.get("email") or "")
        open_h, open_m, open_ampm = self._ampm_time_dropdowns(draft.get("hours_from"), side="from")
        close_h, close_m, close_ampm = self._ampm_time_dropdowns(draft.get("hours_to"), side="to")
        lat_field = self._editor_text_field(self._("lat"), "" if draft.get("lat") is None else str(draft.get("lat")))
        lng_field = self._editor_text_field(self._("lng"), "" if draft.get("lng") is None else str(draft.get("lng")))
        maps_field = self._editor_text_field(self._("contact_maps"), draft.get("maps_url") or "")
        currency_dd = ft.Dropdown(
            label=self._("currency"),
            value=draft.get("currency") or "SYP",
            options=[
                ft.dropdown.Option(code, f"{code} ({symbol})")
                for code, symbol in CURRENCY_OPTIONS
            ],
            border_radius=14,
            bgcolor=C.surface_alt,
            border_color=C.border,
            color=C.text,
            expand=True,
        )
        social = draft.get("social") if isinstance(draft.get("social"), dict) else {}
        social_fields = {
            key: self._editor_text_field(self._(f"social_{key}"), social.get(key) or "")
            for key, _label, _prefix in SOCIAL_NETWORKS
        }
        sections_host = ft.Column(spacing=10)
        self._menu_sections_host = sections_host
        photos_host = ft.Column(spacing=8)
        photo_jobs: list[tuple[int, ft.Container]] = []
        section_jobs: list[tuple[int, ft.Container]] = []

        def hydrate_editor_photos():
            jobs = photo_jobs + section_jobs
            if sid and jobs:
                self.page.run_task(self._hydrate_menu_images, sid, jobs)

        def sync_draft_from_fields():
            draft["restaurant_name"] = (name_field.value or "").strip()
            draft["description"] = (desc_field.value or "").strip()
            draft["address"] = (address_field.value or "").strip()
            draft["phone"] = (phone_field.value or "").strip()
            draft["whatsapp"] = (whatsapp_field.value or "").strip()
            draft["email"] = (email_field.value or "").strip()
            hours_from = self._read_ampm_dropdowns(open_h, open_m, open_ampm)
            hours_to = self._read_ampm_dropdowns(close_h, close_m, close_ampm)
            draft["hours_from"] = hours_from
            draft["hours_to"] = hours_to
            draft["hours"] = format_hours_range(hours_from, hours_to)
            try:
                draft["lat"] = float((lat_field.value or "").strip()) if (lat_field.value or "").strip() else None
            except ValueError:
                draft["lat"] = None
            try:
                draft["lng"] = float((lng_field.value or "").strip()) if (lng_field.value or "").strip() else None
            except ValueError:
                draft["lng"] = None
            draft["maps_url"] = (maps_field.value or "").strip() or maps_href(
                address=draft["address"],
                lat=draft.get("lat"),
                lng=draft.get("lng"),
            )
            draft["currency"] = currency_dd.value or "SYP"
            draft["social"] = {
                key: (field.value or "").strip()
                for key, field in social_fields.items()
            }
            gallery = list(draft.get("gallery_file_ids") or [])
            cover = self._menu_file_id(draft.get("cover_file_id"))
            if cover and cover not in gallery:
                gallery.insert(0, cover)
            draft["gallery_file_ids"] = gallery
            draft["cover_file_id"] = gallery[0] if gallery else cover

        def make_photo_row(file_id, label: str, on_set, on_clear, jobs: list, *, circle: bool = False, size: int = 72) -> ft.Control:
            box, fid = self._menu_photo_box(
                file_id,
                width=size,
                height=size,
                radius=size // 2 if circle else 14,
                fallback_icon=ft.Icons.ADD_A_PHOTO_OUTLINED,
            )
            if fid:
                jobs.append((fid, box))

            async def pick(_e=None):
                uploaded = await self._upload_menu_photo()
                if not uploaded:
                    return
                on_set(uploaded.get("id"))
                rebuild_photos()
                rebuild_sections()
                hydrate_editor_photos()

            def clear(_e=None):
                on_clear()
                rebuild_photos()
                rebuild_sections()
                hydrate_editor_photos()

            return ft.Row(
                [
                    box,
                    ft.Column(
                        [
                            ft.Text(label, size=12, weight=ft.FontWeight.W_600, color=C.text),
                            ft.Row(
                                [
                                    ghost_button(
                                        self._("change_photo") if fid else label,
                                        lambda e: self.page.run_task(pick),
                                        ft.Icons.PHOTO_CAMERA_OUTLINED,
                                    ),
                                    ft.IconButton(
                                        ft.Icons.CLOSE,
                                        icon_color=C.danger,
                                        tooltip=self._("remove_photo"),
                                        visible=bool(fid),
                                        on_click=clear,
                                    ),
                                ],
                                spacing=4,
                                wrap=True,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

        def rebuild_photos():
            photo_jobs.clear()
            gallery_ids = [
                fid
                for fid in (self._menu_file_id(x) for x in (draft.get("gallery_file_ids") or []))
                if fid
            ]
            thumbs: list[ft.Control] = []
            for gid in gallery_ids:
                box, fid = self._menu_photo_box(gid, width=84, height=84, radius=14)
                if fid:
                    photo_jobs.append((fid, box))

                def remove_gallery(_e=None, remove_id=gid):
                    draft["gallery_file_ids"] = [
                        x for x in (draft.get("gallery_file_ids") or []) if self._menu_file_id(x) != remove_id
                    ]
                    if self._menu_file_id(draft.get("cover_file_id")) == remove_id:
                        remaining = draft["gallery_file_ids"]
                        draft["cover_file_id"] = remaining[0] if remaining else None
                    rebuild_photos()
                    hydrate_editor_photos()

                def make_cover(_e=None, cover_id=gid):
                    ids = [x for x in (draft.get("gallery_file_ids") or []) if self._menu_file_id(x) != cover_id]
                    ids.insert(0, cover_id)
                    draft["gallery_file_ids"] = ids
                    draft["cover_file_id"] = cover_id
                    rebuild_photos()
                    hydrate_editor_photos()

                thumbs.append(
                    ft.Column(
                        [
                            ft.Stack(
                                [
                                    box,
                                    ft.Container(
                                        content=ft.Icon(ft.Icons.CLOSE, size=14, color="#FFFFFF"),
                                        bgcolor="#E11D48",
                                        width=22,
                                        height=22,
                                        border_radius=11,
                                        alignment=ft.Alignment.CENTER,
                                        right=4,
                                        top=4,
                                        ink=True,
                                        on_click=remove_gallery,
                                    ),
                                ]
                            ),
                            ft.TextButton(
                                self._("set_as_cover"),
                                on_click=make_cover,
                                visible=self._menu_file_id(draft.get("cover_file_id")) != gid,
                            ),
                        ],
                        spacing=4,
                        width=84,
                    )
                )

            async def add_gallery(_e=None):
                uploaded = await self._upload_menu_photos(multiple=True)
                ids = list(draft.get("gallery_file_ids") or [])
                for row in uploaded:
                    fid = self._menu_file_id(row.get("id"))
                    if fid and fid not in ids:
                        ids.append(fid)
                if ids and not draft.get("cover_file_id"):
                    draft["cover_file_id"] = ids[0]
                draft["gallery_file_ids"] = ids
                rebuild_photos()
                hydrate_editor_photos()

            def set_cover_photo(fid):
                draft["cover_file_id"] = fid
                ids = [x for x in (draft.get("gallery_file_ids") or []) if self._menu_file_id(x) != fid]
                if fid:
                    ids.insert(0, fid)
                draft["gallery_file_ids"] = ids

            def clear_cover_photo():
                cover = self._menu_file_id(draft.get("cover_file_id"))
                draft["cover_file_id"] = None
                if cover:
                    draft["gallery_file_ids"] = [
                        x for x in (draft.get("gallery_file_ids") or []) if self._menu_file_id(x) != cover
                    ]

            photos_host.controls = [
                make_photo_row(
                    draft.get("cover_file_id"),
                    self._("add_cover_photo"),
                    set_cover_photo,
                    clear_cover_photo,
                    photo_jobs,
                    size=86,
                ),
                make_photo_row(
                    draft.get("logo_file_id"),
                    self._("add_logo"),
                    lambda fid: draft.__setitem__("logo_file_id", fid),
                    lambda: draft.__setitem__("logo_file_id", None),
                    photo_jobs,
                    circle=True,
                    size=64,
                ),
                ft.Text(self._("gallery_title"), size=14, weight=ft.FontWeight.W_700, color=C.text),
                ft.Row(
                    [
                        *thumbs,
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Icon(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, color=C.primary),
                                    ft.Text(self._("add_gallery_photos"), size=11, color=C.text_muted),
                                ],
                                spacing=4,
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            width=84,
                            height=84,
                            bgcolor=C.surface_alt,
                            border=ft.Border.all(1, C.border),
                            border_radius=14,
                            alignment=ft.Alignment.CENTER,
                            ink=True,
                            on_click=lambda e: self.page.run_task(add_gallery),
                        ),
                    ],
                    spacing=8,
                    wrap=True,
                ),
            ]
            try:
                photos_host.update()
            except Exception:
                pass

        def rebuild_sections():
            section_jobs.clear()
            controls: list[ft.Control] = []
            for si, sec in enumerate(draft.get("sections") or []):
                product_controls: list[ft.Control] = []
                for pi, prod in enumerate(sec.get("products") or []):
                    pname = ft.TextField(
                        label=self._("product_name"),
                        value=prod.get("name") or "",
                        border_radius=12,
                        dense=True,
                        bgcolor=C.surface,
                        border_color=C.border,
                        focused_border_color=C.primary,
                        color=C.text,
                        expand=True,
                        on_change=lambda e, s_i=si, p_i=pi: draft["sections"][s_i]["products"][p_i].__setitem__(
                            "name", e.control.value or ""
                        ),
                    )
                    pprice = ft.TextField(
                        label=self._("product_price"),
                        value=prod.get("price") or "",
                        border_radius=12,
                        dense=True,
                        bgcolor=C.surface,
                        border_color=C.border,
                        focused_border_color=C.primary,
                        color=C.text,
                        expand=True,
                        keyboard_type=ft.KeyboardType.NUMBER,
                        on_change=lambda e, s_i=si, p_i=pi: draft["sections"][s_i]["products"][p_i].__setitem__(
                            "price", e.control.value or ""
                        ),
                    )
                    pdesc = ft.TextField(
                        label=self._("product_description"),
                        value=prod.get("description") or "",
                        border_radius=12,
                        dense=True,
                        bgcolor=C.surface,
                        border_color=C.border,
                        focused_border_color=C.primary,
                        color=C.text,
                        expand=True,
                        on_change=lambda e, s_i=si, p_i=pi: draft["sections"][s_i]["products"][p_i].__setitem__(
                            "description", e.control.value or ""
                        ),
                    )
                    allergen_chips = []
                    selected = set(prod.get("allergens") or [])

                    def toggle_allergen(code: str, s_i: int, p_i: int):
                        cur = set(draft["sections"][s_i]["products"][p_i].get("allergens") or [])
                        if code in cur:
                            cur.remove(code)
                        else:
                            cur.add(code)
                        draft["sections"][s_i]["products"][p_i]["allergens"] = list(cur)
                        rebuild_sections()

                    for code, label in ALLERGENS:
                        active = code in selected
                        allergen_chips.append(
                            ft.Container(
                                content=ft.Text(label, size=11, color=C.bg if active else C.text),
                                bgcolor=C.primary if active else C.surface_alt,
                                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                border_radius=999,
                                border=ft.Border.all(1, C.primary if active else C.border),
                                on_click=lambda e, c=code, s_i=si, p_i=pi: toggle_allergen(c, s_i, p_i),
                                ink=True,
                            )
                        )
                    product_controls.append(
                        card(
                            ft.Column(
                                [
                                    make_photo_row(
                                        prod.get("image_file_id"),
                                        self._("product_photo"),
                                        lambda fid, s_i=si, p_i=pi: draft["sections"][s_i]["products"][p_i].__setitem__(
                                            "image_file_id", fid
                                        ),
                                        lambda s_i=si, p_i=pi: draft["sections"][s_i]["products"][p_i].__setitem__(
                                            "image_file_id", None
                                        ),
                                        section_jobs,
                                        size=72,
                                    ),
                                    pname,
                                    pprice,
                                    pdesc,
                                    ft.Row(
                                        [
                                            ft.Text(self._("visible"), size=12, color=C.text_muted, expand=True),
                                            ft.Switch(
                                                value=bool(prod.get("visible", True)),
                                                active_color=C.primary,
                                                on_change=lambda e, s_i=si, p_i=pi: draft["sections"][s_i][
                                                    "products"
                                                ][p_i].__setitem__("visible", bool(e.control.value)),
                                            ),
                                        ],
                                        spacing=6,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    muted(self._("allergens")),
                                    ft.Row(allergen_chips, spacing=6, wrap=True),
                                    ft.OutlinedButton(
                                        content=self._("delete_product"),
                                        icon=ft.Icons.DELETE_OUTLINE,
                                        on_click=lambda e, s_i=si, p_i=pi: (
                                            draft["sections"][s_i]["products"].pop(p_i),
                                            rebuild_sections(),
                                            hydrate_editor_photos(),
                                        ),
                                        style=ft.ButtonStyle(
                                            color=C.danger,
                                            side=ft.BorderSide(1, C.danger),
                                            padding=12,
                                            shape=ft.RoundedRectangleBorder(radius=12),
                                        ),
                                    ),
                                ],
                                spacing=8,
                            ),
                            padding=10,
                        )
                    )

                sname = ft.TextField(
                    label=self._("section_name"),
                    value=sec.get("name") or "",
                    border_radius=12,
                    bgcolor=C.surface_alt,
                    border_color=C.border,
                    focused_border_color=C.primary,
                    color=C.text,
                    expand=True,
                    on_change=lambda e, s_i=si: draft["sections"][s_i].__setitem__("name", e.control.value or ""),
                )
                controls.append(
                    card(
                        ft.Column(
                            [
                                sname,
                                ft.Row(
                                    [
                                        ft.Text(self._("visible"), size=13, color=C.text, expand=True),
                                        ft.Switch(
                                            value=bool(sec.get("visible", True)),
                                            active_color=C.primary,
                                            tooltip=self._("visible"),
                                            on_change=lambda e, s_i=si: draft["sections"][s_i].__setitem__(
                                                "visible", bool(e.control.value)
                                            ),
                                        ),
                                    ],
                                    spacing=6,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.OutlinedButton(
                                    content=self._("delete_section"),
                                    icon=ft.Icons.DELETE_FOREVER_OUTLINED,
                                    on_click=lambda e, s_i=si: (
                                        draft["sections"].pop(s_i),
                                        rebuild_sections(),
                                    ),
                                    style=ft.ButtonStyle(
                                        color="#EF4444",
                                        side=ft.BorderSide(1, "#EF4444"),
                                        padding=12,
                                        shape=ft.RoundedRectangleBorder(radius=12),
                                    ),
                                ),
                                *product_controls,
                                ghost_button(
                                    self._("add_product"),
                                    lambda e, s_i=si: (
                                        draft["sections"][s_i].setdefault("products", []).append(empty_product()),
                                        rebuild_sections(),
                                    ),
                                    ft.Icons.ADD,
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=12,
                    )
                )
            sections_host.controls = controls
            try:
                sections_host.update()
            except Exception:
                pass

        def pick_colors(primary: str, secondary: str):
            draft["primary_color"] = primary
            draft["secondary_color"] = secondary
            rebuild_color_row()

        color_row = ft.Row(spacing=8, wrap=True)

        def rebuild_color_row():
            swatches = []
            for primary, secondary in COLOR_PRESETS:
                active = draft.get("primary_color") == primary and draft.get("secondary_color") == secondary
                swatches.append(
                    ft.Container(
                        width=36,
                        height=36,
                        bgcolor=primary,
                        border_radius=10,
                        border=ft.Border.all(3, secondary if active else C.border),
                        on_click=lambda e, p=primary, sec=secondary: pick_colors(p, sec),
                        ink=True,
                    )
                )
            color_row.controls = swatches
            try:
                color_row.update()
            except Exception:
                pass

        rebuild_color_row()
        rebuild_photos()
        rebuild_sections()
        hydrate_editor_photos()

        async def fill_gps(_e=None):
            coords = await self._capture_gps()
            if not coords:
                return
            lat, lng = coords
            lat_field.value = f"{lat:.6f}"
            lng_field.value = f"{lng:.6f}"
            maps_field.value = maps_href(lat=lat, lng=lng, address=address_field.value or "")
            draft["lat"] = lat
            draft["lng"] = lng
            draft["maps_url"] = maps_field.value
            try:
                lat_field.update()
                lng_field.update()
                maps_field.update()
            except Exception:
                self.page.update()
            self.toast(self._("gps_ok"))

        async def save_menu(_e=None):
            if not sid:
                return
            sync_draft_from_fields()
            try:
                storage = await asyncio.to_thread(self.api.update_menu_data, sid, draft)
                self.current_storage = storage
                self.offline.upsert_home_storage(storage)
                self.toast(self._("menu_saved"))
                self.go_menu()
            except ApiError as err:
                self.toast(err.message, error=True)
            except Exception as err:
                self.toast(str(err), error=True)

        body = ft.Column(
            [
                ft.Row(
                    [
                        ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=self._request_back),
                        ft.Text(self._("menu_editor_title"), size=18, weight=ft.FontWeight.BOLD, color=C.text, expand=True),
                        primary_button(self._("save_menu"), lambda e: self.page.run_task(save_menu), ft.Icons.SAVE, expand=False),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                name_field,
                desc_field,
                currency_dd,
                photos_host,
                section_title(self._("contact_title")),
                address_field,
                phone_field,
                whatsapp_field,
                email_field,
                section_title(self._("contact_hours")),
                ft.Text(self._("hours_from"), size=12, color=C.text_muted),
                ft.Row([open_h, open_m, open_ampm], spacing=8),
                ft.Text(self._("hours_to"), size=12, color=C.text_muted),
                ft.Row([close_h, close_m, close_ampm], spacing=8),
                section_title(self._("open_maps")),
                lat_field,
                lng_field,
                ghost_button(
                    self._("use_gps"),
                    lambda e: self.page.run_task(fill_gps),
                    ft.Icons.MY_LOCATION,
                ),
                maps_field,
                section_title(self._("social_title")),
                *social_fields.values(),
                card(
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(self._("public_vault"), weight=ft.FontWeight.W_700, color=C.text, size=13, expand=True),
                                    self._info_button("public_vault_tip", "public_vault"),
                                    ft.Switch(
                                        value=bool(s.get("is_public")),
                                        active_color=C.primary,
                                        on_change=lambda e, st=sid: self.page.run_task(
                                            self._toggle_public, st, bool(e.control.value)
                                        ),
                                    ),
                                ],
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            )
                            if self._is_owner() and sid
                            else ft.Container(),
                            ft.Row(
                                [
                                    ft.Text(self._("digital_menu"), weight=ft.FontWeight.W_700, color=C.text, size=13, expand=True),
                                    self._info_button("digital_menu_tip", "digital_menu"),
                                    ft.Switch(
                                        value=True,
                                        active_color=C.primary,
                                        on_change=lambda e, st=sid: self.page.run_task(
                                            self._toggle_menu_kind, st, bool(e.control.value)
                                        ),
                                    ),
                                ],
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            )
                            if self._is_owner() and sid
                            else ft.Container(),
                        ],
                        spacing=4,
                    ),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                )
                if self._is_owner()
                else ft.Container(),
                section_title(self._("menu_colors")),
                color_row,
                ghost_button(
                    self._("add_section"),
                    lambda e: (draft.setdefault("sections", []).append(empty_section()), rebuild_sections()),
                    ft.Icons.ADD_CIRCLE_OUTLINE,
                ),
                sections_host,
            ],
            spacing=10,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )
        self.set_view(body)

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
        for kind, label in [
            ("all", self._("filter_all")),
            ("images", self._("filter_images")),
            ("docs", self._("filter_docs")),
            ("notes", self._("filter_notes")),
        ]:
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

        def mode_chip(icon, mode: str, active: bool, tip_key: str):
            return ft.Container(
                content=ft.Icon(icon, size=20, color=C.bg if active else C.text_muted),
                bgcolor=C.primary if active else C.surface_alt,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=12,
                border=ft.Border.all(1, C.primary if active else C.border),
                on_click=lambda e, m=mode: select(m),
                on_long_press=lambda e, k=tip_key: self._show_info_dialog(self._(k), [self._(k)]),
                ink=True,
            )

        view_toggle.controls = [
            mode_chip(ft.Icons.VIEW_LIST, "list", list_active, "list_view"),
            mode_chip(ft.Icons.GRID_VIEW, "icons", icons_active, "icons_view"),
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
                        tooltip=self._("edit"),
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
                        tooltip=self._("delete"),
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
                    # Mobile: long-press the full-width item to drag.
                    show_default_drag_handles=True,
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
            title=ft.Text(self._("rename_storage"), color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Container(content=name_field, width=320),
            actions=[
                ft.Button(content=self._("cancel"), on_click=close),
                ft.Button(content=self._("save"), on_click=save, bgcolor=C.primary, color=C.bg),
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

        open_label = self._("play") if playable else self._("open")
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

        def open_full(_=None, i=fid, n=name, ct=content_type):
            # Same full-screen experience as icons browse mode.
            self.page.run_task(self._open_full_preview, storage_id, i, n, ct)

        days = f.get("days_remaining")
        days_txt = f"{days}d left" if days is not None else ""
        details_lines = [
            name,
            f"{fmt_size(f.get('size_original'))} → {fmt_size(f.get('size_compressed'))}",
        ]
        if archived:
            details_lines.append("archived")
        if days_txt:
            details_lines.append(days_txt)
        if pending:
            details_lines.append(self._("pending"))
        elif cached:
            details_lines.append(self._("cached"))
        elif self._offline_mode:
            details_lines.append(self._("online_only"))
        details_text = "\n".join(details_lines)

        def show_details(_=None, title=name, body=details_text):
            self._show_file_details(title, body)

        menu_items = [
            ft.PopupMenuItem(content=open_label, icon=open_icon, on_click=open_full),
            ft.PopupMenuItem(
                content=self._("details"),
                icon=ft.Icons.INFO_OUTLINE,
                on_click=show_details,
            ),
            ft.PopupMenuItem(
                content=self._("download"),
                icon=ft.Icons.DOWNLOAD,
                on_click=lambda e, i=fid, n=name: self.page.run_task(self._download, storage_id, i, n),
            ),
        ]
        if self._is_owner():
            menu_items.append(
                ft.PopupMenuItem(
                    content=self._("move_to"),
                    icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINE,
                    on_click=lambda e, i=fid, n=name: self.page.run_task(self._show_move_dialog, storage_id, i, n),
                )
            )
        if can_write:
            menu_items.extend(
                [
                    ft.PopupMenuItem(
                        content=self._("unarchive") if archived else self._("archive"),
                        icon=ft.Icons.ARCHIVE_OUTLINED,
                        on_click=lambda e, i=fid, a=not archived: self.page.run_task(
                            self._archive, storage_id, i, a
                        ),
                    ),
                    ft.PopupMenuItem(
                        content=self._("delete"),
                        icon=ft.Icons.DELETE_OUTLINE,
                        on_click=lambda e, i=fid: self.page.run_task(self._delete_file, storage_id, i),
                    ),
                ]
            )

        leading = self._leading_placeholder(content_type)
        trailing: list[ft.Control] = []
        if pending:
            trailing.append(chip(self._("pending"), C.warning))
        elif cached:
            trailing.append(chip(self._("cached"), C.success))
        elif self._offline_mode:
            trailing.append(chip(self._("online_only"), C.warning))
        if playable:
            trailing.append(
                ft.IconButton(
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    icon_color=C.primary,
                    tooltip=self._("play"),
                    on_click=open_full,
                )
            )
        trailing.append(
            ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                icon_color=C.text_muted,
                tooltip=self._("details"),
                items=menu_items,
            )
        )

        header = ft.Container(
            ink=True,
            border_radius=14,
            padding=10,
            on_click=open_full,
            content=ft.Row(
                [
                    leading,
                    ft.Text(
                        name,
                        color=C.text,
                        weight=ft.FontWeight.W_600,
                        size=14,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
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
            content=header,
        )
        return tile, leading

    def _show_file_details(self, title: str, body: str):
        def close(_e=None):
            self.page.pop_dialog()

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.surface,
            title=ft.Text(self._("file_details_title"), color=C.text, weight=ft.FontWeight.W_700),
            content=ft.Container(
                width=320,
                content=ft.Column(
                    [
                        ft.Text(title, color=C.text, weight=ft.FontWeight.W_600, size=14),
                        muted(body),
                    ],
                    spacing=8,
                    tight=True,
                ),
            ),
            actions=[
                ft.Button(content=self._("close"), on_click=close, bgcolor=C.primary, color=C.bg),
            ],
        )
        self.page.show_dialog(dialog)

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

        days = f.get("days_remaining")
        days_txt = f"{days}d left" if days is not None else ""
        details_text = "\n".join(
            x
            for x in [
                name,
                f"{fmt_size(f.get('size_original'))} → {fmt_size(f.get('size_compressed'))}",
                days_txt,
            ]
            if x
        )

        def show_details(_=None, title=name, body=details_text):
            self._show_file_details(title, body)

        menu_items = [
            ft.PopupMenuItem(
                content=self._("open"),
                icon=ft.Icons.OPEN_IN_FULL,
                on_click=open_full,
            ),
            ft.PopupMenuItem(
                content=self._("details"),
                icon=ft.Icons.INFO_OUTLINE,
                on_click=show_details,
            ),
            ft.PopupMenuItem(
                content=self._("download"),
                icon=ft.Icons.DOWNLOAD,
                on_click=lambda e, i=fid, n=name: self.page.run_task(self._download, storage_id, i, n),
            ),
        ]
        if self._is_owner():
            menu_items.append(
                ft.PopupMenuItem(
                    content=self._("move_to"),
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
                        content=self._("archive"),
                        icon=ft.Icons.ARCHIVE_OUTLINED,
                        on_click=lambda e, i=fid: self.page.run_task(self._archive, storage_id, i, True),
                    ),
                    ft.PopupMenuItem(
                        content=self._("delete"),
                        icon=ft.Icons.DELETE_OUTLINE,
                        on_click=lambda e, i=fid: self.page.run_task(self._delete_file, storage_id, i),
                    ),
                ]
            )

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
                                    tooltip=self._("details"),
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

    async def _fetch_pdf_preview_pages(self, storage_id: int, file_id: int) -> list[Path]:
        """Load PDF page PNGs from local cache or Django preview API (mobile-safe)."""
        if isinstance(file_id, str):
            return []
        cached = self.offline.list_pdf_preview_pages(storage_id, int(file_id))
        if cached:
            return cached
        meta = await asyncio.to_thread(self.api.pdf_preview_meta, storage_id, int(file_id))
        n = int(meta.get("max_pages") or 0)
        if n <= 0:
            return []
        pages: list[Path] = []
        for i in range(1, n + 1):
            dest = self.offline.pdf_preview_page_path(storage_id, int(file_id), i)
            if not dest.exists() or dest.stat().st_size == 0:
                await asyncio.to_thread(
                    self.api.pdf_preview_page, storage_id, int(file_id), i, dest
                )
            pages.append(dest)
        return pages

    def _pdf_open_panel(self, storage_id: int, path: Path, name: str) -> ft.Control:
        """Compact inline panel — opens the real PDF.js viewer on tap."""
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.PICTURE_AS_PDF, size=40, color=C.primary),
                    muted(self._("pdf_tap_to_open")),
                    primary_button(
                        self._("pdf_open_viewer"),
                        lambda e, sid=storage_id, p=path, n=name: self.page.run_task(
                            self._open_pdf_official, sid, n, p
                        ),
                        ft.Icons.OPEN_IN_BROWSER,
                        expand=False,
                    ),
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            height=200,
            expand=True,
            alignment=ft.Alignment.CENTER,
            bgcolor=C.surface,
            border_radius=12,
            border=ft.Border.all(1, C.border),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            padding=16,
        )

    def _pdf_shell(
        self,
        storage_id: int,
        name: str,
        path: Path,
        body: ft.Control,
        *,
        subtitle: str,
        viewer_url: str | None = None,
    ):
        """Fixed-size chrome around the PDF viewer (container size does not grow)."""

        def _back(_e=None):
            self._stop_pdf_server()
            self.page.run_task(self._open_storage, storage_id)

        self._set_back(_back)
        actions: list[ft.Control] = [
            ft.IconButton(ft.Icons.ARROW_BACK, icon_color=C.text, on_click=self._request_back),
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
                    muted(subtitle),
                ],
                spacing=2,
                expand=True,
            ),
        ]
        if viewer_url:
            actions.append(
                ft.IconButton(
                    ft.Icons.REFRESH,
                    icon_color=C.text,
                    tooltip=self._("pdf_reopen"),
                    on_click=lambda e, u=viewer_url: webbrowser.open(u),
                )
            )
        actions.append(
            ft.IconButton(
                ft.Icons.OPEN_IN_NEW,
                icon_color=C.text,
                tooltip=self._("open_with_app"),
                on_click=lambda e, p=path, n=name: self.page.run_task(
                    self._open_local_file, p, n, "application/pdf"
                ),
            )
        )
        # Fixed frame: expands to remaining page height; content zooms inside, not the frame.
        frame = ft.Container(
            content=body,
            expand=True,
            bgcolor="#404040",
            border_radius=12,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            border=ft.Border.all(1, C.border),
        )
        self.set_view(
            ft.Column(
                [ft.Row(actions), frame],
                spacing=8,
                expand=True,
            )
        )

    async def _open_pdf_official(self, storage_id: int, name: str, path: Path) -> bool:
        """Open Mozilla's official PDF.js viewer (search/zoom/highlight built-in)."""
        if not can_serve_pdf(path):
            return False
        self._stop_pdf_server()
        try:
            session = await asyncio.to_thread(
                prepare_pdf_viewer_dir, path, work_root=PREVIEW_DIR
            )
            server, url = await asyncio.to_thread(start_pdf_viewer_server, session)
        except Exception as exc:
            self.toast(str(exc), error=True)
            return False
        self._pdf_server = server

        if self._supports_pdf_webview():
            assert fwv is not None

            async def _on_webview_error(e):
                msg = str(getattr(e, "data", e) or "")
                # Old APKs without usesCleartextTraffic hit this on http://127.0.0.1
                if "CLEARTEXT" in msg.upper() or "ERR_" in msg.upper():
                    self.toast(self._("pdf_cleartext_hint"), error=True)
                    await self._open_local_file(path, name, "application/pdf")

            wv = fwv.WebView(
                url=url,
                expand=True,
                bgcolor="#404040",
                on_web_resource_error=_on_webview_error,
            )
            self._pdf_shell(
                storage_id,
                name,
                path,
                wv,
                subtitle=self._("pdf_official"),
            )
            try:
                await wv.set_javascript_mode(fwv.JavaScriptMode.UNRESTRICTED)
            except Exception:
                pass
            try:
                await wv.enable_zoom()
            except Exception:
                pass
            return True

        # Windows/Linux: Flet has no in-app WebView — open official viewer in the browser.
        try:
            webbrowser.open(url)
        except Exception:
            await self._open_local_file(path, name, "application/pdf")
            return True

        body = ft.Column(
            [
                ft.Icon(ft.Icons.PICTURE_AS_PDF, size=56, color=C.primary),
                ft.Text(
                    self._("pdf_opened_external"),
                    color=C.text,
                    text_align=ft.TextAlign.CENTER,
                    size=14,
                ),
                muted(self._("pdf_external_hint")),
                primary_button(
                    self._("pdf_reopen"),
                    lambda e, u=url: webbrowser.open(u),
                    ft.Icons.OPEN_IN_BROWSER,
                    expand=False,
                ),
            ],
            spacing=14,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        self._pdf_shell(
            storage_id,
            name,
            path,
            body,
            subtitle=self._("pdf_official"),
            viewer_url=url,
        )
        return True

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

        if is_pdf(content_type, name):
            ok = await self._open_pdf_official(storage_id, name, dest)
            if not ok:
                await self._open_local_file(dest, name, "application/pdf")
            return

        self.go_full_viewer(storage_id, name, dest, content_type or "")

    def go_full_viewer(
        self,
        storage_id: int,
        name: str,
        path: Path,
        content_type: str,
        pdf_pages: list[Path] | None = None,
    ):
        # pdf_pages kept for call-site compatibility; unused — real PDF uses official viewer.
        _ = pdf_pages
        if is_pdf(content_type, name):
            self.page.run_task(self._open_pdf_official, storage_id, name, path)
            return

        def _back(_e=None):
            self.page.run_task(self._open_storage, storage_id)

        self._set_back(_back)
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
                                on_click=self._request_back,
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
            sid = int((self.current_storage or {}).get("id") or 0)
            return self._pdf_open_panel(sid, path, name)

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
            if is_pdf(content_type, name):
                panel.content = self._pdf_open_panel(storage_id, dest, name)
                self.page.update()
                return
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
            sid = int((self.current_storage or {}).get("id") or 0)
            return self._pdf_open_panel(sid, path, name)

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
        self._set_back(lambda: self.page.run_task(self._open_storage, sid))
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
                                on_click=self._request_back,
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
        self._show_info_dialog(
            self._("help_title"),
            [
                self._("help_p1"),
                self._("help_p2"),
                self._("help_p3"),
                self._("help_p4"),
                self._("help_p5"),
                self._("help_p6"),
                self._("help_p7"),
                self._("help_p8"),
                self._("help_p9"),
                self._("help_p10"),
            ],
        )

    def go_archive(self):
        s = self.current_storage or {}
        sid = s.get("id")
        self._set_back(lambda: self.page.run_task(self._open_storage, sid))
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
                                on_click=self._request_back,
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
        self._set_back(lambda: self.page.run_task(self._open_storage, sid))
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
                                on_click=self._request_back,
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
