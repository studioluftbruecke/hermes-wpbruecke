"""The WPBrücke page in node and in Chromium (WG5): Download connector, Install in WordPress, and the
health and builder badges, against an SDK shim that has captureGatewayFileDownload and one that lacks it.

desktop_page_harness.mjs stands in for react, react/jsx-runtime and @hermes/plugin-sdk and mounts the
real desktop/plugin.js. Each case prints one RESULT line with the checks a person would make on the
screen; in Chromium it also fails on any console error or page error. The node cases need only node;
the Chromium run is marked `chromium` and also needs the Playwright package (PLAYWRIGHT_FROM, else
node's own lookup). Set WPB_EVIDENCE_DIR to keep its screenshots (one WebP per state).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from wpb_fakes import mod

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "desktop_page_harness.mjs"
PLUGIN_JS = ROOT / "desktop" / "plugin.js"
CASES = [
    "settings-missing/with", "settings-missing/without", "settings-older/with", "settings-older/without",
    "settings-current/with", "settings-ssh/with", "settings-fail/with", "fleet/with", "fleet/without",
    "one-site/without", "activity-closed/with", "activity-open/with",
]
SHOTS = {"connector-button.webp", "connector-cli.webp", "card-broken-elementor.webp", "activity-closed.webp",
         "activity-open.webp"}


def node_or_skip() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    return node


def results(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if line.startswith("RESULT "):
            r = json.loads(line[len("RESULT "):])
            out[f"{r.get('scenario')}/{r.get('shim')}"] = r
    return out


@pytest.fixture(scope="module")
def node_results(tmp_path_factory):
    node = node_or_skip()
    env = {**os.environ, "TMPDIR": str(tmp_path_factory.mktemp("wpb-page"))}
    done = subprocess.run([node, str(HARNESS), "node", str(PLUGIN_JS), *CASES], capture_output=True, text=True,
                          timeout=240, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    return results(done.stdout)


@pytest.mark.parametrize("case", CASES)
def test_page_in_node(node_results, case):
    r = node_results.get(case)
    assert r is not None, f"no result for {case}"
    assert r["ok"] is True, json.dumps(r, indent=1, ensure_ascii=False)


@pytest.mark.chromium
def test_page_in_chromium(tmp_path):
    node = node_or_skip()
    probe = subprocess.run([node, str(HARNESS), "probe"], capture_output=True, text=True, timeout=60)
    if probe.returncode != 0 or not json.loads(probe.stdout or "{}").get("playwright"):
        pytest.skip("the playwright package is not found (set PLAYWRIGHT_FROM)")
    chromium = mod("proof.runner").find_chromium()
    out = Path(os.environ.get("WPB_EVIDENCE_DIR") or tmp_path / "shots")
    # TMPDIR stays the caller's: Chromium refuses a singleton socket path as long as pytest's tmp_path.
    env = {**os.environ, "WPB_CHROMIUM": chromium or ""}
    done = subprocess.run([node, str(HARNESS), "chromium", str(PLUGIN_JS), str(out)], capture_output=True, text=True,
                          timeout=300, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    got = results(done.stdout)
    assert set(got) == set(CASES), sorted(got)
    failed = {case: r for case, r in got.items() if r.get("ok") is not True}
    assert not failed, json.dumps(failed, indent=1, ensure_ascii=False)
    assert {p.name for p in out.glob("*.webp")} >= SHOTS
