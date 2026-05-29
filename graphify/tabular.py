"""Heuristics for identifying structured tab-delimited text files."""
from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from graphify.source_text import decode_source_text

_TABULAR_SAMPLE_BYTES = 64 * 1024
_TABULAR_SAMPLE_LINES = 256
_TABULAR_MIN_TAB_RATIO = 0.8
_TABULAR_MIN_STABLE_RATIO = 0.8


def decode_structured_text(raw: bytes, *, replace: bool = False) -> tuple[str | None, str | None]:
    result = decode_source_text(raw, replace=replace)
    return result.text, result.encoding


def _decode_sample(raw: bytes) -> str | None:
    text, _encoding = decode_structured_text(raw)
    return text


def looks_like_tabular_text(path: Path) -> bool:
    """Return True when a .txt file has strong TSV-like structure.

    The heuristic is intentionally conservative so ordinary notes, papers, and
    prose with occasional tabs remain on the document/semantic path.
    """
    if path.suffix.lower() != ".txt":
        return False
    try:
        with path.open("rb") as f:
            raw = f.read(_TABULAR_SAMPLE_BYTES)
    except OSError:
        return False
    if not raw:
        return False
    text = _decode_sample(raw)
    if text is None:
        return False

    non_empty = [line.rstrip("\r") for line in text.splitlines() if line.strip()]
    if not non_empty:
        return False
    sample = non_empty[:_TABULAR_SAMPLE_LINES]
    if "\t" not in sample[0]:
        return False

    tab_lines = [line for line in sample if "\t" in line]
    tab_ratio = len(tab_lines) / len(sample)
    if tab_ratio < _TABULAR_MIN_TAB_RATIO:
        return False

    column_counts = [len(line.split("\t")) for line in tab_lines]
    if not column_counts:
        return False
    mode_columns, mode_count = Counter(column_counts).most_common(1)[0]
    if mode_columns < 2:
        return False
    stable_ratio = mode_count / len(column_counts)
    return stable_ratio >= _TABULAR_MIN_STABLE_RATIO


@dataclass(frozen=True)
class ParsedColumn:
    name: str
    key: str
    column_index: int


@dataclass(frozen=True)
class ParsedRow:
    row_no: int
    raw_line: str
    line_hash: str
    values: tuple[str, ...]
    row_json: dict[str, str]
    row_values_json: list[dict[str, object]]


@dataclass(frozen=True)
class ParsedTabularFile:
    source_sha256: str
    encoding: str
    columns: tuple[ParsedColumn, ...]
    rows: tuple[ParsedRow, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TabularProfile:
    row_count: int
    column_count: int
    file_size: int
    columns: tuple[ParsedColumn, ...]


def decode_structured_text_strict(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16le")
        return text.lstrip("\ufeff"), "utf-16le"
    sample = raw[:4096]
    if sample[1::2].count(0) > max(8, len(sample) // 4):
        text = raw.decode("utf-16le")
        if "\x00" not in text:
            return text.lstrip("\ufeff"), "utf-16le"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("structured-text", raw, 0, min(len(raw), 1), "unsupported encoding")


def _line_hash(source_file: str, row_no: int, raw_line: str) -> str:
    payload = f"{source_file}\0{row_no}\0{raw_line}".encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(payload).hexdigest()


def tabular_source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_columns(raw_headers: list[str]) -> tuple[ParsedColumn, ...]:
    seen: dict[str, int] = {}
    columns: list[ParsedColumn] = []
    for idx, raw_name in enumerate(raw_headers, start=1):
        name = raw_name.strip().strip("\r") or f"column_{idx}"
        count = seen.get(name, 0) + 1
        seen[name] = count
        key = name if count == 1 else f"{name}_{count}"
        columns.append(ParsedColumn(name=name, key=key, column_index=idx))
    return tuple(columns)


def read_tabular_profile(path: Path) -> TabularProfile:
    raw = path.read_bytes()
    text, _encoding = decode_structured_text_strict(raw)
    parsed = csv.reader(StringIO(text), delimiter="\t")
    header = next(parsed, [])
    row_count = sum(1 for _ in parsed)
    columns = _parse_columns(header)
    return TabularProfile(
        row_count=row_count,
        column_count=len(columns),
        file_size=len(raw),
        columns=columns,
    )


def parse_tabular_file(path: Path, *, source_file: str | None = None) -> ParsedTabularFile:
    raw = path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    text, encoding = decode_structured_text_strict(raw)
    source_file = source_file or path.as_posix()
    physical_lines = text.splitlines()
    reader = csv.reader(StringIO(text), delimiter="\t")
    parsed = list(reader)
    if not parsed:
        return ParsedTabularFile(source_sha256, encoding, (), (), ())

    columns = _parse_columns(parsed[0])

    rows: list[ParsedRow] = []
    width = len(columns)
    for row_no, values in enumerate(parsed[1:], start=2):
        padded = tuple((values + [""] * max(0, width - len(values)))[:width])
        raw_without_eol = physical_lines[row_no - 1] if row_no - 1 < len(physical_lines) else "\t".join(values)
        raw_line = raw_without_eol.rstrip("\r") + "\n"
        row_json = {col.key: padded[col.column_index - 1] for col in columns}
        row_values_json = [
            {
                "name": col.name,
                "normalized_name": col.key,
                "key": col.key,
                "column_index": col.column_index,
                "value": padded[col.column_index - 1],
            }
            for col in columns
        ]
        rows.append(ParsedRow(
            row_no=row_no,
            raw_line=raw_line,
            line_hash=_line_hash(source_file, row_no, raw_line),
            values=padded,
            row_json=row_json,
            row_values_json=row_values_json,
        ))
    return ParsedTabularFile(source_sha256, encoding, columns, tuple(rows), ())


TABULAR_PATH_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".lua", ".luau", ".lh", ".toc",
    ".tab", ".ini", ".txt", ".md", ".xml", ".yaml", ".yml",
}

_TABULAR_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


_CACHED_PATH_EXTENSIONS: set[str] | None = None


def _tabular_path_extensions() -> set[str]:
    global _CACHED_PATH_EXTENSIONS
    if _CACHED_PATH_EXTENSIONS is None:
        from graphify.detect import CODE_EXTENSIONS

        _CACHED_PATH_EXTENSIONS = set(CODE_EXTENSIONS) | TABULAR_PATH_EXTENSIONS
    return _CACHED_PATH_EXTENSIONS


def normalise_tabular_path_value(value: str) -> str:
    return value.strip().strip('"\'').replace("\\", "/")


def normalise_tabular_value(value: str) -> str:
    return value.strip()


_TABULAR_PATH_COLUMN_TERMS = {
    "asset",
    "dir",
    "directory",
    "file",
    "filename",
    "filepath",
    "icon",
    "image",
    "include",
    "lua",
    "model",
    "path",
    "resource",
    "script",
    "source",
    "sound",
    "target",
}


def _column_name_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"[^0-9A-Za-z]+", " ", value.strip())
    tokens: set[str] = set()
    for chunk in cleaned.split():
        tokens.update(
            token.lower()
            for token in re.findall(
                r"[A-Z]+(?=[A-Z][a-z]|[0-9]|\b)|[A-Z]?[a-z]+|[0-9]+",
                chunk,
            )
            if token
        )
        tokens.add(chunk.lower())
    return tokens


def looks_like_tabular_path_column_name(name: str, normalized_name: str | None = None) -> bool:
    values = [name]
    if normalized_name is not None and normalized_name != name:
        values.append(normalized_name)
    tokens: set[str] = set()
    for value in values:
        tokens.update(_column_name_tokens(value))
    compact = {re.sub(r"[^0-9A-Za-z]+", "", value).lower() for value in values}
    return bool(tokens & _TABULAR_PATH_COLUMN_TERMS) or bool(
        compact & _TABULAR_PATH_COLUMN_TERMS
    )


def is_tabular_url_value(value: str) -> bool:
    """Check if a pre-normalized value looks like a URL."""
    return _TABULAR_URL_RE.match(value) is not None


def looks_like_tabular_path_value(value: str) -> bool:
    cleaned = normalise_tabular_path_value(value)
    if not cleaned or len(cleaned) > 512:
        return False
    if is_tabular_url_value(cleaned):
        return False
    suffix = Path(cleaned).suffix.lower()
    if suffix in _tabular_path_extensions():
        return True
    if "/" in cleaned:
        parts = [p for p in cleaned.split("/") if p and p not in (".", "..")]
        return len(parts) >= 2 and any("." in p for p in parts[-1:])
    return False
