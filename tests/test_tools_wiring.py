"""Tool wiring for wp_proof, wp_seo and wp_build (the layers themselves are tested in their own files)."""
import json

from wpb_fakes import FakeWP, Settings, create_paired, load_plugin, mod, secrets

PW = secrets(WPBRUECKE_KEY1="pw pw pw")
WPORG = {"wporg_fetch": lambda u: {"version": "1"}}


def make(tmp_path, caps=None, **site):
    fake = FakeWP(user="hermes", caps=caps or {"edit_posts": True, "upload_files": True})
    rt = mod("runtime").Runtime(Settings(), PW, tmp_path, transport=fake, **WPORG)
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1, **site}, "person")
    return fake, rt


def test_proof_of_a_draft_without_connector_is_not_proven_and_checks_alt_in_html(tmp_path):
    fake, rt = make(tmp_path)
    post = fake.add_post(status="draft", content='<p>x</p><img src="a.png"><img src="b.png" alt="B">')
    out = mod("tools.proof").handle(rt, {"site": "s", "id": post["id"]})
    assert out["ok"] and out["verdict"] == "not_proven"
    chk = {c["check"]: c for c in out["checks"]}
    assert "needs the WPBrücke connector" in chk["render"]["detail"]
    assert chk["alt_text"]["verdict"] == "differs" and "1 of 2" in chk["alt_text"]["detail"]
    assert not [r for r in fake.requests if r["method"] != "GET"]


def test_proof_refuses_a_url_on_another_host(tmp_path):
    fake, rt = make(tmp_path)
    out = mod("tools.proof").handle(rt, {"site": "s", "url": "https://elsewhere.example/"})
    assert out == {"ok": False, "error": "bad_target", "message": out["message"]} and "not on test.example" in out["message"]


def test_seo_check_post_report_is_saved_and_stage_fix_goes_through_the_draft_lane(tmp_path):
    fake, rt = make(tmp_path)
    post = fake.add_post(status="draft", title="Hi", content="<p>Short text about gardens.</p>")
    rep = mod("tools.seo").handle(rt, {"action": "check_post", "site": "s", "id": post["id"]})
    assert rep["ok"] and rep["report"]["scope"] == "post" and rep["report"]["checks"]
    rid = rep["report"]["id"]
    assert mod("tools.seo").handle(rt, {"action": "get", "report": rid})["report"]["id"] == rid
    calls = []

    def run_draft(a):
        calls.append(a)
        return {"ok": True, "desk_item": None}
    out = mod("tools.seo").handle(rt, {"action": "stage_fix", "report": rid, "fix": "description", "value": "A garden note."},
                                  run_draft=run_draft)
    assert out["ok"] and calls == [{"site": "s", "action": "set_seo", "type": "post", "id": post["id"],
                                    "seo": {"description": "A garden note."}, "note": f"SEO fix from report {rid}: description"}]


def test_seo_stage_fix_is_gated_unattended(tmp_path):
    fake, rt = make(tmp_path)
    post = fake.add_post(status="draft", title="Hi", content="<p>Text.</p>")
    rid = mod("tools.seo").handle(rt, {"action": "check_post", "site": "s", "id": post["id"]})["report"]["id"]
    plugin = load_plugin()
    d = plugin.decide_seo(rt, {"action": "stage_fix", "report": rid, "fix": "description", "value": "x"},
                          mod("lanes").RunContext(single_query=True))
    assert d["verdict"] == "block"
    d = plugin.decide_seo(rt, {"action": "stage_fix", "report": rid, "fix": "description", "value": "x"},
                          mod("lanes").RunContext())
    assert d["verdict"] in ("card", "allow")


def test_build_needs_the_pack_switched_on(tmp_path):
    fake, rt = make(tmp_path)
    out = mod("tools.build").handle(rt, {"action": "start", "site": "s", "brief": {"name": "X"}})
    assert not out["ok"] and "pack is off" in out["message"]


def test_build_page_becomes_a_core_block_draft_on_the_desk(tmp_path):
    fake, rt = make(tmp_path, packs={"generatepress": True})
    start = mod("tools.build").handle(rt, {"action": "start", "site": "s", "brief": {"name": "Bakery"}})
    assert start["ok"] and start["run"]["mode"] == "core"
    rid = start["run"]["id"]
    early = mod("tools.build").handle(rt, {"action": "build_page", "run": rid, "slug": "home", "title": "Home",
                                           "sections": [{"type": "hero", "heading": "x"}]}, run_draft=lambda a: 1 / 0)
    assert not early["ok"] and early["error"] == "state"  # refused before any draft is written
    gp = mod("gp.build")
    run = start["run"]
    for step in ("brief", "brand", "sitemap"):
        run = gp.advance(run, step, by="hermes") if run["steps"][gp.ORDER.index(step)]["state"] == "todo" else run
        run = gp.advance(run, step, by="hermes")
        run = gp.gate(run, step, "pass", by="you")
    run = gp.advance(run, "pages", by="hermes")
    mod("tools.build")._save(rt, run)
    seen = []

    def run_draft(a):
        seen.append(a)
        return mod("tools.draft").run(rt, a, approved=True)
    out = mod("tools.build").handle(rt, {"action": "build_page", "run": rid, "slug": "home", "title": "Home",
                                         "sections": [{"type": "hero", "heading": "Fresh bread", "body": "Daily."}]},
                                    run_draft=run_draft)
    assert out["ok"], out
    assert seen[0]["type"] == "page" and seen[0]["content"].startswith("<!-- wp:")
    page = fake.posts[out["wp_id"]]
    assert page["status"] == "draft" and "Fresh bread" in page["content"]["raw"]
    item = mod("desk").Desk(rt.store).get(out["desk_item"])
    assert item["kind"] == "build_page"
    assert out["desk_item"] in mod("tools.build").handle(rt, {"action": "get", "run": rid})["run"]["pages"]


def test_registered_tool_list_matches(tmp_path):
    names = []

    class Ctx:
        def __getattr__(self, name):
            return lambda *a, **k: names.append(k.get("name")) if name == "register_tool" else None
    load_plugin().register(Ctx())
    assert [n for n in names if n] == ["wp_doctor", "wp_sites", "wp_content", "wp_draft", "wp_desk", "wp_proof", "wp_seo",
                                       "wp_build"]
    assert json.dumps(names)
