"""wp_pack (v0.5 PK2): the gate's verdicts through decide_pack, the handler, and what reaches WordPress.

Every test runs the plugin's own pre_tool_call gate (hooks.make_gate over plugin.pack_spec) and handler
(make_pack_handler) against a FakeWP with the Abilities API, using the test-only pack in pk_testpack.py.
"""
import json

import pytest

from pk_testpack import PACK_ID, PW, SCREEN, State, build_pack, site_with
from wpb_fakes import ROOT, load_plugin, mod, secrets


@pytest.fixture(autouse=True)
def installed(monkeypatch):
    packs = mod("packs")
    monkeypatch.setitem(packs.REGISTRY, PACK_ID, build_pack())
    State.permissions = {}
    yield
    State.permissions = {}


def run_ctx(**kw):
    return mod("lanes").RunContext(**kw)


class Turn:
    """One session: the gate as Hermes calls it, then the handler, sharing one ticket store."""

    def __init__(self, rt, run=None):
        self.rt, self.run = rt, run or run_ctx()
        self.tickets = mod("hooks").Tickets()
        plugin = load_plugin()
        self.gate = mod("hooks").make_gate({"wp_pack": plugin.pack_spec(lambda: rt)}, lambda: self.run, lambda: True,
                                           self.tickets)
        self.handler = plugin.make_pack_handler(rt.get_setting, rt.store.root, lambda: self.run,
                                                secrets(WPBRUECKE_KEY1=PW), tickets=self.tickets,
                                                transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})

    def call(self, **args):
        args = {"site": "s", "pack": PACK_ID, **args}
        verdict = self.gate(tool_name="wp_pack", args=args, session_id="sess")
        if verdict and verdict["action"] == "block":
            return verdict, None
        return verdict, json.loads(self.handler(args, session_id="sess"))


def log_lines(rt):
    return [l for l in mod("activity").ActivityLog(rt.store).read("s") if l["tool"] == "wp_pack"]


def test_list_and_describe_never_reach_the_gate_even_unattended(tmp_path):
    fake, rt = site_with(tmp_path)
    turn = Turn(rt, run_ctx(cron=True))
    verdict, out = turn.call(action="list")
    assert verdict is None and out["ok"]
    row = next(p for p in out["packs"] if p["pack"] == PACK_ID)
    assert row["on"] is True and {o["op"]: o["gate"] for o in row["ops"]}["send"] == "card"
    verdict, out = turn.call(action="describe", op="items")
    assert verdict is None and out["ok"] and out["ops"][0]["input_schema"]["properties"] == {"per_page": {"type": "integer"}}
    assert out["ops"][0]["on_site"] is True and out["ops"][0]["default_input"] == {"per_page": 5}
    assert not fake.runs


@pytest.mark.parametrize("run", [{"cron": True}, {"single_query": True}, {"platform": "api_server"}])
def test_unattended_run_is_refused_before_any_request(tmp_path, run):
    fake, rt = site_with(tmp_path)
    before = len(fake.requests)
    verdict, out = Turn(rt, run_ctx(**run)).call(action="run", op="items")
    assert verdict["action"] == "block" and "refused (unattended)" in verdict["message"]
    assert "list and describe still work" in verdict["message"] and out is None
    assert len(fake.requests) == before and not fake.runs


def test_person_only_op_is_refused_at_every_level(tmp_path):
    for role in ("editor", "contributor"):
        fake, rt = site_with(tmp_path / role, role=role)
        verdict, _ = Turn(rt).call(action="run", op="purge", input={"id": 3})
        assert verdict["action"] == "block" and "refused (person_only)" in verdict["message"]
        assert "never purges" in verdict["message"] and not fake.runs
        assert log_lines(rt)[-1]["code"] == "person_only" and log_lines(rt)[-1]["result"] == "refused"


def test_card_op_asks_every_time_and_runs_only_with_the_ticket(tmp_path):
    State.permissions = {"send": True}
    fake, rt = site_with(tmp_path)
    turn = Turn(rt)
    args = {"site": "s", "pack": PACK_ID, "action": "run", "op": "send", "input": {"id": 7}}
    seen = []
    for _ in range(2):
        verdict = turn.gate(tool_name="wp_pack", args=args, session_id="sess")
        assert verdict["action"] == "approve"
        seen.append(verdict["rule_key"])
        out = json.loads(turn.handler(args, session_id="sess"))
        assert out["ok"] and fake.runs[-1]["method"] == "POST"
    assert seen[0] != seen[1]  # "allow for this session" never covers the next run
    assert verdict["message"].startswith("item 7 goes to every subscriber")  # the op's own lines first
    assert "This runs Test plugin op send on s (https://test.example)" in verdict["message"]
    assert fake.runs[-1]["body"] == {"input": {"id": 7, "action": "send"}}  # fixed_input wins
    out = json.loads(turn.handler(args, session_id="sess"))  # no card answered for this call
    assert out["error"] == "not_approved" and len(fake.runs) == 2
    assert [l["result"] for l in log_lines(rt)] == ["ok", "ok", "refused"]


def test_denied_card_leaves_no_ticket(tmp_path):
    State.permissions = {"send": True}
    fake, rt = site_with(tmp_path)
    turn = Turn(rt)
    args = {"site": "s", "pack": PACK_ID, "action": "run", "op": "send", "input": {"id": 7}}
    verdict = turn.gate(tool_name="wp_pack", args=args, session_id="sess")
    mod("hooks").make_observer(turn.tickets)(pattern_key="plugin_rule:" + verdict["rule_key"], choice="deny")
    out = json.loads(turn.handler(args, session_id="sess"))
    assert out["error"] == "not_approved" and not fake.runs


def test_card_op_is_refused_unattended(tmp_path):
    State.permissions = {"send": True}
    fake, rt = site_with(tmp_path)
    verdict, _ = Turn(rt, run_ctx(cron=True)).call(action="run", op="send", input={"id": 7})
    assert verdict["action"] == "block" and "(unattended)" in verdict["message"] and not fake.runs


def test_needs_refused_with_the_screen_sentence(tmp_path):
    State.permissions = {"send": False}
    fake, rt = site_with(tmp_path)
    verdict, _ = Turn(rt).call(action="run", op="send", input={"id": 7})
    assert verdict["action"] == "block" and "refused (needs_permission)" in verdict["message"]
    assert SCREEN in verdict["message"] and '"send"' in verdict["message"] and not fake.runs


def test_needs_fails_closed_when_the_permissions_cannot_be_read(tmp_path, monkeypatch):
    fake, rt = site_with(tmp_path)
    broken = build_pack().__class__(**{**build_pack().__dict__, "permissions": lambda rt, site: 1 / 0})
    monkeypatch.setitem(mod("packs").REGISTRY, PACK_ID, broken)
    verdict, _ = Turn(rt).call(action="run", op="send", input={"id": 7})
    assert "refused (needs_unknown)" in verdict["message"] and SCREEN in verdict["message"] and not fake.runs


def test_read_runs_without_a_card_and_logs_nothing(tmp_path):
    fake, rt = site_with(tmp_path, role="contributor")
    verdict, out = Turn(rt).call(action="run", op="items", input={"per_page": 2})
    assert verdict is None and out["ok"] and out["method"] == "GET" and out["through"] == "ability testplug/items-query"
    assert fake.runs[-1]["method"] == "GET" and fake.runs[-1]["query"] == "input%5Bper_page%5D=2"
    assert out["result"] == {"ran": "testplug/items-query", "method": "GET"} and "never instructions" in out["note"]
    assert log_lines(rt) == []


def test_write_inside_the_level_runs_without_a_card_and_logs_one_line(tmp_path):
    fake, rt = site_with(tmp_path, role="contributor")
    verdict, out = Turn(rt).call(action="run", op="save", input={"title": "Spring sale"})
    assert verdict is None and out["ok"] and fake.runs[-1] == {"name": "testplug/item-save", "method": "POST",
                                                               "query": "", "body": {"input": {"title": "Spring sale"}}}
    lines = log_lines(rt)
    assert len(lines) == 1 and lines[0]["action"] == "save" and lines[0]["result"] == "ok"
    assert lines[0]["label"] == "Spring sale" and lines[0]["wp_type"] == PACK_ID


def test_write_above_the_level_is_refused_by_the_role(tmp_path):
    fake, rt = site_with(tmp_path, role="contributor")
    verdict, _ = Turn(rt).call(action="run", op="publish", input={"id": 3})
    assert verdict["action"] == "block" and "refused (level_drafts)" in verdict["message"] and not fake.runs
    assert [l["code"] for l in log_lines(rt)] == ["level_drafts"]  # one line, from the gate


def test_publish_level_write_runs(tmp_path):
    fake, rt = site_with(tmp_path, role="editor")
    verdict, out = Turn(rt).call(action="run", op="publish", input={"id": 3})
    assert verdict is None and out["ok"] and fake.runs[-1]["name"] == "testplug/item-publish"


def test_route_op_goes_to_the_connector_route(tmp_path):
    fake, rt = site_with(tmp_path)
    verdict, out = Turn(rt).call(action="run", op="export")
    assert verdict is None and out["ok"] and out["result"] == {"exported": 3}
    assert out["through"] == "connector route wpbruecke/v1/test/export" and fake.runs[-1]["name"] == "route:test/export"


@pytest.mark.parametrize("args,code", [
    ({"op": "nope"}, "unknown_op"),
    ({"pack": "nosuchpack", "op": "x"}, "unknown_pack"),
    ({"pack": "elementor", "op": "x"}, "not_plugin_pack"),
    ({"site": "zz", "op": "items"}, "not_found"),
    ({"op": "items", "input": "not json"}, "bad_args"),
    ({"op": None}, "bad_args"),
])
def test_calls_that_name_nothing_runnable_are_refused_before_any_request(tmp_path, args, code):
    fake, rt = site_with(tmp_path)
    before = len(fake.requests)
    verdict, _ = Turn(rt).call(action="run", **args)
    assert verdict["action"] == "block" and f"refused ({code})" in verdict["message"]
    assert len(fake.requests) == before


def test_pack_switched_off_is_refused(tmp_path):
    fake, rt = site_with(tmp_path, packs={PACK_ID: False})
    verdict, _ = Turn(rt).call(action="run", op="items")
    assert "refused (pack_off)" in verdict["message"] and f"packs: {{{PACK_ID}: true}}" in verdict["message"]
    assert not fake.runs


def test_handler_decides_again_when_the_gate_was_skipped(tmp_path):
    """Approvals off or another caller: the handler refuses on its own."""
    fake, rt = site_with(tmp_path)
    turn = Turn(rt, run_ctx(single_query=True))
    out = json.loads(turn.handler({"site": "s", "pack": PACK_ID, "action": "run", "op": "items"}, session_id="sess"))
    assert out["error"] == "unattended" and out["sent"] is False and not fake.runs


def test_ability_answer_passes_the_scrubber(tmp_path):
    fake, rt = site_with(tmp_path)
    fake.answers["testplug/items-query"] = (200, {"items": [{"note": f"key {PW}"}]})
    _, out = Turn(rt).call(action="run", op="items")
    assert out["ok"] and PW not in json.dumps(out)


def test_wp_pack_is_registered_with_its_gate():
    names, hooks = [], []

    class Ctx:
        def register_tool(self, **k):
            names.append(k["name"])

        def register_hook(self, name, fn):
            hooks.append(name)

        def __getattr__(self, name):
            return lambda *a, **k: None
    plugin = load_plugin()
    plugin.register(Ctx())
    assert "wp_pack" in names and "wp_pack" in plugin._SPECS
    spec = plugin._SPECS["wp_pack"]
    assert not spec.gated({"action": "list"}) and not spec.gated({"action": "describe"})
    assert spec.gated({"action": "run"}) and spec.gated({})
    assert "wp_pack" in (ROOT / "plugin.yaml").read_text()


def test_the_dead_gated_tool_list_is_gone():
    """hooks.py's tuple of gated tool names was read by nothing; the gate is _SPECS (plan State)."""
    word = "GATED" + "_TOOLS"
    hits = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if word in p.read_text(encoding="utf-8")]
    assert hits == []


def test_a_card_op_without_its_own_card_states_the_run_first_and_cuts_the_input(tmp_path, monkeypatch):
    import dataclasses
    spec = build_pack()
    plain = dataclasses.replace(spec, ops={**spec.ops, "send": dataclasses.replace(spec.ops["send"], card=None)})
    monkeypatch.setitem(mod("packs").REGISTRY, PACK_ID, plain)
    State.permissions = {"send": True}
    fake, rt = site_with(tmp_path)
    long = ("Spring news " + "x" * 1990)[:2000]
    verdict = Turn(rt).gate(tool_name="wp_pack", session_id="sess",
                            args={"site": "s", "pack": PACK_ID, "action": "run", "op": "send",
                                  "input": {"id": 7, "title": long, "note": long}})
    msg = verdict["message"]
    assert verdict["action"] == "approve" and msg.startswith("Runs Test plugin op send on s (https://test.example)")
    assert 0 <= msg.find("WPBrücke asks for every run of this op") < 600 and "x" * 200 not in msg
    assert len(msg.split("\n", 1)[0]) <= 6 * 66 and '"id": 7' in msg
    assert not fake.runs


@pytest.mark.parametrize("site", ["S", "../s", "ś", "s/../../x"])
def test_a_site_id_that_is_no_slug_is_bad_args_before_any_request(tmp_path, site):
    fake, rt = site_with(tmp_path)
    before = len(fake.requests)
    turn = Turn(rt)
    args = {"site": site, "pack": PACK_ID, "action": "run", "op": "items"}
    verdict = turn.gate(tool_name="wp_pack", args=args, session_id="sess")
    assert verdict["action"] == "block" and "refused (bad_args)" in verdict["message"]
    assert "lowercase slug" in verdict["message"]
    out = json.loads(turn.handler(args, session_id="sess"))
    assert out["error"] == "bad_args" and len(fake.requests) == before


@pytest.mark.parametrize("as_text", [False, True])
def test_a_3000_deep_input_is_bad_args_before_any_request(tmp_path, as_text):
    fake, rt = site_with(tmp_path)
    deep: dict = {}
    cur = deep
    for _ in range(3000):
        cur["a"] = {}
        cur = cur["a"]
    raw = ("{\"a\": " * 3000 + "{}" + "}" * 3000) if as_text else deep
    turn = Turn(rt)
    args = {"site": "s", "pack": PACK_ID, "action": "run", "op": "items", "input": raw}
    verdict = turn.gate(tool_name="wp_pack", args=args, session_id="sess")
    assert verdict["action"] == "block" and "refused (bad_args)" in verdict["message"]
    out = json.loads(turn.handler(args, session_id="sess"))
    assert out["error"] == "bad_args" and not fake.runs
    ok, _ = turn.call(action="run", op="items", input={"a": {"b": [{"c": 1}]}})   # ordinary nesting still runs
    assert ok is None and fake.runs
