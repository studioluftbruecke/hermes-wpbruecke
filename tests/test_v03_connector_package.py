"""POST /connector-package (WG5): the page's Download connector reads the same zip the CLI writes."""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import re
import sys
import types
from argparse import Namespace

import pytest
from wpb_fakes import mod

ROOT = pathlib.Path(__file__).resolve().parents[1]
api = mod("api")
cz = mod("connector_zip")
cli = mod("cli")

KEYS = {"path", "filename", "version", "sha256"}


def sha(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def cli_zip(out: pathlib.Path, capsys) -> str:
    """Run `hermes wpbruecke connector-zip --out <out>` through the CLI handler; the sha it prints."""
    code = cli.make_handler(lambda k, d=None: d, None, lambda n: None)(
        Namespace(wpbruecke_command="connector-zip", out=str(out)))
    assert code == 0
    printed = capsys.readouterr().out
    found = re.search(r"sha256 ([0-9a-f]{64})", printed)
    assert found, printed
    return found.group(1)


def test_the_route_zip_is_the_cli_zip(tmp_path, capsys):
    status, body = api.connector_package(tmp_path / "data")
    assert status == 200 and KEYS <= set(body) and body["ok"] is True
    zip_path = pathlib.Path(body["path"])
    assert zip_path.is_file() and (tmp_path / "data") in zip_path.parents
    assert body["sha256"] == sha(zip_path)
    printed = cli_zip(tmp_path / "cli", capsys)
    assert body["sha256"] == printed == sha(tmp_path / "cli" / "wpbruecke-connector.zip")


def test_version_and_download_name(tmp_path):
    _, body = api.connector_package(tmp_path)
    assert body["version"] == cz.CONNECTOR_VERSION == cz.header_version()
    assert body["filename"] == f"wpbruecke-connector-{cz.CONNECTOR_VERSION}.zip"
    assert body["workspace"] == "host"


def test_a_second_call_rewrites_the_same_file(tmp_path):
    first = api.connector_package(tmp_path)[1]
    second = api.connector_package(tmp_path)[1]
    assert first["path"] == second["path"] and first["sha256"] == second["sha256"]
    assert [p.name for p in (tmp_path / "connector").iterdir()] == ["wpbruecke-connector.zip"]


def test_ssh_workspace_is_named_in_the_answer(tmp_path):
    status, body = api.connector_package(tmp_path, "ssh")
    assert status == 200 and body["workspace"] == "ssh"


def test_a_failed_build_answers_the_refusal_shape(tmp_path, monkeypatch):
    def broken(out):
        raise ValueError("plugin header Version '9' does not match CONNECTOR_VERSION '0.3.0'")
    monkeypatch.setattr(cz, "build_zip", broken)
    status, body = api.connector_package(tmp_path)
    assert status == 500 and body["ok"] is False and body["error"] == "zip_failed"
    assert "does not match" in body["message"] and "path" not in body
    assert api.connector_package(None)[0] == 500


def test_the_page_needs_the_version_this_plugin_ships():
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    found = re.search(r"^const CONNECTOR_NEEDED = '([^']+)'", js, re.M)
    assert found, "desktop/plugin.js CONNECTOR_NEEDED not found"
    assert found.group(1) == cz.CONNECTOR_VERSION, "bump CONNECTOR_NEEDED in desktop/plugin.js with the connector"


# --- the FastAPI route ------------------------------------------------------------------------

def load_router(monkeypatch, tmp_path, terminal: dict):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    fake = types.ModuleType("hermes_cli.config")
    fake.load_config_readonly = lambda: {"terminal": terminal}
    monkeypatch.setitem(sys.modules, "hermes_cli.config", fake)
    spec = importlib.util.spec_from_file_location("wpb_plugin_api_v03", ROOT / "dashboard" / "plugin_api.py")
    plugin_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin_api)
    monkeypatch.setattr(plugin_api, "_home", lambda: tmp_path)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/wpbruecke")
    return plugin_api, TestClient(app)


@pytest.mark.parametrize("terminal, workspace", [({}, "host"), ({"backend": "local"}, "host"),
                                                 ({"backend": "SSH", "ssh_host": "box"}, "ssh")])
def test_post_connector_package(monkeypatch, tmp_path, capsys, terminal, workspace):
    plugin_api, client = load_router(monkeypatch, tmp_path, terminal)
    res = client.post("/api/plugins/wpbruecke/connector-package", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert KEYS <= set(body) and body["workspace"] == workspace
    assert pathlib.Path(body["path"]).parent == tmp_path / "plugin-data" / plugin_api.data_namespace() / "connector"
    assert body["sha256"] == sha(body["path"]) == cli_zip(tmp_path / "cli", capsys)
