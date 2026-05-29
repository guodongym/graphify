from __future__ import annotations

from graphify.extract import extract
from graphify.tabular import (
    _line_hash,
    looks_like_tabular_path_column_name,
    looks_like_tabular_path_value,
    parse_tabular_file,
    read_tabular_profile,
)


def labels(result: dict) -> set[str]:
    return {node["label"] for node in result["nodes"]}


def test_parse_tabular_file_basic_tsv(tmp_path):
    path = tmp_path / "sample.tsv"
    path.write_text("ID\tName\n1\tAlpha\n2\tBeta\n3\tGamma\n", encoding="utf-8")

    parsed = parse_tabular_file(path)

    assert [column.name for column in parsed.columns] == ["ID", "Name"]
    assert [column.key for column in parsed.columns] == ["ID", "Name"]
    assert [row.row_no for row in parsed.rows] == [2, 3, 4]
    assert parsed.rows[0].raw_line == "1\tAlpha\n"
    assert parsed.rows[0].values == ("1", "Alpha")
    assert parsed.rows[0].row_json == {"ID": "1", "Name": "Alpha"}
    assert parsed.rows[2].row_json == {"ID": "3", "Name": "Gamma"}


def test_parse_tabular_file_deduplicates_duplicate_headers(tmp_path):
    path = tmp_path / "duplicate.tsv"
    path.write_text("Name\tName\nAlpha\tBeta\n", encoding="utf-8")

    parsed = parse_tabular_file(path)

    assert [column.name for column in parsed.columns] == ["Name", "Name"]
    assert [column.key for column in parsed.columns] == ["Name", "Name_2"]
    assert parsed.rows[0].row_json == {"Name": "Alpha", "Name_2": "Beta"}


def test_read_tabular_profile_counts_rows_and_columns(tmp_path):
    path = tmp_path / "profile.tsv"
    raw = b"ID\tName\n1\tAlpha\n2\tBeta\n"
    path.write_bytes(raw)

    profile = read_tabular_profile(path)

    assert profile.row_count == 2
    assert profile.column_count == 2
    assert profile.file_size == len(raw)
    assert [column.key for column in profile.columns] == ["ID", "Name"]
    assert not hasattr(profile, "rows")


def test_line_hash_is_stable_for_same_input():
    assert _line_hash("sample.tsv", 2, "1\tAlpha\n") == _line_hash("sample.tsv", 2, "1\tAlpha\n")


def test_parse_tabular_file_handles_utf8_bom(tmp_path):
    path = tmp_path / "bom.tsv"
    path.write_bytes("ID\tName\n1\tAlpha\n".encode("utf-8-sig"))

    parsed = parse_tabular_file(path)

    assert parsed.encoding == "utf-8-sig"
    assert [column.name for column in parsed.columns] == ["ID", "Name"]
    assert parsed.rows[0].row_json == {"ID": "1", "Name": "Alpha"}


def test_parse_tabular_file_handles_gb18030(tmp_path):
    path = tmp_path / "gb18030.tsv"
    path.write_bytes("ID\tName\n1\t成都\n".encode("gb18030"))

    parsed = parse_tabular_file(path)

    assert parsed.encoding == "gb18030"
    assert parsed.rows[0].row_json == {"ID": "1", "Name": "成都"}


def test_looks_like_tabular_path_value_detects_path_like_values():
    assert looks_like_tabular_path_value('"scripts\\Map\\成都\\ai\\动物表现\\动物逃跑.lua"') is True
    assert looks_like_tabular_path_value("config/data/file.txt") is True
    assert looks_like_tabular_path_value("scripts/combat/attack.go") is True
    assert looks_like_tabular_path_value("attack.go") is True
    assert looks_like_tabular_path_value("https://example.com/file.lua") is False
    assert looks_like_tabular_path_value("plain-value") is False


def test_looks_like_tabular_path_column_name_filters_prose_columns():
    assert looks_like_tabular_path_column_name("Script") is True
    assert looks_like_tabular_path_column_name("SourceFile") is True
    assert looks_like_tabular_path_column_name("IconPath") is True
    assert looks_like_tabular_path_column_name("Desc") is False
    assert looks_like_tabular_path_column_name("Description") is False


def test_tsv_dispatches_to_tab_extractor(tmp_path):
    path = tmp_path / "sample.tsv"
    path.write_text("ID\tName\n1\tAlpha\n", encoding="utf-8")

    result = extract([path], cache_root=tmp_path, parallel=False)

    assert "sample.tsv" in labels(result)
    assert "ID 1" in labels(result)
