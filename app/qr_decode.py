"""Decode QR payloads from camera JPEG/PNG frames (mobile APK)."""

from __future__ import annotations

import io
from typing import Optional


def decode_qr_payload(image_bytes: bytes | None) -> Optional[str]:
    """Return the first QR payload found in image bytes, or None."""
    if not image_bytes:
        return None
    try:
        from PIL import Image
        from pyzbar.pyzbar import ZBarSymbol, decode
    except Exception:
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("L", "RGB", "RGBA"):
            img = img.convert("RGB")
        results = decode(img, symbols=[ZBarSymbol.QRCODE])
        for item in results:
            text = (item.data or b"").decode("utf-8", errors="replace").strip()
            if text:
                return text
    except Exception:
        return None
    return None
