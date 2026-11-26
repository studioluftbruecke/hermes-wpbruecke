"""Opt-in: the Elementor pack end to end on the full harness (WordPress, Elementor, this connector).

Drives the plugin's own handlers as the seeded harness users against a real Elementor, the way the
agent's tools run: read the tree, stage a copy with a heading and a text edit, preview and proof it,
apply it (the agent's apply and the person's Apply copy on the review screen), and the refusals: the
live page changed since staging (``live_changed``), the drafter on a page (``drafter_page``), the
reader on every POST route, and an Editor without ``unfiltered_html`` (``kses_would_strip``).

R26: with ``ELEMENTOR=<release>`` or ``ELEMENTOR=latest`` the harness boots that Elementor instead of
its pin. A release outside ``ELEMENTOR_TESTED`` is then treated as tested for this run only, so a green
run is the evidence that widens the range. Skipped unless ``WPB_HARNESS=1`` (``harness_support.py``).
"""
from __future__ import annotations

import html
import os
import re
import time

import pytest

import harness_support as hs
from wpb_fakes import mod

pytestmark = pytest.mark.skipif(hs.skip_reason(hs.STUB) is not None, reason=hs.skip_reason(hs.STUB) or "")

STAMP = time.strftime("%H%M%S")
HEADING, TEXT, BUTTON = "e4f5a6b", "c7d8e9f", "b1c2d3e"   # the seeded V3 page's widgets
CANVAS_HEADING = "a7b8c9d"                                 # the seeded Canvas page's heading
EMBED = "aa11bb2"                                          # the HTML widget of the stub's admin page

cz = mod("connector_zip")
widgets = mod("packs.elementor.widgets")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    yield from hs.boot("full", tmp_path_factory.mktemp("harness-full"), extra_mounts=((hs.STUB, hs.STUB_GUEST),))


@pytest.fixture(scope="module")
def elementor(harness):
    """The booted Elementor release; on an R26 run (ELEMENTOR set) it counts as tested for this run."""
    version = harness.plugins["elementor"]
    saved = widgets.ELEMENTOR_TESTED
    if widgets.untested_reason(version) and os.environ.get("ELEMENTOR"):
        lo, hi = sorted((saved[0], saved[1], version), key=widgets.version_tuple)[::2]
        widgets.ELEMENTOR_TESTED = (lo, hi)
    yield version
    widgets.ELEMENTOR_TESTED = saved


@pytest.fixture(scope="module")
def admin(harness):
    return harness.user("hermes_admin")


@pytest.fixture(scope="module")
def pages(harness):
    return {"v3": int(harness.seed["pages"]["elementor"]), "canvas": int(harness.seed["pages"]["elementor_canvas"])}


@pytest.fixture(scope="module")
def editor(harness, elementor):
    return hs.Agent("hermes_editor", harness.pw("hermes_editor"))


def state(admin, post_id) -> dict:
    return hs.rest(*admin, "GET", f"/wpb-ae2/v1/state/{post_id}")[1] or {}


def test_booted_elementor_is_inside_the_tested_range(harness, elementor):
    pinned = os.environ.get("ELEMENTOR")
    print(f"Elementor {elementor} booted ({'override ' + pinned if pinned else 'pinned'}); "
          f"ELEMENTOR_TESTED {widgets.ELEMENTOR_TESTED}")
    assert widgets.untested_reason(elementor) is None
    assert harness.plugins["wpbruecke-connector"] == cz.CONNECTOR_VERSION


def test_pack_is_on_and_the_connector_is_new_enough(editor):
    report = editor.doctor()["sites"][0]
    assert report["connector"]["version"] == cz.CONNECTOR_VERSION
    assert report["packs"]["elementor"] == {"label": "Elementor", "detected": True, "on": True, "needs": None}


def test_content_get_answers_the_tree_with_editable_marks(editor, pages):
    got = editor.content(action="get", type="page", id=pages["v3"])
    assert got["content_format"] == "elementor"
    assert got["builder"]["pack"] == "elementor"
    heading = hs.find(got["elementor"]["elements"], HEADING)
    assert "title" in heading["editable"]
    assert got["elementor"]["elements"][0].get("read_only")   # the container itself is layout


def test_stage_preview_proof_and_agent_apply(editor, admin, pages):
    new_heading, new_text = f"Harness heading {STAMP}", f"Harness text {STAMP}"
    staged = editor.draft(action="stage", type="page", id=pages["v3"], note="harness", elementor_edits=[
        {"element_id": HEADING, "set": {"title": new_heading}},
        {"element_id": TEXT, "set": {"editor": f"<p>{new_text}</p>"}}])
    assert staged.get("ok"), staged
    assert staged["live_unchanged"] is True
    assert staged["elementor"]["read_back"]
    copy = staged["wp_id"]

    status, preview = hs.preview_html("hermes_editor", editor.secret("WPBRUECKE_KEY1"), copy)
    assert status == 200
    assert new_heading in preview and new_text in preview
    css = re.search(r"(/wp-content/uploads/elementor/css/post-%d\.css[^'\"]*)" % copy, preview)
    assert css, "the preview links the copy's own Elementor CSS"
    assert hs.anon(css.group(1))[0] == 200

    proof = editor.proof(staged["desk_item"])
    shots = proof.get("shots") or []
    assert sorted(s.get("width") for s in shots) == [390, 768, 1440], proof
    assert all(s.get("status") == 200 for s in shots)

    applied = editor.draft(action="apply", item=staged["desk_item"])
    assert applied.get("confirmed"), applied
    status, _, live = hs.anon(hs.live_path(admin, pages["v3"]))
    assert status == 200
    assert new_heading in live and new_text in live, "the live page shows the edit with no cache clear"
    assert state(admin, copy).get("status") == "trash"

    lines = [l for l in editor.log_lines() if l.get("action") in ("stage", "apply") and l.get("wp_id") in (copy, pages["v3"])]
    assert {l["action"] for l in lines} == {"stage", "apply"}
    assert all(l.get("label") for l in lines)
    asked = editor.doctor(action="log", label=lines[0]["label"])
    assert asked["count"] >= 2


def test_review_screen_apply_keeps_the_canvas_template(editor, admin, pages):
    heading = f"Harness canvas {STAMP}"
    staged = editor.draft(action="stage", type="page", id=pages["canvas"], elementor_edits=[
        {"element_id": CANVAS_HEADING, "set": {"title": heading}}])
    assert staged.get("ok"), staged
    assert staged["elementor"]["template"] == "elementor_canvas"
    copy = staged["wp_id"]
    status, preview = hs.preview_html("hermes_editor", editor.secret("WPBRUECKE_KEY1"), copy)
    assert status == 200 and heading in preview
    assert "elementor-template-canvas" in preview
    assert not re.search(r"<header\b", preview) and "wp-block-template-part" not in preview

    hs.rest(*admin, "POST", "/wpb-ae2/v1/user", {"login": "hermes_person", "role": "editor"})
    cookies = hs.login_cookies(admin, "hermes_person")
    assert cookies, "the person logs in through wp-login.php"
    status, _, body = hs.http("GET", f"/wp-admin/admin.php?page=wpbruecke-review&post={copy}", cookies=cookies)
    screen = body.decode("utf-8", "replace")
    assert status == 200 and "Elementor layout" in screen
    forms = re.findall(r'<form[^>]*admin-post\.php[^>]*>(.*?)</form>', screen, re.S)
    form = next(f for f in forms if 'value="wpbruecke_review_apply"' in f)
    fields = dict(re.findall(r'<input type="hidden" (?:id="[^"]*" )?name="([^"]+)" value="([^"]*)"', form))
    status, headers, _ = hs.http("POST", "/wp-admin/admin-post.php", cookies=cookies,
                                 form={k: html.unescape(v) for k, v in fields.items()})
    location = next((v for k, v in headers if k.lower() == "location"), "")
    assert "wpb_notice=applied" in location, location

    status, _, live = hs.anon(hs.live_path(admin, pages["canvas"]))
    assert status == 200 and heading in live
    assert state(admin, copy).get("status") == "trash"
    assert state(admin, pages["canvas"]).get("template") == "elementor_canvas"


def test_apply_refused_when_the_live_page_changed(editor, admin, pages):
    staged = editor.draft(action="stage", type="page", id=pages["v3"], elementor_edits=[
        {"element_id": HEADING, "set": {"title": f"R31 {STAMP}"}}])
    assert staged.get("ok"), staged   # after the person's wp-admin visit too: Elementor's role changes stop nothing (D42)
    status, _ = hs.rest(*admin, "POST", f"/wp/v2/pages/{pages['v3']}", {"excerpt": f"A person's edit {STAMP}"})
    assert status == 200
    before = state(admin, pages["v3"])
    refused = editor.draft(action="apply", item=staged["desk_item"])
    after = state(admin, pages["v3"])
    assert refused.get("error") == "live_changed", refused
    assert (before["data_sha"], before["modified_gmt"]) == (after["data_sha"], after["modified_gmt"])


def test_drafter_edits_its_own_elementor_post_and_never_a_page(harness, admin, pages, elementor):
    drafter = harness.user("hermes_drafter")
    _, own = hs.rest(*admin, "POST", "/wpb-ae2/v1/fixture", {
        "from": pages["v3"], "type": "post", "status": "draft", "author": harness.creds["hermes_drafter"]["id"],
        "title": f"Harness drafter post {STAMP}"})
    agent = hs.Agent(*drafter)
    staged = agent.draft(action="stage", type="post", id=own["id"], elementor_edits=[
        {"element_id": HEADING, "set": {"title": f"Drafter staged {STAMP}"}}])
    assert staged.get("ok"), staged
    saved = agent.draft(action="update", type="post", id=staged["wp_id"], elementor_edits=[
        {"element_id": TEXT, "set": {"editor": f"<p>Drafter saved {STAMP}</p>"}}])
    assert saved.get("ok"), saved

    applied = agent.draft(action="apply", item=staged["desk_item"])
    assert str(applied.get("error") or "").startswith("level_"), applied
    assert hs.rest(*drafter, "POST", f"/wpbruecke/v1/elementor/{staged['wp_id']}/apply")[0] == 403

    page = agent.draft(action="stage", type="page", id=pages["v3"], elementor_edits=[
        {"element_id": HEADING, "set": {"title": "x"}}])
    assert page.get("error") == "drafter_page", page
    assert hs.rest(*drafter, "POST", f"/wpbruecke/v1/elementor/{pages['v3']}/stage")[0] == 403


def test_a_dynamic_tag_elementor_cannot_resolve_stops_stage_and_save_and_nothing_is_written(harness, editor, admin, pages):
    """Review row 1: Elementor 4.4.2 free resolves no dynamic tag, so its save would turn the button's tag
    into []. The page reads as locked, the agent's stage sends nothing, the routes refuse 409."""
    _, page = hs.rest(*admin, "POST", "/wpb-ae2/v1/fixture", {
        "from": pages["v3"], "type": "page", "status": "publish", "dynamic_tag": "post-custom-field",
        "title": f"Harness dynamic tag {STAMP}"})
    before = state(admin, page["id"])
    got = editor.content(action="get", type="page", id=page["id"])
    assert got["elementor"]["editable"] is False and f"{BUTTON} text" in got["elementor"]["read_only"]
    staged = editor.draft(action="stage", type="page", id=page["id"], elementor_edits=[
        {"element_id": HEADING, "set": {"title": "x"}}])
    assert staged.get("error") == "elementor_unknown_dynamic", staged

    ed = harness.user("hermes_editor")
    status, res = hs.rest(*ed, "POST", f"/wpbruecke/v1/elementor/{page['id']}/stage")
    assert (status, res.get("code")) == (409, "wpbruecke_unknown_dynamic"), res
    assert f"{BUTTON} text" in res["message"]
    _, draft = hs.rest(*admin, "POST", "/wpb-ae2/v1/fixture", {
        "from": page["id"], "type": "page", "status": "draft", "author": harness.creds["hermes_editor"]["id"],
        "title": f"Harness dynamic draft {STAMP}"})
    draft_before = state(admin, draft["id"])
    status, res = hs.rest(*ed, "POST", f"/wpbruecke/v1/elementor/{draft['id']}",
                          {"edits": [{"element_id": TEXT, "set": {"editor": "<p>x</p>"}}]})
    assert (status, res.get("code")) == (409, "wpbruecke_unknown_dynamic"), res
    assert state(admin, draft["id"])["data_sha"] == draft_before["data_sha"]
    assert state(admin, page["id"])["data_sha"] == before["data_sha"]


def test_reader_is_refused_on_every_post_route(harness, pages):
    reader = harness.user("hermes_reader")
    v3 = pages["v3"]
    for path, body in ((f"/wpbruecke/v1/elementor/{v3}/stage", {}),
                       (f"/wpbruecke/v1/elementor/{v3}", {"edits": [{"element_id": HEADING, "set": {"title": "r"}}]}),
                       (f"/wpbruecke/v1/elementor/{v3}/apply", {})):
        status, res = hs.rest(*reader, "POST", path, body)
        assert (status, (res or {}).get("code")) == (403, "wpbruecke_reader_read_only"), path
    assert hs.rest(*reader, "GET", f"/wpbruecke/v1/elementor/{v3}")[0] == 200


def test_editor_without_unfiltered_html_is_refused_and_the_embed_stays(harness, admin, pages):
    _, page = hs.rest(*admin, "POST", "/wpb-ae2/v1/fixture", {
        "from": pages["v3"], "type": "page", "status": "publish", "html_widget": True,
        "title": f"Harness admin embed {STAMP}"})
    _, user = hs.rest(*admin, "POST", "/wpb-ae2/v1/user", {"login": "hermes_nohtml", "role": "editor",
                                                           "no_unfiltered": True})
    assert user["unfiltered_html"] is False
    nohtml = ("hermes_nohtml", user["pw"])
    status, res = hs.rest(*nohtml, "POST", f"/wpbruecke/v1/elementor/{page['id']}/stage")
    assert (status, res.get("code")) == (409, "kses_would_strip")

    _, copy = hs.rest(*harness.user("hermes_editor"), "POST", f"/wpbruecke/v1/elementor/{page['id']}/stage")
    before = state(admin, copy["copy"])
    status, res = hs.rest(*nohtml, "POST", f"/wpbruecke/v1/elementor/{copy['copy']}",
                          {"edits": [{"element_id": HEADING, "set": {"title": "No html"}}]})
    after = state(admin, copy["copy"])
    assert (status, res.get("code")) == (409, "kses_would_strip")
    assert before["data_sha"] == after["data_sha"]
    embed = (hs.find(after.get("elements"), EMBED) or {}).get("settings", {}).get("html", "")
    assert "<script>" in embed and "<iframe" in embed
