"""In-memory menu cart and order QR helpers."""

from __future__ import annotations

import base64
import io
import re
from typing import Any


def parse_price_number(price: Any) -> float:
    raw = str(price or "").strip()
    if not raw:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", "."))
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class MenuCart:
    """Session cart for one restaurant menu storage."""

    def __init__(self):
        self._storage_id: int | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._order_note: str = ""

    def bind_storage(self, storage_id: int | None) -> None:
        if storage_id != self._storage_id:
            self._storage_id = storage_id
            self._items.clear()
            self._order_note = ""

    def count(self) -> int:
        return sum(int(item.get("qty") or 0) for item in self._items.values())

    def is_empty(self) -> bool:
        return not self._items

    def get_qty(self, product_id: str) -> int:
        return int(self._items.get(product_id, {}).get("qty") or 0)

    def get_item_note(self, product_id: str) -> str:
        return str(self._items.get(product_id, {}).get("note") or "").strip()

    def set_item_note(self, product_id: str, note: str) -> None:
        entry = self._items.get(product_id)
        if not entry:
            return
        entry["note"] = (note or "").strip()

    def get_order_note(self) -> str:
        return self._order_note.strip()

    def set_order_note(self, note: str) -> None:
        self._order_note = (note or "").strip()

    def set_qty(self, product_id: str, qty: int, *, meta: dict[str, Any] | None = None) -> None:
        if qty <= 0:
            self._items.pop(product_id, None)
            return
        entry = dict(self._items.get(product_id) or {})
        if meta:
            entry.update(meta)
        entry["product_id"] = product_id
        entry["qty"] = int(qty)
        if "note" not in entry:
            entry["note"] = ""
        self._items[product_id] = entry

    def add_one(self, product_id: str, *, meta: dict[str, Any]) -> None:
        self.set_qty(product_id, self.get_qty(product_id) + 1, meta=meta)

    def change_qty(self, product_id: str, delta: int) -> None:
        entry = self._items.get(product_id)
        if not entry:
            return
        self.set_qty(product_id, int(entry.get("qty") or 0) + delta)

    def clear(self) -> None:
        self._items.clear()
        self._order_note = ""

    def lines(self) -> list[dict[str, Any]]:
        return list(self._items.values())

    def total_numeric(self) -> float:
        total = 0.0
        for item in self._items.values():
            total += parse_price_number(item.get("price")) * int(item.get("qty") or 0)
        return total


def format_line_calc(item: dict, *, formatted_total: str = "") -> str:
    """Compact price line e.g. 200×3 = 600 ل.س"""
    qty = max(0, int(item.get("qty") or 0))
    unit = parse_price_number(item.get("price"))
    unit_num = int(unit) if unit == int(unit) else f"{unit:g}"
    if formatted_total:
        total_str = formatted_total
    else:
        line_total = unit * qty
        total_str = f"{int(line_total)}" if line_total == int(line_total) else f"{line_total:g}"
    return f"{unit_num}×{qty} = {total_str}"


def format_order_text(
    *,
    restaurant: str,
    items: list[dict[str, Any]],
    total_label: str,
    formatted_total: str,
    order_note: str = "",
    item_note_label: str = "Note",
    order_note_label: str = "Order note",
) -> str:
    """Plain-text order payload encoded inside the order QR."""
    rest = (restaurant or "").strip() or "Menu"
    lines = [
        "━━━━━━━━ ORDER ━━━━━━━━",
        rest,
        "",
    ]
    for item in items:
        name = (item.get("name") or "Item").strip()
        line_total_raw = parse_price_number(item.get("price")) * int(item.get("qty") or 0)
        line_total_fmt = (item.get("line_total_display") or "").strip()
        if not line_total_fmt and line_total_raw:
            line_total_fmt = f"{int(line_total_raw)}" if line_total_raw == int(line_total_raw) else f"{line_total_raw:g}"
        calc = format_line_calc(item, formatted_total=line_total_fmt)
        lines.append(f"• {name}    {calc}")
        note = (item.get("note") or "").strip()
        if note:
            lines.append(f"  ↳ {item_note_label}: {note}")

    order_note_text = (order_note or "").strip()
    if order_note_text:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"📝 {order_note_label}: {order_note_text}")

    lines.extend(["", "────────────────────────", f"{total_label}: {formatted_total}", "────────────────────────"])
    return "\n".join(lines).strip()


def order_qr_base64(text: str, *, box_size: int = 8) -> str:
    """Return base64 PNG for embedding in ft.Image."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=3,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0B1220", back_color="#FFFFFF")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
