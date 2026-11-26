"""Opt-in: the WooCommerce pack end to end on the full harness (WordPress, WooCommerce, this connector).

Drives the plugin's own handlers as two users with the connector's shop roles (made by the live stub
``live/stubs/wpb-pk3-stub``, the seeded ``hermes_shop`` is WooCommerce's Shop Manager, which the access
model refuses): the shop drafter lists products, uploads an image, writes a draft product whose
description is laid out in the site's design, proves it through the signed preview link, and is refused a
publish by WPBrücke (``level_drafts``) and by the connector's guard on WooCommerce's own REST API (the
product stays a draft); the shop publisher creates a live product and reads back ``publish``, changes it
and proves it at its permalink. Deactivating the connector removes both roles; activating brings them back.

With ``WPB_EVIDENCE_OUT=<file.json>`` the run also writes its evidence (codes, statuses, no secrets) and a
WebP of the draft's 1440 px proof next to it. Skipped unless ``WPB_HARNESS=1`` (``harness_support.py``).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import harness_support as hs
from wpb_fakes import load_plugin, mod

STUB = hs.HARNESS.parent / "live" / "stubs" / "wpb-pk3-stub"
STUB_GUEST = "/wordpress/wp-content/plugins/wpb-pk3-stub"
CONNECTOR = "wpbruecke-connector/wpbruecke-connector"
OUT = os.environ.get("WPB_EVIDENCE_OUT")
STAMP = time.strftime("%H%M%S")

pytestmark = pytest.mark.skipif(hs.skip_reason(STUB) is not None, reason=hs.skip_reason(STUB) or "")


@pytest.fixture(scope="module")
def evidence():
    data: dict = {"boxes": {}}
    yield data
    if OUT:
        Path(OUT).write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module")
def harness(tmp_path_factory, evidence):
    for h in hs.boot("full", tmp_path_factory.mktemp("harness-woo"), extra_mounts=((STUB, STUB_GUEST),)):
        evidence["booted"] = {k: h.plugins.get(k) for k in ("woocommerce", "wpbruecke-connector", "elementor")}
        evidence["booted"].update({k: (h.ready.get("booted") or {}).get(k) for k in ("wp", "php")})
        yield h


@pytest.fixture(scope="module")
def admin(harness):
    return harness.user("hermes_admin")


@pytest.fixture(scope="module")
def setup(admin, evidence):
    status, got = hs.rest(*admin, "POST", "/wpb-pk3/v1/setup")
    assert status == 200, got
    evidence["boxes"]["setup"] = {"theme": got["theme"], "category_id": got["category_id"],
                                  "roles": {k: sorted(v or []) for k, v in got["roles"].items()}}
    return got


class ShopAgent(hs.Agent):
    """hs.Agent with the WooCommerce pack switched on and wp_pack's handler (attended chat run)."""

    def __init__(self, login: str, pw: str):
        super().__init__(login, pw, packs=False)
        self.pw = pw
        self.rt().sites.update(self.site, {"packs": {"woocommerce": True}}, "person")
        run = mod("lanes").RunContext()
        self.pack_h = load_plugin().make_pack_handler(self.settings, self.tmp, lambda: run, self.secret,
                                                      tickets=mod("hooks").Tickets(), **self.kw)

    def pack(self, op: str, **input_) -> dict:
        args = {"site": self.site, "pack": "woocommerce", "action": "run", "op": op, "input": input_}
        return json.loads(self.pack_h(args, session_id=f"harness-{self.login}"))

    def rest(self, method, path, body=None):
        return hs.rest(self.login, self.pw, method, path, body)


@pytest.fixture(scope="module")
def drafter(setup):
    return ShopAgent("hermes_shop_drafter", setup["users"]["hermes_shop_drafter"]["pw"])


@pytest.fixture(scope="module")
def publisher(setup):
    return ShopAgent("hermes_shop_publisher", setup["users"]["hermes_shop_publisher"]["pw"])


@pytest.fixture(scope="module")
def draft(drafter, setup, tmp_path_factory, evidence):
    """The drafter's draft product: an uploaded image, a description in the site's design, a category."""
    from PIL import Image, ImageDraw
    png = tmp_path_factory.mktemp("img") / f"apron-{STAMP}.png"
    img = Image.new("RGB", (800, 800), (236, 229, 218))
    draw = ImageDraw.Draw(img)
    draw.polygon([(300, 140), (500, 140), (520, 300), (640, 330), (600, 700), (200, 700), (160, 330), (280, 300)],
                 fill=(181, 68, 44))
    draw.arc((300, 40, 500, 240), 200, 340, fill=(120, 44, 28), width=12)
    draw.rectangle((260, 470, 380, 560), outline=(236, 229, 218), width=6)
    draw.rectangle((420, 470, 540, 560), outline=(236, 229, 218), width=6)
    img.save(png)
    up = drafter.draft(action="upload_media", path=str(png), alt="A rust-coloured linen apron")
    assert up.get("ok") is not False and up["media"]["id"], up
    out = drafter.pack("draft-product", name=f"Linen apron {STAMP}", regular_price="29.90",
                       image_id=up["media"]["id"], category_ids=[setup["category_id"]],
                       short_description="Washed linen, two pockets.",
                       body={"heading": "Made to last", "lead": "Washed linen that softens with every wear.",
                             "points": ["Two deep pockets", "Adjustable neck strap", "Machine washable"],
                             "closing": "Cut and sewn in small batches."})
    assert out["ok"], out
    evidence["boxes"]["drafter_draft_product"] = {
        "ok": out["ok"], "status": out["result"]["status"], "created": out["result"]["created"],
        "read_back": out["result"]["read_back"], "design": out["result"].get("design"),
        "image_id": out["result"]["image_id"], "through": out["through"], "method": out["method"]}
    return {"id": out["result"]["id"], "media": up["media"]["id"], "out": out}


def test_the_shop_roles_carry_their_rights_and_levels(setup, drafter, publisher, evidence):
    roles = setup["roles"]
    assert "publish_products" not in roles["wpbruecke_shop_drafter"]
    assert {"edit_products", "upload_files", "read_private_products", "assign_product_terms", "edit_posts"} <= \
        set(roles["wpbruecke_shop_drafter"])
    assert {"publish_products", "edit_published_products", "publish_posts"} <= set(roles["wpbruecke_shop_publisher"])
    for role in roles.values():
        assert not {"manage_options", "manage_woocommerce", "edit_theme_options", "list_users",
                    "edit_others_products"} & set(role)
    assert drafter.check["level"] == "drafts" and publisher.check["level"] == "publish"
    evidence["boxes"]["levels"] = {"drafter": drafter.check, "publisher": publisher.check}


def test_products_query_as_the_drafter(drafter, evidence):
    out = drafter.pack("products-query")
    assert out["ok"] and out["method"] == "GET" and "products" in out["result"], out
    evidence["boxes"]["drafter_products_query"] = {"ok": True, "count": len(out["result"]["products"])}


def test_the_drafter_writes_a_draft_product_in_the_site_design(draft, drafter, admin, evidence):
    stored = hs.rest(*admin, "GET", f"/wpb-pk3/v1/product/{draft['id']}")[1]
    assert stored["status"] == "draft" and stored["author"] == "hermes_shop_drafter"
    assert stored["image_id"] == draft["media"] and stored["regular_price"] == "29.90"
    assert "<h3" in stored["description"] and "<li>Two deep pockets</li>" in stored["description"]
    color = ((draft["out"]["result"].get("design") or {}).get("color") or {}).get("value")
    if color:
        assert color.lower() in stored["description"].lower()
    lines = [line for line in drafter.log_lines() if line["tool"] == "wp_pack"]
    assert [(line["action"], line["label"], line["result"]) for line in lines] == [
        ("draft-product", f"Linen apron {STAMP}", "ok")]
    evidence["boxes"]["drafter_draft_stored"] = {
        "status": stored["status"], "author": stored["author"], "image_set": stored["image_id"] == draft["media"],
        "category_ids": stored["category_ids"], "heading_coloured": bool(color and color.lower() in
                                                                        stored["description"].lower()),
        "log_line": {k: lines[0][k] for k in ("action", "label", "result", "wp_type")}}


def test_the_proof_renders_the_draft_through_the_preview_link(draft, drafter, evidence):
    out = drafter.pack("prove-product", id=draft["id"])
    assert out["ok"], out
    res = out["result"]
    assert res["through"] == "signed preview link" and res["product_status"] == "draft"
    shots = {s["width"]: s for s in res["shots"]}
    assert 1440 in shots and shots[1440]["status"] == 200, res
    render = next(c for c in res["checks"] if c["check"] == "render")
    assert render["verdict"] == "match", res["checks"]
    entry = {"verdict": res["verdict"], "checks": {c["check"] + (f"@{c['width']}" if c.get("width") else ""):
                                                   c["verdict"] for c in res["checks"]},
             "widths": sorted(shots), "seconds": res["seconds"]}
    if OUT:
        from PIL import Image
        src = drafter.rt().store.path("proofs", f"{res['proof_id']}/{shots[1440]['file']}")
        webp = Path(OUT).with_name(Path(OUT).stem + "-draft-1440.webp")
        img = Image.open(src)
        img = img.crop((0, 0, img.width, min(img.height, 1800)))
        img.save(webp, "WEBP", quality=60)
        entry["webp"] = webp.name
    evidence["boxes"]["drafter_proof"] = entry


def test_the_drafter_is_refused_every_publish(draft, drafter, admin, evidence):
    create = drafter.pack("create-product", name=f"Should not exist {STAMP}", status="publish")
    update = drafter.pack("update-product", id=draft["id"], status="publish")
    assert create["error"] == "level_drafts" and create["sent"] is False
    assert update["error"] == "level_drafts" and update["sent"] is False
    # WooCommerce's own REST API with the same password: the connector's guard refuses, the product stays a draft
    rows = {}
    for status in ("publish", "private"):
        code, body = drafter.rest("PUT", f"/wc/v3/products/{draft['id']}", {"status": status})
        rows[status] = {"http": code, "code": (body or {}).get("code"), "message": (body or {}).get("message")}
        assert code in (400, 403) and "publish_products" in str((body or {}).get("message")), body
    code, body = drafter.rest("PUT", f"/wc/v3/products/{draft['id']}", {"name": f"Linen apron {STAMP}"})
    assert code == 200 and body["status"] == "draft"   # a draft edit through wc/v3 still works
    stored = hs.rest(*admin, "GET", f"/wpb-pk3/v1/product/{draft['id']}")[1]
    assert stored["status"] == "draft"
    evidence["boxes"]["drafter_publish_refused"] = {
        "create_product": create["error"], "update_product": update["error"], "wc_v3_put": rows,
        "wc_v3_draft_edit": code, "status_after": stored["status"]}


def test_the_route_refuses_others_products_and_the_reader(harness, setup, drafter, evidence):
    code, body = drafter.rest("POST", "/wpbruecke/v1/shop/product", {"id": setup["admins_draft"], "name": "x"})
    assert code == 403 and body["code"] == "wpbruecke_cannot_edit_product"
    reader = harness.user("hermes_reader")
    rcode, rbody = hs.rest(*reader, "POST", "/wpbruecke/v1/shop/product", {"name": "x"})
    assert rcode == 403 and rbody["code"] == "wpbruecke_reader_read_only"
    out = drafter.pack("draft-product", id=setup["admins_draft"], name="x")
    assert out["error"] == "route_forbidden" and "another user's product" in out["message"]
    evidence["boxes"]["route_refusals"] = {"others_draft": [code, body["code"]], "reader": [rcode, rbody["code"]],
                                           "wp_pack_others_draft": out["error"]}


def test_the_publisher_publishes_with_read_back(publisher, admin, evidence):
    out = publisher.pack("create-product", name=f"Wool scarf {STAMP}", regular_price="45.00", status="publish",
                         body={"heading": "Warm all winter", "lead": "Merino, knitted loose.",
                               "points": ["180 cm long"]})
    assert out["ok"], json.dumps(out)[:3000]
    back = out["result"]["read_back"]
    assert back["ok"] and back["status"] == "publish" and out["result"]["status"] == "publish"
    assert {"name", "regular_price", "status"} <= set(back["as_sent"])
    pid = out["result"]["id"]
    upd = publisher.pack("update-product", id=pid, regular_price="49.00")
    assert upd["ok"] and upd["result"]["read_back"]["changed"] == ["regular_price"], json.dumps(upd)[:3000]
    assert upd["result"]["read_back"]["status"] == "publish"
    # a draft of its own, then published through WooCommerce's ability
    dr = publisher.pack("draft-product", name=f"Canvas tote {STAMP}", regular_price="19.00")
    pub = publisher.pack("update-product", id=dr["result"]["id"], status="publish")
    assert dr["result"]["status"] == "draft" and pub["ok"] and pub["result"]["read_back"]["status"] == "publish", \
        json.dumps(pub)[:3000]
    assert "status" in pub["result"]["read_back"]["changed"]
    proof = publisher.pack("prove-product", id=pid)
    assert proof["ok"] and proof["result"]["through"] == "permalink", proof
    stored = hs.rest(*admin, "GET", f"/wpb-pk3/v1/product/{pid}")[1]
    assert stored["status"] == "publish" and stored["regular_price"] == "49.00"
    lines = [(line["action"], line["result"]) for line in publisher.log_lines() if line["tool"] == "wp_pack"]
    assert lines == [("create-product", "ok"), ("update-product", "ok"), ("draft-product", "ok"),
                     ("update-product", "ok")]
    evidence["boxes"]["publisher_publish"] = {
        "create_read_back": {k: back[k] for k in ("status", "as_sent", "not_as_sent")},
        "update_changed": upd["result"]["read_back"]["changed"], "draft_then_publish": pub["result"]["read_back"]["status"],
        "proof": {"through": proof["result"]["through"], "verdict": proof["result"]["verdict"]},
        "stored": {"status": stored["status"], "regular_price": stored["regular_price"]}, "log": lines}


def test_deactivating_the_connector_removes_the_shop_roles(admin, evidence):
    code, _ = hs.rest(*admin, "POST", f"/wp/v2/plugins/{CONNECTOR}", {"status": "inactive"})
    assert code == 200
    try:
        gone = hs.rest(*admin, "GET", "/wpb-pk3/v1/roles")[1]
    finally:
        back_code, _ = hs.rest(*admin, "POST", f"/wp/v2/plugins/{CONNECTOR}", {"status": "active"})
    again = hs.rest(*admin, "GET", "/wpb-pk3/v1/roles")[1]
    assert gone == {"wpbruecke_shop_drafter": None, "wpbruecke_shop_publisher": None}
    assert back_code == 200 and "publish_products" in again["wpbruecke_shop_publisher"]
    evidence["boxes"]["deactivation"] = {"after_deactivate": gone,
                                         "after_activate": {k: bool(v) for k, v in again.items()}}
