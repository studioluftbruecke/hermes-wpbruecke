"""Proof layer without a browser: resolver rule, child environment, checks, overall verdict, slot."""
from __future__ import annotations

import json
import os
import stat
import struct
import zlib
from pathlib import Path

import pytest

from wpb_fakes import mod

runner = mod("proof.runner")
checks = mod("proof.checks")
store_mod = mod("store")

SENTINEL = "WPBRUECKE_KEY1"


def write_png(path: Path, width: int, height: int, pixel) -> Path:
    """A plain RGB PNG with the stdlib only (the test venv has no Pillow)."""
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.write_bytes(png)
    return path


# --- resolver rule --------------------------------------------------------------------------------

def test_resolver_rule_with_connect_ip_maps_the_host_and_keeps_extras_resolvable():
    rule = runner.resolver_rules("Site.Example.test", "192.0.2.10", ["fonts.example.test", "cdn.example.test"])
    assert rule == ("MAP site.example.test 192.0.2.10, MAP * ~NOTFOUND, "
                    "EXCLUDE fonts.example.test, EXCLUDE cdn.example.test")


def test_resolver_rule_without_connect_ip_excludes_host_and_extras():
    assert runner.resolver_rules("site.example.test") == "MAP * ~NOTFOUND, EXCLUDE site.example.test"
    assert runner.resolver_rules("site.example.test", "", ["site.example.test", "a.example.test",
                                                           "a.example.test"]) == \
        "MAP * ~NOTFOUND, EXCLUDE site.example.test, EXCLUDE a.example.test"


@pytest.mark.parametrize("host,ip,extra", [
    ("a.example.test, MAP * 192.0.2.1", "", ()),
    ("", "", ()),
    ("a.example.test", "192.0.2.1,MAP", ()),
    ("a.example.test", "", ("b.example.test, MAP x y",)),
])
def test_resolver_rule_refuses_injection(host, ip, extra):
    with pytest.raises(ValueError):
        runner.resolver_rules(host, ip, extra)


def test_browser_args_carry_rule_webrtc_policy_and_throwaway_profile():
    args = runner.browser_args("/bin/chrome", "/tmp/wpb-chrome-x", "MAP * ~NOTFOUND, EXCLUDE a.example.test")
    assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE a.example.test" in args
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--remote-debugging-pipe" in args
    assert "--user-data-dir=/tmp/wpb-chrome-x" in args


# --- child environment ----------------------------------------------------------------------------

def test_child_env_drops_every_secret(monkeypatch):
    monkeypatch.setenv(SENTINEL, "sentinel-value")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    env = runner.child_env("/tmp/wpb-chrome-test")
    assert SENTINEL not in env and "OPENAI_API_KEY" not in env
    assert set(env) <= {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "HOME"}
    assert env["HOME"] == "/tmp/wpb-chrome-test"
    assert env["TMPDIR"] != env["HOME"] and len(env["TMPDIR"]) <= runner.MAX_TEMP_BASE


def test_short_temp_base_skips_a_long_tmpdir(monkeypatch, tmp_path):
    long_dir = tmp_path / ("x" * 80)
    long_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(long_dir))
    assert len(runner.short_temp_base()) <= runner.MAX_TEMP_BASE


def test_spawned_child_gets_no_secret_and_the_rule(tmp_path, monkeypatch):
    """A fake browser records env and argv; the real spawn path runs, with ~80 extra fds open."""
    dump = tmp_path / "child.txt"
    script = tmp_path / "fake-chromium"
    script.write_text(f"#!/bin/sh\n{{ env; echo ARGV; for a in \"$@\"; do echo \"$a\"; done; "
                      f"echo CWD=$(pwd); echo CORE=$(ulimit -c); }} > '{dump}'\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(SENTINEL, "sentinel-value")
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(80)]
    try:
        result = runner.capture("https://site.example.test/", host="site.example.test",
                                connect_ip="192.0.2.10", extra_hosts=("fonts.example.test",),
                                out_dir=tmp_path / "out", chromium_path=str(script), timeout_s=5)
    finally:
        for fd in held:
            os.close(fd)
    assert "error" in result and result["widths"] == {}
    text = dump.read_text(encoding="utf-8")
    env_part, argv_part = text.split("ARGV\n", 1)
    env = dict(line.split("=", 1) for line in env_part.splitlines() if "=" in line)
    assert SENTINEL not in env
    assert "sentinel-value" not in text
    assert ("--host-resolver-rules=MAP site.example.test 192.0.2.10, MAP * ~NOTFOUND, "
            "EXCLUDE fonts.example.test") in argv_part
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in argv_part
    assert f"CWD={env['HOME']}" in argv_part and "CORE=0" in argv_part
    assert Path(env["HOME"]).name.startswith("wpb-chrome-")
    assert not os.path.exists(env["HOME"]), "the throwaway profile is removed"
    assert list((tmp_path / "out").iterdir()) == [], "nothing is written into the run folder"


def test_find_chromium_override(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    assert runner.find_chromium(str(exe)) is None, "not executable yet"
    exe.chmod(0o755)
    assert runner.find_chromium(str(exe)) == str(exe)
    assert runner.find_chromium(str(tmp_path / "missing")) is None


def test_capture_without_a_browser_says_so(tmp_path):
    result = runner.capture("https://site.example.test/", host="site.example.test",
                            out_dir=tmp_path, chromium_path=str(tmp_path / "missing"))
    assert result["widths"] == {} and "no Chromium" in result["error"]


# --- checks on a recorded capture -----------------------------------------------------------------

def recorded_capture() -> dict:
    """Shaped like a real capture() of a page (read back from JSON, so width keys are strings)."""
    base = {"bytes": 30000, "status": 200, "load_ms": 900, "lcp_ms": 700, "cls": 0.01,
            "console_errors": [], "images": [], "links": [], "links_checked": False,
            "contrast": [], "contrast_checked": 40, "contrast_unknown": 2}
    w1440 = dict(base, file="1440.png", height=2100, page_height=2100, overflow_px=0, links_checked=True,
                 links=[{"href": "https://site.example.test/a/", "status": 200, "same": True},
                        {"href": "https://site.example.test/gone/", "status": 404, "same": True},
                        {"href": "https://elsewhere.example.test/", "status": None, "same": False}],
                 images=[{"src": "https://site.example.test/a.jpg", "alt": "A dog", "w": 800, "h": 600},
                         {"src": "https://site.example.test/b.jpg", "alt": None, "w": 800, "h": 600},
                         {"src": "https://site.example.test/line.png", "alt": "", "w": 10, "h": 1}],
                 failed_requests=[{"url": "https://cdn.example.test/x.png", "host": "cdn.example.test",
                                   "error": "net::ERR_NAME_NOT_RESOLVED"}])
    w390 = dict(base, file="390.png", height=4000, page_height=5200, overflow_px=612, lcp_ms=1500,
                console_errors=["Uncaught TypeError: x is undefined"],
                contrast=[{"text": "Light grey footer", "ratio": 2.3, "large": False}],
                failed_requests=[{"url": "https://site.example.test/style.css", "host": "site.example.test",
                                  "error": "HTTP 404"}])
    return {"chromium": "Chrome/145", "widths": {"1440": w1440, "390": w390},
            "blocked_hosts": ["cdn.example.test"], "seconds": 6.1}


def by(rows, check, width=None):
    found = [r for r in rows if r["check"] == check and r.get("width") == width]
    assert len(found) == 1, (check, width, rows)
    return found[0]


def test_evaluate_recorded_capture():
    rows = checks.evaluate(json.loads(json.dumps(recorded_capture())))
    assert {r["check"] for r in rows} == set(checks.CHECKS)
    assert all(r["verdict"] in ("match", "differs", "not_proven") and r["detail"] for r in rows)
    render = by(rows, "render", 1440)
    assert render["verdict"] == "match" and "cdn.example.test" in render["detail"]
    assert "cut at 4000 of 5200 px" in by(rows, "render", 390)["detail"]
    assert by(rows, "overflow", 1440)["verdict"] == "match"
    over = by(rows, "overflow", 390)
    assert over["verdict"] == "differs" and "612 px wider" in over["detail"]
    console = by(rows, "console_errors")
    assert console["verdict"] == "differs"
    assert "TypeError" in console["detail"] and "style.css" in console["detail"]
    assert "cdn.example.test" not in console["detail"], "blocked hosts are reported on render"
    links = by(rows, "broken_links")
    assert links["verdict"] == "differs" and "/gone/ (404)" in links["detail"]
    assert "1 link to other hosts not checked" in links["detail"]
    alt = by(rows, "alt_text")
    assert alt["verdict"] == "differs" and "b.jpg" in alt["detail"] and "a.jpg" not in alt["detail"]
    contrast = by(rows, "contrast")
    assert contrast["verdict"] == "differs" and "2.3:1" in contrast["detail"]
    assert by(rows, "diff")["verdict"] == "not_proven"
    speed = by(rows, "speed_local")
    assert speed["verdict"] == "match" and "1500 ms (390 px)" in speed["detail"]
    assert checks.overall(rows) == "differs"


def test_evaluate_clean_page_matches():
    cap = recorded_capture()
    w1440, w390 = cap["widths"]["1440"], cap["widths"]["390"]
    w1440["links"] = w1440["links"][:1]
    w1440["images"] = [w1440["images"][0]]
    w390.update(overflow_px=0, console_errors=[], contrast=[], failed_requests=[])
    rows = checks.evaluate(cap)
    assert {r["verdict"] for r in rows if checks.CHECKS[r["check"]]["counts"]} == {"match"}
    assert checks.overall(rows) == "match", "an unproven diff and the local speed are not judged"


def test_evaluate_failed_capture_is_not_proven_with_reasons():
    rows = checks.evaluate({"chromium": None, "widths": {}, "blocked_hosts": [],
                            "error": "browser closed the DevTools pipe"})
    assert checks.overall(rows) == "not_proven"
    assert all(r["verdict"] == "not_proven" and r["detail"] for r in rows)
    assert "DevTools pipe" in by(rows, "render")["detail"]


def test_evaluate_http_error_page_differs():
    cap = {"widths": {1440: {"file": "1440.png", "status": 404, "height": 900, "overflow_px": 0,
                             "links_checked": True, "contrast_checked": 3}}}
    rows = checks.evaluate(cap)
    assert by(rows, "render", 1440)["verdict"] == "differs"
    assert checks.overall(rows) == "differs"


def test_evaluate_navigation_error_per_width():
    cap = {"widths": {1440: {"file": None, "status": None,
                             "error": "the browser could not load the page: net::ERR_NAME_NOT_RESOLVED"}}}
    rows = checks.evaluate(cap)
    assert by(rows, "render", 1440)["verdict"] == "not_proven"
    assert "ERR_NAME_NOT_RESOLVED" in by(rows, "overflow")["detail"] or \
        "no width" in by(rows, "overflow")["detail"]
    assert checks.overall(rows) == "not_proven"


def test_diff_rows_from_diffs():
    cap = recorded_capture()
    rows = checks.evaluate(cap, before={"widths": {"390": {"height": 3000}}},
                           diffs={"1440": {"pct": 0.02, "size": [1440, 2100]},
                                  "390": {"pct": 18.5, "size": [390, 4000]}})
    assert by(rows, "diff", 1440)["verdict"] == "match"
    d = by(rows, "diff", 390)
    assert d["verdict"] == "differs" and "18.5%" in d["detail"] and "from 3000 to 4000 px" in d["detail"]
    rows = checks.evaluate(cap, before={"error": "timed out after 60 s", "widths": {}}, diffs={})
    assert "timed out" in by(rows, "diff")["detail"]


def test_overall():
    def r(check, verdict):
        return {"check": check, "verdict": verdict, "detail": "x"}
    assert checks.overall([r("render", "match"), r("diff", "differs"), r("speed_local", "differs")]) == "match"
    assert checks.overall([r("render", "match"), r("overflow", "not_proven")]) == "not_proven"
    assert checks.overall([r("render", "not_proven"), r("overflow", "differs")]) == "differs"
    assert checks.overall([]) == "not_proven"


def test_registry_shape():
    assert set(checks.CHECKS) == {"render", "console_errors", "overflow", "broken_links", "alt_text",
                                  "contrast", "diff", "speed_local"}
    for spec in checks.CHECKS.values():
        assert spec["label"] and spec["method"] and spec["not_proven_reason"]
        assert isinstance(spec["weight"], int)
    assert sum(s["weight"] for s in checks.CHECKS.values()) == 100


# --- run_proof ------------------------------------------------------------------------------------

def fake_capture(calls):
    def capture(url, *, host, connect_ip="", widths=(1440, 768, 390), extra_hosts=(), out_dir,
                chromium_path="", timeout_s=60, prefix="", max_height=4000):
        calls.append({"url": url, "host": host, "connect_ip": connect_ip, "prefix": prefix,
                      "extra_hosts": extra_hosts, "out_dir": out_dir})
        shade = 200 if prefix else 255
        out = {"chromium": "Chrome/145", "widths": {}, "blocked_hosts": [], "seconds": 1.0}
        for w in widths:
            name = f"{w}-{prefix}.png" if prefix else f"{w}.png"
            write_png(Path(out_dir) / name, 8, 6, lambda x, y: (shade, shade, shade) if x < 4 else (0, 0, 0))
            out["widths"][w] = {"file": name, "bytes": 100, "height": 6, "page_height": 6, "load_ms": 500,
                                "status": 200, "lcp_ms": 400, "cls": 0, "overflow_px": 0,
                                "console_errors": [], "failed_requests": [], "images": [],
                                "links": [], "links_checked": True, "contrast": [], "contrast_checked": 5}
        return out
    return capture


def test_run_proof_writes_the_run_into_the_store(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "capture", fake_capture(calls))
    store = store_mod.Store(tmp_path / "data")
    run = checks.run_proof(store=store, url="https://site.example.test/?p=1", host="site.example.test",
                           connect_ip="192.0.2.10", widths=[1440, 390], extra_hosts=["fonts.example.test"],
                           chromium_path="", target={"kind": "url", "site": "site"},
                           before_url="https://site.example.test/?p=2", run_id="proof-test-1")
    folder = tmp_path / "data" / "proofs" / "proof-test-1"
    stored = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert stored == run
    assert run["verdict"] == "match"
    assert run["target"] == {"kind": "url", "url": "https://site.example.test/?p=1", "site": "site",
                             "wp_id": None, "item": None}
    assert [s["width"] for s in run["shots"]] == [1440, 390]
    assert run["shots"][0]["file"] == "1440.png" and run["shots"][0]["before_file"] == "1440-before.png"
    assert set(run["perf"]) == {"1440", "390"}
    assert [c["prefix"] for c in calls] == ["", "before"]
    assert all(c["out_dir"] == folder for c in calls)
    assert all(c["connect_ip"] == "192.0.2.10" and c["extra_hosts"] == ("fonts.example.test",) for c in calls)
    assert sorted(p.name for p in folder.iterdir()) == ["1440-before.png", "1440.png", "390-before.png",
                                                        "390.png", "run.json"]
    diff_rows = [c for c in run["checks"] if c["check"] == "diff"]
    try:
        import PIL  # noqa: F401
    except ImportError:
        assert all(r["verdict"] == "not_proven" and "Pillow" in r["detail"] for r in diff_rows)
        assert run["diff"] == []
    else:
        assert run["diff"] == [{"width": 1440, "pct": 50.0}, {"width": 390, "pct": 50.0}]
    assert str(tmp_path) not in json.dumps(run), "no absolute path in the stored run"


def test_run_proof_refuses_while_the_slot_is_held(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "capture", fake_capture(calls))
    store = store_mod.Store(tmp_path / "data")
    with store.chromium_slot():
        with pytest.raises(store_mod.Busy):
            checks.run_proof(store=store, url="https://site.example.test/", host="site.example.test",
                             connect_ip="", widths=[390], extra_hosts=[], chromium_path="",
                             target={"kind": "url"})
    assert calls == []
    run = checks.run_proof(store=store, url="https://site.example.test/", host="site.example.test",
                           connect_ip="", widths=[390], extra_hosts=[], chromium_path="", target={})
    assert run["verdict"] == "match" and run["target"]["kind"] == "url"


def test_run_proof_holds_the_slot_during_capture(tmp_path, monkeypatch):
    store = store_mod.Store(tmp_path / "data")
    seen = []

    def capture(url, **kw):
        with pytest.raises(store_mod.Busy):
            with store_mod.Store(tmp_path / "data").chromium_slot():
                pass
        seen.append(url)
        return {"chromium": None, "widths": {}, "blocked_hosts": [], "error": "no Chromium found"}
    monkeypatch.setattr(runner, "capture", capture)
    run = checks.run_proof(store=store, url="https://site.example.test/", host="site.example.test",
                           connect_ip="", widths=[390], extra_hosts=[], chromium_path="", target={})
    assert seen and run["verdict"] == "not_proven" and run["error"] == "no Chromium found"


@pytest.mark.parametrize("bad", ["../escape", "Proof_1", "a/b"])
def test_run_proof_refuses_unsafe_ids(tmp_path, bad):
    store = store_mod.Store(tmp_path / "data")
    with pytest.raises(store_mod.StoreError):
        checks.run_proof(store=store, url="https://site.example.test/", host="site.example.test",
                         connect_ip="", widths=[390], extra_hosts=[], chromium_path="", target={},
                         run_id=bad)


def test_run_proof_refuses_unknown_target_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "capture", fake_capture([]))
    with pytest.raises(ValueError):
        checks.run_proof(store=store_mod.Store(tmp_path), url="https://site.example.test/",
                         host="site.example.test", connect_ip="", widths=[390], extra_hosts=[],
                         chromium_path="", target={"kind": "publish"})


# --- Pillow diff ----------------------------------------------------------------------------------

def test_diff_png_counts_changed_pixels(tmp_path):
    pytest.importorskip("PIL")
    diff = mod("proof.diff")
    a = write_png(tmp_path / "a.png", 10, 10, lambda x, y: (255, 255, 255))
    b = write_png(tmp_path / "b.png", 10, 10, lambda x, y: (0, 0, 0) if y < 5 else (255, 255, 255))
    c = write_png(tmp_path / "c.png", 10, 10, lambda x, y: (250, 250, 250))
    assert diff.diff_png(a, a) == {"pct": 0.0, "changed": 0, "total": 100, "size": [10, 10]}
    assert diff.diff_png(a, b)["pct"] == 50.0
    assert diff.diff_png(a, c)["changed"] == 0, "below the threshold"
    taller = write_png(tmp_path / "t.png", 10, 20, lambda x, y: (255, 255, 255) if y < 10 else (0, 0, 0))
    assert diff.diff_png(a, taller) == {"pct": 50.0, "changed": 100, "total": 200, "size": [10, 20]}


def test_thumbnail_is_small_jpeg(tmp_path):
    pytest.importorskip("PIL")
    diff = mod("proof.diff")
    big = write_png(tmp_path / "big.png", 720, 1600, lambda x, y: ((x * 7) % 256, (y * 3) % 256, 90))
    data = diff.thumbnail(big, width=360)
    assert data[:3] == b"\xff\xd8\xff"
    from PIL import Image
    import io
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (360, 450)
