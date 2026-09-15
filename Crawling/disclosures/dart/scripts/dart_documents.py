"""Offline, dependency-free parsing of OpenDART original-document ZIP files.

All offsets refer to Unicode characters in ``combined_text``, not UTF-8 bytes.
Tables retain their original cell anchors/spans and a rectangular display grid.
This module neither makes network requests nor extracts archive paths to disk.
"""

from __future__ import annotations

import codecs
import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PARSER_VERSION = "dart-document-1.0.0"
_TEXT_EXTENSIONS = {".xml", ".html", ".htm", ".xhtml", ".txt"}
_VOID_TAGS = {"br", "hr", "img", "image", "col", "meta", "link", "input", "pgbrk", "pbr"}
_HIDDEN_TAGS = {"script", "style"}
_CELL_TAGS = {"td", "th", "te", "tu"}
_BLOCK_TAGS = {
    "p", "div", "title", "subtitle", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "section-1", "section-2", "section-3", "section-4", "li", "ul",
    "ol", "table", "table-group", "document-name", "company-name", "preamble",
}
_MAX_FILE_BYTES = 100 * 1024 * 1024
_MAX_TOTAL_BYTES = 300 * 1024 * 1024


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)


class _DocumentParser(HTMLParser):
    """Tolerant markup reader; declarations and external entities are never loaded.

HTMLParser preserves custom DART tags (including uppercase TITLE, TE, and TU)
without HTML DOM repair that could otherwise move or discard those elements.
"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("_root")
        self.stack = [self.root]
        self.tables: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower().split(":")[-1]
        node = _Node(tag, {key.lower(): value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag == "table":
            self.tables.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower().split(":")[-1]
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _decode_document(raw: bytes) -> tuple[str, str, str | None, list[str]]:
    header = raw[:2048]
    declaration = re.search(rb"encoding\s*=\s*['\"]([^'\"]+)['\"]", header, re.I)
    if declaration is None:
        declaration = re.search(rb"charset\s*=\s*['\"]?([A-Za-z0-9_-]+)", header, re.I)
    declared = declaration.group(1).decode("ascii", errors="replace") if declaration else None
    candidates = []
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        candidates.append("utf-32")
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates.append("utf-16")
    elif raw.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    if declared:
        candidates.append(declared)
    candidates.extend(["utf-8-sig", "cp949", "euc-kr"])
    seen = set()
    for candidate in candidates:
        try:
            canonical = codecs.lookup(candidate).name
            if canonical in seen:
                continue
            seen.add(canonical)
            decoded = raw.decode(candidate, errors="strict")
            warnings = []
            if declared:
                try:
                    if codecs.lookup(declared).name != canonical:
                        warnings.append("declared_encoding_fallback")
                except LookupError:
                    warnings.append("unknown_declared_encoding")
            return decoded, canonical, declared, warnings
        except (UnicodeDecodeError, LookupError):
            continue
    # Preserve undecodable locations explicitly rather than silently losing bytes.
    encoding, decoded = min(
        ((enc, raw.decode(enc, errors="replace")) for enc in ("utf-8", "cp949")),
        key=lambda item: item[1].count("\ufffd"),
    )
    return decoded, encoding, declared, ["replacement_decoding"]


def _normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    value = "\n".join(re.sub(r"[^\S\n\t]+", " ", line).strip(" \t") for line in value.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _node_text(node: _Node) -> str:
    fragments: list[str] = []

    def visit(part: _Node | str) -> None:
        if isinstance(part, str):
            fragments.append(part)
            return
        if part.tag in _HIDDEN_TAGS:
            return
        if part.tag in {"br", "pgbrk", "pbr", "hr"}:
            fragments.append("\n")
            return
        if part.tag in _BLOCK_TAGS:
            fragments.append("\n\n")
        for child in part.children:
            visit(child)
        if part.tag in _CELL_TAGS:
            fragments.append("\t")
        elif part.tag == "tr":
            fragments.append("\n")
        elif part.tag in _BLOCK_TAGS:
            fragments.append("\n\n")

    visit(node)
    return _normalize_text("".join(fragments))


def _table_rows(table: _Node) -> list[_Node]:
    rows = []

    def visit(node: _Node) -> None:
        for child in node.children:
            if not isinstance(child, _Node) or child.tag == "table":
                continue
            if child.tag == "tr":
                rows.append(child)
            else:
                visit(child)

    visit(table)
    return rows


def _span(value: str | None) -> int:
    try:
        return max(1, min(1000, int(value or "1")))
    except ValueError:
        return 1


def _parse_table(table: _Node, index: int) -> dict[str, Any]:
    source_rows = _table_rows(table)
    occupied: dict[tuple[int, int], str] = {}
    cells = []
    max_column = 0
    for row_index, row in enumerate(source_rows):
        column = 0
        for cell in row.children:
            if not isinstance(cell, _Node) or cell.tag not in _CELL_TAGS:
                continue
            while (row_index, column) in occupied:
                column += 1
            rowspan = min(_span(cell.attrs.get("rowspan")), len(source_rows) - row_index)
            colspan = _span(cell.attrs.get("colspan"))
            value = _node_text(cell).strip()
            cells.append({
                "row": row_index, "column": column, "rowspan": rowspan,
                "colspan": colspan, "text": value, "is_header": cell.tag == "th",
                "tag": cell.tag,
            })
            for next_row in range(row_index, row_index + rowspan):
                for next_column in range(column, column + colspan):
                    occupied[(next_row, next_column)] = value
            column += colspan
            max_column = max(max_column, column)
    return {
        "table_index": index,
        "rows": [[occupied.get((row, column), "") for column in range(max_column)] for row in range(len(source_rows))],
        "cells": cells,
        "row_count": len(source_rows),
        "column_count": max_column,
        "span_policy": "grid_repeats_anchor_text; cells_preserve_original_anchors",
    }


def parse_document_zip(zip_path: str | Path) -> dict[str, Any]:
    """Parse every XML/HTML/text entry, retaining per-file boundaries and quality.

    Invalid ZIPs raise ``zipfile.BadZipFile``. Oversized archives raise ValueError.
    Individual malformed markup entries are recorded in ``errors`` so remaining
    attachments remain usable. Relative archive paths are only metadata.
    """
    files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    ignored_files: list[str] = []
    combined: list[str] = []
    offset = 0
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(entry.file_size for entry in entries) > _MAX_TOTAL_BYTES:
            raise ValueError("Document archive exceeds safe expanded size or entry count")
        for entry in entries:
            extension = Path(entry.filename).suffix.lower()
            if entry.is_dir() or extension not in _TEXT_EXTENSIONS:
                if not entry.is_dir():
                    ignored_files.append(entry.filename)
                continue
            if entry.file_size > _MAX_FILE_BYTES:
                raise ValueError("Document archive entry exceeds safe expanded size")
            try:
                raw = archive.read(entry)
                decoded, encoding, declared, warnings = _decode_document(raw)
                if extension == ".txt":
                    text = _normalize_text(decoded)
                    tables = []
                else:
                    parser = _DocumentParser()
                    parser.feed(decoded)
                    parser.close()
                    text = _node_text(parser.root)
                    tables = [_parse_table(table, index) for index, table in enumerate(parser.tables)]
                if combined:
                    offset += 2
                files.append({
                    "filename": entry.filename, "encoding": encoding,
                    "declared_encoding": declared, "source_bytes": len(raw),
                    "text": text, "tables": tables, "warnings": warnings,
                    "start_char": offset, "end_char": offset + len(text),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                })
                combined.append(text)
                offset += len(text)
            except (UnicodeError, ValueError, RecursionError) as exc:
                errors.append({"filename": entry.filename, "error_type": type(exc).__name__, "error": str(exc)[:200]})
    combined_text = "\n\n".join(combined)
    return {
        "parser_version": PARSER_VERSION, "files": files,
        "combined_text": combined_text,
        "text_sha256": hashlib.sha256(combined_text.encode("utf-8")).hexdigest(),
        "quality": {
            "characters": len(combined_text),
            "replacement_characters": combined_text.count("\ufffd"),
            "table_count": sum(len(item["tables"]) for item in files),
            "xml_file_count": sum(Path(item["filename"]).suffix.lower() == ".xml" for item in files),
            "parsed_file_count": len(files), "ignored_file_count": len(ignored_files),
            "failed_file_count": len(errors),
            "empty_file_count": sum(not item["text"] for item in files),
            "hangul_characters": len(re.findall(r"[가-힣]", combined_text)),
        },
        "ignored_files": ignored_files, "errors": errors,
    }
