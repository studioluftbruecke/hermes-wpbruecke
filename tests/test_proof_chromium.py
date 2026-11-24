"""The proof runner in a real Chromium against a local server, mapped through the resolver rule.

Skipped where no Chromium exists (see conftest.py); a skip is not a pass.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

from wpb_fakes import mod

runner = mod("proof.runner")
checks = mod("proof.checks")
store_mod = mod("store")

HOST = "proof.example.test"

NORMAL = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Normal</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;font:16px sans-serif;color:#111;background:#fff">
<h1>Hello world</h1><p>A plain page. <a href="/wide.html">Wide page</a>.</p>
<img src="/dot.svg" alt="A black square" width="40" height="40">
<img src="http://localhost:PORT/dot.svg" alt="Same square from an extra host" width="40" height="40">
<img src="http://blocked.example.test:PORT/dot.svg" alt="" width="40" height="40">
</body></html>"""

WIDE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Wide</title></head>
<body style="margin:0;font:16px sans-serif"><p>Too wide.</p>
<div style="width:2000px;height:40px;background:#888"></div></body></html>"""

DOT = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40"/></svg>'


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture()
def site(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    port = server.server_address[1]
    (root / "index.html").write_text(NORMAL.replace("PORT", str(port)), encoding="utf-8")
    (root / "wide.html").write_text(WIDE, encoding="utf-8")
    (root / "dot.svg").write_text(DOT, encoding="utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{HOST}:{port}"
    server.shutdown()
    server.server_close()


def by(rows, check, width=None):
    return next(r for r in rows if r["check"] == check and r.get("width") == width)


@pytest.mark.chromium
def test_normal_and_wide_page_through_the_resolver_rule(site, tmp_path):
    store = store_mod.Store(tmp_path / "data")
    common = dict(store=store, host=HOST, connect_ip="127.0.0.1", widths=[1440, 768, 390],
                  extra_hosts=["localhost"], chromium_path="", target={"kind": "url"})

    normal = checks.run_proof(url=f"{site}/index.html", run_id="proof-normal", **common)
    assert normal["chromium"].startswith(("Chrome/", "HeadlessChrome/"))
    assert [s["status"] for s in normal["shots"]] == [200, 200, 200]
    assert all(s["bytes"] > 1000 for s in normal["shots"])
    assert normal["blocked_hosts"] == ["blocked.example.test"], "only the unlisted host is blocked"
    for w in (1440, 768, 390):
        assert by(normal["checks"], "overflow", w)["verdict"] == "match"
        assert by(normal["checks"], "render", w)["verdict"] == "match"
    assert "blocked.example.test" in by(normal["checks"], "render", 1440)["detail"]
    assert by(normal["checks"], "broken_links")["verdict"] == "match"
    assert by(normal["checks"], "alt_text")["verdict"] == "match"
    assert by(normal["checks"], "console_errors")["verdict"] == "match"
    assert by(normal["checks"], "contrast")["verdict"] == "match"
    assert normal["verdict"] == "match"
    folder = tmp_path / "data" / "proofs" / "proof-normal"
    assert sorted(p.name for p in folder.iterdir()) == ["1440.png", "390.png", "768.png", "run.json"]
    assert json.loads((folder / "run.json").read_text(encoding="utf-8"))["verdict"] == "match"

    wide = checks.run_proof(url=f"{site}/wide.html", run_id="proof-wide",
                            before_url=f"{site}/index.html", **common)
    for w in (1440, 768, 390):
        row = by(wide["checks"], "overflow", w)
        assert row["verdict"] == "differs" and f"{2000 - w} px wider" in row["detail"]
    assert wide["verdict"] == "differs"
    assert [s["before_file"] for s in wide["shots"]] == ["1440-before.png", "768-before.png", "390-before.png"]


@pytest.mark.chromium
def test_without_connect_ip_the_fake_host_does_not_resolve(site, tmp_path):
    cap = runner.capture(f"{site}/index.html", host=HOST, widths=(390,), out_dir=tmp_path / "out")
    row = cap["widths"][390]
    assert "ERR_NAME_NOT_RESOLVED" in row["error"] and row["file"] is None
    rows = checks.evaluate(cap)
    assert by(rows, "render", 390)["verdict"] == "not_proven"
    assert checks.overall(rows) == "not_proven"
