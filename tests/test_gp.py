"""GeneratePress pack: the block serializer (probe P19's rules) and the build run state machine."""
import json
import pathlib
import re

import pytest

from wpb_fakes import mod

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
BRAND = {"colors": [{"slug": "accent", "name": "Accent", "color": "#C4472B"},
                    {"slug": "base", "name": "Base", "color": "#F7F3EC"},
                    {"slug": "text", "name": "Text", "color": "#22201D"}],
         "fonts": {"body": "Inter", "headings": "Fraunces"}}
PLAN = [
    {"type": "hero", "heading": "Bakery & café", "body": "Fresh <bread>.\n\nSince 1952.",
     "button": {"label": "Opening hours", "href": "#hours"}},
    {"type": "features", "heading": "What we bake",
     "items": [{"heading": "Bread", "body": "Sourdough."}, {"heading": "Cakes", "body": ""}, "Coffee"]},
    {"type": "text", "heading": "About", "body": ["One.", "Two."]},
    {"type": "cta", "heading": "Order", "body": "By Friday.", "button": {"label": "Order now", "href": "/o?a=1&b=2"}},
    {"type": "contact", "heading": "Find us", "items": ["Main street 1", "Mon-Fri 7-18"]},
]


@pytest.fixture(scope="module")
def b():
    return mod("packs.generatepress.blocks")


@pytest.fixture(scope="module")
def build():
    return mod("packs.generatepress.build")


# --- a tiny block-comment parser (balanced open/close, JSON attributes) ------------------------

TOKEN = re.compile(r"<!--\s+(/)?wp:([a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)?)\s+(\{.*?\}\s+)?(/)?-->", re.S)


def parse_blocks(content: str) -> list[dict]:
    """Blocks as a tree [{name, attrs, inner}]; raises on unbalanced or mismatched comments."""
    root = {"name": None, "inner": []}
    stack = [root]
    for m in TOKEN.finditer(content):
        closing, name, attrs, void = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            top = stack.pop()
            assert top["name"] == name, f"closing {name} inside {top['name']}"
            continue
        node = {"name": name, "attrs": json.loads(attrs) if attrs else {}, "inner": []}
        stack[-1]["inner"].append(node)
        if not void:
            stack.append(node)
    assert len(stack) == 1, f"unclosed {stack[-1]['name']}"
    return root["inner"]


def walk(blocks):
    for blk in blocks:
        yield blk
        yield from walk(blk["inner"])


# --- serializer rules ----------------------------------------------------------------------------

def test_rule1_empty_styles_and_css_are_never_emitted(b):
    out = b.serialize([b.element("div", {}, [b.text("p", "x", {}, uid="aaaaaaa2")], uid="aaaaaaa1")])
    assert '"styles"' not in out and '"css"' not in out
    attrs = [blk["attrs"] for blk in walk(parse_blocks(out))]
    assert attrs[0] == {"uniqueId": "aaaaaaa1", "tagName": "div"}


def test_rule2_siblings_are_joined_with_a_blank_line_and_the_first_child_hugs_its_parent(b):
    out = b.serialize(b.element("div", {"padding": "1px"}, [b.text("p", "a", uid="aaaaaaa2"),
                                                             b.text("p", "b", uid="aaaaaaa3")], uid="aaaaaaa1"))
    assert '<div class="gb-element-aaaaaaa1"><!-- wp:generateblocks/text' in out
    assert "<!-- /wp:generateblocks/text -->\n\n<!-- wp:generateblocks/text" in out
    assert out.endswith("<!-- /wp:generateblocks/text --></div>\n<!-- /wp:generateblocks/element -->")
    two = b.serialize([b.core_paragraph("a"), b.core_paragraph("b")])
    assert two == "<!-- wp:paragraph -->\n<p>a</p>\n<!-- /wp:paragraph -->\n\n<!-- wp:paragraph -->\n<p>b</p>\n<!-- /wp:paragraph -->"


def test_rule3_unstyled_text_keeps_its_uid_class_as_className(b):
    out = b.serialize(b.text("h3", "Card", uid="abcd1234"))
    assert out == ('<!-- wp:generateblocks/text {"uniqueId":"abcd1234","tagName":"h3","className":"gb-text-abcd1234"} -->\n'
                   '<h3 class="gb-text gb-text-abcd1234">Card</h3>\n<!-- /wp:generateblocks/text -->')
    styled = b.serialize(b.text("h3", "Card", {"color": "red"}, uid="abcd1234"))
    assert '"className"' not in styled and 'class="gb-text gb-text-abcd1234"' in styled


def test_element_html_class_is_only_the_uid_class_and_links_carry_htmlAttributes(b):
    el = b.serialize(b.element("section", {"color": "red"}, uid="abcd1234"))
    assert '<section class="gb-element-abcd1234"></section>' in el
    link = b.serialize(b.text("a", "Go", {"color": "red"}, href="#x", uid="abcd1235"))
    head = parse_blocks(link)[0]["attrs"]
    assert list(head) == ["uniqueId", "tagName", "styles", "css", "htmlAttributes"]
    assert head["htmlAttributes"] == {"href": "#x"}
    assert '<a class="gb-text gb-text-abcd1235" href="#x">Go</a>' in link


def test_attribute_key_order_with_className_and_htmlAttributes(b):
    out = b.serialize(b.text("a", "Go", href="#x", uid="abcd1234"))
    assert list(parse_blocks(out)[0]["attrs"]) == ["uniqueId", "tagName", "className", "htmlAttributes"]
    assert '{"uniqueId":"abcd1234","tagName":"a","className":"gb-text-abcd1234","htmlAttributes":{"href":"#x"}}' in out


# --- css --------------------------------------------------------------------------------------

def test_css_is_kebab_case_sorted_with_the_padding_shorthand(b):
    styles = {"display": "inline-flex", "paddingTop": "10px", "paddingBottom": "10px", "paddingLeft": "20px",
              "paddingRight": "20px", "backgroundColor": "#C4472B", "color": "#ffffff"}
    assert b.css_for("text", "wpbqb001", styles) == \
        ".gb-text-wpbqb001{background-color:#C4472B;color:#ffffff;display:inline-flex;padding:10px 20px}"
    assert b.css_for("element", "x1234567", {"paddingTop": "48px", "paddingBottom": "48px", "backgroundColor": "#F7F3EC"}) == \
        ".gb-element-x1234567{background-color:#F7F3EC;padding-bottom:48px;padding-top:48px}"


@pytest.mark.parametrize("sides,want", [
    (("1px", "1px", "1px", "1px"), "1px"),
    (("1px", "2px", "1px", "2px"), "1px 2px"),
    (("1px", "2px", "3px", "2px"), "1px 2px 3px"),
    (("1px", "2px", "3px", "4px"), "1px 2px 3px 4px"),
])
def test_css_box_shorthand_is_minimal_for_padding_and_margin(b, sides, want):
    for prop in ("padding", "margin"):
        styles = dict(zip((f"{prop}Top", f"{prop}Right", f"{prop}Bottom", f"{prop}Left"), sides))
        assert b.css_for("element", "abcd1234", styles) == f".gb-element-abcd1234{{{prop}:{want}}}"


def test_css_nested_selectors_media_queries_and_empty(b):
    css = b.css_for("element", "abcd1234", {"display": "grid", "@media (max-width:767px)": {"gridTemplateColumns": "1fr"},
                                            "&:hover": {"opacity": "0.9"}})
    assert css == (".gb-element-abcd1234{display:grid}.gb-element-abcd1234:hover{opacity:0.9}"
                   "@media (max-width:767px){.gb-element-abcd1234{grid-template-columns:1fr}}")
    assert b.css_for("text", "abcd1234", {}) == ""
    with pytest.raises(ValueError):
        b.css_for("box", "abcd1234", {"color": "red"})


# --- escaping and ids ---------------------------------------------------------------------------

def test_text_and_attributes_are_escaped_like_the_editor(b):
    out = b.serialize(b.text("a", 'Fish & <chips> "now"', href='/o?a=1&b="2">', uid="abcd1234"))
    assert '>Fish &amp; &lt;chips&gt; "now"</a>' in out
    assert 'href="/o?a=1&amp;b=&quot;2&quot;&gt;"' in out
    head = out.split("\n", 1)[0]
    assert "\\u0026" in head and "\\u0022" in head and "\\u003e" in head and "&" not in head
    assert parse_blocks(out)[0]["attrs"]["htmlAttributes"]["href"] == '/o?a=1&b="2">'
    assert b.esc_text("a &amp; b") == "a &amp; b"  # an entity stays as it is
    assert "\\u002d\\u002d" in b.comment_json({"x": "a--b"})
    assert b.comment_json({"x": "Bäckerei"}) == '{"x":"Bäckerei"}'
    core = b.serialize(b.core_heading("Q&A <now>", 3))
    assert core == '<!-- wp:heading {"level":3} -->\n<h3 class="wp-block-heading">Q&amp;A &lt;now&gt;</h3>\n<!-- /wp:heading -->'


def test_uids_are_deterministic_8_chars_and_unique(b):
    page = lambda seed: b.serialize([b.element("div", {"color": "red"}, [b.text("p", "x") for _ in range(30)])], seed=seed)
    one, two, other = page("s"), page("s"), page("t")
    assert one == two and one != other
    ids = [blk["attrs"]["uniqueId"] for blk in walk(parse_blocks(one))]
    assert len(ids) == 31 == len(set(ids)) and all(re.fullmatch(r"[a-z0-9]{8}", i) for i in ids)
    with pytest.raises(ValueError):
        b.text("p", "x", uid="Bad-id!!")
    with pytest.raises(ValueError):
        b.serialize([b.text("p", "x", uid="abcd1234"), b.text("p", "y", uid="abcd1234")])


def test_core_blocks_markup(b):
    assert b.serialize(b.core_image("/a.png", 'A "cat"')) == (
        '<!-- wp:image -->\n<figure class="wp-block-image"><img src="/a.png" alt="A &quot;cat&quot;"/></figure>\n<!-- /wp:image -->')
    btn = b.serialize(b.core_button("Go", "#g", background="#000000", color="#ffffff"))
    assert btn.startswith('<!-- wp:button {"style":{"color":{"text":"#ffffff","background":"#000000"}}} -->')
    assert ('<a class="wp-block-button__link has-text-color has-background wp-element-button" href="#g" '
            'style="color:#ffffff;background-color:#000000">Go</a>') in btn
    grp = b.serialize(b.core_group([], background="#eeeeee"))
    assert grp.startswith('<!-- wp:group {"style":{"color":{"background":"#eeeeee"}},"layout":{"type":"constrained"}} -->\n'
                          '<div class="wp-block-group has-background" style="background-color:#eeeeee">')
    with pytest.raises(ValueError):
        b.core_heading("x", 7)


def test_media_block(b):
    out = b.serialize(b.media("/a.png", "Shop", uid="abcd1234"))
    assert out == ('<!-- wp:generateblocks/media {"uniqueId":"abcd1234","tagName":"img","htmlAttributes":'
                   '{"src":"/a.png","alt":"Shop"}} -->\n<img class="gb-media-abcd1234" src="/a.png" alt="Shop"/>\n'
                   '<!-- /wp:generateblocks/media -->')


# --- Q1 fixture ---------------------------------------------------------------------------------

def test_q1_patterns_equal_the_markup_the_editor_kept_byte_for_byte(b):
    want = json.loads((FIX / "q1_expected.json").read_text(encoding="utf-8"))
    got = b.q1_patterns()
    assert list(got) == ["p15", "grid", "button", "core"]
    for name in want:
        assert got[name] == want[name], name


# --- section plans ------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["generateblocks", "core"])
def test_section_plan_gives_balanced_blocks_with_brand_colours(b, mode):
    out = b.page_from_section_plan(PLAN, BRAND, mode, seed="t")
    assert out == b.page_from_section_plan(PLAN, BRAND, mode, seed="t")
    tree = parse_blocks(out)
    assert len(tree) == len(PLAN)
    names = {blk["name"] for blk in walk(tree)}
    assert "#C4472B" in out and "#F7F3EC" in out
    assert "Bakery &amp; café" in out and "Fresh &lt;bread&gt;." in out and "Since 1952." in out
    assert "<!-- wp:" in out and "\n\n\n" not in out
    if mode == "generateblocks":
        assert names == {"generateblocks/element", "generateblocks/text"}
        for blk in walk(tree):
            a = blk["attrs"]
            assert a.get("styles") != {} and a.get("css") != ""
            if blk["name"] == "generateblocks/text" and "styles" not in a:
                assert a["className"] == f"gb-text-{a['uniqueId']}"
        assert "repeat(3, minmax(0, 1fr))" in out
    else:
        assert not any(n.startswith("generateblocks/") for n in names)
        assert {"group", "heading", "paragraph", "columns", "column", "buttons", "button"} == names


def test_section_plan_cta_text_colour_reads_on_the_accent(b):
    pal = b.palette(BRAND)
    assert pal["on_accent"] == "#F7F3EC" and b.contrast_ratio(pal["on_accent"], pal["accent"]) > 4
    dark = b.palette({"colors": [{"slug": "accent", "color": "#ffdd00"}]})
    assert dark["on_accent"] == "#222222"


@pytest.mark.parametrize("plan,mode", [
    ([], "core"), ([{"type": "slider"}], "core"), ([{"type": "hero"}], "elementor"),
    ([{"type": "cta", "button": {"label": "x"}}], "core"),
])
def test_section_plan_refuses_bad_input(b, plan, mode):
    with pytest.raises(ValueError):
        b.page_from_section_plan(plan, BRAND, mode)


# --- build run ------------------------------------------------------------------------------------

def walk_to(build, run, step):
    for n, s in enumerate(build.ORDER):
        if s == step:
            return run
        while run["steps"][n]["state"] in ("todo", "running"):
            build.advance(run, s, "hermes")
        if run["steps"][n]["state"] == "waiting":
            build.gate(run, s, "pass")
    return run


def test_new_run_has_the_contract_shape(build):
    run = build.new_run("test-wp", {"name": "Bakery", "must_have": "opening hours"},
                        mode=build.mode_for(True), run_id="build-1", now="t0")
    assert set(run) == {"id", "site", "brief", "brand", "sitemap", "steps", "pages", "created_at", "updated_at", "mode"}
    assert run["mode"] == "generateblocks" and build.mode_for(False) == "core"
    assert run["brief"] == {"name": "Bakery", "audience": "", "must_have": ["opening hours"], "tone": ""}
    assert [s["key"] for s in run["steps"]] == ["brief", "brand", "sitemap", "pages", "header_footer", "proof", "handoff"]
    for s in run["steps"]:
        assert s["state"] == "todo" and s["gate"]["who"] == "you" and set(s["actors"]) <= set(build.ACTORS)
    assert run["steps"][0]["actors"] == ["client"]
    with pytest.raises(build.BuildError):
        build.new_run("x", mode="elementor")


def test_advance_and_gate_walk_a_step_through_changes_to_done(build):
    run = build.new_run("s", run_id="r")
    build.advance(run, "brief", "client")
    assert run["steps"][0]["state"] == "running"
    build.advance(run, "brief", "client", note="brief written")
    assert run["steps"][0]["state"] == "waiting" and run["steps"][0]["gate"]["state"] == "open"
    build.gate(run, "brief", "changes", note="who edits later?")
    assert run["steps"][0]["state"] == "running" and run["steps"][0]["gate"] == {
        "who": "you", "state": "changes", "note": "who edits later?"}
    build.advance(run, "brief", "hermes")
    build.gate(run, "brief", "pass", note="ok")
    assert run["steps"][0]["state"] == "done" and build.current(run) == "brand"


@pytest.mark.parametrize("action,code", [
    (lambda bl, r: bl.advance(r, "brand", "hermes"), "out_of_order"),           # no skipping a gate
    (lambda bl, r: bl.gate(r, "brief", "pass"), "state"),                      # gate not open yet
    (lambda bl, r: bl.advance(r, "brief", "you"), "who"),                      # not an actor of the brief
    (lambda bl, r: bl.advance(r, "nope", "hermes"), "unknown_step"),
    (lambda bl, r: bl.advance(r, "brief", "robot"), "who"),
])
def test_refused_transitions_on_a_fresh_run(build, action, code):
    run = build.new_run("s")
    with pytest.raises(build.BuildError) as err:
        action(build, run)
    assert err.value.code == code


def test_waiting_step_needs_its_gate_and_only_the_gate_person_decides(build):
    run = build.new_run("s")
    build.advance(run, "brief", "client")
    build.advance(run, "brief", "client")
    with pytest.raises(build.BuildError) as err:
        build.advance(run, "brief", "client")
    assert err.value.code == "state"
    with pytest.raises(build.BuildError) as err:
        build.advance(run, "brand", "hermes")
    assert err.value.code == "out_of_order"
    with pytest.raises(build.BuildError) as err:
        build.gate(run, "brief", "pass", by="client")
    assert err.value.code == "who"
    with pytest.raises(build.BuildError):
        build.gate(run, "brief", "changes", note="  ")
    with pytest.raises(build.BuildError):
        build.gate(run, "brief", "maybe")


def test_a_done_step_never_moves_again(build):
    run = walk_to(build, build.new_run("s"), "brand")
    for act in (lambda: build.advance(run, "brief", "hermes"), lambda: build.gate(run, "brief", "pass"),
                lambda: build.gate(run, "brief", "changes", note="x")):
        with pytest.raises(build.BuildError) as err:
            act()
        assert err.value.code == "done"


def test_pages_are_desk_items_added_while_the_pages_step_runs(build):
    run = walk_to(build, build.new_run("s"), "pages")
    with pytest.raises(build.BuildError):
        build.add_page(run, "d1")
    build.advance(run, "pages", "hermes")
    build.add_page(run, "d1")
    build.add_page(run, "d2")
    build.add_page(run, "d1")
    assert run["pages"] == ["d1", "d2"]
    walk_to(build, run, None)
    assert build.current(run) is None and all(s["state"] == "done" for s in run["steps"])


def test_handoff_guide_names_the_pages_and_the_editor_steps(build):
    run = build.new_run("bakery", {"name": "Bakery Jana"}, mode="generateblocks")
    run["pages"] = ["d1", "d2"]
    items = [{"id": "d1", "title": "Home", "links": {"edit": "https://a.example/wp-admin/post.php?post=5&action=edit"}},
             {"id": "d2", "title": "Kontakt", "links": {}}]
    text = build.handoff_guide(run, items, site_url="https://a.example/", client="Jana")
    assert text.startswith("# Your new website: Bakery Jana")
    assert "- **Home** (edit: https://a.example/wp-admin/post.php?post=5&action=edit)" in text
    assert "- **Kontakt**\n" in text and "https://a.example/wp-admin/" in text
    assert "GenerateBlocks" in text and "This guide is for Jana." in text and "**Publish**" in text
    core = build.new_run("s", mode="core")
    core["sitemap"] = [{"slug": "home", "title": "Home"}]
    assert "- **Home**" in build.handoff_guide(core) and "Group, Heading" in build.handoff_guide(core)
