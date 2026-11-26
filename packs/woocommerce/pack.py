"""The WooCommerce pack (v0.5 PK3): products through WooCommerce's abilities and the connector's draft route.

Levels follow the site's WordPress role (the role is the lock, D26 a, D27 a):

- Read: ``products-query`` (ability ``woocommerce/products-query``; WooCommerce asks ``read_private_products``).
- Drafts: ``draft-product`` creates a draft product or edits a draft one through the connector's
  ``POST wpbruecke/v1/shop/product`` (WooCommerce's own create asks ``publish_products``, so a drafts-only
  role cannot create through the abilities, P27). The connector forces the status to draft, and its
  publish guard keeps the role from publishing through ``wc/v3`` (P41).
- Publish: ``create-product`` and ``update-product`` (abilities ``product-create``, ``product-update``) change
  live products directly, then read the product back and report its status and the fields that changed.
- ``prove-product`` renders the product page in a real browser at the site's widths: a draft through the
  connector's signed preview link, a live product at its permalink.

No order, customer or delete op (D26 a). Descriptions can come as words (``body``) that ``design.py`` lays
out in the site's design (D34).
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from ...client import WPError
from .. import OpSpec, PackSpec
from . import design

ID = "woocommerce"
LABEL = "WooCommerce"
NEEDS_CONNECTOR = "0.5.0"
QUERY = "woocommerce/products-query"
SCOPE = ("The WooCommerce pack lists products, writes draft products (name, description, prices, categories, "
         "featured image), and at the Publish level creates and updates live products with a read-back; it never "
         "reads or changes orders or customers and never deletes a product.")
SCREEN = ("The site's admin gives the Hermes user the role WPBrücke shop drafter or WPBrücke shop publisher in "
          "wp-admin > Users, then the person confirms the level again on the WPBrücke page.")
READ_BACK_FIELDS = ("name", "status", "regular_price", "sale_price", "sku", "stock_status")
# product-update accepts these only together with product_type_alias (its input schema's oneOf, measured)
TYPED_FIELDS = ("regular_price", "sale_price", "manage_stock", "stock_quantity", "stock_status")


def type_alias(row: dict) -> Optional[str]:
    """WooCommerce's agent-facing type alias for a product as products-query lists it."""
    kind = row.get("type")
    if kind == "external":
        return "affiliate"
    if kind == "grouped":
        return "grouped"
    if kind == "simple":
        return "digital" if row.get("downloadable") else "virtual" if row.get("virtual") else "physical"
    return None


def detect(facts: dict, post: Optional[dict] = None) -> bool:
    """WooCommerce is active: its REST namespace, or its plugin among the site's active plugins or assets."""
    facts = facts or {}
    if "wc/v3" in (facts.get("namespaces") or []):
        return True
    seo = facts.get("seo_public") or {}
    slugs = [str(p).lower() for p in seo.get("plugins_in_html") or []]
    slugs += [str(p.get("plugin") or "").split("/")[0].lower() for p in facts.get("plugins") or []
              if isinstance(p, dict) and p.get("status") == "active"]
    return "woocommerce" in slugs


# --- shared pieces ----------------------------------------------------------------------------------

def _refused(code: str, message: str, sent: bool = False, **extra: Any) -> dict:
    return {"ok": False, "error": code, "message": message, "sent": sent, **extra}


def _with_design(rt, site: dict, data: dict) -> "tuple[dict, Optional[dict], Optional[dict]]":
    """(input to send, the design used, refusal): ``body`` becomes ``description`` in the site's design."""
    data = dict(data)
    if "body" not in data:
        return data, None, None
    body = data.pop("body")
    if data.get("description"):
        return data, None, _refused("route_invalid_input", "Give the description as body (words WPBrücke lays out "
                                    "in the site's design) or as description (HTML), not both. Nothing was sent.")
    from .. import design_tokens
    try:
        text, used = design.render(body, design_tokens(rt, site))
    except design.BadBody as e:
        return data, None, _refused("route_invalid_input", f"{e}. Nothing was sent.")
    data["description"] = text
    return data, used, None


def _connector_version(rt, site: dict) -> Optional[str]:
    facts = rt.site_facts(site)
    if not (facts.get("connector") or facts.get("connector_ns")):
        return None
    return str((facts.get("connector") or {}).get("version") or "") or "unknown"


def _product_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _same(stored: Any, sent: Any) -> bool:
    """Whether WooCommerce kept a sent value (``19.9`` and ``19.90`` are the same price)."""
    if isinstance(sent, bool) or isinstance(stored, bool):
        return bool(stored) is bool(sent) if isinstance(sent, bool) and isinstance(stored, bool) else False
    if isinstance(sent, (list, tuple)):
        return isinstance(stored, (list, tuple)) and sorted(map(_norm, stored)) == sorted(map(_norm, sent))
    a, b = _number(stored), _number(sent)
    if a is not None and b is not None:
        return a == b
    return _norm(stored) == _norm(sent)


def _empty(value: Any) -> bool:
    return value is None or value == [] or _norm(value) == ""


def compare(sent: dict, current: dict, before: Optional[dict] = None) -> dict:
    """What the site kept of ``sent``: ``as_sent``, ``not_as_sent``; of those, ``dropped`` (nothing of it was
    kept: the field is empty or, with ``before``, unchanged) and ``truncated`` (a cut of what was sent);
    ``not_compared`` the sent fields the read-back cannot see."""
    keys = [k for k in sent if k != "id"]
    compared = [k for k in keys if k in current]
    as_sent = [k for k in compared if _same(current[k], sent[k])]
    not_as = sorted(k for k in compared if k not in as_sent)
    truncated = sorted(k for k in not_as if isinstance(sent[k], str) and not _empty(current[k])
                       and _norm(sent[k]).startswith(_norm(current[k])))
    dropped = sorted(k for k in not_as if k not in truncated and (
        (_empty(current[k]) and not _empty(sent[k])) or (before is not None and k in before
                                                          and _same(current[k], before[k]))))
    out = {"as_sent": as_sent, "not_as_sent": not_as, "dropped": dropped, "truncated": truncated,
           "not_compared": sorted(k for k in keys if k not in current)}
    if dropped or truncated:
        out["note"] = ("The write went through, but WordPress or WooCommerce did not keep "
                       + ", ".join(f"{k} ({'cut short' if k in truncated else 'not saved'})"
                                   for k in dropped + truncated)
                       + ("; stock_quantity is kept only with manage_stock true" if "stock_quantity" in dropped
                          else "") + ". Tell the person; do not report these fields as changed.")
    return out


def _stored(current: dict, keys: list) -> dict:
    """What the site holds for the fields that did not read back as sent (long text cut)."""
    return {k: current[k][:200] if isinstance(current[k], str) else current[k] for k in keys}


def _texts(rt, site: dict, product_id: int) -> dict:
    """The description and short description (``wp/v2/product``, edit context), or {} when unreadable."""
    try:
        post = rt.client(site).get(f"/wp-json/wp/v2/product/{int(product_id)}", context="edit",
                                   _fields="id,title,content,excerpt")
    except WPError:
        return {}
    post = post or {}
    return {"name": ((post.get("title") or {}).get("raw")), "description": (post.get("content") or {}).get("raw"),
            "short_description": (post.get("excerpt") or {}).get("raw")}


def _read(rt, site: dict, product_id: int) -> "tuple[Optional[dict], Optional[dict]]":
    """(the products-query row, None) or (None, the error)."""
    from ... import abilities
    got = abilities.run_declared(rt, site, QUERY, {"id": int(product_id)})
    if not got.ok:
        return None, {"ok": False, "error": got.error, "message": got.message}
    rows = [p for p in ((got.result or {}).get("products") or []) if isinstance(p, dict)]
    row = next((p for p in rows if p.get("id") == int(product_id)), None)
    if row is None:
        return None, {"ok": False, "error": "not_found", "message": f"products-query did not return product {product_id}."}
    return row, None


def read_back(rt, site: dict, product_id: int, sent: Optional[dict] = None, before: Optional[dict] = None) -> dict:
    """The product as WooCommerce answers it now (``products-query`` with its id), plus its description and
    short description (``wp/v2/product``, edit context). With ``sent``, every sent field the read-back can
    see is compared (``compare``): a field WooCommerce dropped is never silent. ``ok`` says the read-back
    ran; with ``before``, ``changed`` lists the fields whose value moved."""
    row, err = _read(rt, site, product_id)
    if err:
        return err
    fields = {k: row.get(k) for k in READ_BACK_FIELDS}
    fields.update(permalink=row.get("permalink"), product_type_alias=type_alias(row))
    texts = {k: v for k, v in _texts(rt, site, product_id).items() if k != "name"}
    current = {**row, **texts}
    out: dict = {"ok": True, "status": fields["status"], "fields": fields,
                 "has_description": bool(_norm(texts.get("description"))) if texts else None}
    if sent:
        out.update(compare(sent, current, before))
        out["stored"] = _stored(current, out["not_as_sent"])
    if before is not None:
        watched = set(READ_BACK_FIELDS) | set(texts) | set(sent or {})
        out["changed"] = sorted(k for k in current if k in watched and k in before
                                and not _same(current[k], before[k]))
    return out


def _snapshot(rt, site: dict, product_id: int) -> Optional[dict]:
    """Every readable field before an update (None when the product cannot be read)."""
    row, err = _read(rt, site, product_id)
    if err:
        return None
    texts = {k: v for k, v in _texts(rt, site, product_id).items() if k != "name"}
    return {**row, "product_type_alias": type_alias(row), **texts}


# --- op runs (OpSpec.run; send is the op's one ability or route call) -----------------------------

ROUTE_MESSAGES = {
    "wpbruecke_not_draft": ("Product {id} is not a draft. At the Drafts level Hermes edits draft products only; a "
                            "live product changes with update-product at the Publish level (shop publisher role)."),
    "wpbruecke_cannot_edit_product": ("WordPress does not let this site's Hermes user edit product {id} (another "
                                      "user's product, or a published one). Nothing changed."),
    "wpbruecke_no_woocommerce": "WooCommerce is not active on this site. Nothing changed.",
}


def draft_product(rt, site: dict, data: dict, send) -> dict:
    """POST wpbruecke/v1/shop/product: a new draft product, or an edit of a draft one (``id``)."""
    version = _connector_version(rt, site)
    if version is None:
        return _refused("route_not_found", f"draft-product goes through the WPBrücke connector {NEEDS_CONNECTOR} or "
                        "newer, and this site has no connector. Nothing was sent.")
    data, used, bad = _with_design(rt, site, data)
    if bad:
        return bad
    if "id" in data and _product_id(data["id"]) is None:
        return _refused("route_invalid_input", "id must be the product's id (a positive number). Nothing was sent.")
    before = _texts(rt, site, data["id"]) if "id" in data else None
    out = send(data)
    if not out.get("ok"):
        wp_code = out.get("wp_code") or ""
        if wp_code == "rest_no_route":
            out["message"] = (f"The site's WPBrücke connector ({version}) has no shop route; draft-product needs "
                              f"connector {NEEDS_CONNECTOR} or newer. Nothing changed.")
        elif wp_code in ROUTE_MESSAGES:
            out["message"] = ROUTE_MESSAGES[wp_code].format(id=data.get("id"))
        return out
    result = dict(out.get("result") or {})
    # The route answers the saved product; name and description are read back as WordPress stored them.
    current = {k: result[k] for k in ("name", "regular_price", "sale_price", "category_ids", "image_id") if k in result}
    if _product_id(result.get("id")):
        current.update({k: v for k, v in _texts(rt, site, result["id"]).items() if v is not None})
    back = {"status": result.get("status"), "draft": result.get("status") == "draft",
            **compare({k: v for k, v in data.items() if k != "id"}, current, before or None)}
    back["stored"] = _stored(current, back["not_as_sent"])
    result["read_back"] = back
    if used is not None:
        result["design"] = used
    out["result"] = result
    return out


def create_product(rt, site: dict, data: dict, send) -> dict:
    """woocommerce/product-create, then the product read back (status and the fields as sent)."""
    data, used, bad = _with_design(rt, site, data)
    if bad:
        return bad
    out = send(data)
    if not out.get("ok"):
        return out
    product = (out.get("result") or {}).get("product") or {}
    pid = _product_id(product.get("id"))
    result: dict = {"id": pid, "name": product.get("name"), "status": product.get("status"),
                    "permalink": product.get("permalink"), "product": product}
    if pid:
        back = read_back(rt, site, pid, sent=data)
        result["read_back"] = back
        if back.get("ok"):
            result["status"] = back["status"]
    else:
        result["read_back"] = {"ok": False, "error": "no_id", "message": "WooCommerce answered without a product id."}
    if used is not None:
        result["design"] = used
    out["result"] = result
    return out


def update_product(rt, site: dict, data: dict, send) -> dict:
    """woocommerce/product-update on a product (live ones included: Publish level only, D27 a), with the
    product read before and after: ``changed`` lists the fields that moved."""
    pid = _product_id(data.get("id"))
    if pid is None:
        return _refused("ability_invalid_input", "update-product needs id, the product's id. Nothing was sent.")
    data, used, bad = _with_design(rt, site, data)
    if bad:
        return bad
    before = _snapshot(rt, site, pid)
    if any(k in data for k in TYPED_FIELDS) and not data.get("product_type_alias"):
        alias = (before or {}).get("product_type_alias")
        if not alias:
            return _refused("ability_invalid_input", "WooCommerce changes prices and stock only together with the "
                            "product's type (product_type_alias: physical, virtual, digital, affiliate or grouped), "
                            f"and product {pid} could not be read to find it. Pass it. Nothing was sent.")
        data["product_type_alias"] = alias
    out = send(data)
    if not out.get("ok"):
        return out
    product = (out.get("result") or {}).get("product") or {}
    back = read_back(rt, site, pid, sent={k: v for k, v in data.items() if k not in ("id", "product_type_alias")},
                     before=before)
    result: dict = {"id": pid, "name": product.get("name") or (back.get("fields") or {}).get("name"),
                    "status": back.get("status") or product.get("status"), "permalink": product.get("permalink"),
                    "read_back": back, "product": product}
    if before is None:
        back["changed_note"] = "the product could not be read before the update, so changed is unknown"
    if used is not None:
        result["design"] = used
    out["result"] = result
    return out


def prove_product(rt, site: dict, data: dict, send) -> dict:
    """The product page in a real browser at the site's proof widths: a draft through the connector's signed
    preview link (the op's route), a live product at its permalink. Read-only."""
    from ...proof import checks as proof_checks
    from ...tools import proof as proof_tool
    pid = _product_id(data.get("id"))
    if pid is None:
        return _refused("route_invalid_input", "prove-product needs id, the product's id. Nothing was sent.")
    client = rt.client(site)
    try:
        post = client.get(f"/wp-json/wp/v2/product/{pid}", context="edit", _fields="id,status,link,type")
    except WPError as e:
        code = "route_forbidden" if e.status in (401, 403) else "route_not_found" if e.status == 404 else "route_failed"
        return _refused(code, f"WordPress did not return product {pid} to this site's Hermes user ({e.message}).",
                        sent=e.status != 0, wp_code=e.code or None)
    status, link = (post or {}).get("status"), (post or {}).get("link")
    if status == "publish":
        url, kind = link, "url"
    else:
        if _connector_version(rt, site) is None:
            return _refused("route_not_found", "A draft product's page opens only through the WPBrücke connector's "
                            "signed preview link, and this site has no connector. Nothing was proven.")
        got = send({"post_id": pid})
        if not got.get("ok"):
            return got
        url, kind = (got.get("result") or {}).get("url"), "draft"
    if not url:
        return _refused("route_failed", f"The site gave no page address for product {pid}. Nothing was proven.", sent=True)
    target = {"kind": kind, "site": site["id"], "wp_id": pid, "url": link}
    run = proof_checks.run_proof(store=rt.store, url=url, host=urlparse(site["url"]).hostname,
                                 connect_ip=site.get("connect_ip") or "", widths=site["proof"]["widths"],
                                 extra_hosts=site["proof"]["extra_hosts"], chromium_path=rt.settings.chromium_path,
                                 target=target)
    summary = proof_tool.summary(run)
    summary.update(product_status=status, through="signed preview link" if kind == "draft" else "permalink")
    return {"ok": True, "result": summary, "sent": True, "method": "POST" if kind == "draft" else "GET"}


# --- the pack ---------------------------------------------------------------------------------------

_PRICE = {"type": "string", "description": "decimal, e.g. 19.90; empty clears it"}
DRAFT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "integer", "minimum": 1, "description": "edit this draft product; without it a new draft"},
        "name": {"type": "string", "description": "the product's name (needed for a new product)"},
        "body": design.SCHEMA,
        "description": {"type": "string", "description": "HTML; WordPress filters what the user may not post"},
        "short_description": {"type": "string"},
        "regular_price": _PRICE, "sale_price": _PRICE,
        "category_ids": {"type": "array", "items": {"type": "integer"}, "description": "product category ids"},
        "image_id": {"type": "integer", "minimum": 0, "description": "featured image: a media id (0 removes it)"},
    },
}
PROVE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["id"],
                "properties": {"id": {"type": "integer", "minimum": 1, "description": "the product's id"}}}

OPS = {
    "products-query": OpSpec(
        ability=QUERY, level="read", default_input={"per_page": 10}, permission="read_private_products",
        description="List products (search, sku, status, stock_status, id, page, per_page); no orders."),
    "draft-product": OpSpec(
        route="shop/product", level="drafts", method="POST", permission="edit_products", input_schema=DRAFT_SCHEMA,
        run=draft_product,
        description=("Create a draft product, or edit a draft one (id), through the WPBrücke connector: name, "
                     "description (or body: words laid out in the site's design), short description, prices, "
                     "category ids, featured image id. Always saved as a draft; never publishes.")),
    "create-product": OpSpec(
        ability="woocommerce/product-create", level="publish", permission="publish_products", run=create_product,
        description=("Publish level: create a product through WooCommerce (status publish makes it live), then read "
                     "it back. body (words in the site's design) may replace description.")),
    "update-product": OpSpec(
        ability="woocommerce/product-update", level="publish", permission="edit_published_products",
        run=update_product,
        description=("Publish level: change a product, a live one included, through WooCommerce (id plus the fields "
                     "to change; for prices and stock WPBrücke adds the product's product_type_alias), then read it "
                     "back: status and the fields that changed.")),
    "prove-product": OpSpec(
        route="preview-link", level="read", method="POST", input_schema=PROVE_SCHEMA, run=prove_product,
        description=("Render the product page in a real browser at the site's proof widths and check it: a draft "
                     "through the connector's signed preview link, a live product at its permalink. Read-only.")),
}

PACK = PackSpec(
    id=ID, label=LABEL, kind="plugin", detect=detect,
    looks_for="WooCommerce active on the site (its wc/v3 REST namespace)",
    requires={"connector": NEEDS_CONNECTOR, "namespaces": ["wc/v3"],
              "abilities": sorted(op.ability for op in OPS.values() if op.ability)},
    scope=SCOPE, ops=OPS, skill="references/pack-woocommerce.md", screens={"": SCREEN},
)
