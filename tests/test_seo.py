"""SEO layer (M6): post check, site check, PageSpeed parse, description target, score arithmetic."""
import json

import pytest

from wpb_fakes import mod

SITE = "https://example.test"

EN_POST = {
    "title": "Five things I learned running my own WordPress server at home for a year",
    "slug": "five-things-i-learned-running-my-own-wordpress-server",
    "excerpt": "",
    "images": [{"src": "https://example.test/wp-content/uploads/2026/11/server-shelf.jpg", "alt": "A small server on a shelf"}],
    "content_html": """
<p>Last winter I moved my blog from a shared host to a small box under my desk. It was cheaper than I
thought, and it taught me more than any course.</p>
<h2>Backups come first</h2>
<p>Make a backup before you change anything. Test the restore once a month. A backup you never tried is
only a hope.</p>
<h2>Updates are boring</h2>
<p>Turn on automatic updates for minor releases. Read the notes for big ones. Most weeks nothing breaks,
and that is the point.</p>
<h3>Plugins</h3>
<p>Keep few plugins. Each one is code you did not write and must trust.</p>
<figure><img src="https://example.test/wp-content/uploads/2026/11/IMG_2041.jpg" alt=""></figure>
<p>Read more on <a href="https://other.example/guide">a guide elsewhere</a>.</p>
""",
}

DE_POST = {
    "title": "Warum ich meinen Garten im Herbst nicht mehr aufräume",
    "slug": "warum-ich-meinen-garten-im-herbst-nicht-mehr-aufraeume",
    "excerpt": "",
    "images": [],
    "content_html": """
<p>Früher habe ich jeden Oktober alle Beete leer geräumt. Heute lasse ich die Stängel stehen. Im Winter
wohnen darin Käfer und Bienen.</p>
<h2>Was ich stattdessen mache</h2>
<p>Ich schneide nur die Wege frei. Das Laub bleibt unter den Sträuchern liegen. Es schützt den Boden vor
Frost und wird im Frühjahr zu Erde.</p>
<p>Die Vögel finden Samen in den alten Blüten. Das sieht nicht ordentlich aus, aber der Garten lebt.
Mehr dazu steht in <a href="/naturgarten-anfangen/">meinem ersten Beitrag</a>.</p>
""",
}

INTERNAL = [
    {"id": 7, "title": "How I test backups of my WordPress server", "url": "https://example.test/test-backups/"},
    {"id": 8, "title": "Baking sourdough bread", "url": "https://example.test/sourdough/"},
    {"id": 9, "title": {"rendered": "Choosing plugins you can trust"}, "url": "https://example.test/plugins-trust/"},
]


def by_check(report):
    return {c["check"]: c for c in report["checks"]}


def assert_rows_ok(report):
    seo = mod("seo")
    assert sum(c["weight"] for c in report["checks"]) == 100
    for c in report["checks"]:
        assert c["verdict"] in seo.VERDICTS
        assert c["detail"].strip()
        if "fix" in c:
            assert c["fix"]["kind"] in seo.FIX_KINDS and c["fix"]["why"]


# --- readability -------------------------------------------------------------------------------

def test_syllable_counters():
    post = mod("seo.post")
    assert [post.syllables_en(w) for w in ("the", "time", "wanted", "jumped", "people", "every", "beautiful", "boxes")] \
        == [1, 1, 2, 1, 2, 3, 3, 2]
    assert [post.syllables_de(w) for w in ("Haus", "Häuser", "schreiben", "Bauer", "Theater", "See", "über", "Biene")] \
        == [1, 2, 2, 2, 3, 1, 2, 2]


def test_readability_formulas_by_hand():
    post = mod("seo.post")
    en = post.readability("The cat sat. The dog ran away.", "en")
    # 7 words, 2 sentences, syllables 1+1+1+1+1+1+2 = 8
    assert en["formula"] == "flesch" and en["words"] == 7 and en["sentences"] == 2
    assert en["score"] == round(206.835 - 1.015 * 3.5 - 84.6 * 8 / 7, 1)
    de = post.readability("Der Hund läuft. Die Katze schläft heute.", "de_DE")
    # words 7, sentences 2; syllables: der1 hund1 läuft1 die1 katze2 schläft1 heute2 = 9
    assert de["formula"] == "flesch_amstad"
    assert de["score"] == round(180 - 3.5 - 58.5 * 9 / 7, 1)
    assert post.band(64) == "plain" and post.band(95) == "very easy" and post.band(10) == "very hard"
    fr = post.readability("Le chat dort.", "fr")
    assert fr == {"formula": None, "score": None, "label": "no formula for fr", "words": None, "sentences": None}


# --- post check --------------------------------------------------------------------------------

def test_english_post_check():
    post = mod("seo.post")
    r = post.check_post(EN_POST, site_url=SITE, lang="en", description=None, description_source=None,
                        internal_posts=INTERNAL)
    assert r["scope"] == "post" and r["lang"] == "en"
    assert_rows_ok(r)
    c = by_check(r)
    # title 73 chars -> differs with a shorter suggestion
    assert c["title"]["verdict"] == "differs"
    short = c["title"]["fix"]["value"]
    assert c["title"]["fix"]["kind"] == "title" and len(short) <= 60 and EN_POST["title"].startswith(short)
    assert short.split()[-1].lower() not in mod("seo.post").STOP["en"]
    # description missing -> proposed from the first paragraph, <= 155, cut at a word / sentence
    d = c["description"]
    assert d["verdict"] == "differs" and d["fix"]["kind"] == "description"
    assert 70 <= len(d["fix"]["value"]) <= 155
    assert d["fix"]["value"].startswith("Last winter I moved my blog")
    assert d["fix"]["value"].endswith(".")
    # headings: H2, H2, H3, no H1 in content
    assert c["headings"]["verdict"] == "match"
    # only an external link -> differs; the backup and plugin posts share words, sourdough does not
    il = c["internal_links"]
    assert il["verdict"] == "differs" and il["fix"]["kind"] == "internal_links"
    ids = [p["id"] for p in il["fix"]["value"]]
    assert len(ids) == 2 and 8 not in ids and 7 in ids
    # alt: 1 of 2, the camera file is named, with no made-up suggestion
    alt = c["alt"]
    assert alt["verdict"] == "differs" and "1 of 2 images" in alt["detail"] and "IMG_2041.jpg" in alt["detail"]
    assert alt["fix"]["value"] == [{"src": "https://example.test/wp-content/uploads/2026/11/IMG_2041.jpg",
                                    "file": "IMG_2041.jpg", "suggested": None}]
    assert c["slug"]["verdict"] == "match"
    rd = c["readability"]
    assert rd["verdict"] in ("match", "differs") and rd["readability"]["formula"] == "flesch"
    assert rd["detail"].startswith("Flesch ")
    # score = matched weights / proven weights
    proven = [x for x in r["checks"] if x["verdict"] != "not_proven"]
    assert r["score"] == round(100 * sum(x["weight"] for x in proven if x["verdict"] == "match")
                               / sum(x["weight"] for x in proven))


def test_german_post_check():
    post = mod("seo.post")
    desc = "Warum ich im Herbst die Beete stehen lasse: Stängel, Laub und Samen helfen Käfern, Bienen und Vögeln."
    r = post.check_post(DE_POST, site_url=SITE, lang="de", description=desc, description_source="connector")
    assert_rows_ok(r)
    c = by_check(r)
    assert c["title"]["verdict"] == "match"
    assert c["description"]["verdict"] == "match" and "(connector)" in c["description"]["detail"]
    assert c["internal_links"]["verdict"] == "match" and c["internal_links"]["detail"].startswith("1 internal")
    assert c["alt"]["verdict"] == "match" and c["alt"]["detail"] == "No images."
    assert c["readability"]["readability"]["formula"] == "flesch_amstad"
    assert c["readability"]["detail"].startswith("Flesch-Amstad ")
    # 'ich', 'im', 'nicht' make three filler words in the slug -> differs with a cleaner suggestion
    s = c["slug"]
    assert s["verdict"] == "differs" and "Suggested: warum-meinen-garten-herbst-mehr-aufraeume" in s["detail"]


def test_extra_h1_and_skipped_level_and_other_language():
    post = mod("seo.post")
    p = {"title": "A plain title that is long enough", "slug": "a-plain-title",
         "content_html": "<h1>Again</h1><p>Text.</p><h2>One</h2><h4>Deep</h4><p>Bonjour.</p>", "images": []}
    r = post.check_post(p, site_url=SITE, lang="fr", description=None, description_source=None)
    c = by_check(r)
    h = c["headings"]
    assert h["verdict"] == "differs" and "H1 in the content" in h["detail"] and "skips H3" in h["detail"]
    assert c["readability"]["verdict"] == "not_proven" and c["readability"]["detail"] == "no formula for fr"
    assert_rows_ok(r)


def test_slug_rules_and_empty_slug():
    post = mod("seo.post")
    base = {"title": "Über uns und unsere kleine Gärtnerei", "content_html": "<p>x</p>", "images": []}
    c = by_check(post.check_post({**base, "slug": "%c3%bcber-uns"}, site_url=SITE, lang="de", description=None,
                                 description_source=None))
    assert c["slug"]["verdict"] == "differs" and "encoded" in c["slug"]["detail"]
    c = by_check(post.check_post({**base, "slug": ""}, site_url=SITE, lang="de", description=None,
                                 description_source=None))
    assert c["slug"]["verdict"] == "not_proven" and "ueber-uns-unsere-kleine-gaertnerei" in c["slug"]["detail"]
    long = post.slugify("gardening " * 12)
    assert long == "-".join(["gardening"] * 6) and len(long) <= 60


def test_description_too_long_and_short_title():
    post = mod("seo.post")
    long = "word " * 50
    p = {"title": "Short", "slug": "short", "content_html": "<p>Hi.</p>", "images": []}
    c = by_check(post.check_post(p, site_url=SITE, description=long, description_source="yoast"))
    assert c["title"]["verdict"] == "differs" and "fix" not in c["title"]
    d = c["description"]
    assert d["verdict"] == "differs" and len(d["fix"]["value"]) <= 155 and d["fix"]["value"].endswith("…")
    assert c["readability"]["verdict"] == "not_proven" and "needs at least" in c["readability"]["detail"]


def test_propose_description_cuts_at_a_word():
    post = mod("seo.post")
    para = ("This paragraph keeps going without a full stop for a long while so the proposal has to cut it "
            "somewhere sensible near the limit and never in the middle of a word like extraordinarily")
    out = post.propose_description([para])
    assert len(out) <= 155 and out.endswith("…") and para.startswith(out[:-1])
    assert para[len(out) - 1] == " "


# --- score -------------------------------------------------------------------------------------

def test_score_arithmetic():
    seo = mod("seo")
    rows = [seo.row("a", "A", "match", "ok", 40), seo.row("b", "B", "differs", "no", 30),
            seo.row("c", "C", "not_proven", "why", 30)]
    assert seo.score(rows) == round(100 * 40 / 70)  # 57
    assert seo.score([seo.row("c", "C", "not_proven", "why", 100)]) is None
    assert seo.score([seo.row("a", "A", "match", "ok", 100)]) == 100
    with pytest.raises(ValueError):
        seo.row("x", "X", "not_proven", "  ", 10)
    with pytest.raises(ValueError):
        seo.row("x", "X", "maybe", "d", 10)
    with pytest.raises(ValueError):
        seo.row("x", "X", "differs", "d", 10, fix={"kind": "slug", "value": "", "why": ""})


# --- site check --------------------------------------------------------------------------------

def p6_facts():
    """The test site's public shape (P6): noindex, sitemap 404, robots 200, no description, no canonical."""
    return {"site": "test-wp", "url": SITE, "reachable": True,
            "namespaces": ["oembed/1.0", "wp/v2", "wp-site-health/v1", "wp-abilities/v1"],
            "seo_public": {"robots_meta": "noindex, nofollow", "noindex": True, "meta_description": None,
                           "canonical": None, "title": "Wordpress Test Page", "robots_txt": 200, "core_sitemap": 404,
                           "third_party_hosts": [], "wp_version_meta": "7.1.4", "themes_in_html": ["twentytwentyfive"]}}


PROOF = {"id": "pr1", "widths": [1440, 768, 390], "console_errors": [], "verdict": "match",
         "checks": [{"check": "overflow", "verdict": "match", "detail": "0 px", "width": 390},
                    {"check": "overflow", "verdict": "match", "detail": "0 px", "width": 768},
                    {"check": "console", "verdict": "match", "detail": "0 errors"}],
         "perf": {"1440": {"load_ms": 2000, "lcp_ms": 900, "cls": 0},
                  "768": {"load_ms": 1600, "lcp_ms": 800, "cls": 0},
                  "390": {"load_ms": 2500, "lcp_ms": 1200, "cls": 0.01}}}


def test_site_check_on_the_test_site_shape():
    site = mod("seo.site")
    r = site.check_site(p6_facts(), home_html=None, proof_run=PROOF, pagespeed=None)
    assert r["scope"] == "site" and r["site"] == "test-wp"
    assert_rows_ok(r)
    c = by_check(r)
    ix = c["indexing"]
    assert ix["verdict"] == "differs" and ix["label"] == "Indexing is off"
    assert "Discourage search engines" in ix["detail"] and "noindex, nofollow" in ix["detail"]
    assert ix["action"]["url"] == "https://example.test/wp-admin/options-reading.php" and "fix" not in ix
    assert c["sitemap"]["verdict"] == "differs" and c["sitemap"]["label"] == "No sitemap"
    assert "once indexing is on" in c["sitemap"]["detail"]
    assert c["robots_txt"]["verdict"] == "match"
    assert c["home_description"]["verdict"] == "differs" and "connector" in c["home_description"]["detail"]
    assert c["home_description"]["action"]["kind"] == "install_connector"
    assert c["canonical"]["verdict"] == "differs"
    assert c["mobile_layout"]["verdict"] == "match" and "390" in c["mobile_layout"]["detail"]
    assert c["console"]["verdict"] == "match"
    sp = c["speed"]
    assert sp["verdict"] == "not_proven" and sp["source"] == "local, not Google"
    assert sp["detail"].startswith("PageSpeed needs a key (keyless answers 429)")
    assert "1.6 to 2.5 s" in sp["detail"] and sp["measure"]["load_ms"] == (1600, 2500)
    assert c["broken_links"]["verdict"] == "not_proven"
    # proven: indexing 25 d, sitemap 10 d, robots 5 m, desc 10 d, canonical 5 d, mobile 10 m, console 5 m = 70
    assert r["score"] == round(100 * 20 / 70)


def test_site_check_without_proof_run_and_with_seo_plugin():
    site = mod("seo.site")
    facts = p6_facts()
    facts["namespaces"].append("yoast/v1")
    r = site.check_site(facts, home_html=None, proof_run=None, pagespeed=None)
    assert_rows_ok(r)
    c = by_check(r)
    for key in ("mobile_layout", "console", "broken_links"):
        assert c[key]["verdict"] == "not_proven" and c[key]["detail"] == "no proof run yet"
    assert c["speed"]["verdict"] == "not_proven" and "no proof run yet" in c["speed"]["detail"]
    assert "Yoast SEO (yoast/v1)" in c["home_description"]["detail"]
    assert "Yoast SEO" in c["indexing"]["detail"]


def test_site_check_with_pagespeed_and_home_html_and_links():
    site = mod("seo.site")
    home = ('<html><head><meta name="description" content="A garden blog about letting things grow.">'
            '<link rel="canonical" href="https://example.test/"></head><body></body></html>')
    proof = {**PROOF, "links": [{"href": "https://example.test/a/", "status": 200},
                                {"href": "https://example.test/gone/", "status": 404},
                                {"href": "https://other.example/", "status": None}]}
    r = site.check_site(p6_facts(), home_html=home, proof_run=proof,
                        pagespeed={"performance": 93, "lcp_ms": 1800, "cls": 0.02, "source": "google"})
    c = by_check(r)
    assert c["indexing"]["verdict"] == "match"   # the fresh home page has no robots meta
    assert c["home_description"]["verdict"] == "match" and c["canonical"]["verdict"] == "match"
    assert c["speed"]["verdict"] == "match" and c["speed"]["source"] == "google"
    assert c["speed"]["detail"].startswith("Google PageSpeed, mobile: performance 93")
    bl = c["broken_links"]
    assert bl["verdict"] == "differs" and "1 of 2 links broken" in bl["detail"] and "/gone/" in bl["detail"]
    slow = site.check_site(p6_facts(), home_html=None, proof_run=None,
                           pagespeed={"performance": 41, "lcp_ms": 5200, "cls": 0.3})
    assert by_check(slow)["speed"]["verdict"] == "differs"


def test_unreachable_site_is_not_proven_not_differs():
    site = mod("seo.site")
    r = site.check_site({"site": "x", "url": SITE, "reachable": False, "seo_public": {"robots_txt": None,
                                                                                      "core_sitemap": None}},
                        home_html=None, proof_run=None, pagespeed=None)
    assert_rows_ok(r)
    assert all(c["verdict"] == "not_proven" for c in r["checks"]) and r["score"] is None


# --- PageSpeed ---------------------------------------------------------------------------------

PSI_RESPONSE = {  # the shape of a v5 runPagespeed answer, trimmed to what is read
    "id": "https://example.test/",
    "lighthouseResult": {
        "requestedUrl": "https://example.test/", "lighthouseVersion": "12.0.0",
        "categories": {"performance": {"id": "performance", "title": "Performance", "score": 0.87}},
        "audits": {"largest-contentful-paint": {"id": "largest-contentful-paint", "score": 0.8,
                                                "numericValue": 2712.4, "numericUnit": "millisecond",
                                                "displayValue": "2.7 s"},
                   "cumulative-layout-shift": {"id": "cumulative-layout-shift", "score": 1,
                                               "numericValue": 0.0412, "numericUnit": "unitless",
                                               "displayValue": "0.041"}}},
}


def test_pagespeed_parses_and_hides_the_key():
    site = mod("seo.site")
    seen = []

    def fetch(url):
        seen.append(url)
        return json.loads(json.dumps(PSI_RESPONSE))

    out = site.pagespeed("https://example.test/", "SECRETKEY123", fetch)
    assert out["performance"] == 87 and out["lcp_ms"] == 2712 and out["cls"] == 0.041
    assert "SECRETKEY123" not in json.dumps(out)
    u = seen[0]
    assert u.startswith("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?")
    assert "strategy=mobile" in u and "category=performance" in u and "key=SECRETKEY123" in u
    assert "url=https%3A%2F%2Fexample.test%2F" in u


def test_pagespeed_errors_never_carry_the_key():
    site = mod("seo.site")

    def boom(url):
        raise OSError(f"timeout fetching {url}")

    with pytest.raises(site.PageSpeedError) as e:
        site.pagespeed("https://example.test/", "SECRETKEY123", boom)
    assert "SECRETKEY123" not in str(e.value) and "***" in str(e.value)
    quota = {"error": {"code": 429, "message": "Quota exceeded for quota metric 'Queries'"}}
    with pytest.raises(site.PageSpeedError, match="429"):
        site.pagespeed("https://example.test/", "SECRETKEY123", lambda u: quota)
    called = []
    with pytest.raises(site.PageSpeedError, match="needs a key"):
        site.pagespeed("https://example.test/", "", lambda u: called.append(u))
    assert called == []


# --- description target ------------------------------------------------------------------------

def test_description_target():
    fields = mod("seo.fields")
    none = fields.description_target({"namespaces": ["wp/v2"]})
    assert none == {"where": None, "meta_key": None,
                    "reason": "WordPress core has no description field; install the WPBrücke connector."}
    conn = fields.description_target({"namespaces": ["wp/v2", "wpbruecke/v1"]})
    assert conn["where"] == "connector" and conn["meta_key"] == "_wpbruecke_description"
    for ns, where, key in (("yoast/v1", "yoast", "_yoast_wpseo_metadesc"),
                           ("rankmath/v1", "rankmath", "rank_math_description"),
                           ("seopress/v1", "seopress", "_seopress_titles_desc")):
        t = fields.description_target({"namespaces": ["wp/v2", ns], "connector": {"version": "0.1.0"}})
        assert (t["where"], t["meta_key"]) == (where, key) and t["reason"]
        bare = fields.description_target({"namespaces": ["wp/v2", ns]})
        assert bare["where"] is None and "connector" in bare["reason"]
    said = fields.description_target({"namespaces": [], "connector": {"version": "0.1", "seo_plugin": "rankmath"}})
    assert said["where"] == "rankmath"
