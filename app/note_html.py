"""Render / wrap a small HTML subset for storage notes (Flet TextSpans)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import flet as ft

from .theme import C

_COLOR_RE = re.compile(r'color\s*:\s*(#[0-9a-fA-F]{3,6})', re.I)
_SIZE_RE = re.compile(r'font-size\s*:\s*(\d{1,2})\s*px', re.I)


class _SpanParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.spans: list[ft.TextSpan] = []
        self._styles: list[dict] = [{}]

    def _cur(self) -> dict:
        return self._styles[-1]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'br':
            self.spans.append(ft.TextSpan('\n'))
            return
        style = dict(self._cur())
        attr_map = {k.lower(): (v or '') for k, v in attrs}
        if tag in ('b', 'strong'):
            style['weight'] = ft.FontWeight.BOLD
        elif tag in ('i', 'em'):
            style['italic'] = True
        elif tag == 'u':
            style['decoration'] = ft.TextDecoration.UNDERLINE
        elif tag in ('h1', 'h2', 'h3'):
            style['weight'] = ft.FontWeight.BOLD
            style['size'] = {'h1': 22, 'h2': 18, 'h3': 16}[tag]
        elif tag == 'span':
            css = attr_map.get('style', '')
            cm = _COLOR_RE.search(css)
            sm = _SIZE_RE.search(css)
            if cm:
                style['color'] = cm.group(1)
            if sm:
                style['size'] = int(sm.group(1))
        elif tag in ('p', 'div'):
            if self.spans and not (self.spans[-1].text or '').endswith('\n'):
                self.spans.append(ft.TextSpan('\n'))
        self._styles.append(style)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'br':
            return
        if len(self._styles) > 1:
            self._styles.pop()
        if tag in ('p', 'div', 'h1', 'h2', 'h3'):
            self.spans.append(ft.TextSpan('\n'))

    def handle_data(self, data):
        if not data:
            return
        st = self._cur()
        self.spans.append(
            ft.TextSpan(
                data,
                style=ft.TextStyle(
                    color=st.get('color', C.text),
                    size=st.get('size', 14),
                    weight=st.get('weight', ft.FontWeight.NORMAL),
                    italic=st.get('italic', False),
                    decoration=st.get('decoration'),
                ),
            )
        )


def note_to_text_control(html: str | None, size: int = 14, max_lines: int | None = None) -> ft.Control:
    raw = (html or '').strip()
    if not raw:
        return ft.Text('Empty note', size=13, color=C.text_muted, italic=True)
    parser = _SpanParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return ft.Text(raw, size=size, color=C.text, max_lines=max_lines, overflow=ft.TextOverflow.ELLIPSIS)
    spans = parser.spans or [ft.TextSpan(raw, style=ft.TextStyle(color=C.text, size=size))]
    return ft.Text(
        spans=spans,
        selectable=max_lines is None,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if max_lines else None,
    )


def note_plain_preview(html: str | None, limit: int = 100) -> str:
    import re

    raw = (html or '').strip()
    if not raw:
        return 'Empty note'
    text = re.sub(r'<br\s*/?>', ' ', raw, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + '…'


def wrap_selection(value: str, start: int, end: int, open_tag: str, close_tag: str) -> str:
    """Wrap selected range; if empty selection, insert tags at cursor."""
    value = value or ''
    start = max(0, min(start, len(value)))
    end = max(start, min(end, len(value)))
    if start == end:
        return value[:start] + open_tag + close_tag + value[end:]
    return value[:start] + open_tag + value[start:end] + close_tag + value[end:]


NOTE_COLORS = [
    ('#F8FAFC', 'White'),
    ('#14B8A6', 'Teal'),
    ('#38BDF8', 'Blue'),
    ('#F59E0B', 'Amber'),
    ('#F43F5E', 'Rose'),
    ('#22C55E', 'Green'),
]

NOTE_SIZES = [12, 14, 18, 22]
