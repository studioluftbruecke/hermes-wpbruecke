"""The page's routes use only public Hermes: the data folder name is restated and held equal to Hermes' own."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")


def load_api():
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location("wpb_plugin_api_test", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api


def test_no_private_manifest_import_in_the_routes():
    assert "hermes_cli.plugins_manifest" not in SOURCE and "_portable_data_namespace" not in SOURCE


def test_the_data_folder_name_is_hermes_own():
    manifest = pytest.importorskip("hermes_cli.plugins_manifest")
    if not hasattr(manifest, "_portable_data_namespace"):
        pytest.skip("this Hermes has no _portable_data_namespace")
    api = load_api()
    for key in ("wpbruecke", "Some Plugin.x", "--", "über"):
        assert api.data_namespace(key) == manifest._portable_data_namespace(key)
    assert api.data_namespace() == manifest._portable_data_namespace("wpbruecke")


def test_the_data_folder_sits_under_plugin_data(tmp_path, monkeypatch):
    api = load_api()
    monkeypatch.setattr(api, "_home", lambda: tmp_path)
    path = api._data_dir()
    assert path == tmp_path / "plugin-data" / api.data_namespace() and path.is_dir()
    assert path.name.startswith("agent-plugin-wpbruecke-")
