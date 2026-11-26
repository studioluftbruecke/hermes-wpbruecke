"""v0.4 AE2: the Elementor model. What an edit may change on the harness page's real tree, every refusal
before a write, and the hash that proves a save read back as reported."""
import copy
import json
import pathlib
import re

import pytest

from wpb_fakes import ROOT, mod

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "elementor" / "harness-v3-page.json"


def tree() -> list:
    return copy.deepcopy(json.loads(FIX.read_text())["elements"])


def doc(elements=None, version="4.4.2", **kw):
    M = mod("packs.elementor.model")
    return M.ElementorDoc(post_id=7, version=version, elements=tree() if elements is None else elements, **kw)


def refused(fn, *a):
    M = mod("packs.elementor.model")
    with pytest.raises(M.PackRefused) as e:
        fn(*a)
    return e.value


def edits(*pairs):
    return mod("packs.elementor.model").normalize_edits([{"element_id": eid, "set": s} for eid, s in pairs])


def widget(eid, wtype, settings):
    return {"id": eid, "elType": "widget", "widgetType": wtype, "settings": settings, "elements": []}


# --- what is editable -------------------------------------------------------------------------------------

def test_the_harness_page_marks_its_four_widgets_editable_and_the_container_read_only():
    M = mod("packs.elementor.model")
    t = tree()
    assert M.editable_paths(M.find(t, "e4f5a6b")) == ["title", "link.url", "link.is_external", "link.nofollow"]
    assert M.editable_paths(M.find(t, "c7d8e9f")) == ["editor"]
    assert "image.id" in M.editable_paths(M.find(t, "f4a5b6c"))
    assert M.editable_paths(M.find(t, "a1b2c3d")) == []
    assert "layout element" in M.read_only_reason(M.find(t, "a1b2c3d"))


def test_a_widget_outside_the_six_and_v4_atomic_elements_are_read_only():
    M = mod("packs.elementor.model")
    t = tree()
    t[0]["elements"] += [widget("aa11bb2", "html", {"html": "<script>x()</script>"}),
                         widget("cc33dd4", "e-heading", {"title": {"$$type": "string", "value": "V4"}}),
                         {"id": "ee55ff6", "elType": "e-flexbox", "settings": {}, "elements": []}]
    d = doc(t)
    assert "outside the pack's six widget types" in M.read_only_reason(M.find(t, "aa11bb2"))
    assert "V4 atomic" in M.read_only_reason(M.find(t, "cc33dd4"))
    assert "V4 atomic" in M.read_only_reason(M.find(t, "ee55ff6"))
    for eid, s in (("aa11bb2", {"html": "<p>x</p>"}), ("cc33dd4", {"title": "x"}), ("a1b2c3d", {"title": "x"})):
        r = refused(M.check_edits, d, edits((eid, s)))
        assert r.code == "elementor_read_only" and "heading, text-editor, button, image, icon-list" in r.message


def test_a_path_the_widget_does_not_open_is_refused_with_the_editable_list():
    M = mod("packs.elementor.model")
    r = refused(M.check_edits, doc(), edits(("e4f5a6b", {"header_size": "h1"})))
    assert r.code == "elementor_read_only" and "editable: title, link.url" in r.message


# --- dynamic tags -----------------------------------------------------------------------------------------

def test_a_key_fed_by_a_dynamic_tag_is_refused_and_the_others_stay_editable():
    M = mod("packs.elementor.model")
    t = tree()
    M.find(t, "e4f5a6b")["settings"]["__dynamic__"] = {"title": '[elementor-tag id="1" name="post-title"]'}
    d = doc(t)
    r = refused(M.check_edits, d, edits(("e4f5a6b", {"title": "New"})))
    assert r.code == "elementor_dynamic" and "dynamic tag" in r.message
    M.check_edits(d, edits(("e4f5a6b", {"link": {"url": "https://example.test/x"}})))
    assert M.dynamic_paths(M.find(t, "e4f5a6b")) == ["title"]
    assert "title" not in M.editable_paths(M.find(t, "e4f5a6b"))


def test_a_dynamic_tag_on_one_icon_list_item_refuses_that_item_only():
    M = mod("packs.elementor.model")
    t = tree()
    t[0]["elements"].append(widget("ab12cd3", "icon-list", {"icon_list": [
        {"_id": "it1", "text": "One", "__dynamic__": {"text": "[elementor-tag]"}}, {"_id": "it2", "text": "Two"}]}))
    d = doc(t)
    assert refused(M.check_edits, d, edits(("ab12cd3", {"icon_list.it1.text": "x"}))).code == "elementor_dynamic"
    M.check_edits(d, edits(("ab12cd3", {"icon_list": {"it2": {"text": "Zwei"}}})))


# --- no element added or removed ----------------------------------------------------------------------------

def test_an_edit_that_would_add_an_element_is_refused():
    M = mod("packs.elementor.model")
    d = doc()
    r = refused(M.check_edits, d, edits(("zz99yy8", {"title": "A new heading"})))
    assert r.code == "elementor_structure" and "existing elements only" in r.message
    r = refused(M.normalize_edits, [{"element_id": "a1b2c3d", "set": {"title": "x"},
                                     "elements": [widget("n1", "heading", {})]}])
    assert r.code == "elementor_structure"


def test_an_edit_that_would_add_or_remove_list_items_is_refused():
    M = mod("packs.elementor.model")
    t = tree()
    t[0]["elements"].append(widget("ab12cd3", "icon-list", {"icon_list": [{"_id": "it1", "text": "One"}]}))
    d = doc(t)
    r = refused(M.normalize_edits, [{"element_id": "ab12cd3", "set": {"icon_list": [{"_id": "it1", "text": "x"}]}}])
    assert r.code == "elementor_structure" and "list" in r.message
    assert refused(M.check_edits, d, edits(("ab12cd3", {"icon_list.new1.text": "x"}))).code == "elementor_structure"


def test_a_saved_tree_with_an_element_removed_fails_the_read_back():
    M = mod("packs.elementor.model")
    before = tree()
    stored = {"e4f5a6b": {"title": "New heading"}}
    after = M.apply_stored(before, stored)
    assert M.readback_problem(before, stored, after) is None
    after[0]["elements"].pop()
    assert "added, removed or moved" in M.readback_problem(before, stored, after)
    moved = M.apply_stored(before, stored)
    moved[0]["elements"].reverse()
    assert "added, removed or moved" in M.readback_problem(before, stored, moved)


# --- the tested range (R26) -------------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["4.4.1", "4.5.0", "3.32.2", "", "4.4"])
def test_a_version_outside_the_range_is_read_only_and_names_the_range(version):
    W, P = mod("packs.elementor.widgets"), mod("packs.elementor.pack")
    lo, hi = W.ELEMENTOR_TESTED
    why = W.untested_reason(version)
    assert why and lo in why and "read-only" in why
    d = doc(version=version)
    r = refused(P.check, d, edits(("e4f5a6b", {"title": "x"})))
    assert r.code == "elementor_untested" and lo in r.message
    view = P.view(d, P.lock_reason(d, True))
    assert view["editable"] is False and lo in view["read_only"]
    assert all(not el["editable"] for el in view["elements"][0]["elements"])


def test_the_tested_release_is_open():
    W = mod("packs.elementor.widgets")
    assert W.ELEMENTOR_TESTED[0] == "4.4.2" and W.untested_reason("4.4.2") is None


# --- links, images, values --------------------------------------------------------------------------------

def test_a_link_and_an_image_edit_pass_and_change_the_hash():
    M = mod("packs.elementor.model")
    d = doc()
    e = edits(("b1c2d3e", {"text": "Book now", "link": {"url": "https://example.test/book", "is_external": True}}),
              ("f4a5b6c", {"image": {"id": 12}}))
    M.check_edits(d, e)
    stored = {"b1c2d3e": {"text": "Book now", "link.url": "https://example.test/book", "link.is_external": "on"},
              "f4a5b6c": {"image.id": 12, "image.url": "https://harness.example/a.png"}}
    after = M.apply_stored(d.elements, stored)
    assert M.find(after, "b1c2d3e")["settings"]["link"] == {"url": "https://example.test/book", "is_external": "on",
                                                            "nofollow": ""}
    assert M.content_hash(after) != d.hash and M.readback_problem(d.elements, stored, after) is None


def test_bad_values_are_refused_before_a_write():
    M = mod("packs.elementor.model")
    d = doc()
    for s in ({"link": {"url": "javascript:alert(1)"}}, {"link": {"nofollow": "maybe"}}, {"title": 5j}):
        assert refused(M.check_edits, d, edits(("e4f5a6b", s))).code == "bad_args", s
    assert refused(M.check_edits, d, edits(("f4a5b6c", {"image": {"id": "abc"}}))).code == "bad_args"


def test_alt_text_goes_with_an_outside_image_only():
    M = mod("packs.elementor.model")
    d = doc()
    r = refused(M.check_edits, d, edits(("f4a5b6c", {"image": {"alt": "A dog"}})))
    assert r.code == "elementor_alt" and "media item" in r.message
    M.check_edits(d, edits(("f4a5b6c", {"image": {"url": "https://cdn.example/dog.jpg", "alt": "A dog"}})))


def test_kses_would_strip_refuses_every_edit_on_that_page():
    M = mod("packs.elementor.model")
    r = refused(M.check_edits, doc(kses_would_strip=True), edits(("e4f5a6b", {"title": "x"})))
    assert r.code == "kses_would_strip" and "unfiltered_html" in r.message


# --- the hash -------------------------------------------------------------------------------------------

def test_the_hash_covers_texts_and_shape_but_not_style_settings_elementor_normalises():
    M = mod("packs.elementor.model")
    base = M.content_hash(tree())
    styled = tree()
    M.find(styled, "e4f5a6b")["settings"]["typography_font_size"] = {"unit": "px", "size": 30}
    styled[0]["settings"] = {}
    assert M.content_hash(styled) == base
    texted = tree()
    M.find(texted, "c7d8e9f")["settings"]["editor"] = "<p>Other</p>"
    assert M.content_hash(texted) != base
    html = tree()
    html[0]["elements"].append(widget("aa11bb2", "html", {"html": "x"}))
    assert M.content_hash(html) != base


def test_the_connector_opens_exactly_the_paths_widgets_py_names():
    W = mod("packs.elementor.widgets")
    php = (ROOT / "connector" / "wpbruecke-connector" / "includes" / "elementor.php").read_text()
    body = php[php.index("function wpbruecke_elementor_editable"):php.index("function wpbruecke_elementor_is_atomic")]
    link = ("link.url", "link.is_external", "link.nofollow")
    got = {}
    for m in re.finditer(r"'([a-z-]+)'\s*=>\s*(array_merge\(\s*)?array\(([^)]*)\)(\s*,\s*\$link)?", body):
        paths = tuple(re.findall(r"'([^']+)'", m.group(3)))
        got[m.group(1)] = paths + (link if m.group(4) else ())
    assert got == {k: tuple(v) for k, v in W.EDITABLE.items()}
