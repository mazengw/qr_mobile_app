"""Convert Office files (docx / xlsx) to simple HTML using stdlib only.

No third-party packages — safe for Flet Android/iOS APK builds that install
wheels from pypi.flet.dev with --only-binary.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_OFFICE_BYTES = 40 * 1024 * 1024
MAX_XLSX_ROWS = 500
MAX_XLSX_COLS = 40
MAX_XLSX_SHEETS = 8

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def is_docx_name(name: str) -> bool:
    return Path(name or "").suffix.lower() == ".docx"


def is_xlsx_name(name: str) -> bool:
    return Path(name or "").suffix.lower() == ".xlsx"


def can_preview_office(path: Path, name: str = "") -> bool:
    label = name or path.name
    if not (is_docx_name(label) or is_xlsx_name(label)):
        return False
    try:
        return path.is_file() and 0 < path.stat().st_size <= MAX_OFFICE_BYTES
    except OSError:
        return False


def office_to_html(path: Path, name: str = "") -> str:
    label = name or path.name
    if is_docx_name(label):
        body = _docx_body_html(path)
        title = html.escape(label)
        return _wrap_html(title, body, kind="docx")
    if is_xlsx_name(label):
        body = _xlsx_body_html(path)
        title = html.escape(label)
        return _wrap_html(title, body, kind="xlsx")
    raise ValueError(f"Unsupported office type: {label}")


def _wrap_html(title: str, body: str, *, kind: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5"/>
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  html, body {{ margin: 0; padding: 0; background: #0B1220; color: #E8EEF8;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  .bar {{ position: sticky; top: 0; z-index: 2; padding: 10px 14px;
    background: #121A2B; border-bottom: 1px solid #243044; font-size: 13px;
    color: #9DB0C9; }}
  .wrap {{ padding: 14px 16px 40px; max-width: 960px; margin: 0 auto; }}
  h1, h2, h3 {{ color: #F3F7FF; }}
  p {{ line-height: 1.55; margin: 0.55em 0; white-space: pre-wrap; word-break: break-word; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }}
  th, td {{ border: 1px solid #2A3A52; padding: 6px 8px; vertical-align: top; }}
  th {{ background: #1A2438; color: #B7C7DC; text-align: left; }}
  .sheet {{ margin: 18px 0 28px; }}
  .sheet h2 {{ font-size: 16px; margin: 0 0 8px; color: #5EEAD4; }}
  .muted {{ color: #8092A8; font-size: 12px; }}
  .empty {{ padding: 24px; text-align: center; color: #8092A8; }}
</style>
</head>
<body>
  <div class="bar">{kind.upper()} preview · {title}</div>
  <div class="wrap">
{body}
  </div>
</body>
</html>
"""


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _docx_body_html(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        try:
            raw = zf.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("Invalid DOCX (missing word/document.xml)") from exc

    root = ET.fromstring(raw)
    parts: list[str] = []
    body = root.find(f"{_W_NS}body")
    if body is None:
        # Namespace-agnostic fallback
        for el in root.iter():
            if _local(el.tag) == "body":
                body = el
                break
    if body is None:
        return '<p class="empty">Empty document</p>'

    for child in list(body):
        local = _local(child.tag)
        if local == "p":
            text = _docx_para_text(child)
            if text.strip():
                parts.append(f"<p>{html.escape(text)}</p>")
            else:
                parts.append("<p>&nbsp;</p>")
        elif local == "tbl":
            parts.append(_docx_table_html(child))

    if not parts:
        return '<p class="empty">Empty document</p>'
    return "\n".join(parts)


def _docx_para_text(p_el: ET.Element) -> str:
    chunks: list[str] = []
    for node in p_el.iter():
        if _local(node.tag) == "t" and node.text:
            chunks.append(node.text)
        elif _local(node.tag) == "tab":
            chunks.append("\t")
        elif _local(node.tag) in {"br", "cr"}:
            chunks.append("\n")
    return "".join(chunks)


def _docx_table_html(tbl: ET.Element) -> str:
    rows_html: list[str] = []
    for tr in tbl.iter():
        if _local(tr.tag) != "tr":
            continue
        # Only direct-ish rows: skip nested by checking parent chain lightly
        cells: list[str] = []
        for tc in list(tr):
            if _local(tc.tag) != "tc":
                continue
            cell_text = []
            for p in tc.iter():
                if _local(p.tag) == "p":
                    cell_text.append(_docx_para_text(p))
            text = "\n".join(t for t in cell_text if t).strip()
            cells.append(f"<td>{html.escape(text)}</td>")
        if cells:
            rows_html.append("<tr>" + "".join(cells) + "</tr>")
    if not rows_html:
        return ""
    return "<table>" + "".join(rows_html) + "</table>"


def _col_letters_to_index(cell_ref: str) -> int:
    m = re.match(r"^([A-Za-z]+)", cell_ref or "")
    if not m:
        return 0
    letters = m.group(1).upper()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return max(0, n - 1)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out: list[str] = []
    for si in root:
        if _local(si.tag) != "si":
            continue
        texts: list[str] = []
        for node in si.iter():
            if _local(node.tag) == "t" and node.text is not None:
                texts.append(node.text)
        out.append("".join(texts))
    return out


def _xlsx_sheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Return list of (sheet_name, zip_path)."""
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError as exc:
        raise ValueError("Invalid XLSX (missing xl/workbook.xml)") from exc

    rels: dict[str, str] = {}
    try:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            if _local(rel.tag) != "Relationship":
                continue
            rid = rel.attrib.get("Id") or ""
            target = rel.attrib.get("Target") or ""
            if rid and target:
                if not target.startswith("xl/") and not target.startswith("/"):
                    target = "xl/" + target.lstrip("/")
                elif target.startswith("/"):
                    target = target.lstrip("/")
                rels[rid] = target
    except KeyError:
        pass

    sheets: list[tuple[str, str]] = []
    for el in wb.iter():
        if _local(el.tag) != "sheet":
            continue
        name = el.attrib.get("name") or f"Sheet{len(sheets) + 1}"
        rid = el.attrib.get(f"{_R_NS}id") or el.attrib.get("r:id") or el.attrib.get("id")
        target = rels.get(rid or "", "")
        if not target:
            # Fallback common paths
            target = f"xl/worksheets/sheet{len(sheets) + 1}.xml"
        sheets.append((name, target))
        if len(sheets) >= MAX_XLSX_SHEETS:
            break
    if not sheets:
        # Last resort: any worksheet files
        for info in zf.namelist():
            if info.startswith("xl/worksheets/sheet") and info.endswith(".xml"):
                sheets.append((Path(info).stem, info))
                if len(sheets) >= MAX_XLSX_SHEETS:
                    break
    return sheets


def _xlsx_cell_value(c_el: ET.Element, shared: list[str]) -> str:
    cell_type = c_el.attrib.get("t", "")
    v_el = None
    is_el = None
    for child in list(c_el):
        loc = _local(child.tag)
        if loc == "v":
            v_el = child
        elif loc == "is":
            is_el = child
    if cell_type == "s" and v_el is not None and v_el.text is not None:
        try:
            idx = int(v_el.text)
            return shared[idx] if 0 <= idx < len(shared) else v_el.text
        except ValueError:
            return v_el.text or ""
    if cell_type == "inlineStr" and is_el is not None:
        texts = []
        for node in is_el.iter():
            if _local(node.tag) == "t" and node.text is not None:
                texts.append(node.text)
        return "".join(texts)
    if v_el is not None and v_el.text is not None:
        return v_el.text
    return ""


def _xlsx_sheet_html(zf: zipfile.ZipFile, sheet_path: str, shared: list[str]) -> str:
    try:
        raw = zf.read(sheet_path)
    except KeyError:
        return '<p class="muted">Sheet missing</p>'
    root = ET.fromstring(raw)
    # Map (row, col) -> value
    grid: dict[tuple[int, int], str] = {}
    max_r = 0
    max_c = 0
    for c_el in root.iter():
        if _local(c_el.tag) != "c":
            continue
        ref = c_el.attrib.get("r") or ""
        # parent row
        row_el = None
        # Use row index from ref or parent
        m = re.match(r"^[A-Za-z]+(\d+)$", ref)
        if m:
            r_idx = int(m.group(1)) - 1
            c_idx = _col_letters_to_index(ref)
        else:
            continue
        if r_idx >= MAX_XLSX_ROWS or c_idx >= MAX_XLSX_COLS:
            continue
        val = _xlsx_cell_value(c_el, shared)
        if val == "":
            continue
        grid[(r_idx, c_idx)] = val
        max_r = max(max_r, r_idx)
        max_c = max(max_c, c_idx)

    if not grid:
        return '<p class="muted">Empty sheet</p>'

    rows: list[str] = []
    for r in range(0, max_r + 1):
        cells = []
        for c in range(0, max_c + 1):
            cells.append(f"<td>{html.escape(grid.get((r, c), ''))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    note = ""
    if max_r + 1 >= MAX_XLSX_ROWS or max_c + 1 >= MAX_XLSX_COLS:
        note = f'<p class="muted">Preview truncated to {MAX_XLSX_ROWS} rows × {MAX_XLSX_COLS} cols.</p>'
    return note + "<table>" + "".join(rows) + "</table>"


def _xlsx_body_html(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheets = _xlsx_sheet_targets(zf)
        if not sheets:
            return '<p class="empty">No worksheets found</p>'
        parts: list[str] = []
        for name, target in sheets:
            parts.append('<section class="sheet">')
            parts.append(f"<h2>{html.escape(name)}</h2>")
            parts.append(_xlsx_sheet_html(zf, target, shared))
            parts.append("</section>")
        return "\n".join(parts)
