"""Tests for structured .ini extraction."""
from __future__ import annotations

from pathlib import Path

from graphify.extract import collect_files, extract, extract_ini
from graphify.validate import validate_extraction


def labels(result: dict) -> set[str]:
    return {n["label"] for n in result["nodes"]}


def edge_labels(result: dict, relation: str, context: str | None = None) -> set[tuple[str, str]]:
    by_id = {n["id"]: n["label"] for n in result["nodes"]}
    return {
        (by_id.get(e["source"], e["source"]), by_id.get(e["target"], e["target"]))
        for e in result["edges"]
        if e["relation"] == relation and (context is None or e.get("context") == context)
    }


def test_collect_files_includes_ini(tmp_path):
    path = tmp_path / "WorldMap.ini"
    path.write_text("[WorldMap]\nWndType=WndFrame\n", encoding="utf-8")

    assert path in collect_files(tmp_path)


def test_collect_files_includes_mixedcase_ini(tmp_path):
    path = tmp_path / "WorldMap.Ini"
    path.write_text("[WorldMap]\nWndType=WndFrame\n", encoding="utf-8")

    assert path in collect_files(tmp_path)


def test_extract_ini_finds_sections_parent_type_and_assets(tmp_path):
    ini = tmp_path / "WorldMap.ini"
    ini.write_text(
        "; main map panel\n"
        "[WorldMap]\n"
        "WndType=WndFrame\n"
        "Left=10\n"
        "Top=20\n"
        "Image=assets/ui/worldmap.UITex\n"
        "\n"
        "[BtnClose]\n"
        "Parent=WorldMap\n"
        "WndType=Button\n"
        "Width=32\n",
        encoding="utf-8",
    )

    result = extract_ini(ini)

    assert {"WorldMap.ini", "WorldMap", "BtnClose", "WndFrame", "Button"}.issubset(labels(result))
    assert ("WorldMap.ini", "WorldMap") in edge_labels(result, "contains")
    assert ("WorldMap.ini", "BtnClose") in edge_labels(result, "contains")
    assert ("BtnClose", "WorldMap") in edge_labels(result, "parent")
    assert ("WorldMap", "WndFrame") in edge_labels(result, "wnd_type")
    assert ("BtnClose", "Button") in edge_labels(result, "wnd_type")
    assert ("WorldMap", "assets/ui/worldmap.UITex") in edge_labels(result, "references", "asset")
    assert "Left" not in labels(result)
    assert "Top" not in labels(result)
    assert "Width" not in labels(result)
    assert validate_extraction(result) == []


def test_extract_ini_parent_handles_quoted_values(tmp_path):
    ini = tmp_path / "Window.ini"
    ini.write_text(
        "[WorldMap]\n"
        "WndType=WndFrame\n"
        "[BtnClose]\n"
        "Parent=\"WorldMap\"\n"
        "WndType=Button\n",
        encoding="utf-8",
    )

    result = extract_ini(ini)

    assert ("BtnClose", "WorldMap") in edge_labels(result, "parent")
    assert validate_extraction(result) == []


def test_extract_ini_decodes_gb18030_and_utf8_bom(tmp_path):
    gb = tmp_path / "GameWorldConstList.ini"
    gb.write_bytes("[常量]\nName=成都\nMode=Active\n".encode("gb18030"))
    bom = tmp_path / "bom.ini"
    bom.write_bytes("[Panel]\nWndType=WndFrame\n".encode("utf-8-sig"))

    gb_result = extract_ini(gb)
    bom_result = extract_ini(bom)

    assert "常量" in labels(gb_result)
    assert "Name" in labels(gb_result)
    assert "Mode=Active" in labels(gb_result)
    assert "Panel" in labels(bom_result)
    assert validate_extraction(gb_result) == []
    assert validate_extraction(bom_result) == []


def test_extract_ini_decodes_utf16le(tmp_path):
    ini = tmp_path / "utf16.ini"
    ini.write_bytes("[面板]\nName=成都\nMode=Active\n".encode("utf-16"))

    result = extract_ini(ini)

    assert "面板" in labels(result)
    assert "Name" in labels(result)
    assert "Mode=Active" in labels(result)
    assert validate_extraction(result) == []


def test_extract_ini_decodes_no_bom_cjk_heavy_utf16le(tmp_path):
    ini = tmp_path / "cjk_utf16.ini"
    title = "成都扬州苏州南京武汉北京上海广州深圳杭州"
    ini.write_bytes(f"[面板]\n标题={title}\n模式=激活\n".encode("utf-16-le"))

    result = extract_ini(ini)

    assert "面板" in labels(result)
    assert "标题" in labels(result)
    assert f"标题={title}" in labels(result)
    assert "模式=激活" in labels(result)
    assert validate_extraction(result) == []


def test_extract_ini_limits_duplicate_sections_and_value_noise(tmp_path):
    ini = tmp_path / "settings.ini"
    ini.write_text(
        "[Settings]\n"
        "MaxLevel=120\n"
        "QuotedMax=\"120\"\n"
        "Mode=Normal\n"
        "Mode=Hard\n"
        "\n"
        "[Settings]\n"
        "Mode=Expert\n",
        encoding="utf-8",
    )

    result = extract_ini(ini)

    assert "Settings" in labels(result)
    assert "Settings #2" in labels(result)
    assert "MaxLevel=120" not in labels(result)
    assert "QuotedMax=120" not in labels(result)
    assert "Mode" in labels(result)
    assert "Mode=Normal" in labels(result)
    assert "Mode=Hard" in labels(result)
    assert "Mode=Expert" in labels(result)
    assert validate_extraction(result) == []


def test_extract_ini_large_file_warns_and_returns_file_node(tmp_path, monkeypatch):
    import graphify.extract as extract_mod

    monkeypatch.setattr(extract_mod, "_INI_MAX_BYTES", 16)
    ini = tmp_path / "large.ini"
    ini.write_text("[Panel]\nWndType=WndFrame\n", encoding="utf-8")

    result = extract_ini(ini)

    assert "large.ini" in labels(result)
    assert result.get("truncated") is True
    assert result.get("warnings")
    assert validate_extraction(result) == []


def test_extract_ini_byte_cap_preserves_utf16le_record_boundary(tmp_path, monkeypatch):
    import graphify.extract as extract_mod

    body = "[面板]\n标题=成都\n模式=激活\n"
    first_record_len = len("[面板]\n".encode("utf-16-le"))
    monkeypatch.setattr(extract_mod, "_INI_MAX_BYTES", first_record_len - 1)
    ini = tmp_path / "large_utf16.ini"
    ini.write_bytes(body.encode("utf-16-le"))

    result = extract_ini(ini)

    assert "面板" in labels(result)
    assert result.get("truncated") is True
    assert not any("decoded with replacement" in warning for warning in result.get("warnings", []))
    assert validate_extraction(result) == []


def test_lua_strings_reference_unique_ini_section_and_file(tmp_path):
    ini = tmp_path / "WorldMap.ini"
    ini.write_text("[WorldMap]\nWndType=WndFrame\n", encoding="utf-8")
    lua = tmp_path / "ui.lua"
    lua.write_text('local panel = "WorldMap"\nlocal file = "WorldMap.ini"\n', encoding="utf-8")

    result = extract([lua, ini], cache_root=tmp_path, parallel=False)

    assert ("ui.lua", "WorldMap") in edge_labels(result, "references", "ini")
    assert ("ui.lua", "WorldMap.ini") in edge_labels(result, "references", "ini")
    assert validate_extraction(result) == []


def test_lua_strings_reference_unique_ini_path(tmp_path):
    config = tmp_path / "Config"
    config.mkdir()
    ini = config / "WorldMap.ini"
    ini.write_text("[WorldMap]\nWndType=WndFrame\n", encoding="utf-8")
    lua = tmp_path / "ui.lua"
    lua.write_text('local file = "Config/WorldMap.ini"\n', encoding="utf-8")

    result = extract([lua, ini], cache_root=tmp_path, parallel=False)

    assert ("ui.lua", "WorldMap.ini") in edge_labels(result, "references", "ini")
    assert validate_extraction(result) == []


def test_lua_ini_path_disambiguates_duplicate_file_names(tmp_path):
    config = tmp_path / "Config"
    traits = tmp_path / "Traits"
    config.mkdir()
    traits.mkdir()
    target = config / "WorldMap.ini"
    other = traits / "WorldMap.ini"
    target.write_text("[WorldMap]\nWndType=WndFrame\n", encoding="utf-8")
    other.write_text("[TraitWorldMap]\nWndType=WndFrame\n", encoding="utf-8")
    lua = tmp_path / "ui.lua"
    lua.write_text('local file = "Config/WorldMap.ini"\n', encoding="utf-8")

    result = extract([lua, target, other], cache_root=tmp_path, parallel=False)

    target_ids = {
        n["id"]
        for n in result["nodes"]
        if n["label"] == "WorldMap.ini" and n["source_file"].endswith("Config/WorldMap.ini")
    }
    other_ids = {
        n["id"]
        for n in result["nodes"]
        if n["label"] == "WorldMap.ini" and n["source_file"].endswith("Traits/WorldMap.ini")
    }
    assert target_ids
    assert other_ids
    assert any(
        e["target"] in target_ids
        for e in result["edges"]
        if e["relation"] == "references" and e.get("context") == "ini"
    )
    assert not any(
        e["target"] in other_ids
        for e in result["edges"]
        if e["relation"] == "references" and e.get("context") == "ini"
    )
    assert validate_extraction(result) == []


def test_lua_strings_do_not_reference_ambiguous_ini_sections(tmp_path):
    first = tmp_path / "First.ini"
    second = tmp_path / "Second.ini"
    lua = tmp_path / "ui.lua"
    first.write_text("[BtnClose]\nWndType=Button\n", encoding="utf-8")
    second.write_text("[BtnClose]\nWndType=Button\n", encoding="utf-8")
    lua.write_text('local button = "BtnClose"\n', encoding="utf-8")

    result = extract([lua, first, second], cache_root=tmp_path, parallel=False)

    assert ("ui.lua", "BtnClose") not in edge_labels(result, "references", "ini")
    assert validate_extraction(result) == []


def test_lua_ini_references_ignore_commented_strings(tmp_path):
    ini = tmp_path / "WorldMap.ini"
    ini.write_text("[WorldMap]\nWndType=WndFrame\n[Inventory]\nWndType=WndFrame\n", encoding="utf-8")
    lua = tmp_path / "ui.lua"
    lua.write_text('-- local stale = "WorldMap"\nlocal panel = "Inventory"\n', encoding="utf-8")

    result = extract([lua, ini], cache_root=tmp_path, parallel=False)

    assert ("ui.lua", "Inventory") in edge_labels(result, "references", "ini")
    assert ("ui.lua", "WorldMap") not in edge_labels(result, "references", "ini")
    assert validate_extraction(result) == []


def test_ini_asset_reference_connects_to_scanned_file_node(tmp_path):
    scripts = tmp_path / "scripts" / "ui"
    scripts.mkdir(parents=True)
    target = scripts / "WorldMap.lua"
    target.write_text("function show() end\n", encoding="utf-8")
    ini = tmp_path / "WorldMap.ini"
    ini.write_text("[WorldMap]\nWndType=WndFrame\nScript=scripts/ui/WorldMap.lua\n", encoding="utf-8")

    result = extract([ini, target], cache_root=tmp_path, parallel=False)

    target_file_ids = {
        n["id"]
        for n in result["nodes"]
        if n["label"] == "WorldMap.lua" and n["source_file"].endswith("scripts/ui/WorldMap.lua")
    }
    assert target_file_ids
    assert any(
        e["target"] in target_file_ids
        for e in result["edges"]
        if e["relation"] == "references" and e.get("context") == "asset"
    )
    assert not any(e.get("target_ref") for e in result["edges"])
    assert not any(
        n["label"] == "WorldMap.lua" and n["source_file"].endswith("WorldMap.ini")
        for n in result["nodes"]
    )
    assert validate_extraction(result) == []
