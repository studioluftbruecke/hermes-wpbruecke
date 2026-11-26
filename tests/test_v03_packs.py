"""v0.3 WG3: the pack contract. The registry, packs in the site file (registry ids only, guarded), design
tokens read live with the site file's overrides, and what wp_doctor and /overview say per pack."""
import json

from wpb_fakes import ROLE_CAPS, ROOT, FakeWP, Settings, create_paired, load_plugin, mod, secrets

THEME_JSON = {
    "settings": {"color": {"palette": {"theme": [{"slug": "base", "name": "Base", "color": "#FFFFFF"},
                                                 {"slug": "contrast", "name": "Contrast", "color": "#111111"},
                                                 {"slug": "accent-1", "name": "Accent 1", "color": "#FFEE58"}],
                                       "default": [{"slug": "black", "name": "Black", "color": "#000000"}]}},
                 "typography": {"fontFamilies": {"theme": [{"slug": "manrope", "name": "Manrope",
                                                            "fontFamily": "Manrope, sans-serif"},
                                                           {"slug": "fira-code", "name": "Fira Code",
                                                            "fontFamily": "\"Fira Code\", monospace"}]}}},
    "styles": {"typography": {"fontFamily": "var:preset|font-family|manrope"},
               "elements": {"heading": {"typography": {"fontFamily": "var:preset|font-family|fira-code"}}}},
}


def setup(tmp_path, role="editor", connector=False, **site):
    fake = FakeWP(user="hermes", caps=ROLE_CAPS[role], roles=[role],
                  namespaces=["wp/v2"] + (["wpbruecke/v1"] if connector else []))
    if connector:
        fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.3.0"})
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1, **site}, "person")
    return fake, rt


def wire(rt, run):
    plugin = load_plugin()
    tickets = mod("hooks").Tickets()
    kw = dict(transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})

    def rt_factory():
        return mod("runtime").Runtime(rt.get_setting, rt.get_secret, rt.store.root, **kw)
    specs = {"wp_sites": mod("hooks").Spec(lambda a: bool(mod("tools.sites").touches_guarded(a)),
                                           lambda a, r: plugin.decide_sites(rt_factory(), a, r))}
    gate = mod("hooks").make_gate(specs, lambda: run, lambda: True, tickets)
    sites = plugin.make_gated_sites_handler(rt.get_setting, rt.store.root, lambda: run, rt.get_secret,
                                            tickets=tickets, **kw)
    return gate, (lambda a: json.loads(sites(a, session_id="x")))


# --- the registry ---------------------------------------------------------------------------------------

def test_the_registry_holds_generatepress_with_the_full_contract():
    packs = mod("packs")
    assert {"elementor", "generatepress", "woocommerce", "fluentcrm"} <= set(packs.ids()) and packs.ids() == sorted(packs.REGISTRY)
    gp = packs.get("generatepress")
    assert gp.kind == "theme" and gp.skill == "references/pack-generatepress.md" and gp.tokens is not None
    assert gp.scope.endswith(".") and "does not edit existing pages" in gp.scope
    assert set(gp.requires) == {"connector", "namespaces", "abilities"}
    op = packs.OpSpec(ability="x/y", level="drafts", gate="card")
    assert (op.route, op.needs, op.default_input) == (None, None, {})


def test_the_skill_file_of_every_pack_exists():
    skills = ROOT / "skills" / "wpbruecke"
    for spec in mod("packs").REGISTRY.values():
        assert (skills / spec.skill).is_file() and spec.skill.count("/") == 1, spec.skill  # P35: not nested
    skill = (skills / "SKILL.md").read_text()
    assert "references/pack-generatepress.md" in skill


def test_features_read_the_registry():
    features = mod("features")
    rule = features.BY_KEY["build_run"]
    assert rule.pack == "generatepress" and "packs.generatepress: true" in rule.fix
    facts = {"reachable": True, "keyed": True, "rights": {"draft": True}}
    site = {"key_slot": 1, "packs": {"generatepress": False}}
    assert "pack is off" in features.check(rule, site, facts)
    assert features.check(rule, {**site, "packs": {"generatepress": True}}, facts) is None


# --- packs in the site file ---------------------------------------------------------------------------

def test_an_unknown_pack_is_refused_with_the_known_ids(tmp_path):
    fake, rt = setup(tmp_path)
    sites_mod = mod("sites")
    try:
        sites_mod.validate({"id": "x", "url": "https://x.example", "packs": {"divi": True}})
        raise AssertionError("divi: true was accepted")
    except sites_mod.UnknownPackError as e:
        assert "divi" in str(e) and "known packs: " + ", ".join(mod("packs").ids()) in str(e)
    gate, sites = wire(rt, mod("lanes").RunContext())
    args = {"action": "update", "site": "s", "changes": {"packs": {"divi": True}}}
    verdict = gate("wp_sites", args, "x")
    assert verdict["action"] == "block" and "unknown_pack" in verdict["message"] and "generatepress" in verdict["message"]
    out = sites(args)
    assert out["ok"] is False and out["error"] == "unknown_pack" and "generatepress" in out["message"]
    assert rt.sites.get("s")["packs"] == {pid: False for pid in mod("packs").ids()}
    direct = mod("tools.sites").handle(rt, args)
    assert direct["error"] == "unknown_pack"


def test_an_agent_change_to_packs_gets_a_card_and_an_unattended_one_is_refused(tmp_path):
    fake, rt = setup(tmp_path)
    args = {"action": "update", "site": "s", "changes": {"packs": {"generatepress": True}}}
    assert mod("tools.sites").touches_guarded(args) == ["packs"]
    gate, sites = wire(rt, mod("lanes").RunContext())
    assert sites(args)["error"] == "not_approved" and rt.sites.get("s")["packs"]["generatepress"] is False
    card = gate("wp_sites", args, "x")
    assert card["action"] == "approve" and "packs.generatepress: false -> true" in card["message"]
    assert sites(args)["ok"] and rt.sites.get("s")["packs"]["generatepress"] is True
    off = {"action": "update", "site": "s", "changes": {"packs": {"generatepress": False}}}
    gate_u, sites_u = wire(rt, mod("lanes").RunContext(single_query=True))
    verdict = gate_u("wp_sites", off, "x")
    assert verdict["action"] == "block" and "packs_guarded" in verdict["message"]
    out = sites_u(off)
    assert out["ok"] is False and out["error"] == "packs_guarded" and rt.sites.get("s")["packs"]["generatepress"]


def test_other_site_fields_stay_free(tmp_path):
    fake, rt = setup(tmp_path)
    gate, sites = wire(rt, mod("lanes").RunContext())
    free = {"action": "update", "site": "s", "changes": {"design": {"fonts": {"headings": "Fraunces"}}}}
    assert gate("wp_sites", free, "x") is None and sites(free)["ok"]
    assert rt.sites.get("s")["design"] == {"fonts": {"headings": "Fraunces"}}


# --- design tokens ------------------------------------------------------------------------------------

def test_tokens_come_from_theme_json_and_the_site_file_overrides_key_by_key(tmp_path):
    fake, rt = setup(tmp_path, design={"colors": [{"slug": "accent-1", "name": "Brand red", "value": "#C4472B"},
                                                  {"slug": "brand-cream", "value": "#F7F3EC"}],
                                       "fonts": {"headings": "Fraunces"}})
    fake.routes[("GET", "/wp-json/wp/v2/global-styles/themes/twentytwentyfive")] = FakeWP.j(200, THEME_JSON)
    out = mod("tools.sites").handle(rt, {"action": "get", "site": "s"})
    design = out["design"]
    assert design["source"] == "theme.json" and "error" not in design
    colors = {c["slug"]: c for c in design["colors"]}
    assert colors["accent-1"] == {"slug": "accent-1", "name": "Brand red", "value": "#C4472B"}
    assert colors["base"]["value"] == "#FFFFFF" and colors["contrast"]["value"] == "#111111"
    assert colors["brand-cream"]["value"] == "#F7F3EC" and colors["black"]["value"] == "#000000"
    assert design["fonts"] == {"body": "Manrope, sans-serif", "headings": "Fraunces"}
    assert sorted(design["overridden"]) == ["colors.accent-1", "colors.brand-cream", "fonts.headings"]
    assert rt.sites.get("s")["design"]["fonts"] == {"headings": "Fraunces"}  # the file keeps overrides only
    assert len(rt.sites.get("s")["design"]["colors"]) == 2


def test_generatepress_tokens_come_through_the_connector_ability(tmp_path):
    fake, rt = setup(tmp_path, connector=True)
    fake.routes[("GET", "/")] = (200, {"Content-Type": "text/html"},
                                 b"<html><head><link rel='stylesheet' href='https://test.example/wp-content/themes/"
                                 b"generatepress/style.css'></head><body></body></html>")
    fake.routes[("GET", "/wp-json/wp-abilities/v1/abilities/wpbruecke/gp-brand-get/run")] = FakeWP.j(200, {
        "generatepress": True, "colors": [{"slug": "accent", "name": "Accent", "color": "#1e73be"},
                                          {"slug": "base", "name": "Base", "color": "#f0f0f0"}],
        "staged": None, "review_url": "x"})
    rt.site_facts(rt.site("s"), refresh=True)
    design = mod("packs").design_tokens(rt, rt.site("s"))
    assert design["source"] == "generatepress" and design["overridden"] == []
    assert design["colors"][0] == {"slug": "accent", "name": "Accent", "value": "#1e73be"}
    assert not [r for r in fake.requests if "global-styles" in r["path"]]


def test_a_failed_token_read_keeps_the_overrides_and_says_why(tmp_path):
    fake, rt = setup(tmp_path, design={"logo": "https://test.example/logo.svg"})
    design = mod("packs").design_tokens(rt, rt.site("s"))
    assert design["logo"] == "https://test.example/logo.svg" and design["overridden"] == ["logo"]
    assert "error" in design


def test_bad_design_overrides_are_refused(tmp_path):
    sites_mod = mod("sites")
    for bad in ({"colors": [{"slug": "x", "value": "red; background: url(x)"}]}, {"fonts": {"mono": "x"}},
                {"logo": "javascript:alert(1)"}, {"spacing": {}}):
        try:
            sites_mod.validate({"id": "x", "url": "https://x.example", "design": bad})
            raise AssertionError(f"accepted {bad}")
        except sites_mod.SiteError:
            pass


# --- doctor and overview --------------------------------------------------------------------------------

def test_doctor_lists_packs_with_detected_needs_and_on(tmp_path):
    fake, rt = setup(tmp_path, packs={"generatepress": True})
    rep = mod("tools.doctor").run(rt, "s")["sites"][0]
    gp = rep["packs"]["generatepress"]
    assert gp == {"label": "GeneratePress", "detected": False, "on": True,
                  "needs": "needs GeneratePress (or a child theme of it) as the active theme"}
    assert any(l.startswith("pack GeneratePress: not detected, on; needs") for l in rep["lines"])
    fake.routes[("GET", "/")] = (200, {"Content-Type": "text/html"},
                                 b"<html><head><link href='https://test.example/wp-content/themes/generatepress/x.css'>"
                                 b"<script src='https://test.example/wp-content/plugins/elementor/x.js'></script>"
                                 b"</head></html>")
    rep = mod("tools.doctor").run(rt, "s", refresh=True)["sites"][0]
    assert rep["packs"]["generatepress"] == {"label": "GeneratePress", "detected": True, "on": True, "needs": None}
    assert rep["builders"] == ["elementor"]
    assert "pack GeneratePress: detected, on" in rep["lines"]


def test_overview_rows_carry_builders_and_packs(tmp_path):
    fake, rt = setup(tmp_path)
    status, body = mod("api").overview(rt)
    row = body["sites"][0]
    assert status == 200 and row["builders"] == []
    assert row["packs"]["generatepress"] == {"label": "GeneratePress", "detected": False, "on": False,
                                             "needs": "needs GeneratePress (or a child theme of it) as the active theme"}
    assert set(row["packs"]) == set(mod("packs").ids())
