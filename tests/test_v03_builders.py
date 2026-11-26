"""v0.3 WG4: which builder made a page, and the guard that keeps a builder page's body (D20 a) and the
blocks Hermes does not know (D21 a). Writes are checked against the fake WordPress' request log."""
import json
import pathlib

import pytest

from wpb_fakes import ROLE_CAPS, FakeWP, Settings, create_paired, load_plugin, mod, secrets

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "builders"
CASES = [(f.stem, c) for f in sorted(FIX.glob("*.json")) for c in json.loads(f.read_text())["cases"]]
ELEMENTOR_META = {"_elementor_edit_mode": "builder", "_elementor_template_type": "wp-page",
                  "_elementor_data": "[{\"id\":\"a1b2c3d\",\"elType\":\"container\",\"elements\":[]}]"}
STILL = "title, excerpt, SEO fields and the featured image"


@pytest.mark.parametrize("builder,case", CASES, ids=[f"{b}: {c['name']}" for b, c in CASES])
def test_detection_per_builder_from_fixtures(builder, case):
    got = mod("builders").detect(case["post"], case.get("facts") or {}, {"packs": {}})
    want = case["expect"]
    assert (got["id"], got["source"]) == (want["id"], want["source"]), got
    assert got["pack"] is None  # no builder pack exists in v0.3
    if want.get("evidence"):
        assert any(want["evidence"] in e for e in got["evidence"]), got["evidence"]


def test_every_builder_id_has_a_fixture():
    ids = {c["expect"]["id"] for _, c in CASES}
    assert set(mod("builders").BUILDERS) <= ids


def test_a_builder_pack_is_named_only_when_it_exists_and_is_on(monkeypatch):
    packs, builders = mod("packs"), mod("builders")
    fake_pack = packs.PackSpec(id="elementor", label="Elementor", kind="builder", detect=lambda f, p: True,
                               looks_for="Elementor", requires={}, scope="Edits texts of existing widgets.",
                               builder="elementor")
    post = {"content": {"raw": "<p>x</p>"}, "meta": ELEMENTOR_META}
    monkeypatch.delitem(packs.REGISTRY, "elementor")
    assert builders.detect(post, {}, {"packs": {"elementor": True}})["pack"] is None  # not in the registry
    monkeypatch.setitem(packs.REGISTRY, "elementor", fake_pack)
    assert builders.detect(post, {}, {"packs": {"elementor": False}})["pack"] is None
    on = builders.detect(post, {}, {"packs": {"elementor": True}})
    assert on["pack"] == "elementor" and builders.body_editable(on)


# --- the write guard --------------------------------------------------------------------------------

def setup(tmp_path, role="editor", connector=True, **site):
    fake = FakeWP(user="hermes", caps=ROLE_CAPS[role], roles=[role],
                  namespaces=["wp/v2"] + (["wpbruecke/v1"] if connector else []))
    if connector:
        fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.3.0"})
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1, **site}, "person")
    return fake, rt


def handlers(rt, run=None):
    plugin = load_plugin()
    run = run or mod("lanes").RunContext()
    tickets = mod("hooks").Tickets()
    kw = dict(transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})
    d = plugin.make_draft_handler(rt.get_setting, rt.store.root, lambda: run, rt.get_secret, tickets=tickets, **kw)
    c = plugin.make_content_handler(rt.get_setting, rt.store.root, rt.get_secret, **kw)
    return (lambda a: json.loads(d(a, session_id="x"))), (lambda a: json.loads(c(a)))


def writes(fake, since=0):
    return [f"{r['method']} {r['path']}" for r in fake.requests[since:] if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]


def elementor_page(fake, status="publish", field=True):
    extra = {"meta": dict(ELEMENTOR_META)}
    if field:
        extra["wpbruecke_builder"] = {"id": "elementor", "evidence": ["meta _elementor_edit_mode=builder"]}
    return fake.add_post(ptype="page", status=status, title="Landing", content="<p>fallback copy</p>", **extra)


def test_wp_content_get_returns_the_builder_with_and_without_the_connector_field(tmp_path):
    fake, rt = setup(tmp_path)
    _, content = handlers(rt)
    with_field = elementor_page(fake)
    without = elementor_page(fake, field=False)
    a = content({"site": "s", "action": "get", "type": "page", "id": with_field["id"]})
    b = content({"site": "s", "action": "get", "type": "page", "id": without["id"]})
    assert a["builder"] == {"id": "elementor", "pack": None, "evidence": ["meta _elementor_edit_mode=builder"],
                            "source": "connector"}
    assert b["builder"]["id"] == "elementor" and b["builder"]["source"] == "rest_meta"
    plain = fake.add_post(content="<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->")
    assert content({"site": "s", "action": "get", "id": plain["id"]})["builder"]["id"] == "blocks"


@pytest.mark.parametrize("action,status", [("update", "draft"), ("stage", "publish")])
def test_a_body_change_on_a_builder_page_is_refused_before_any_write(tmp_path, action, status):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = elementor_page(fake, status=status)
    n = len(fake.requests)
    out = draft({"site": "s", "action": action, "type": "page", "id": page["id"], "content": "New body"})
    assert out["ok"] is False and out["error"] == "builder_page" and out["sent"] is False
    assert "Elementor" in out["message"] and STILL in out["message"] and "Nothing was sent" in out["message"]
    assert writes(fake, n) == []
    assert fake.posts[page["id"]]["content"]["raw"] == "<p>fallback copy</p>"


def test_title_and_seo_still_write_on_a_builder_page(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = elementor_page(fake, status="draft")
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "title": "Landing new",
                 "seo": {"description": "A fresh description."}})
    assert out["ok"], out
    stored = fake.posts[page["id"]]
    assert stored["title"]["raw"] == "Landing new" and stored["meta"]["_wpbruecke_description"] == "A fresh description."
    assert stored["content"]["raw"] == "<p>fallback copy</p>"


def test_stage_and_apply_of_a_title_change_keep_the_builder_body(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = elementor_page(fake)
    staged = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"], "title": "Landing v2"})
    assert staged["ok"] and any("Elementor" in n for n in staged["notes"])
    n = len(fake.requests)
    ap = draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    assert ap["ok"] and ap["confirmed"] and ap["applied_fields"] == ["excerpt", "title"]
    posted = [json.loads(r["body"]) for r in fake.requests[n:]
              if r["method"] == "POST" and r["path"] == f"/wp-json/wp/v2/pages/{page['id']}"]
    assert posted and all("content" not in b for b in posted)
    assert fake.posts[page["id"]]["title"]["raw"] == "Landing v2"


def test_apply_of_a_copy_whose_body_differs_is_refused_before_any_write(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = elementor_page(fake)
    staged = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"], "title": "Landing v2"})
    fake.posts[staged["wp_id"]]["content"] = {"raw": "<p>edited in the copy</p>", "rendered": ""}
    n = len(fake.requests)
    out = draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    assert out["error"] == "builder_page" and writes(fake, n) == []


def test_a_server_side_builder_without_the_connector_is_refused_as_unknown(tmp_path):
    fake, rt = setup(tmp_path, connector=False)
    fake.routes[("GET", "/")] = (200, {"Content-Type": "text/html"},
                                 b"<html><head><link rel='stylesheet' href='https://test.example/wp-content/plugins/"
                                 b"beaver-builder-lite-version/css/x.css'></head><body></body></html>")
    rt.site_facts(rt.site("s"), refresh=True)
    draft, _ = handlers(rt)
    page = fake.add_post(ptype="page", status="draft", title="Menu", content="<h2>Menu</h2><p>Bread.</p>")
    n = len(fake.requests)
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "content": "New"})
    assert out["error"] == "builder_page" and "Beaver Builder" in out["message"] and "connector" in out["message"]
    assert writes(fake, n) == []
    assert draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "title": "Menu 2"})["ok"]


# --- blocks an edit would drop (D21) --------------------------------------------------------------------

KADENCE = ('<!-- wp:kadence/rowlayout {"uniqueID":"_a1"} --><!-- wp:kadence/column --><!-- wp:paragraph --><p>Hours'
           '</p><!-- /wp:paragraph --><!-- /wp:kadence/column --><!-- /wp:kadence/rowlayout -->'
           '<!-- wp:paragraph --><p>Intro</p><!-- /wp:paragraph -->')


def test_unknown_blocks_an_edit_would_drop_are_refused_unless_named(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.add_post(ptype="page", status="draft", title="Home", content=KADENCE)
    n = len(fake.requests)
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "content": "Only text now"})
    assert out["error"] == "unknown_blocks" and "kadence/rowlayout (1)" in out["message"]
    assert "kadence/column (1)" in out["message"] and "drop_blocks" in out["message"] and writes(fake, n) == []
    kept = draft({"site": "s", "action": "update", "type": "page", "id": page["id"],
                  "content": KADENCE.replace("Intro", "Intro, new")})
    assert kept["ok"]
    named = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "content": "Only text now",
                   "drop_blocks": ["kadence/*"]})
    assert named["ok"] and "Only text now" in fake.posts[page["id"]]["content"]["raw"]


def test_a_named_block_does_not_cover_another():
    b = mod("builders")
    lost = b.dropped_blocks(KADENCE, "", {"core"}, ["kadence/rowlayout"])
    assert lost == {"kadence/column": 1}


def test_generateblocks_are_known_while_the_generatepress_pack_is_on(tmp_path):
    gb = '<!-- wp:generateblocks/element {"uniqueId":"a1b2c3d4"} --><div>x</div><!-- /wp:generateblocks/element -->'
    for on, expect in ((False, "unknown_blocks"), (True, None)):
        fake, rt = setup(tmp_path / str(on), packs={"generatepress": on})
        draft, _ = handlers(rt)
        page = fake.add_post(ptype="page", status="draft", title="Built", content=gb)
        out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "content": "Plain"})
        assert out.get("error") == expect if expect else out["ok"], out


def test_stage_drops_unknown_blocks_only_when_named(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    live = fake.add_post(ptype="page", title="Home", content=KADENCE)
    n = len(fake.requests)
    assert draft({"site": "s", "action": "stage", "type": "page", "id": live["id"], "content": "x"})["error"] == \
        "unknown_blocks"
    assert writes(fake, n) == []
    assert draft({"site": "s", "action": "stage", "type": "page", "id": live["id"], "content": "x",
                  "drop_blocks": ["kadence"]})["ok"]


def test_apply_with_another_posts_id_than_its_desk_item_is_refused_and_deletes_nothing(tmp_path):
    # Review 2026-11-25 (row a3): the item's source got the other post's body, and the other post was
    # deleted as if it were the copy.
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    target = elementor_page(fake)
    plain = fake.add_post(ptype="page", status="publish", title="Plain", content="<p>plain</p>")
    staged = draft({"site": "s", "action": "stage", "type": "page", "id": plain["id"], "title": "Plain v2"})
    n = len(fake.requests)
    out = draft({"site": "s", "action": "apply", "item": staged["desk_item"], "id": target["id"]})
    assert out["ok"] is False and out["error"] == "bad_args", out
    assert writes(fake, n) == []
    assert target["id"] in fake.posts and fake.posts[plain["id"]]["content"]["raw"] == "<p>plain</p>"
    ok = draft({"site": "s", "action": "apply", "item": staged["desk_item"], "id": staged["wp_id"]})
    assert ok["ok"] and ok["wp_id"] == plain["id"], ok


def test_apply_never_runs_on_a_published_post_even_with_a_source_meta(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    source = fake.add_post(ptype="page", status="publish", title="Source", content="<p>s</p>")
    live = fake.add_post(ptype="page", status="publish", title="Live", content="<p>l</p>",
                         meta={"_wpbruecke_source": source["id"]})
    n = len(fake.requests)
    out = draft({"site": "s", "action": "apply", "type": "page", "id": live["id"]})
    assert out["ok"] is False and out["error"] == "not_staged", out
    assert writes(fake, n) == [] and live["id"] in fake.posts
