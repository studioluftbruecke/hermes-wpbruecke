"""The abilities lane (v0.5 PK1): discover, the method from the annotations, default input, WordPress' refusals
in WPBrücke's shape, only declared abilities, the facts cache and the doctor's view.

Recorded answers: tests/fixtures/abilities/ (one full WordPress Playground boot: WordPress 7.1.5,
WooCommerce 11.3.1, FluentCRM 3.3.5; trimmed, the site address replaced, emails masked).
"""
import json
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from pk_testpack import PACK_ID, PW, AbilitiesWP, build_pack, site_with
from wpb_fakes import mod

FIX = Path(__file__).resolve().parent / "fixtures" / "abilities"
LISTING = json.loads((FIX / "listing.json").read_text())
RUNS = json.loads((FIX / "runs.json").read_text())["runs"]


@pytest.fixture(autouse=True)
def installed(monkeypatch):
    monkeypatch.setitem(mod("packs").REGISTRY, PACK_ID, build_pack())


def ab():
    return mod("abilities")


def recorded(name):
    return next(i for i in LISTING["items"] if i["name"] == name)


def client(fake):
    return mod("client").WordPressClient("https://test.example", "hermes", PW, transport=fake)


def answer(case):
    r = RUNS[case]
    return r["status"], r["response"]


# --- discover -----------------------------------------------------------------------------------------

def test_recorded_listing_parses_with_the_methods_wordpress_wants():
    found = {a.name: a for a in ab().parse_listing(LISTING["items"])}
    assert found["woocommerce/products-query"].method == "GET"           # readonly
    assert found["fluent-crm/get-crm-context"].method == "GET"
    assert found["woocommerce/product-create"].method == "POST"          # neither
    assert found["elementor/clear-cache"].method == "DELETE"              # destructive + idempotent (P30)
    delete = found["woocommerce/product-delete"]
    assert delete.destructive and delete.method == ("DELETE" if delete.idempotent else "POST")
    crm = found["fluent-crm/get-crm-context"]
    assert crm.label and crm.category and "include" in crm.input_schema["properties"]
    assert len(LISTING["names"]) == int(LISTING["x_wp_total"]) == 83


def test_listing_is_not_filtered_by_permission_so_every_name_is_seen():
    by_ns = {}
    for row in LISTING["names"]:
        by_ns[row["name"].split("/")[0]] = by_ns.get(row["name"].split("/")[0], 0) + 1
    assert by_ns == {"core": 3, "elementor": 41, "fluent-crm": 28, "woocommerce": 8, "wpbruecke": 3}  # P29


def test_discover_reads_every_page():
    fake = AbilitiesWP(listing=[dict(recorded(n)) for n in ("woocommerce/products-query", "fluent-crm/get-crm-context")]
                       + [{"name": f"x/a{i}", "meta": {"annotations": {"readonly": True}}} for i in range(150)])
    found = ab().discover(client(fake))
    assert len(found) == 152 and [r["query"] for r in fake.requests if "abilities" in r["path"]] == [
        {"per_page": ["100"], "page": ["1"]}, {"per_page": ["100"], "page": ["2"]}]


def test_missing_annotations_count_as_a_write():
    a = ab().Ability.from_dict({"name": "x/y"})
    assert a.method == "POST" and not a.readonly


# --- run: method, query, body, default input ------------------------------------------------------------

def test_readonly_run_is_a_get_with_input_in_the_query():
    fake = AbilitiesWP(listing=LISTING["items"], answers={"woocommerce/products-query": answer("products_query_shop_publisher")})
    a = ab().Ability.from_dict(recorded("woocommerce/products-query"))
    got = ab().run(client(fake), a, {"per_page": 5, "status": ["publish", "draft"], "featured": True})
    assert got.ok and got.method == "GET" and got.sent and got.status == 200
    assert got.result["products"][0]["name"] == "PK1 sample product"
    assert parse_qsl(fake.runs[-1]["query"]) == [("input[per_page]", "5"), ("input[status][0]", "publish"),
                                                 ("input[status][1]", "draft"), ("input[featured]", "true")]
    assert fake.runs[-1]["body"] is None


def test_query_pairs_match_what_the_harness_accepted():
    assert ab().query_pairs({"per_page": 5}) == [tuple(p) for p in RUNS["products_query_shop_publisher"]["query"]]
    assert ab().query_pairs({"include_segment_counts": False}) == \
        [tuple(p) for p in RUNS["crm_context_editor_fcrm_read_emails"]["query"]]


def test_readonly_without_input_gets_the_default_input():
    """P30: FluentCRM's readonly abilities answer 400 \"input is not of type object\" to a GET without input."""
    assert RUNS["crm_context_no_input"]["status"] == 400 and RUNS["list_campaigns_no_input"]["status"] == 400
    fake = AbilitiesWP(listing=LISTING["items"])
    a = ab().Ability.from_dict(recorded("fluent-crm/get-crm-context"))
    ab().run(client(fake), a, None, default_input={"include_segment_counts": False})
    assert parse_qsl(fake.runs[-1]["query"]) == [("input[include_segment_counts]", "false")]
    ab().run(client(fake), a, {"include": ["sender"]}, default_input={"include_segment_counts": False})
    assert parse_qsl(fake.runs[-1]["query"]) == [("input[include][0]", "sender")]   # the call's input wins


def test_write_run_is_a_post_with_the_input_in_the_body_and_no_default():
    fake = AbilitiesWP(listing=LISTING["items"])
    a = ab().Ability.from_dict(recorded("woocommerce/product-create"))
    ab().run(client(fake), a, None, default_input={"name": "never"})
    assert fake.runs[-1]["method"] == "POST" and fake.runs[-1]["body"] == {"input": {}}
    ab().run(client(fake), a, {"name": "Mug"})
    assert fake.runs[-1]["body"] == {"input": {"name": "Mug"}}


def test_destructive_idempotent_run_is_a_delete_with_the_input_in_the_query():
    fake = AbilitiesWP(listing=LISTING["items"])
    a = ab().Ability.from_dict(recorded("elementor/clear-cache"))
    got = ab().run(client(fake), a, {"post_id": 13})
    assert got.method == "DELETE" and fake.runs[-1]["method"] == "DELETE"
    assert fake.runs[-1]["body"] is None


def test_an_ability_without_input_schema_gets_no_input():
    fake = AbilitiesWP()
    ab().run(client(fake), ab().Ability(name="other/thing", readonly=True), None, default_input={"x": 1})
    assert fake.runs[-1]["query"] == ""
    ab().run(client(fake), ab().Ability(name="other/thing"), None)
    assert fake.runs[-1]["body"] == {}


# --- WordPress' refusals, recorded, in WPBrücke's shape -------------------------------------------------

def run_case(case, ability, hint=None, given=None):
    fake = AbilitiesWP(listing=LISTING["items"], answers={ability: answer(case)})
    a = ab().Ability.from_dict(recorded(ability)) if any(i["name"] == ability for i in LISTING["items"]) \
        else ab().Ability(name=ability, readonly=True)
    return ab().run(client(fake), a, given or {"per_page": 5}, permission_hint=hint).to_dict()


def test_cannot_execute_names_the_permission_the_op_declares():
    out = run_case("products_query_editor", "woocommerce/products-query", hint="read_private_products")
    assert out["ok"] is False and out["error"] == "ability_forbidden" and out["wp_code"] == "rest_ability_cannot_execute"
    assert "It needs the permission read_private_products" in out["message"] and "the Hermes user (hermes)" in out["message"]
    assert out["sent"] is True and "Nothing changed" in out["message"]


def test_plugin_permission_refusal_names_the_permissions_wordpress_lists():
    out = run_case("crm_context_author_no_permission", "fluent-crm/get-crm-context", hint="ignored_hint")
    assert out["error"] == "ability_forbidden" and out["status"] == 403
    assert "fcrm_read_emails" in out["permission"] and "ignored_hint" not in out["message"]
    assert "It needs one of the permissions fcrm_view_dashboard, fcrm_read_contacts, fcrm_read_emails" in out["message"]


def test_ability_invalid_permissions_maps_to_forbidden():
    out = ab().refusal_for("fluent-crm/upsert-campaign", "POST", 403,
                           {"code": "ability_invalid_permissions",
                            "message": 'Ability "fluent-crm/upsert-campaign" does not have necessary permission.'},
                           permission_hint="fcrm_manage_emails")
    assert out[0] == "ability_forbidden" and out[3] == "fcrm_manage_emails" and "fcrm_manage_emails" in out[1]


def test_not_found_invalid_input_and_method():
    assert run_case("not_found", "woocommerce/no-such-ability")["error"] == "ability_not_found"
    out = run_case("products_query_invalid_input", "woocommerce/products-query")
    assert out["error"] == "ability_invalid_input" and "not of type integer" in out["message"]
    out = run_case("list_campaigns_no_input", "fluent-crm/list-campaigns")
    assert out["error"] == "ability_invalid_input" and "input is not of type object" in out["message"]
    out = run_case("products_query_post", "woocommerce/products-query")
    assert out["error"] == "ability_method" and "Read-only abilities require GET" in out["message"]


def test_no_abilities_api_at_all():
    fake = AbilitiesWP()
    fake.routes[("GET", "/wp-json/wp-abilities/v1/abilities/x/y/run")] = fake.j(
        404, {"code": "rest_no_route", "message": "No route was found matching the URL and request method."})
    out = ab().run(client(fake), ab().Ability(name="x/y", readonly=True)).to_dict()
    assert out["error"] == "ability_not_found" and "wp-abilities/v1" in out["message"]


def test_unreachable_site_is_not_sent():
    fake = AbilitiesWP()
    fake.down = True
    out = ab().run(client(fake), ab().Ability(name="x/y", readonly=True)).to_dict()
    assert out["error"] == "unreachable" and out["sent"] is False


# --- only declared abilities (D24 a), the facts cache, the doctor ------------------------------------------

def test_undeclared_ability_is_refused_before_any_request(tmp_path):
    fake, rt = site_with(tmp_path)
    before = len(fake.requests)
    got = ab().run_declared(rt, rt.site("s"), "testplug/undeclared", {"id": 1})
    assert got.error == "ability_not_declared" and not got.sent and len(fake.requests) == before
    got = ab().run_declared(rt, rt.site("s"), "woocommerce/product-delete", {"id": 1})
    assert got.error == "ability_not_declared" and len(fake.requests) == before


def test_declared_ability_of_a_pack_that_is_off_is_not_runnable(tmp_path):
    fake, rt = site_with(tmp_path, packs={PACK_ID: False})
    got = ab().run_declared(rt, rt.site("s"), "testplug/items-query")
    assert got.error == "ability_not_declared" and not fake.runs


def test_declared_ability_runs_with_the_listing_from_the_facts(tmp_path):
    fake, rt = site_with(tmp_path)
    got = ab().run_declared(rt, rt.site("s"), "testplug/items-query")
    assert got.ok and fake.runs[-1]["method"] == "GET" and fake.runs[-1]["query"] == "input%5Bper_page%5D=5"
    listings = [r for r in fake.requests if r["path"] == "/wp-json/wp-abilities/v1/abilities"]
    ab().run_declared(rt, rt.site("s"), "testplug/items-query")
    assert len([r for r in fake.requests if r["path"] == "/wp-json/wp-abilities/v1/abilities"]) == len(listings)


def test_declared_but_missing_on_the_site_reads_the_listing_once_then_refuses(tmp_path):
    fake, rt = site_with(tmp_path)
    fake.listing = [a for a in fake.listing if a["name"] != "testplug/item-save"]
    rt.site_facts(rt.site("s"))
    got = ab().run_declared(rt, rt.site("s"), "testplug/item-save", {"title": "x"})
    assert got.error == "ability_not_found" and not got.sent and not fake.runs


def test_facts_keep_the_listing_out_of_the_page(tmp_path):
    fake, rt = site_with(tmp_path)
    facts = rt.site_facts(rt.site("s"), refresh=True)
    assert facts["abilities"] == {"count": 7, "namespaces": {"other": 1, "testplug": 6}, "error": None}
    assert {a["name"] for a in facts["_abilities"]} >= {"testplug/items-query", "other/thing"}
    status, detail = mod("api").site_detail(rt, "s")
    assert status == 200 and "_abilities" not in detail["facts"] and detail["facts"]["abilities"]["count"] == 7


def test_facts_without_the_namespace_read_no_listing(tmp_path):
    fake, rt = site_with(tmp_path)
    fake.namespaces = ["wp/v2"]
    facts = rt.site_facts(rt.site("s"), refresh=True)
    assert "abilities" not in facts and not [r for r in fake.requests if "wp-abilities" in r["path"]]


def test_failed_listing_is_reported_not_raised(tmp_path):
    fake, rt = site_with(tmp_path)
    fake.routes[("GET", "/wp-json/wp-abilities/v1/abilities")] = fake.j(500, {"code": "x", "message": "boom"})
    facts = rt.site_facts(rt.site("s"), refresh=True)
    assert facts["abilities"]["count"] is None and "boom" in facts["abilities"]["error"]["message"]
    assert "_abilities" not in facts


def test_doctor_lists_enabled_and_seen_not_enabled(tmp_path):
    fake, rt = site_with(tmp_path)
    report = mod("tools.doctor").run(rt, "s", True)["sites"][0]
    view = report["abilities"]
    assert [e["ability"] for e in view["enabled"]] == ["testplug/item-publish", "testplug/item-save",
                                                       "testplug/items-query", "testplug/purge", "testplug/send"]
    assert view["seen_not_enabled"] == ["other/thing", "testplug/undeclared"]
    line = next(l for l in report["lines"] if l.startswith("abilities:"))
    assert "7 seen" in line and "2 seen, not enabled" in line


def test_doctor_with_the_pack_off_enables_nothing(tmp_path):
    fake, rt = site_with(tmp_path, packs={PACK_ID: False})
    view = mod("tools.doctor").run(rt, "s", True)["sites"][0]["abilities"]
    assert view["enabled"] == [] and len(view["seen_not_enabled"]) == 7
    assert view["declared_by_packs_off"]["testplug/items-query"] == [PACK_ID]


def test_doctor_without_the_abilities_api(tmp_path):
    fake, rt = site_with(tmp_path)
    fake.namespaces = ["wp/v2"]
    view = mod("tools.doctor").run(rt, "s", True)["sites"][0]["abilities"]
    assert view["count"] is None and "WordPress 6.9" in view["error"]


def test_opspec_shape_is_checked_at_import():
    op = mod("packs").OpSpec
    with pytest.raises(ValueError):
        op(level="read")                                    # neither ability nor route
    with pytest.raises(ValueError):
        op(ability="a/b", route="x")
    with pytest.raises(ValueError):
        op(ability="a/b", gate="sometimes")
    with pytest.raises(ValueError):
        op(ability="a/b", level="admin")
    with pytest.raises(ValueError):
        op(ability="a/b", method="GET")                      # method is for routes only


def test_fixtures_hold_no_secret_or_host():
    text = (FIX / "listing.json").read_text() + (FIX / "runs.json").read_text()
    assert ":9400" not in text and "Authorization" not in text and "@example.test" not in text.replace(
        "someone@example.test", "")
