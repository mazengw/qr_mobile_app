"""Digital menu storage helpers (restaurant menu customization)."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

COLOR_PRESETS: list[tuple[str, str]] = [
    ("#0D9488", "#0F172A"),
    ("#6594FF", "#FFFFFF"),
    ("#94A3B8", "#0F172A"),
    ("#38BDF8", "#0B1220"),
    ("#A855F7", "#FFFFFF"),
    ("#22C55E", "#052E16"),
    ("#EAB308", "#FFFFFF"),
    ("#F43F5E", "#0F172A"),
]

ALLERGENS: list[tuple[str, str]] = [
    ("grain", "Grain"),
    ("crustaceans", "Crustaceans"),
    ("fish", "Fish"),
    ("eggs", "Eggs"),
    ("peanuts", "Peanuts"),
    ("soy", "Soy"),
    ("milk", "Milk"),
    ("nuts", "Nuts"),
    ("celery", "Celery"),
    ("mustard", "Mustard"),
    ("sesame", "Sesame"),
    ("sulfites", "Sulfites"),
    ("lupin", "Lupin"),
    ("molluscs", "Shellfish"),
]


SOCIAL_NETWORKS: list[tuple[str, str, str]] = [
    ("instagram", "Instagram", "https://instagram.com/"),
    ("facebook", "Facebook", "https://facebook.com/"),
    ("tiktok", "TikTok", "https://www.tiktok.com/@"),
    ("twitter", "X / Twitter", "https://x.com/"),
    ("snapchat", "Snapchat", "https://www.snapchat.com/add/"),
    ("website", "Website", ""),
]

CURRENCY_OPTIONS: list[tuple[str, str]] = [
    ("SYP", "ل.س"),
    ("USD", "$"),
    ("EUR", "€"),
    ("TRY", "₺"),
    ("SAR", "ر.س"),
    ("AED", "د.إ"),
    ("LBP", "ل.ل"),
]


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def normalize_menu_data(raw: Any, *, restaurant_name: str = "", description: str = "") -> dict:
    data = raw if isinstance(raw, dict) else {}
    sections_in = data.get("sections") if isinstance(data.get("sections"), list) else []
    sections: list[dict] = []
    for sec in sections_in:
        if not isinstance(sec, dict):
            continue
        products_in = sec.get("products") if isinstance(sec.get("products"), list) else []
        products: list[dict] = []
        for prod in products_in:
            if not isinstance(prod, dict):
                continue
            allergens = prod.get("allergens") if isinstance(prod.get("allergens"), list) else []
            products.append(
                {
                    "id": str(prod.get("id") or new_id()),
                    "name": str(prod.get("name") or ""),
                    "description": str(prod.get("description") or ""),
                    "price": str(prod.get("price") or ""),
                    "image_file_id": _optional_file_id(prod.get("image_file_id")),
                    "visible": bool(prod.get("visible", True)),
                    "allergens": [str(a) for a in allergens],
                }
            )
        sections.append(
            {
                "id": str(sec.get("id") or new_id()),
                "name": str(sec.get("name") or ""),
                "description": str(sec.get("description") or ""),
                "visible": bool(sec.get("visible", True)),
                "products": products,
            }
        )
    gallery = _file_id_list(data.get("gallery_file_ids"))
    cover = _optional_file_id(data.get("cover_file_id"))
    if cover and cover not in gallery:
        gallery.insert(0, cover)
    social_in = data.get("social") if isinstance(data.get("social"), dict) else {}
    social = {
        key: str(social_in.get(key) or data.get(key) or "").strip()
        for key, _label, _prefix in SOCIAL_NETWORKS
    }
    return {
        "restaurant_name": str(data.get("restaurant_name") or restaurant_name or ""),
        "description": str(data.get("description") or description or ""),
        "logo_file_id": _optional_file_id(data.get("logo_file_id")),
        "cover_file_id": gallery[0] if gallery else cover,
        "gallery_file_ids": gallery,
        "address": str(data.get("address") or "").strip(),
        "phone": str(data.get("phone") or "").strip(),
        "whatsapp": str(data.get("whatsapp") or "").strip(),
        "email": str(data.get("email") or "").strip(),
        "hours": format_hours_range(data.get("hours_from"), data.get("hours_to"), fallback=str(data.get("hours") or "").strip()),
        "hours_from": normalize_ampm_time(data.get("hours_from"), fallback=data.get("hours"), side="from"),
        "hours_to": normalize_ampm_time(data.get("hours_to"), fallback=data.get("hours"), side="to"),
        "maps_url": str(data.get("maps_url") or "").strip(),
        "lat": _optional_coord(data.get("lat")),
        "lng": _optional_coord(data.get("lng")),
        "currency": _normalize_currency(data.get("currency")),
        "social": social,
        "primary_color": str(data.get("primary_color") or "#0D9488"),
        "secondary_color": str(data.get("secondary_color") or "#0F172A"),
        "sections": sections,
    }


def _optional_file_id(value: Any) -> int | None:
    if value in (None, "", False, 0, "0"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _file_id_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        fid = _optional_file_id(item)
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


def social_href(kind: str, value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "mailto:", "tel:")):
        return raw
    prefixes = {key: prefix for key, _label, prefix in SOCIAL_NETWORKS}
    prefix = prefixes.get(kind, "")
    handle = raw.lstrip("@")
    if kind == "website":
        return f"https://{handle}"
    if prefix:
        return f"{prefix}{handle}"
    return raw


def _normalize_currency(value: Any) -> str:
    code = str(value or "SYP").strip().upper()
    allowed = {item[0] for item in CURRENCY_OPTIONS}
    return code if code in allowed else "SYP"


def currency_symbol(code: str) -> str:
    wanted = _normalize_currency(code)
    for item in CURRENCY_OPTIONS:
        if item[0] == wanted:
            return item[1]
    return wanted


def format_menu_price(price: Any, currency: str = "SYP") -> str:
    raw = str(price or "").strip()
    if not raw:
        return ""
    code = _normalize_currency(currency)
    symbol = currency_symbol(code)
    upper = raw.upper()
    if symbol and (raw.endswith(symbol) or raw.startswith(symbol) or code in upper):
        return raw
    if code in {"USD", "EUR"}:
        return f"{symbol}{raw}"
    return f"{raw} {symbol}"


def _optional_coord(value: Any) -> float | None:
    if value in (None, "", False):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def normalize_ampm_time(value: Any, *, fallback: str = "", side: str = "from") -> dict:
    if isinstance(value, dict):
        try:
            hour = int(value.get("h") or value.get("hour") or 0)
            minute = int(value.get("m") or value.get("minute") or 0)
        except (TypeError, ValueError):
            hour, minute = 0, 0
        ampm = str(value.get("ampm") or "AM").upper()
        if hour:
            return {
                "h": min(12, max(1, hour)),
                "m": min(59, max(0, minute)),
                "ampm": "PM" if ampm == "PM" else "AM",
            }
    parsed = _parse_hours_fallback(str(fallback or ""), side=side)
    return parsed or {}


def _parse_hours_fallback(text: str, *, side: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None

    parts = re.split(r"\s*(?:-|–|to)\s*", raw, maxsplit=1, flags=re.IGNORECASE)
    chunk = parts[0] if side == "from" else (parts[1] if len(parts) > 1 else parts[0])
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", chunk, flags=re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or ("AM" if hour < 12 else "PM")).upper()
    if hour == 0:
        hour, ampm = 12, "AM"
    elif hour > 12:
        hour -= 12
        ampm = "PM"
    return {"h": hour, "m": minute, "ampm": "PM" if ampm == "PM" else "AM"}


def format_ampm_time(value: Any) -> str:
    t = value if isinstance(value, dict) else None
    if not t:
        return ""
    try:
        hour = int(t.get("h") or 0)
        minute = int(t.get("m") or 0)
    except (TypeError, ValueError):
        return ""
    if not hour:
        return ""
    ampm = "PM" if str(t.get("ampm") or "AM").upper() == "PM" else "AM"
    return f"{hour}:{minute:02d} {ampm}"


def format_hours_range(hours_from: Any, hours_to: Any, *, fallback: str = "") -> str:
    start = format_ampm_time(hours_from)
    end = format_ampm_time(hours_to)
    if start and end:
        return f"{start} – {end}"
    return (fallback or "").strip()


def maps_href(*, address: str = "", maps_url: str = "", lat: float | None = None, lng: float | None = None) -> str:
    url = (maps_url or "").strip()
    if url:
        return url if url.startswith(("http://", "https://")) else f"https://{url}"
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    text = (address or "").strip()
    if not text:
        return ""
    from urllib.parse import quote_plus

    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(text)}"


def tel_href(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+")
    return f"tel:{digits}" if digits else ""


def whatsapp_href(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return f"https://wa.me/{digits}" if digits else ""


def mailto_href(value: str) -> str:
    email = (value or "").strip()
    return f"mailto:{email}" if email and "@" in email else ""


def empty_section(name: str = "") -> dict:
    return {
        "id": new_id(),
        "name": name or "Section",
        "description": "",
        "visible": True,
        "products": [empty_product()],
    }


def empty_product(name: str = "") -> dict:
    return {
        "id": new_id(),
        "name": name or "",
        "description": "",
        "price": "",
        "image_file_id": None,
        "visible": True,
        "allergens": [],
    }


def clone_menu(data: dict) -> dict:
    return copy.deepcopy(normalize_menu_data(data))
