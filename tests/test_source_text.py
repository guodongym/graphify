"""Tests for shared source text decoding."""
from __future__ import annotations

from graphify.source_text import decode_source_text


def test_utf16le_nul_parity_sniff_accepts_structured_source():
    raw = "Name\tValue\nAlpha\t1\nBeta\t2\n".encode("utf-16-le")

    result = decode_source_text(raw)

    assert result.encoding == "utf-16-le"
    assert result.text == "Name\tValue\nAlpha\t1\nBeta\t2\n"
    assert result.warnings == ()


def test_short_nul_sample_is_not_treated_as_utf16le():
    result = decode_source_text(b"A\x00B\x00", replace=True)

    assert result.encoding == "utf-8-replace"
    assert result.text is not None
    assert "\x00" not in result.text
    assert "utf-16-le" not in result.encoding


def test_gb18030_fallback_decodes_chinese_source_text():
    raw = "技能名称=气纯\n".encode("gb18030")

    result = decode_source_text(raw)

    assert result.encoding == "gb18030"
    assert result.text == "技能名称=气纯\n"


def test_control_heavy_text_is_rejected_without_replacement():
    raw = b"abc\x01\x02\x03\x04\x05def"

    result = decode_source_text(raw)

    assert result.text is None
    assert result.encoding is None
    assert any("sanity checks" in warning for warning in result.warnings)


def test_utf16le_sniff_rejects_decoded_embedded_nul_and_falls_back():
    raw = ("ABCDEFGHIJ" + "\x00" + "KLMNOPQRSTUVWXYZ\n").encode("utf-16-le")

    result = decode_source_text(raw, replace=True)

    assert result.encoding == "utf-8-replace"
    assert result.text is not None
    assert "\x00" not in result.text
    assert any("utf-16-le sniff rejected" in warning for warning in result.warnings)
