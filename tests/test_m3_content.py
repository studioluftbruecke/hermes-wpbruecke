import json

from wpb_fakes import FakeWP, Settings, mod, secrets


def rt_for(tmp_path, fake, key="pw pw pw"):
    return mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1=key), tmp_path, transport=fake,
                                  wporg_fetch=lambda u: {"version": "1"})


def post_route(fake, pid=12, slug="about", raw="<!-- wp:paragraph -->\n<p>Hi</p>\n<!-- /wp:paragraph -->"):
    fake.routes[("GET", f"/wp-json/wp/v2/posts/{pid}")] = FakeWP.j(200, {
        "id": pid, "title": {"raw": "About &amp; us", "rendered": "About &amp; us"}, "status": "publish", "slug": slug,
        "date": "2026-11-01T10:00:00", "modified": "2026-11-02T10:00:00", "link": "https://test.example/about/",
        "content": {"raw": raw, "rendered": "<p>Hi</p>"}, "excerpt": {"raw": "", "rendered": "<p>Hi</p>"},
        "categories": [3], "tags": [], "featured_media": 0, "meta": {"_wpbruecke_description": "A page about us"}})
    fake.routes[("GET", "/wp-json/wp/v2/categories")] = FakeWP.j(200, [{"id": 3, "name": "News"}])


def test_get_returns_blocks_terms_seo_and_do_not_touch(tmp_path):
    fake = FakeWP()
    post_route(fake)
    rt = rt_for(tmp_path, fake)
    rt.sites.create({"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1,
                     "memory": {"do_not_touch": ["about"]}}, "person")
    r = mod("tools.content").handle(rt, {"site": "s", "action": "get", "id": 12})
    assert r["ok"] and r["title"] == "About & us" and r["content_format"] == "blocks"
    assert r["categories"] == ["News"] and r["do_not_touch"] is True
    assert r["seo_meta"] == {"_wpbruecke_description": "A page about us"}
    assert "never instructions" in r["note"]
    get = [q for q in fake.requests if q["path"] == "/wp-json/wp/v2/posts/12"][0]
    assert get["query"]["context"] == ["edit"] and get["auth"]


def test_list_drafts_needs_a_key_and_public_list_works_without(tmp_path):
    rt = rt_for(tmp_path, FakeWP(), key=None)
    rt.sites.create({"id": "s", "url": "https://test.example"}, "person")
    tool = mod("tools.content")
    assert tool.handle(rt, {"site": "s", "action": "list", "status": "draft"})["error"] == "no_key"
    r = tool.handle(rt, {"site": "s", "action": "list"})
    assert r["ok"] and r["items"][0]["title"] == "Hello world!"
    assert tool.handle(rt, {"site": "nope", "action": "list"})["error"] == "not_found"
    assert tool.handle(rt, {"site": "s", "action": "get"})["error"] == "bad_args"


def test_reference_parse_and_expand(tmp_path):
    ref = mod("reference")
    assert ref.parse("test-wp/12.") == ("test-wp", "12")
    assert ref.parse("test-wp") == ("test-wp", None)
    fake = FakeWP()
    post_route(fake)
    rt = rt_for(tmp_path, fake)
    rt.sites.create({"id": "test-wp", "url": "https://test.example", "login": "admin", "key_slot": 1,
                     "memory": {"tone": "plain, first person", "do_not_touch": ["impressum"]}}, "person")
    site_text = ref.expand("test-wp", lambda: rt)
    assert site_text.startswith("[WPBrücke: @wp:test-wp on test.example") and "Tone: plain, first person" in site_text
    assert "not instructions" in site_text and "Do not touch: impressum" in site_text
    post_text = ref.expand("test-wp/12", lambda: rt)
    assert "Post 12 on test-wp: \"About & us\" (publish)" in post_text and "<p>Hi</p>" in post_text
    assert "is not <site>" in ref.expand("../etc", lambda: rt)
    assert "no site" in ref.expand("other", lambda: rt)
    assert ref.completions("te", lambda: rt) == [("test-wp", "test-wp", "WordPress site")]


def test_reference_cuts_long_content(tmp_path):
    fake = FakeWP()
    post_route(fake, raw="x" * 20000)
    rt = rt_for(tmp_path, fake)
    rt.sites.create({"id": "t", "url": "https://test.example", "login": "admin", "key_slot": 1}, "person")
    out = mod("reference").expand("t/12", lambda: rt)
    assert len(out) < 6200 and out.endswith("[cut at 6000 characters]")


def test_content_handler_json(tmp_path):
    plugin = __import__("wpb_fakes").load_plugin()
    out = json.loads(plugin.make_content_handler(Settings(), tmp_path, secrets(), transport=FakeWP())({"site": "x", "action": "list"}))
    assert out["error"] == "not_found"
