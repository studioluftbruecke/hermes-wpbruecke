"""The connector zip: deterministic, one top folder, every PHP file, nothing from outside connector/."""
from __future__ import annotations

import zipfile

import pytest
from wpb_fakes import mod

cz = mod("connector_zip")


def test_two_builds_have_the_same_sha(tmp_path):
    a = cz.build_zip(tmp_path / "a" / "wpbruecke-connector.zip")
    b = cz.build_zip(tmp_path / "b")
    assert a["sha256"] == b["sha256"] and a["bytes"] == b["bytes"] > 0
    assert b["path"].endswith("wpbruecke-connector.zip")


def test_one_top_folder_fixed_times_and_modes(tmp_path):
    info = cz.build_zip(tmp_path / "c.zip")
    with zipfile.ZipFile(info["path"]) as zf:
        names = zf.namelist()
        assert names == sorted(names)
        assert {n.split("/")[0] for n in names} == {"wpbruecke-connector"}
        for zi in zf.infolist():
            assert zi.date_time == (1980, 1, 1, 0, 0, 0)
            mode = zi.external_attr >> 16
            assert mode == (0o40755 if zi.is_dir() else 0o100644), zi.filename
        assert zf.testzip() is None


def test_every_php_file_and_the_licence_are_inside(tmp_path):
    info = cz.build_zip(tmp_path / "d.zip")
    want = {f"wpbruecke-connector/{p.relative_to(cz.SOURCE).as_posix()}" for p in cz.SOURCE.rglob("*.php")}
    assert want and want <= set(info["files"])
    for name in ("wpbruecke-connector.php", "uninstall.php", "readme.txt", "LICENSE", "includes/role.php",
                 "includes/preview.php", "includes/review.php", "includes/seo.php", "includes/updates.php",
                 "includes/abilities.php", "includes/rest.php"):
        assert f"wpbruecke-connector/{name}" in info["files"]


def test_header_version_matches_constant():
    assert cz.header_version() == cz.CONNECTOR_VERSION
    text = cz.MAIN_FILE.read_text(encoding="utf-8")
    assert f"define( 'WPBRUECKE_CONNECTOR_VERSION', '{cz.CONNECTOR_VERSION}' );" in text
    assert "Plugin Name:       WPBrücke Connector" in text and "License:           MIT" in text


def test_nothing_from_outside_the_connector_folder(tmp_path):
    info = cz.build_zip(tmp_path / "e.zip")
    for name in info["files"]:
        rel = name.split("/", 1)[1]
        path = (cz.SOURCE / rel).resolve()
        assert cz.CONNECTOR_DIR.resolve() in path.parents and path.is_file(), name
        with zipfile.ZipFile(info["path"]) as zf:
            assert zf.read(name) == path.read_bytes()


def test_refuses_to_write_inside_the_connector_folder():
    with pytest.raises(ValueError, match="inside the connector folder"):
        cz.build_zip(cz.SOURCE / "wpbruecke-connector.zip")
    with pytest.raises(ValueError, match="inside the connector folder"):
        cz.build_zip(cz.CONNECTOR_DIR)
    assert not (cz.SOURCE / "wpbruecke-connector.zip").exists()
