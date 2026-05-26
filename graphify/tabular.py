"""Heuristics for identifying structured tab-delimited text files."""
from __future__ import annotations

from collections import Counter
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
