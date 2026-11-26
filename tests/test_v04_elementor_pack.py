"""v0.4 AE2: the Elementor pack through wp_content and wp_draft, against a fake connector with the four
Elementor routes (elementor_fakes.py). Each test checks what a user of the tools sees: the answer, the
refusal, which writes left the process, what the page holds afterwards."""
import json

import pytest

from elementor_fakes import ElementorSite
from wpb_fakes import Settings, create_paired, load_plugin, mod, secrets

SCOPE_WORDS = "heading, text-editor, button, image, icon-list and call-to-action"


def setup(tmp_path, role="editor", pack=True, version="4.4.2"):
    fake = ElementorSite(role=role, version=version)
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1,
                       "packs": {"elementor": pack}}, "person")
    return fake, rt


def handlers(rt):
    plugin = load_plugin()
    run = mod("lanes").RunContext()
    kw = dict(transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})
    d = plugin.make_draft_handler(rt.get_setting, rt.store.root, lambda: run, rt.get_secret,
                                  tickets=mod("hooks").Tickets(), **kw)
    c = plugin.make_content_handler(rt.get_setting, rt.store.root, rt.get_secret, **kw)
    return (lambda a: json.loads(d(a, session_id="x"))), (lambda a: json.loads(c(a)))


def writes(fake, since=0):
    return [f"{r['method']} {r['path']}" for r in fake.requests[since:] if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]


def heading_edit(text="New heading"):
    return [{"element_id": "e4f5a6b", "set": {"title": text}}]


def stage(draft, page, edits=None, **kw):
    return draft({"site": "s", "action": "stage", "type": "page", "id": page["id"],
                  "elementor_edits": edits or heading_edit(), **kw})


# --- reading ------------------------------------------------------------------------------------------------

def test_get_returns_the_tree_with_editable_paths_and_read_only_reasons(tmp_path):
    fake, rt = setup(tmp_path)
    _, content = handlers(rt)
    page = fake.elementor_page()
    got = content({"site": "s", "action": "get", "type": "page", "id": page["id"]})
    assert got["content_format"] == "elementor" and got["content"] == "" and got["builder"]["pack"] == "elementor"
    tree = got["elementor"]
    assert tree["editable"] is True and tree["version"] == "4.4.2" and SCOPE_WORDS in tree["scope"]
    container = tree["elements"][0]
    assert container["editable"] == [] and "layout element" in container["read_only"]
    heading = container["elements"][0]
    assert heading["editable"][0] == "title" and heading["values"]["title"] == "Original heading"


def test_with_the_pack_off_the_tree_is_shown_read_only(tmp_path):
    fake, rt = setup(tmp_path, pack=False)
    _, content = handlers(rt)
    got = content({"site": "s", "action": "get", "type": "page", "id": fake.elementor_page()["id"]})
    assert got["content_format"] == "elementor" and got["elementor"]["editable"] is False
    assert "pack is off" in got["elementor"]["read_only"]
    assert all(not el["editable"] for el in got["elementor"]["elements"][0]["elements"])


# --- pack off: v0.3 behaviour ---------------------------------------------------------------------------------

def test_with_the_pack_off_edits_are_refused_as_builder_page(tmp_path):
    fake, rt = setup(tmp_path, pack=False)
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    n = len(fake.requests)
    out = stage(draft, page)
    assert out["error"] == "builder_page" and "packs.elementor: true" in out["message"] and writes(fake, n) == []
    body = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"], "content": "New body"})
    assert body["error"] == "builder_page" and writes(fake, n) == []
    title = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"], "title": "Renamed"})
    assert title["ok"] and not any("elementor" in w for w in writes(fake, n))


# --- stage, proof, apply -------------------------------------------------------------------------------------

def test_stage_with_edits_makes_an_elementor_copy_and_leaves_the_live_page(tmp_path):
    fake, rt = setup(tmp_path)
    draft, content = handlers(rt)
    page = fake.elementor_page(template="elementor_canvas")
    live_before = fake.tree(page["id"])
    out = stage(draft, page, heading_edit() + [{"element_id": "c7d8e9f", "set": {"editor": "<p>New text</p>"}}])
    assert out["ok"], out
    copy_id = out["wp_id"]
    assert out["source_id"] == page["id"] and out["live_unchanged"] is True and out["desk_item"]
    assert out["elementor"]["edited"] == ["e4f5a6b title", "c7d8e9f editor"] and out["elementor"]["read_back"]
    assert out["elementor"]["template"] == "elementor_canvas"
    copy_tree = fake.tree(copy_id)
    assert copy_tree[0]["elements"][0]["settings"]["title"] == "New heading"
    assert copy_tree[0]["elements"][1]["settings"]["editor"] == "<p>New text</p>"
    assert fake.tree(page["id"]) == live_before
    assert ("POST", "stage", page["id"]) in fake.calls and ("POST", "save", copy_id) in fake.calls
    got = content({"site": "s", "action": "get", "type": "page", "id": copy_id})
    assert got["elementor"]["elements"][0]["elements"][0]["values"]["title"] == "New heading"


def test_apply_routes_to_the_connector_and_reads_the_live_tree_back(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    staged = stage(draft, page)
    ap = draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    assert ap["ok"] and ap["confirmed"] and ap["content_matches"] and ap["copy_deleted"], ap
    assert ("POST", "apply", staged["wp_id"]) in fake.calls
    assert fake.tree(page["id"])[0]["elements"][0]["settings"]["title"] == "New heading"
    assert fake.posts[staged["wp_id"]]["status"] == "trash"
    assert not any(w.endswith(f"/pages/{page['id']}") for w in writes(fake))  # never a core write of the body


def test_apply_with_another_posts_id_than_its_desk_item_is_still_refused(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page, other = fake.elementor_page(), fake.elementor_page(title="Other")
    staged = stage(draft, page)
    n = len(fake.requests)
    out = draft({"site": "s", "action": "apply", "item": staged["desk_item"], "id": other["id"]})
    assert out["error"] == "bad_args" and writes(fake, n) == []


def test_apply_refuses_when_the_live_page_changed_since_staging(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    staged = stage(draft, page)
    fake.person_edits_live(page["id"], "A person's heading")
    out = draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    assert out["ok"] is False and out["error"] == "live_changed" and "stage the change again" in out["message"]
    assert fake.tree(page["id"])[0]["elements"][0]["settings"]["title"] == "A person's heading"
    assert fake.posts[staged["wp_id"]]["status"] == "draft"


def test_update_on_a_draft_takes_a_link_and_an_image_edit(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page(status="draft")
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "elementor_edits": [
        {"element_id": "b1c2d3e", "set": {"text": "Book now", "link": {"url": "https://example.test/book",
                                                                       "is_external": True}}},
        {"element_id": "f4a5b6c", "set": {"image": {"id": 42}}}]})
    assert out["ok"], out
    tree = fake.tree(page["id"])
    assert tree[0]["elements"][2]["settings"]["link"] == {"url": "https://example.test/book", "is_external": "on",
                                                          "nofollow": ""}
    assert tree[0]["elements"][3]["settings"]["image"]["id"] == 42
    assert tree[0]["elements"][3]["settings"]["image"]["url"].endswith("/42.png")


def test_update_of_a_published_elementor_page_is_refused(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    n = len(fake.requests)
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "elementor_edits": heading_edit()})
    assert out["error"] == "live_post" and writes(fake, n) == []


# --- refusals -----------------------------------------------------------------------------------------------

def test_a_read_back_that_differs_is_refused_and_the_copy_is_removed(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page()

    def drop_button(tree):
        tree[0]["elements"] = [el for el in tree[0]["elements"] if el["id"] != "b1c2d3e"]
        return tree
    fake.tamper = drop_button
    before = set(fake.posts)
    out = stage(draft, page)
    assert out["ok"] is False and out["error"] == "elementor_readback" and "added, removed or moved" in out["message"]
    assert set(fake.posts) == before
    fake.tamper = lambda tree: [{**tree[0], "elements": [{**tree[0]["elements"][0], "settings": {"title": "Other"}}]
                                 + tree[0]["elements"][1:]}]
    assert stage(draft, page)["error"] == "elementor_readback"


@pytest.mark.parametrize("edits,code", [
    ([{"element_id": "zz99yy8", "set": {"title": "x"}}], "elementor_structure"),
    ([{"element_id": "a1b2c3d", "set": {"title": "x"}}], "elementor_read_only"),
    ([{"element_id": "e4f5a6b", "set": {"header_size": "h1"}}], "elementor_read_only"),
    ([{"element_id": "e4f5a6b", "set": {"title": "x"}, "elements": []}], "elementor_structure"),
])
def test_edits_outside_the_scope_are_refused_before_any_write(tmp_path, edits, code):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    n = len(fake.requests)
    out = stage(draft, page, edits)
    assert out["error"] == code and writes(fake, n) == [], out
    if code != "elementor_structure" or "elements" not in edits[0]:
        assert SCOPE_WORDS in out["message"]


def test_a_dynamic_tag_is_refused_before_any_write(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    fake.dynamic_tags = ("post-title",)
    t = fake.tree(page["id"])
    t[0]["elements"][0]["settings"]["__dynamic__"] = {"title": "[elementor-tag id=\"1a2b3c4\" name=\"post-title\" settings=\"%7B%7D\"]"}
    fake.posts[page["id"]]["meta"]["_elementor_data"] = json.dumps(t)
    n = len(fake.requests)
    out = stage(draft, page)
    assert out["error"] == "elementor_dynamic" and writes(fake, n) == []


def test_content_on_an_elementor_page_with_the_pack_on_names_the_scope(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page(status="draft")
    n = len(fake.requests)
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "content": "Plain text"})
    assert out["error"] == "elementor_scope" and "elementor_edits" in out["message"] and SCOPE_WORDS in out["message"]
    assert writes(fake, n) == []


def test_the_drafter_on_a_page_is_refused_before_any_request_to_the_routes(tmp_path):
    fake, rt = setup(tmp_path, role="wpbruecke_drafter")
    draft, _ = handlers(rt)
    page = fake.elementor_page(status="draft")
    n = len(fake.requests)
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "elementor_edits": heading_edit()})
    assert out["error"] == "drafter_page" and "never a page" in out["message"]
    touched = [r["path"] for r in fake.requests[n:] if f"/{page['id']}" in r["path"]]
    assert touched == [] and fake.calls == [] and writes(fake, n) == []  # not even a read: WordPress answers 403


def test_the_drafter_edits_its_own_elementor_post_and_cannot_apply(tmp_path):
    fake, rt = setup(tmp_path, role="wpbruecke_drafter")
    draft, _ = handlers(rt)
    post = fake.elementor_page(ptype="post", status="draft", title="Drafter post")
    out = draft({"site": "s", "action": "update", "type": "post", "id": post["id"], "elementor_edits": heading_edit()})
    assert out["ok"], out
    assert fake.tree(post["id"])[0]["elements"][0]["settings"]["title"] == "New heading"
    applied = draft({"site": "s", "action": "apply", "type": "post", "id": post["id"]})
    assert applied["ok"] is False and applied["error"].startswith("level_")


def test_outside_the_tested_range_edits_are_refused_naming_the_range(tmp_path):
    fake, rt = setup(tmp_path, version="4.5.0")
    draft, _ = handlers(rt)
    page = fake.elementor_page()
    n = len(fake.requests)
    out = stage(draft, page)
    assert out["error"] == "elementor_untested" and "4.4.2" in out["message"] and "4.5.0" in out["message"]
    assert writes(fake, n) == []
    title = draft({"site": "s", "action": "stage", "type": "page", "id": page["id"], "title": "Renamed"})
    assert title["ok"] and ("POST", "stage", page["id"]) not in fake.calls


def test_elementor_edits_on_a_block_page_are_refused(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.add_post(ptype="page", status="draft", title="Blocks", content="<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->")
    out = draft({"site": "s", "action": "update", "type": "page", "id": page["id"], "elementor_edits": heading_edit()})
    assert out["error"] == "bad_args" and "Elementor pages" in out["message"]


# --- the write log and tokens -----------------------------------------------------------------------------------

def test_each_write_leaves_a_log_line_labelled_with_the_title_at_write_time(tmp_path):
    fake, rt = setup(tmp_path)
    draft, _ = handlers(rt)
    page = fake.elementor_page(title="Spring offer")
    staged = stage(draft, page)
    draft({"site": "s", "action": "apply", "item": staged["desk_item"]})
    fake.posts[page["id"]]["title"] = {"raw": "Renamed later", "rendered": "Renamed later"}
    lines = mod("activity").ActivityLog(rt.store).read("s")
    mine = [l for l in lines if l.get("action") in ("stage", "apply")]
    assert [l["action"] for l in mine] == ["stage", "apply"] and all(l["label"] == "Spring offer" for l in mine)
    assert all(l["result"] == "ok" for l in mine) and "Elementor edits" in mine[0]["summary"]


def test_design_tokens_come_from_elementor_globals_when_the_pack_is_on(tmp_path):
    fake, rt = setup(tmp_path)
    tokens = mod("packs").design_tokens(rt, rt.site("s"), refresh=True)
    assert tokens["source"] == "elementor" and tokens["colors"][0]["value"] == "#6EC1E4"
    assert tokens["fonts"] == {"body": "Inter", "headings": "Roboto"}
