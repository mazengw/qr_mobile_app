"""Simple EN/AR strings for QR Vault mobile."""

from __future__ import annotations

from typing import Any

LANG_EN = "en"
LANG_AR = "ar"

_STRINGS: dict[str, dict[str, str]] = {
    "lang_switch_to_ar": {"en": "العربية", "ar": "العربية"},
    "lang_switch_to_en": {"en": "English", "ar": "English"},
    "signed_in_as": {"en": "Signed in as", "ar": "مسجّل كـ"},
    "sign_out": {"en": "Sign out", "ar": "خروج"},
    "your_vaults": {
        "en": "Your vaults — drag the left handle to reorder",
        "ar": "خزائنك — اسحب المقبض الأيسر لإعادة الترتيب",
    },
    "scan_qr": {"en": "Scan QR", "ar": "مسح QR"},
    "refresh": {"en": "Refresh", "ar": "تحديث"},
    "help": {"en": "Help", "ar": "مساعدة"},
    "offline_home_banner": {
        "en": "Offline — showing cached vault list. Open a vault to browse saved files and notes.",
        "ar": "غير متصل — تظهر قائمة الخزائن المحفوظة. افتح خزناً لتصفح الملفات والملاحظات المحفوظة.",
    },
    "welcome_back": {"en": "Welcome back", "ar": "مرحباً بعودتك"},
    "sign_in_hint": {
        "en": "Sign in with your phone. We'll send a one-time code.",
        "ar": "سجّل الدخول برقم هاتفك. سنرسل رمزاً لمرة واحدة.",
    },
    "phone_apk_hint": {
        "en": "Phone APK: set API URL to your PC Wi‑Fi IP (Django must listen on 0.0.0.0:8000).",
        "ar": "على الهاتف: ضع عنوان API لـ IP شبكة الكمبيوتر (Django على 0.0.0.0:8000).",
    },
    "phone_number": {"en": "Phone number", "ar": "رقم الهاتف"},
    "full_name": {"en": "Full name (optional)", "ar": "الاسم الكامل (اختياري)"},
    "api_base_url": {"en": "API base URL", "ar": "عنوان الـ API"},
    "sign_in": {"en": "Sign in", "ar": "دخول"},
    "dev_otp_hint": {
        "en": "Dev OTP is always 123456 when Django DEBUG=True",
        "ar": "رمز التطوير دائماً 123456 عندما DEBUG=True",
    },
    "verify_phone": {"en": "Verify phone", "ar": "تأكيد الهاتف"},
    "code_sent_to": {"en": "Code sent to {phone}", "ar": "تم إرسال الرمز إلى {phone}"},
    "otp_code": {"en": "OTP code", "ar": "رمز OTP"},
    "verify_enter": {"en": "Verify & enter vault", "ar": "تأكيد والدخول"},
    "resend_code": {"en": "Resend code", "ar": "إعادة إرسال الرمز"},
    "enter_otp": {"en": "Enter OTP", "ar": "أدخل رمز OTP"},
    "enter_phone": {"en": "Enter a valid phone number", "ar": "أدخل رقم هاتف صالحاً"},
    "scan_title": {"en": "Scan QR", "ar": "مسح QR"},
    "qr_value": {"en": "QR code value", "ar": "قيمة رمز QR"},
    "qr_hint": {"en": "e.g. 1   or   share:<uuid>", "ar": "مثال: 1 أو share:<uuid>"},
    "open_storage": {"en": "Open storage", "ar": "فتح الخزنة"},
    "capture_frame": {"en": "Capture frame", "ar": "التقاط إطار"},
    "scan_status_ready": {
        "en": "Point camera at a vault QR — or paste/type below",
        "ar": "وجّه الكاميرا إلى QR الخزنة — أو الصق/اكتب القيمة أدناه",
    },
    "scan_status_desktop": {
        "en": "Camera works on the mobile APK. On desktop, paste/type the QR payload.",
        "ar": "الكاميرا تعمل في تطبيق الموبايل. على الكمبيوتر الصق/اكتب قيمة QR.",
    },
    "scan_help_tooltip": {
        "en": "Empty storage → you can upload files. Existing storage → browse contents.",
        "ar": "خزنة فارغة ← يمكنك رفع ملفات. خزنة موجودة ← تصفح المحتوى.",
    },
    "enter_qr": {"en": "Enter QR value", "ar": "أدخل قيمة QR"},
    "qr_detected": {"en": "QR detected — opening…", "ar": "تم اكتشاف QR — جارٍ الفتح…"},
    "scanned": {"en": "Scanned: {value}", "ar": "تم المسح: {value}"},
    "offline_scan_open": {
        "en": "Offline — opening cached vault",
        "ar": "غير متصل — فتح الخزنة المحفوظة",
    },
    "offline_scan_miss": {
        "en": "Offline — this QR is not in your cached vaults",
        "ar": "غير متصل — هذا الـ QR غير موجود في الخزائن المحفوظة",
    },
    "offline_cannot_scan": {
        "en": "Offline — cannot scan right now",
        "ar": "غير متصل — لا يمكن المسح الآن",
    },
    "offline_cached_vault": {
        "en": "Offline — showing cached vault",
        "ar": "غير متصل — عرض الخزنة المحفوظة",
    },
    "upload": {"en": "Upload", "ar": "رفع"},
    "add_note": {"en": "Add Note", "ar": "إضافة ملاحظة"},
    "merge_pdf": {"en": "Merge PDF", "ar": "دمج PDF"},
    "share": {"en": "Share", "ar": "مشاركة"},
    "save_offline": {"en": "Save offline", "ar": "حفظ دون اتصال"},
    "archived": {"en": "Archived", "ar": "الأرشيف"},
    "public_vault": {"en": "Public vault", "ar": "خزنة عامة"},
    "public_vault_tip": {
        "en": "Anyone who scans this QR (while signed in) can view files & notes. Only you can edit.",
        "ar": "أي شخص يمسح هذا الـ QR (وهو مسجّل) يمكنه عرض الملفات والملاحظات. أنت فقط من يعدّل.",
    },
    "search_files": {"en": "Search files…", "ar": "بحث في الملفات…"},
    "filter_all": {"en": "All", "ar": "الكل"},
    "filter_images": {"en": "Images", "ar": "صور"},
    "filter_docs": {"en": "Docs", "ar": "مستندات"},
    "filter_notes": {"en": "Notes", "ar": "ملاحظات"},
    "owned": {"en": "OWNED", "ar": "ملكي"},
    "shared": {"en": "SHARED", "ar": "مشارك"},
    "owned_filter": {"en": "Owned", "ar": "ملكي"},
    "shared_filter": {"en": "Shared", "ar": "مشارك"},
    "public": {"en": "PUBLIC", "ar": "عام"},
    "offline": {"en": "OFFLINE", "ar": "دون اتصال"},
    "read_only": {"en": "READ ONLY", "ar": "قراءة فقط"},
    "online_only": {"en": "ONLINE ONLY", "ar": "يتطلب إنترنت"},
    "cached": {"en": "CACHED", "ar": "محفوظ"},
    "pending": {"en": "PENDING", "ar": "قيد الانتظار"},
    "open": {"en": "Open", "ar": "فتح"},
    "play": {"en": "Play", "ar": "تشغيل"},
    "download": {"en": "Download", "ar": "تنزيل"},
    "details": {"en": "Details", "ar": "التفاصيل"},
    "move_to": {"en": "Move to…", "ar": "نقل إلى…"},
    "archive": {"en": "Archive", "ar": "أرشفة"},
    "unarchive": {"en": "Unarchive", "ar": "إلغاء الأرشفة"},
    "delete": {"en": "Delete", "ar": "حذف"},
    "file_details_title": {"en": "File details", "ar": "تفاصيل الملف"},
    "close": {"en": "Close", "ar": "إغلاق"},
    "got_it": {"en": "Got it", "ar": "حسناً"},
    "help_title": {"en": "How QR Vault works", "ar": "كيف يعمل QR Vault"},
    "owner": {"en": "owner", "ar": "مالك"},
    "note": {"en": "Note", "ar": "ملاحظة"},
    "offline_mode": {"en": "Offline mode", "ar": "وضع دون اتصال"},
    "sync": {"en": "Sync", "ar": "مزامنة"},
    "preparing_camera": {"en": "Preparing camera…", "ar": "جارٍ تجهيز الكاميرا…"},
    "point_camera": {"en": "Point camera at a QR code", "ar": "وجّه الكاميرا إلى رمز QR"},
    "paste_qr": {"en": "Paste or type the QR payload", "ar": "الصق أو اكتب قيمة QR"},
    "rename_storage": {"en": "Rename storage", "ar": "إعادة تسمية الخزنة"},
    "storage_meta": {
        "en": "QR {qr} · {perm} · owner {phone}",
        "ar": "QR {qr} · {perm} · المالك {phone}",
    },
    "storage_fallback": {"en": "Storage {qr}", "ar": "خزنة {qr}"},
    "perm_owner": {"en": "owner", "ar": "مالك"},
    "perm_manage": {"en": "manage", "ar": "إدارة"},
    "perm_write": {"en": "write", "ar": "كتابة"},
    "perm_read": {"en": "read", "ar": "قراءة"},
    "badge_owner": {"en": "OWNER", "ar": "مالك"},
    "badge_manage": {"en": "MANAGE", "ar": "إدارة"},
    "badge_write": {"en": "WRITE", "ar": "كتابة"},
    "badge_read": {"en": "READ", "ar": "قراءة"},
    "sync_pending": {
        "en": "{n} item(s) waiting to sync",
        "ar": "{n} عنصر بانتظار المزامنة",
    },
    "offline_mode_body": {
        "en": "Showing cached files & notes. New files/notes queue until you're back online.",
        "ar": "عرض الملفات والملاحظات المحفوظة. العناصر الجديدة تُحفظ حتى يعود الاتصال.",
    },
    "sync_queue_body": {
        "en": "Files and notes will upload automatically when the connection returns.",
        "ar": "ستُرفع الملفات والملاحظات تلقائياً عند عودة الاتصال.",
    },
    "sync_badge": {"en": "SYNC {n}", "ar": "مزامنة {n}"},
    "drag_reorder": {"en": "Drag to reorder", "ar": "اسحب لإعادة الترتيب"},
    "edit": {"en": "Edit", "ar": "تعديل"},
    "share_requests": {"en": "Share requests", "ar": "طلبات المشاركة"},
    "share_accept_hint": {
        "en": "Accept to see this vault under Shared.",
        "ar": "اقبل ليظهر هذا الخزن ضمن المشارَك.",
    },
    "archived_files": {"en": "Archived files", "ar": "الملفات المؤرشفة"},
    "share_storage": {"en": "Share storage", "ar": "مشاركة الخزنة"},
    "files_count": {"en": "{n} files", "ar": "{n} ملف"},
    "list_view": {"en": "List view", "ar": "عرض قائمة"},
    "icons_view": {"en": "Icons view", "ar": "عرض أيقونات"},
    "cancel": {"en": "Cancel", "ar": "إلغاء"},
    "save": {"en": "Save", "ar": "حفظ"},
    "info": {"en": "Info", "ar": "معلومة"},
    "open_with_app": {"en": "Open with app", "ar": "فتح بتطبيق"},
    "pdf_mobile_hint": {
        "en": "Could not load PDF pages from the server. Open with a PDF app, or download a copy.",
        "ar": "تعذّر تحميل صفحات PDF من السيرفر. افتحها بتطبيق PDF أو نزّل نسخة.",
    },
    "pdf_browse": {"en": "Browse pages", "ar": "تصفح الصفحات"},
    "pdf_official": {"en": "PDF.js viewer", "ar": "عارض PDF.js"},
    "pdf_open_viewer": {"en": "Open PDF viewer", "ar": "فتح عارض PDF"},
    "pdf_tap_to_open": {
        "en": "Open the standard PDF viewer (search, zoom, highlight).",
        "ar": "افتح عارض PDF القياسي (بحث، تكبير، تظليل).",
    },
    "pdf_opened_external": {
        "en": "PDF opened in the browser viewer.",
        "ar": "تم فتح PDF في عارض المتصفح.",
    },
    "pdf_external_hint": {
        "en": "Use the browser toolbar for search, zoom, and page navigation. Keep this app open.",
        "ar": "استخدم شريط أدوات المتصفح للبحث والتكبير والتنقل. أبقِ التطبيق مفتوحاً.",
    },
    "pdf_reopen": {"en": "Reopen viewer", "ar": "إعادة فتح العارض"},
    "pdf_open_failed": {
        "en": "Could not open PDF. Try Download from the menu.",
        "ar": "تعذّر فتح ملف PDF. جرّب التنزيل من القائمة.",
    },
    "pdf_render_error": {"en": "PDF render error: {error}", "ar": "خطأ عرض PDF: {error}"},
    "help_p1": {
        "en": "Scan a QR code to open or create a storage vault.",
        "ar": "امسح رمز QR لفتح خزنة أو إنشائها.",
    },
    "help_p2": {
        "en": "Permissions: read (view/download), write (upload/archive/delete), manage (share).",
        "ar": "الصلاحيات: قراءة (عرض/تنزيل)، كتابة (رفع/أرشفة/حذف)، إدارة (مشاركة).",
    },
    "help_p3": {
        "en": "Sharing sends a request. The other user must Accept on Home before the vault appears under Shared.",
        "ar": "المشاركة ترسل طلباً. يجب أن يقبل الطرف الآخر من الرئيسية قبل ظهور الخزنة ضمن المشارَك.",
    },
    "help_p4": {
        "en": "Long-press an item to reorder vaults on Home, and files/notes inside a storage (All + list view).",
        "ar": "اضغط مطولاً على عنصر لإعادة ترتيب الخزائن في الرئيسية، والملفات/الملاحظات داخل الخزنة (الكل + عرض قائمة).",
    },
    "help_p5": {
        "en": "Add Note places a rich note in the list with files. Edit bold/colors/sizes; filter with the Notes chip.",
        "ar": "إضافة ملاحظة تضع ملاحظة غنية مع الملفات. عدّل الخط/الألوان/الحجم؛ صفِّ عبر شريحة الملاحظات.",
    },
    "help_p6": {
        "en": "Archive hides a file from the main list. Open Archived to restore or permanently delete it.",
        "ar": "الأرشفة تخفي الملف من القائمة. افتح الأرشيف للاستعادة أو الحذف النهائي.",
    },
    "help_p7": {
        "en": "Retention: every file is permanently deleted 30 days after upload (even if archived). Download anything you need to keep.",
        "ar": "الاحتفاظ: يُحذف كل ملف نهائياً بعد 30 يوماً من الرفع (حتى المؤرشف). نزّل ما تريد الاحتفاظ به.",
    },
    "help_p8": {
        "en": "Tap a file to preview/play it. On phone, PDFs open with your PDF app.",
        "ar": "اضغط ملفاً للمعاينة/التشغيل. على الهاتف تُفتح ملفات PDF عبر تطبيق PDF لديك.",
    },
    "help_p9": {
        "en": "Browse modes: List (preview under the row) or Icons (full-screen open).",
        "ar": "أوضاع التصفح: قائمة (معاينة تحت الصف) أو أيقونات (فتح بملء الشاشة).",
    },
    "help_p10": {
        "en": "Merge PDF: combine selected PDFs and/or images into one PDF. Optionally archive the source files after merge.",
        "ar": "دمج PDF: اجمع ملفات PDF و/أو صوراً في ملف واحد. يمكن أرشفة المصادر بعد الدمج.",
    },
}


def normalize_lang(lang: str | None) -> str:
    return LANG_AR if (lang or "").lower().startswith("ar") else LANG_EN


def t(lang: str | None, key: str, **kwargs: Any) -> str:
    lang = normalize_lang(lang)
    table = _STRINGS.get(key) or {}
    text = table.get(lang) or table.get(LANG_EN) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
