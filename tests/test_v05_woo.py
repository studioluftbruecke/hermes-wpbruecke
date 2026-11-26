"""The WooCommerce pack (v0.5 PK3): shop roles in the access model, the connector's guard and route (PHP
smoke), and the pack's ops through wp_pack (the plugin's own gate and handler) against a fake WooCommerce.

The fake answers like the harness measured (P41, ``runs/v05-pk0.json``): WooCommerce's own create asks
``publish_products``, the connector's route saves drafts only and refuses other users' and published
products, ``product-update`` refuses a publish without ``publish_products``.
"""
from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import parse_qsl

import pytest

from pk_testpack import PW, AbilitiesWP
from wpb_fakes import ROLE_CAPS, ROOT, FakeWP, Settings, create_paired, load_plugin, mod, php_command as _php, secrets

PACK = "woocommerce"
LISTING_FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "abilities" / "listing.json").read_text())
UPDATE_FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "woo" / "product-update.json").read_text())
LISTING = [a for a in LISTING_FIXTURE["items"] if a["name"].startswith("woocommerce/")] + UPDATE_FIXTURE["items"] + [
    {"name": "woocommerce/orders-query", "label": "Query orders", "category": "woocommerce", "description": "",
     "input_schema": {"type": "object", "properties": {"per_page": {"type": "integer"}}}, "output_schema": {},
     "meta": {"annotations": {"destructive": False, "idempotent": True, "readonly": True}, "show_in_rest": True}},
]


def _caps(base: str, *extra: str, role: str) -> dict:
    caps = {k: v for k, v in ROLE_CAPS[base].items() if k != base}
    return {**caps, **{c: True for c in extra}, role: True}


# What users/me?context=edit lists for the connector's shop roles (P41 whoami) and WooCommerce's Shop Manager.
SHOP_DRAFTER = _caps("contributor", "upload_files", "edit_products", "read_private_products", "assign_product_terms",
                     role="wpbruecke_shop_drafter")
SHOP_PUBLISHER = _caps("author", "edit_products", "edit_published_products", "read_private_products",
                       "assign_product_terms", "publish_products", role="wpbruecke_shop_publisher")
SHOP_MANAGER = {**_caps("editor", role="shop_manager"), "edit_theme_options": True, "list_users": True,
                "manage_woocommerce": True, "edit_products": True, "publish_products": True,
                "edit_others_products": True}
ROLES = {"wpbruecke_shop_drafter": SHOP_DRAFTER, "wpbruecke_shop_publisher": SHOP_PUBLISHER}
PALETTE = [{"slug": "base", "name": "Base", "color": "#FFFFFF"}, {"slug": "accent", "name": "Accent", "color": "#b5442c"}]


class ShopWP(AbilitiesWP):
    """FakeWP with WooCommerce's product abilities, wp/v2/product, the connector's shop and preview routes."""

    def __init__(self, role: str, connector: str | None = "0.5.0"):
        ns = ["oembed/1.0", "wp/v2", "wp-abilities/v1", "wc/v3"] + (["wpbruecke/v1"] if connector else [])
        FakeWP.__init__(self, user="hermes", caps=dict(ROLES[role]), roles=[role], password=PW, namespaces=ns)
        self.listing, self.answers, self.runs = list(LISTING), {}, []
        self.connector = connector
        self.products: dict = {}
        self.route_bodies: list = []

    def product(self, status="draft", author=None, **fields) -> dict:
        self.next_id += 1
        row = {"id": self.next_id, "name": fields.pop("name", f"Product {self.next_id}"), "status": status,
               "regular_price": fields.pop("regular_price", ""), "sale_price": "", "sku": "", "stock_status": "instock",
               "author": self.user if author is None else author, "description": fields.pop("description", ""),
               "short_description": "", "image_id": 0, "category_ids": [], "manage_stock": False,
               "stock_quantity": None, **fields}
        self.products[row["id"]] = row
        return row

    def _public(self, row: dict) -> dict:
        link = f"https://{self.host}/product/p{row['id']}/" if row["status"] == "publish" else \
            f"https://{self.host}/?post_type=product&p={row['id']}"
        out = {k: row[k] for k in ("id", "name", "status", "regular_price", "sale_price", "sku", "stock_status",
                                   "manage_stock", "stock_quantity")}
        return {**out, "permalink": link, "type": "simple", "virtual": False, "downloadable": False}

    def _may_edit(self, row: dict) -> bool:
        if row["author"] != self.user:
            return False
        return row["status"] == "draft" or bool(self.caps.get("edit_published_products"))

    def default(self, method, parsed, headers, body):
        p, authed = parsed.path, self.authed(headers)
        data = json.loads(body.decode()) if body else {}
        if p == "/wp-json/wpbruecke/v1/info" and authed and self.connector:
            return self.j(200, {"version": self.connector, "role": "wpbruecke_drafter", "preview": True})
        if p == "/wp-json/wp/v2/global-styles/themes/twentytwentyfive" and authed:
            return self.j(200, {"settings": {"color": {"palette": {"theme": PALETTE}}, "typography": {"fontFamilies": {
                "theme": [{"slug": "fraunces", "name": "Fraunces", "fontFamily": "Fraunces, serif"}]}}},
                "styles": {"elements": {"heading": {"typography": {"fontFamily": "var:preset|font-family|fraunces"}}}}})
        if p == "/wp-json/wpbruecke/v1/shop/product" and method == "POST" and authed and self.connector == "0.5.0":
            return self.shop_route(data)
        if p == "/wp-json/wpbruecke/v1/preview-link" and method == "POST" and authed and self.connector:
            row = self.products.get(int(data.get("post_id") or 0))
            if not row or row["status"] == "publish":
                return self.j(409, {"code": "wpbruecke_not_previewable", "message": "Preview links are only for drafts."})
            return self.j(200, {"url": f"https://{self.host}/?p={row['id']}&post_type=product&wpb_preview={'a' * 64}"
                                       f"&wpb_exp=9", "expires_at": "2026-11-26T12:00:00+00:00"})
        m = re.match(r"^/wp-json/wp/v2/product/(\d+)$", p)
        if m and method == "GET" and authed:
            row = self.products.get(int(m.group(1)))
            if not row or not self._may_edit(row) and row["status"] != "publish":
                return self.j(404, {"code": "rest_post_invalid_id", "message": "Invalid post ID."})
            pub = self._public(row)
            return self.j(200, {"id": row["id"], "status": row["status"], "type": "product", "link": pub["permalink"],
                                "title": {"raw": row["name"]}, "content": {"raw": row["description"]},
                                "excerpt": {"raw": row["short_description"]}})
        if p.endswith("/run") and p.startswith("/wp-json/wp-abilities/v1/abilities/woocommerce/"):
            name = p[len("/wp-json/wp-abilities/v1/abilities/"):-len("/run")]
            q = {k: v for k, v in parse_qsl(parsed.query)}
            self.runs.append({"name": name, "method": method, "query": parsed.query, "body": data or None})
            given = data.get("input") or {k[6:-1]: v for k, v in q.items() if k.startswith("input[")}
            return self.ability(name, given)
        return super().default(method, parsed, headers, body)

    def shop_route(self, data: dict):
        """The connector's POST shop/product, as includes/shop.php answers."""
        self.route_bodies.append(data)
        if not self.caps.get("edit_products"):
            return self.j(403, {"code": "rest_forbidden", "message": "Sorry, you are not allowed to do that."})
        pid = data.get("id")
        if pid:
            row = self.products.get(int(pid))
            if not row or row["author"] != self.user or (row["status"] != "draft" and not self.caps.get(
                    "edit_published_products")):
                return self.j(403, {"code": "wpbruecke_cannot_edit_product", "message": "This user may not edit that "
                                                                                        "product."})
            if row["status"] != "draft":
                return self.j(403, {"code": "wpbruecke_not_draft", "message": "Only draft products are edited here."})
        else:
            if not data.get("name"):
                return self.j(400, {"code": "wpbruecke_bad_input", "message": "A new product needs a name."})
            row = self.product("draft")
        self.name_before = row["name"]
        for k in ("name", "description", "short_description", "regular_price", "sale_price", "image_id",
                  "category_ids"):
            if k in data:
                row[k] = data[k]
        if len(str(data.get("name") or "").encode()) > NAME_MAX_BYTES:   # not saved, the old name stays (review row 11)
            row["name"] = self.name_before
        if len(str(data.get("description") or "")) > DESCRIPTION_MAX:     # stored cut short
            row["description"] = data["description"][:DESCRIPTION_MAX]
        row["status"] = "draft"
        return self.j(200 if pid else 201, {"id": row["id"], "created": not pid, "status": "draft", "name": row["name"],
                                            "regular_price": row["regular_price"], "sale_price": row["sale_price"],
                                            "category_ids": row["category_ids"], "image_id": row["image_id"],
                                            "has_description": bool(row["description"])})

    def ability(self, name: str, given: dict):
        forbidden = self.j(403, {"code": "rest_ability_cannot_execute", "message": "Sorry, you are not allowed to "
                                                                                    "execute this ability."})
        if name == "woocommerce/products-query":
            if not self.caps.get("read_private_products"):
                return forbidden
            rows = [r for r in self.products.values() if not given.get("id") or r["id"] == int(given["id"])]
            return self.j(200, {"page": 1, "per_page": 10, "products": [self._public(r) for r in rows],
                                "total_pages": 1})
        if name == "woocommerce/product-create":
            if not self.caps.get("publish_products"):
                return forbidden
            row = self.product(given.get("status") or "publish", **{k: v for k, v in given.items() if k != "status"})
            return self.j(200, {"product": self._public(row)})
        if name == "woocommerce/product-update":
            row = self.products.get(int(given.get("id") or 0))
            if not row or not self._may_edit(row):
                return forbidden
            if given.get("status") in ("publish", "future", "private") and not self.caps.get("publish_products"):
                return self.j(403, {"code": "woocommerce_product_publish_forbidden", "message": "no"})
            if "regular_price" in given and not given.get("product_type_alias"):   # the schema's oneOf (measured)
                return self.j(400, {"code": "ability_invalid_input", "message": "product_type_alias is a required "
                                                                                "property of input."})
            given = {k: v for k, v in given.items() if k != "product_type_alias"}
            row.update({k: v for k, v in given.items() if k != "id"})
            if not row["manage_stock"]:     # WooCommerce keeps no stock quantity without stock management (review row 11)
                row["stock_quantity"] = None
            if row["sale_price"] and row["regular_price"] and float(row["sale_price"]) >= float(row["regular_price"]):
                row["sale_price"] = ""      # a sale price at or above the regular price is not kept
            return self.j(200, {"product": self._public(row)})
        return self.j(403, {"code": "rest_ability_cannot_execute", "message": "Sorry."})


NAME_MAX_BYTES = 65535      # names past this were not saved on the harness (review row 11: a 300 KB name)
DESCRIPTION_MAX = 5000      # a fake site that cuts long descriptions


def shop_site(tmp_path, role="wpbruecke_shop_drafter", **kw):
    fake = ShopWP(role, **kw)
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1=PW), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1,
                       "packs": {PACK: True}}, "person")
    return fake, rt


class Turn:
    """The gate as Hermes calls it, then the handler, sharing one ticket store."""

    def __init__(self, rt, run=None):
        self.rt, self.run = rt, run or mod("lanes").RunContext()
        self.tickets = mod("hooks").Tickets()
        plugin = load_plugin()
        self.gate = mod("hooks").make_gate({"wp_pack": plugin.pack_spec(lambda: rt)}, lambda: self.run, lambda: True,
                                           self.tickets)
        self.handler = plugin.make_pack_handler(rt.get_setting, rt.store.root, lambda: self.run,
                                                secrets(WPBRUECKE_KEY1=PW), tickets=self.tickets,
                                                transport=rt.transport, wporg_fetch=lambda u: {"version": "1"})

    def call(self, op, **input_):
        args = {"site": "s", "pack": PACK, "action": "run", "op": op, "input": input_}
        verdict = self.gate(tool_name="wp_pack", args=args, session_id="sess")
        if verdict and verdict["action"] == "block":
            return verdict, None
        return verdict, json.loads(self.handler(args, session_id="sess"))


def log_lines(rt):
    return [line for line in mod("activity").ActivityLog(rt.store).read("s") if line["tool"] == "wp_pack"]


# --- roles in the access model ------------------------------------------------------------------------

def classify(caps):
    access = mod("access")
    roles = [k for k in caps if k.startswith("wpbruecke_") or k in ROLE_CAPS or k == "shop_manager"]
    return access.classify(roles, caps, "0.5.0")


def test_the_shop_drafter_is_drafts_and_the_shop_publisher_is_publish():
    access = mod("access")
    level, why = classify(SHOP_DRAFTER)
    assert level == "drafts" and why == "the user can write drafts with images and draft products"
    assert access.level_words("drafts", SHOP_DRAFTER) == "write drafts with images and draft products"
    level, why = classify(SHOP_PUBLISHER)
    assert level == "publish" and why == "the user can publish its own posts and products"
    assert access.level_words("publish", SHOP_PUBLISHER) == "write and publish its own posts and products"


def test_publish_products_alone_is_a_publish_capability():
    access = mod("access")
    caps = {**ROLE_CAPS["wpbruecke_drafter"], "edit_products": True, "publish_products": True}
    assert classify(caps) == ("publish", "the user can publish products")
    assert access.level_words("publish", caps) == "write and publish products"
    # a site confirmed at Drafts whose role gains publish_products is stopped (wider), like publish_posts
    drafts = {"level": "drafts", "caps_fingerprint": access.caps_fingerprint(SHOP_DRAFTER)}
    assert access.effective(drafts, classify({**SHOP_DRAFTER, "publish_products": True})[0],
                            access.caps_fingerprint({**SHOP_DRAFTER, "publish_products": True}))[0] == "stopped"


def test_shop_manager_stays_refused():
    assert classify(SHOP_MANAGER)[0] == "refused"


@pytest.mark.parametrize("role,level", [("subscriber", "read_public"), ("wpbruecke_reader", "read"),
                                        ("contributor", "drafts"), ("wpbruecke_drafter", "drafts"),
                                        ("author", "publish"), ("editor", "publish"), ("administrator", "refused")])
def test_existing_roles_classify_as_before(role, level):
    access = mod("access")
    got, why = access.classify([role], ROLE_CAPS[role], "0.5.0")
    assert got == level
    if role in ("author", "editor"):
        assert why == {"author": "the user can publish its own posts", "editor": "the user can publish all posts "
                                                                                  "and pages"}[role]
        assert access.level_words("publish", ROLE_CAPS[role]) == {
            "author": "write and publish its own posts", "editor": "write and publish all posts and pages"}[role]
    assert access.level_words("publish") == "write and publish its own posts"


def test_fingerprints_keep_d42():
    access = mod("access")
    editor = dict(ROLE_CAPS["editor"])
    assert access.caps_fingerprint({**editor, "elementor_global_classes_edit": True}, ["editor"]) == \
        access.caps_fingerprint(editor, ["editor"])
    assert access.caps_fingerprint({**SHOP_DRAFTER, "publish_products": True}) != access.caps_fingerprint(SHOP_DRAFTER)


# --- the connector's shop.php: roles, guard, route (PHP smoke over stub WordPress) --------------------

SMOKE = ROOT.parents[1] / "experiments" / "wpbruecke" / "connector" / "php_smoke_shop.php"
CONNECTOR = ROOT / "connector" / "wpbruecke-connector"


@pytest.mark.skipif(_php() is None or not SMOKE.exists(),
                    reason="needs PHP 8 (WPB_PHP or php on PATH) and the development repository's smoke script")
def test_shop_php_roles_guard_and_route_in_the_php_smoke():
    out = subprocess.run([*_php(), "-n", "-d", "error_reporting=-1", str(SMOKE), str(CONNECTOR)],
                         capture_output=True, text=True, timeout=60)
    lines = out.stdout.splitlines()
    assert out.returncode == 0 and "FAIL" not in out.stdout and "Warning" not in out.stdout, out.stdout[-3000:]
    for must in ("guard refuses a drafter's change to publish", "guard refuses a drafter's change to future",
                 "guard refuses a drafter's change to private", "guard leaves a drafter's draft edit alone",
                 "guard leaves a publisher alone", "create: 201, a draft whatever the request asked",
                 "another user's draft refused 403 (no edit_product on it)",
                 "a published product refused 403 wpbruecke_not_draft, nothing saved",
                 "permission callback refuses a user without edit_products (the reader role)",
                 "drafter never holds publish_products, edit_published_products, publish_posts or other users' products"):
        assert f"ok   {must}" in lines, must


def test_the_connector_loads_shop_php_and_the_reader_guard_covers_the_route():
    main = (CONNECTOR / "wpbruecke-connector.php").read_text()
    assert "require_once __DIR__ . '/includes/shop.php';" in main
    reader = (CONNECTOR / "includes" / "reader.php").read_text()
    # every reader POST but preview-link is refused before any route runs; shop/product is not an exception
    assert "shop" not in reader
    shop = (CONNECTOR / "includes" / "shop.php").read_text()
    assert "add_action( 'woocommerce_before_product_object_save', 'wpbruecke_shop_publish_guard'" in shop
    assert "register_deactivation_hook( WPBRUECKE_CONNECTOR_FILE, 'wpbruecke_shop_deactivate' )" in shop


# --- the pack through wp_pack ---------------------------------------------------------------------------

def test_the_pack_is_a_plugin_pack_with_no_order_op():
    spec = mod("packs").get(PACK)
    assert spec.kind == "plugin" and spec.requires["connector"] == "0.5.0" and "wc/v3" in spec.requires["namespaces"]
    assert set(spec.ops) == {"products-query", "draft-product", "create-product", "update-product", "prove-product"}
    assert {n: o.level for n, o in spec.ops.items()} == {
        "products-query": "read", "draft-product": "drafts", "create-product": "publish", "update-product": "publish",
        "prove-product": "read"}
    assert not any("order" in (o.ability or o.route or "") or "delete" in (o.ability or "") for o in spec.ops.values())
    assert "never reads or changes orders" in spec.scope


def test_products_query_reads_without_a_card_and_logs_nothing(tmp_path):
    fake, rt = shop_site(tmp_path)
    fake.product("publish", name="Mug")
    verdict, out = Turn(rt).call("products-query")
    assert verdict is None and out["ok"] and [p["name"] for p in out["result"]["products"]] == ["Mug"]
    assert fake.runs[-1]["method"] == "GET" and "input%5Bper_page%5D=10" in fake.runs[-1]["query"]
    assert log_lines(rt) == []


def test_orders_stay_out_of_reach(tmp_path):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    verdict, _ = Turn(rt).call("orders-query")
    assert verdict["action"] == "block" and "has no op 'orders-query'" in verdict["message"]
    got = mod("abilities").run_declared(rt, rt.site("s"), "woocommerce/orders-query", {"per_page": 1})
    assert got.error == "ability_not_declared" and not got.sent and not fake.runs


def test_draft_product_lays_out_the_body_in_the_site_design_and_logs_one_line(tmp_path):
    fake, rt = shop_site(tmp_path)
    verdict, out = Turn(rt).call("draft-product", name="Linen apron", regular_price="29.90", image_id=40,
                                 body={"heading": "Made to last", "lead": "Washed linen <b>& cotton</b>.",
                                       "points": ["Two pockets", "Adjustable strap"], "closing": "Ships in a week."})
    assert verdict is None and out["ok"], out
    sent = fake.route_bodies[-1]
    assert set(sent) == {"name", "regular_price", "image_id", "description"}
    assert sent["description"].startswith('<h3 style="color:#b5442c;font-family:Fraunces, serif">Made to last</h3>')
    assert "Washed linen &lt;b&gt;&amp; cotton&lt;/b&gt;." in sent["description"]
    assert "<ul><li>Two pockets</li><li>Adjustable strap</li></ul>" in sent["description"]
    result = out["result"]
    back = result["read_back"]
    assert result["status"] == "draft" and back["draft"] is True and back["status"] == "draft"
    assert sorted(back["as_sent"]) == ["description", "image_id", "name", "regular_price"] and back["not_as_sent"] == []
    assert back["dropped"] == [] and back["truncated"] == [] and "note" not in back
    assert result["design"]["color"] == {"slug": "accent", "value": "#b5442c"} and result["design"]["source"] == "theme.json"
    lines = log_lines(rt)
    assert len(lines) == 1
    assert (lines[0]["action"], lines[0]["label"], lines[0]["result"], lines[0]["wp_id"]) == (
        "draft-product", "Linen apron", "ok", result["id"])
    assert "Washed linen" not in json.dumps(lines)   # bodies never reach the log


def test_draft_product_edits_a_draft_and_the_label_comes_from_the_product(tmp_path):
    fake, rt = shop_site(tmp_path)
    row = fake.product("draft", name="Old name")
    verdict, out = Turn(rt).call("draft-product", id=row["id"], regular_price="31")
    assert out["ok"] and fake.route_bodies[-1] == {"id": row["id"], "regular_price": "31"}
    assert log_lines(rt)[-1]["label"] == "Old name"


@pytest.mark.parametrize("make,code,words", [
    (lambda f: f.product("publish"), "wpbruecke_cannot_edit_product", "does not let this site's Hermes user edit"),
    (lambda f: f.product("draft", author="someone"), "wpbruecke_cannot_edit_product", "another user's product"),
])
def test_draft_product_route_refusals_are_logged_as_refused(tmp_path, make, code, words):
    fake, rt = shop_site(tmp_path)
    row = make(fake)
    _, out = Turn(rt).call("draft-product", id=row["id"], name="x")
    assert out["ok"] is False and out["error"] == "route_forbidden" and out["wp_code"] == code and words in out["message"]
    assert fake.products[row["id"]]["name"] != "x"
    assert log_lines(rt)[-1]["result"] == "refused"


def test_the_route_refuses_a_live_product_for_the_publisher_with_the_publish_op_named(tmp_path):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    row = fake.product("publish")
    _, out = Turn(rt).call("draft-product", id=row["id"], name="x")
    assert out["wp_code"] == "wpbruecke_not_draft" and "update-product at the Publish level" in out["message"]


def test_draft_product_without_the_connector_route(tmp_path):
    fake, rt = shop_site(tmp_path, connector=None)
    _, out = Turn(rt).call("draft-product", name="x")
    assert out["error"] == "route_not_found" and "has no connector" in out["message"] and not fake.route_bodies
    fake, rt = shop_site(tmp_path / "old", connector="0.4.0")
    _, out = Turn(rt).call("draft-product", name="x")
    assert out["error"] == "route_not_found" and "connector (0.4.0) has no shop route" in out["message"]
    assert "0.5.0 or newer" in out["message"]


def test_draft_product_refuses_bad_input_before_any_request(tmp_path):
    fake, rt = shop_site(tmp_path)
    verdict, _ = Turn(rt).call("draft-product", name="x", status="publish")
    assert verdict["action"] == "block" and "does not take status" in verdict["message"]
    _, out = Turn(rt).call("draft-product", name="x", description="<p>x</p>", body={"lead": "y"})
    assert out["error"] == "route_invalid_input" and "not both" in out["message"]
    _, out = Turn(rt).call("draft-product", name="x", body={"lead": "y", "script": "z"})
    assert out["error"] == "route_invalid_input" and "does not take script" in out["message"]
    assert not fake.route_bodies


@pytest.mark.parametrize("op,input_", [("create-product", {"name": "x", "status": "publish"}),
                                       ("update-product", {"id": 1, "status": "publish"})])
def test_publish_ops_are_refused_at_the_drafts_level_before_any_request(tmp_path, op, input_):
    fake, rt = shop_site(tmp_path)
    verdict, out = Turn(rt).call(op, **input_)
    assert verdict["action"] == "block" and "refused (level_drafts)" in verdict["message"]
    assert "shop publisher role" in verdict["message"] and out is None and not fake.runs
    assert log_lines(rt)[-1]["code"] == "level_drafts" and log_lines(rt)[-1]["result"] == "refused"


def test_create_product_publishes_and_reads_back_publish(tmp_path):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    verdict, out = Turn(rt).call("create-product", name="Wool scarf", regular_price="45.00", status="publish",
                                 body={"lead": "Warm."})
    assert verdict is None and out["ok"], out
    create = next(r for r in fake.runs if r["name"] == "woocommerce/product-create")
    assert create["method"] == "POST" and create["body"]["input"]["description"] == "<p>Warm.</p>"
    back = out["result"]["read_back"]
    assert out["result"]["status"] == "publish" and back["ok"] and back["status"] == "publish"
    assert set(back["as_sent"]) == {"name", "regular_price", "status", "description"} and back["not_as_sent"] == []
    assert fake.runs[-1]["name"] == "woocommerce/products-query"
    line = log_lines(rt)[-1]
    assert (line["action"], line["label"], line["result"], line["wp_id"]) == ("create-product", "Wool scarf", "ok",
                                                                             out["result"]["id"])


# --- read-back honesty (review row 11): a field the site did not keep is never a silent ok ----------------------

def test_update_product_reports_a_stock_quantity_woocommerce_dropped(tmp_path):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    row = fake.product("publish", name="Scarf", regular_price="20.00")
    for _ in range(2):          # the review saw it twice
        _, out = Turn(rt).call("update-product", id=row["id"], manage_stock=False, stock_quantity=7)
        assert out["ok"], out
        back = out["result"]["read_back"]
        assert back["as_sent"] == ["manage_stock"] and back["not_as_sent"] == ["stock_quantity"]
        assert back["dropped"] == ["stock_quantity"] and back["stored"] == {"stock_quantity": None}
        assert "stock_quantity (not saved)" in back["note"] and "manage_stock true" in back["note"]
    _, out = Turn(rt).call("update-product", id=row["id"], manage_stock=True, stock_quantity=7)
    back = out["result"]["read_back"]
    assert sorted(back["as_sent"]) == ["manage_stock", "stock_quantity"] and back["dropped"] == []
    assert back["changed"] == ["manage_stock", "stock_quantity"]


def test_update_product_reports_a_sale_price_above_the_regular_one_as_dropped(tmp_path):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    row = fake.product("publish", name="Scarf", regular_price="20.00")
    _, out = Turn(rt).call("update-product", id=row["id"], sale_price="25.00")
    back = out["result"]["read_back"]
    assert out["ok"] and back["not_as_sent"] == ["sale_price"] and back["dropped"] == ["sale_price"]
    assert back["changed"] == []


def test_draft_product_reports_a_name_that_was_not_saved_and_a_description_cut_short(tmp_path):
    fake, rt = shop_site(tmp_path)
    row = fake.product("draft", name="Big apron")
    for _ in range(2):
        _, out = Turn(rt).call("draft-product", id=row["id"], name="N" * 300_000, description="<p>" + "d" * 9000 + "</p>")
        assert out["ok"], out
        back = out["result"]["read_back"]
        assert back["draft"] is True and back["not_as_sent"] == ["description", "name"]
        assert back["dropped"] == ["name"] and back["truncated"] == ["description"]
        assert back["stored"]["name"] == "Big apron" and len(back["stored"]["description"]) == 200
        assert "name (not saved)" in back["note"] and "description (cut short)" in back["note"]
    assert fake.products[row["id"]]["name"] == "Big apron"


def test_update_product_changes_a_live_product_and_reports_what_changed(tmp_path):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    row = fake.product("publish", name="Mug", regular_price="12.00")
    _, out = Turn(rt).call("update-product", id=row["id"], regular_price="14.00")
    assert out["ok"], out
    back = out["result"]["read_back"]
    assert back["changed"] == ["regular_price"] and back["status"] == "publish" and back["as_sent"] == ["regular_price"]
    update = next(r for r in fake.runs if r["name"] == "woocommerce/product-update")
    assert update["body"]["input"] == {"id": row["id"], "regular_price": "14.00", "product_type_alias": "physical"}
    assert log_lines(rt)[-1]["label"] == "Mug"   # no name in the input: the label is the product's


def test_prove_product_renders_a_draft_through_the_preview_link_and_a_live_one_at_its_permalink(tmp_path, monkeypatch):
    fake, rt = shop_site(tmp_path, role="wpbruecke_shop_publisher")
    seen = []

    def fake_run_proof(**kw):
        seen.append(kw)
        return {"id": f"proof-{len(seen)}", "verdict": "match", "target": kw["target"], "checks": [],
                "shots": [{"width": 1440, "file": "w1440.png"}], "seconds": 1}

    monkeypatch.setattr(mod("proof.checks"), "run_proof", fake_run_proof)
    draft, live = fake.product("draft"), fake.product("publish")
    verdict, out = Turn(rt).call("prove-product", id=draft["id"])
    assert verdict is None and out["ok"] and out["result"]["through"] == "signed preview link"
    assert "wpb_preview=" in seen[0]["url"] and seen[0]["widths"] == [1440, 768, 390]
    assert "wpb_preview" not in json.dumps(out)   # the signed link never reaches the result
    _, out = Turn(rt).call("prove-product", id=live["id"])
    assert out["result"]["through"] == "permalink" and seen[1]["url"] == f"https://test.example/product/p{live['id']}/"
    assert log_lines(rt) == []   # a proof writes nothing


def test_pack_ops_are_refused_unattended(tmp_path):
    fake, rt = shop_site(tmp_path)
    verdict, _ = Turn(rt, mod("lanes").RunContext(cron=True)).call("draft-product", name="x")
    assert verdict["action"] == "block" and "(unattended)" in verdict["message"] and not fake.route_bodies


def test_design_without_tokens_is_plain_and_still_escaped():
    design = mod("packs.woocommerce.design")
    text, used = design.render({"heading": "A <i>b</i>", "points": ["c"]}, {"colors": [], "fonts": {}})
    assert text == "<h3>A &lt;i&gt;b&lt;/i&gt;</h3>\n<ul><li>c</li></ul>" and used["color"] is None
    bad = {"colors": [{"slug": "accent", "value": "red;background:url(x)"}], "fonts": {"headings": "x;}</style>"}}
    text, used = design.render({"heading": "h"}, bad)
    assert text == "<h3>h</h3>" and used["color"] is None and used["heading_font"] is None


def test_the_heading_colour_must_read_on_the_page_background():
    design = mod("packs.woocommerce.design")
    # Twenty Twenty-Five's palette (the harness theme): accent-1 is yellow on white, accent-3 a dark violet
    tt5 = {"colors": [{"slug": "base", "value": "#FFFFFF"}, {"slug": "contrast", "value": "#111111"},
                      {"slug": "accent-1", "value": "#FFEE58"}, {"slug": "accent-2", "value": "#F6CFF4"},
                      {"slug": "accent-3", "value": "#503AA8"}], "fonts": {}}
    assert design.accent(tt5)["slug"] == "accent-3" and design.contrast("#FFEE58", "#FFFFFF") < 4.5
    light = {"colors": [{"slug": "base", "value": "#FFFFFF"}, {"slug": "accent", "value": "#FFEE58"}], "fonts": {}}
    assert design.accent(light) is None
    dark = {"colors": [{"slug": "base", "value": "#111111"}, {"slug": "accent", "value": "#FFEE58"}], "fonts": {}}
    assert design.accent(dark)["slug"] == "accent"
