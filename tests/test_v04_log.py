"""AE1: the write log. Rotation at 5 MB and at 90 days, whole lines from two processes, every LogQuery
filter, one line per write through wp_draft (with the label), a refusal's code, wp_doctor action=log and
GET /sites/{id}/activity (last 50, newest first)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from wpb_fakes import ROLE_CAPS, FakeWP, Settings, create_paired, load_plugin, mod, secrets

T0 = 1_790_000_000.0
DAY = 86400.0
HERE = Path(__file__).resolve().parent


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def store(tmp_path):
    return mod("store").Store(tmp_path)


def log_of(tmp_path, clock=None, **kw):
    return mod("activity").ActivityLog(store(tmp_path), clock=clock or Clock(), **kw)


def lines_in(path: Path) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def add(log, n: int, **fields):
    base = {"site": "s", "tool": "wp_draft", "action": "update", "wp_type": "post", "wp_id": 1, "label": "x",
            "result": "ok", "summary": "s"}
    return log.append(**{**base, "label": f"line {n}", **fields})


# --- rotation -----------------------------------------------------------------------------------------

def test_rotation_at_5_mb_drops_the_oldest_lines_and_keeps_the_newest(tmp_path):
    activity = mod("activity")
    log = log_of(tmp_path)
    assert log.max_bytes == 5 * 1024 * 1024
    one = (json.dumps(log.line(site="s", tool="wp_draft", action="update", label="old", summary="y" * 200,
                               result="ok")) + "\n").encode()
    path = log.path("s")
    count = log.max_bytes // len(one)
    path.write_bytes(one * count)
    assert path.stat().st_size <= log.max_bytes
    add(log, 1, label="the newest")
    size = path.stat().st_size
    rows = lines_in(path)
    assert size <= log.max_bytes * activity.TRIM_TO
    assert rows[-1]["label"] == "the newest" and len(rows) < count
    assert all(r["label"] == "old" for r in rows[:-1])


def test_rotation_at_90_days_with_the_clock_stepped(tmp_path):
    clock = Clock()
    log = log_of(tmp_path, clock)
    add(log, 1)
    clock.t += 30 * DAY
    add(log, 2)
    clock.t = T0 + 89 * DAY
    add(log, 3)
    assert [r["label"] for r in lines_in(log.path("s"))] == ["line 1", "line 2", "line 3"]
    clock.t = T0 + 91 * DAY
    add(log, 4)
    assert [r["label"] for r in lines_in(log.path("s"))] == ["line 2", "line 3", "line 4"]
    clock.t = T0 + 200 * DAY
    add(log, 5)
    assert [r["label"] for r in lines_in(log.path("s"))] == ["line 5"]


CHILD = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    from wpb_fakes import mod
    data, tag, n, max_bytes, go = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
    log = mod("activity").ActivityLog(mod("store").Store(data), max_bytes=max_bytes)
    while not os.path.exists(go):
        time.sleep(0.005)
    for i in range(n):
        log.append(site="s", tool="wp_draft", action="update", wp_id=i, label=f"{tag} {i}", result="ok",
                   summary=tag * 230)
""")


def two_processes(tmp_path, n: int, max_bytes: int) -> list:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    data, go = tmp_path / "data", tmp_path / "go"
    data.mkdir()
    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    procs = [subprocess.Popen([sys.executable, str(script), str(HERE), str(data), tag, str(n), str(max_bytes), str(go)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for tag in ("a", "b")]
    go.write_text("go")
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    raw = (data / "logs" / "s.jsonl").read_bytes()
    assert raw.endswith(b"\n")
    return [json.loads(l) for l in raw.splitlines()]  # a torn or interleaved line fails here


def test_two_processes_appending_keep_whole_lines(tmp_path):
    rows = two_processes(tmp_path, 400, 50 * 1024 * 1024)
    assert len(rows) == 800
    for tag in ("a", "b"):
        assert [r["wp_id"] for r in rows if r["label"].startswith(tag + " ")] == list(range(400))


def test_two_processes_appending_through_rotations_lose_no_line(tmp_path):
    rows = two_processes(tmp_path, 600, 32 * 1024)
    assert 0 < len(rows) < 1200
    for tag in ("a", "b"):
        ids = [r["wp_id"] for r in rows if r["label"].startswith(tag + " ")]
        # rotation drops only the oldest lines, so what each process still has is one unbroken run
        assert ids == list(range(ids[0], ids[0] + len(ids))) if ids else True, ids
    assert any(r["wp_id"] == 599 for r in rows)


def test_an_append_waits_for_the_store_lock(tmp_path):
    import threading
    import time as time_mod
    log = log_of(tmp_path)
    held, release = threading.Event(), threading.Event()

    def holder():
        with log.store.lock("logs-s"):
            held.set()
            release.wait(5)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    threading.Timer(0.5, release.set).start()
    started = time_mod.monotonic()
    add(log, 1)
    waited = time_mod.monotonic() - started
    t.join(5)
    assert waited >= 0.4 and [r["label"] for r in lines_in(log.path("s"))] == ["line 1"]


# --- LogQuery --------------------------------------------------------------------------------------------

def seeded(tmp_path):
    clock = Clock()
    log = log_of(tmp_path, clock)
    rows = [
        dict(site="s", tool="wp_draft", action="create", wp_id=10, label="About Us", result="ok", summary="created"),
        dict(site="s", tool="wp_draft", action="update", wp_id=11, label="Five things", result="refused",
             code="live_post", summary="update post 11 refused: post 11 is publish"),
        dict(site="s", tool="page", action="publish", wp_id=10, label="About Us", result="ok", summary="published"),
        dict(site="t", tool="wp_seo", action="stage_fix", wp_id=10, label="Other site page", result="ok",
             summary='staged SEO; title "Old name" → "New name"'),
        dict(site="s", tool="wp_draft", action="stage", wp_id=12, label="Contact", result="error", code="http",
             summary="stage page 12 failed"),
    ]
    for i, r in enumerate(rows):
        clock.t = T0 + i * DAY
        log.append(**r)
    return log


@pytest.mark.parametrize("query,labels", [
    ({}, ["Contact", "Other site page", "About Us", "Five things", "About Us"]),
    ({"site": "s"}, ["Contact", "About Us", "Five things", "About Us"]),
    ({"wp_id": 10}, ["Other site page", "About Us", "About Us"]),
    ({"wp_id": "10", "site": "s"}, ["About Us", "About Us"]),
    ({"label": "about us"}, ["About Us", "About Us"]),
    ({"label": "THINGS"}, ["Five things"]),
    ({"q": "old name"}, ["Other site page"]),
    ({"q": "live_post"}, ["Five things"]),
    ({"tool": "page"}, ["About Us"]),
    ({"action": "create"}, ["About Us"]),
    ({"result": "refused"}, ["Five things"]),
    ({"result": "error"}, ["Contact"]),
    ({"since": "2026-09-23"}, ["Contact", "Other site page", "About Us"]),
    ({"until": "2026-09-23"}, ["About Us", "Five things", "About Us"]),
    ({"since": "2026-09-22T00:00:00Z", "until": "2026-09-23"}, ["About Us", "Five things"]),
    ({"limit": 2}, ["Contact", "Other site page"]),
])
def test_each_logquery_filter(tmp_path, query, labels):
    log = seeded(tmp_path)
    assert [l["label"] for l in log.query(query)["lines"]] == labels


def test_the_default_limit_is_50_newest_first(tmp_path):
    clock = Clock()
    log = log_of(tmp_path, clock)
    for i in range(60):
        clock.t = T0 + i
        add(log, i)
    got = log.query({"site": "s"})
    assert got["limit"] == 50 and got["matched"] == 60
    assert [l["label"] for l in got["lines"]] == [f"line {i}" for i in range(59, 9, -1)]


@pytest.mark.parametrize("query", [{"wp_id": "x"}, {"result": "maybe"}, {"since": "last tuesday"}, {"site": "../etc"}])
def test_a_bad_query_is_refused_with_a_reason(tmp_path, query):
    with pytest.raises(Exception) as e:
        seeded(tmp_path).query(query)
    assert str(e.value)


def test_a_line_keeps_no_body_and_a_short_label(tmp_path):
    line = add(log_of(tmp_path), 1, label="T" * 400, summary="S" * 900, content="<p>body</p>",
               read_back={"status": "publish", "content_hash": "abc", "link": "https://x/?p=1", "title": "t"})
    assert set(line) == set(mod("activity").FIELDS)
    assert len(line["label"]) <= 120 and len(line["summary"]) <= 240
    assert line["read_back"] == {"status": "publish", "content_hash": "abc"}


# --- the writes log themselves ----------------------------------------------------------------------------

def setup(tmp_path):
    fake = FakeWP(user="hermes", caps=ROLE_CAPS["editor"], roles=["editor"], namespaces=["wp/v2"])
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1}, "person")
    return fake, rt


def handlers(rt, fake, run=None):
    plugin = load_plugin()
    run = run or mod("lanes").RunContext()
    kw = {"transport": fake, "wporg_fetch": lambda u: {"version": "1"}}
    tickets = mod("hooks").Tickets()
    draft = plugin.make_draft_handler(rt.get_setting, rt.store.root, lambda: run, rt.get_secret, tickets=tickets, **kw)
    doctor = plugin.make_doctor_handler(rt.get_setting, rt.store.root, rt.get_secret, **kw)
    return draft, doctor


def site_lines(rt) -> list:
    path = rt.store.root / "logs" / "s.jsonl"
    return lines_in(path) if path.exists() else []


def test_a_wp_draft_update_logs_one_line_with_the_label(tmp_path):
    fake, rt = setup(tmp_path)
    draft, doctor = handlers(rt, fake)
    post = fake.add_post(status="draft", title="Old title")
    out = json.loads(draft({"site": "s", "action": "update", "id": post["id"], "title": "Spring offers"},
                           session_id="sess-7"))
    assert out["ok"]
    rows = site_lines(rt)
    assert len(rows) == 1
    line = rows[0]
    assert (line["tool"], line["action"], line["wp_type"], line["wp_id"], line["result"]) == \
        ("wp_draft", "update", "post", post["id"], "ok")
    assert line["label"] == "Spring offers" and line["who"] == "agent:sess-7" and line["lane"] == "chat"
    assert line["desk_item"] == out["desk_item"]
    assert '"Old title" → "Spring offers"' in line["summary"]
    asked = json.loads(doctor({"action": "log", "label": "spring"}))
    assert [l["wp_id"] for l in asked["lines"]] == [post["id"]]
    by_old_name = json.loads(doctor({"action": "log", "q": "old title"}))
    assert by_old_name["count"] == 1


def test_a_stage_logs_the_live_title_and_the_copy(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt, fake)
    live = fake.add_post(ptype="page", status="publish", title="Blocks demo")
    out = json.loads(draft({"site": "s", "action": "stage", "type": "page", "id": live["id"], "title": "Blocks demo v2"}))
    assert out["ok"]
    (line,) = site_lines(rt)
    assert line["action"] == "stage" and line["wp_id"] == live["id"] and line["label"] == "Blocks demo"
    assert f"draft copy {out['wp_id']}" in line["summary"] and "live page is unchanged" in line["summary"]


def test_a_refused_write_logs_its_code(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt, fake)
    live = fake.add_post(status="publish", title="Live news")
    out = json.loads(draft({"site": "s", "action": "update", "id": live["id"], "title": "x"}))
    assert out["error"] == "live_post"
    (line,) = site_lines(rt)
    assert (line["result"], line["code"], line["label"], line["wp_id"]) == ("refused", "live_post", "Live news", live["id"])


def test_a_gate_refusal_logs_once_and_the_gate_answer_is_unchanged(tmp_path):
    fake, rt = setup(tmp_path)
    plugin = load_plugin()
    gate = mod("hooks").make_gate({"wp_draft": mod("hooks").Spec(lambda a: True, lambda a, r: plugin.decide_draft(rt, a, r))},
                                  lambda: mod("lanes").RunContext(cron=True), lambda: True, mod("hooks").Tickets())
    verdict = gate("wp_draft", {"site": "s", "action": "create", "title": "Weekly"}, "sess")
    assert verdict["action"] == "block" and "unattended" in verdict["message"]
    (line,) = site_lines(rt)
    assert (line["result"], line["code"], line["label"], line["lane"]) == ("refused", "unattended", "Weekly", "automation")
    assert not [r for r in fake.requests if r["method"] != "GET"]


def test_the_persons_publish_click_logs_a_line(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt, fake)
    out = json.loads(draft({"site": "s", "action": "create", "title": "Launch day"}))
    status, body = mod("api").desk_act(rt, out["desk_item"], "publish")
    assert status == 200
    line = site_lines(rt)[-1]
    assert (line["tool"], line["action"], line["who"], line["lane"], line["label"]) == \
        ("page", "publish", "person", "desk", "Launch day")
    assert line["read_back"]["status"] == "publish"


def test_a_log_problem_never_changes_the_write(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt, fake)
    (tmp_path / "logs").write_text("not a folder")
    out = json.loads(draft({"site": "s", "action": "create", "title": "Still written"}))
    assert out["ok"] and fake.posts[out["wp_id"]]["status"] == "draft"


def test_doctor_log_says_when_there_is_nothing(tmp_path):
    fake, rt = setup(tmp_path)
    _, doctor = handlers(rt, fake)
    out = json.loads(doctor({"action": "log", "site": "s", "label": "never written"}))
    assert out["ok"] and out["lines"] == [] and "no line" in out["note"]
    assert json.loads(doctor({"action": "log", "site": "nope"}))["error"] == "not_found"
    assert json.loads(doctor({"action": "log", "result": "maybe"}))["error"] == "bad_args"


def test_the_route_answers_the_last_50_newest_first(tmp_path):
    fake, rt = setup(tmp_path)
    clock = Clock()
    log = mod("activity").ActivityLog(rt.store, clock=clock)
    for i in range(60):
        clock.t = T0 + i
        add(log, i)
    status, body = mod("api").site_activity(rt, "s")
    assert status == 200 and body["ok"]
    assert [l["label"] for l in body["lines"]] == [f"line {i}" for i in range(59, 9, -1)]
    assert mod("api").site_activity(rt, "nope")[0] == 404


def test_the_route_is_mounted():
    source = (HERE.parent / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    assert '@router.get("/sites/{site_id}/activity")' in source and "site_activity(_rt(), site_id)" in source


def test_doctor_schema_offers_log():
    schema = mod("tools.doctor").SCHEMA["parameters"]["properties"]
    assert schema["action"]["enum"] == ["check", "log"]
    assert {"wp_id", "label", "q", "tool", "log_action", "result", "since", "until", "limit"} <= set(schema)


def test_site_file_changes_desk_notes_and_page_clicks_log(tmp_path):
    fake, rt = setup(tmp_path)
    plugin = load_plugin()
    kw = {"transport": fake, "wporg_fetch": lambda u: {"version": "1"}}
    sites = plugin.make_gated_sites_handler(rt.get_setting, rt.store.root, lambda: mod("lanes").RunContext(),
                                            rt.get_secret, tickets=mod("hooks").Tickets(), **kw)
    desk = plugin.make_desk_handler(rt.get_setting, rt.store.root, rt.get_secret, **kw)
    draft, _ = handlers(rt, fake)
    assert json.loads(sites({"action": "update", "site": "s", "changes": {"label": "Shop"}}, session_id="x"))["ok"]
    assert json.loads(sites({"action": "update", "site": "s", "changes": {"label": "Preview"}, "preview": True}))["ok"]
    refused = json.loads(sites({"action": "update", "site": "s", "changes": {"url": "https://evil.example"}}))
    assert refused["ok"] is False
    item = json.loads(draft({"site": "s", "action": "create", "title": "Note me"}))["desk_item"]
    assert json.loads(desk({"action": "note", "item": item, "text": "check the intro"}))["ok"]
    assert mod("api").desk_review(rt, item)[0] == 200
    assert mod("api").site_patch(rt, "s", {"changes": {"label": "Shop 2"}})[0] == 200
    rows = [(l["tool"], l["action"], l["result"]) for l in site_lines(rt)]
    assert rows == [("wp_sites", "update_site", "ok"), ("wp_sites", "update_site", "refused"),
                    ("wp_draft", "create", "ok"), ("wp_desk", "note", "ok"), ("page", "mark_reviewed", "ok"),
                    ("page", "update_site", "ok")]
    assert site_lines(rt)[4]["label"] == "Note me" and site_lines(rt)[5]["who"] == "person"
