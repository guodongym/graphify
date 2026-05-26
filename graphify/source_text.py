"""Shared bytes-to-text decoding for source-like extraction paths."""
from __future__ import annotations

from dataclasses import dataclass

_SAMPLE_BYTES = 4096
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")
_REPLACEMENT = "\ufffd"
_UTF16LE_STRUCTURAL_MARKERS = (
    b"\n\x00",
    b"\r\x00",
    b"\t\x00",
    b"=\x00",
    b"[\x00",
    b"]\x00",
    b"(\x00",
    b")\x00",
    b"{\x00",
    b"}\x00",
)


@dataclass(frozen=True)
class SourceTextDecodeResult:
    text: str | None
    encoding: str | None
    warnings: tuple[str, ...] = ()


def _has_utf16le_nul_parity(sample: bytes) -> bool:
    even = sample[0::2]
    odd = sample[1::2]
    if not even or not odd:
        return False
    even_nuls = even.count(0)
    odd_nuls = odd.count(0)
    odd_ratio = odd_nuls / len(odd)
    even_ratio = even_nuls / len(even)
    return odd_nuls >= 4 and odd_ratio >= 0.25 and even_ratio <= 0.05


def _has_utf16le_structural_shape(sample: bytes) -> bool:
    if len(sample) < 8:
        return False
    if len(sample) % 2:
        sample = sample[:-1]
    if not sample or not any(marker in sample for marker in _UTF16LE_STRUCTURAL_MARKERS):
        return False
    try:
        text = sample.decode("utf-16-le")
    except UnicodeDecodeError:
        return False
    if not _is_sane_decoded_source(text):
        return False
    return any(marker in text for marker in "\n\r\t=[](){}")


def _looks_like_utf16le(raw: bytes) -> bool:
    if raw.startswith(b"\xff\xfe"):
        return True
    sample = raw[:_SAMPLE_BYTES]
    if len(sample) < 8:
        return False
    return _has_utf16le_nul_parity(sample) or _has_utf16le_structural_shape(sample)


def _is_sane_decoded_source(text: str) -> bool:
    if "\x00" in text:
        return False
    if not text:
        return True
    printable = 0
    control = 0
    for ch in text:
        if ch in "\t\n\r\f":
            continue
        if ch.isprintable():
            printable += 1
        elif ch != "\ufeff":
            control += 1
    if printable == 0 and text.strip("\ufeff\t\n\r\f "):
        return False
    return control <= max(2, len(text) // 100)


def _replacement_decode(raw: bytes, warnings: list[str]) -> SourceTextDecodeResult:
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        text = text.replace("\x00", _REPLACEMENT)
        warnings.append("embedded NUL bytes replaced during fallback decoding")
    warnings.append("decoded with replacement after utf-16-le/utf-8/gb18030 failed")
    return SourceTextDecodeResult(text, "utf-8-replace", tuple(warnings))


def decode_source_text(raw: bytes, *, replace: bool = False) -> SourceTextDecodeResult:
    """Decode source bytes using Graphify's structured-text encoding order."""
    warnings: list[str] = []

    if _looks_like_utf16le(raw):
        encoding = "utf-16" if raw.startswith(b"\xff\xfe") else "utf-16-le"
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            warnings.append("utf-16-le sniff failed to decode")
        else:
            if _is_sane_decoded_source(text):
                return SourceTextDecodeResult(text, "utf-16-le", tuple(warnings))
            warnings.append("utf-16-le sniff rejected by source sanity checks")

    for encoding in _TEXT_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _is_sane_decoded_source(text):
            return SourceTextDecodeResult(text, encoding, tuple(warnings))
        warnings.append(f"{encoding} decoded text rejected by source sanity checks")

    if replace:
        return _replacement_decode(raw, warnings)
    return SourceTextDecodeResult(None, None, tuple(warnings))
