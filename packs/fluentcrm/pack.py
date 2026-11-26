"""The FluentCRM pack (v0.5 PK4): newsletters drafted in the site's design, previewed, test-sent, and scheduled
only where the site's admin switched sending on, after a card every time (A30, A32, A36).

What Hermes may do is the site admin's setting on the connector's screen in wp-admin (Settings > WPBrücke
FluentCRM), read here as ``FcrmPermissions`` through ``GET wpbruecke/v1/fluentcrm/permissions``. While
"Schedule and send campaigns" is off the Hermes user holds FluentCRM's ``fcrm_read_emails`` only, so FluentCRM
itself refuses it every write and send; drafts, previews and test mails go through the connector's three
routes, which lend ``fcrm_manage_emails`` for one fixed ability call (P33). Until the screen was saved for the
user, every op is refused with the sentence naming the screen.

Ops (``wp_pack``):

| op                | through                                   | gate                               | needs      |
| crm-context       | ability get-crm-context                   | level read                         | view       |
| list-campaigns    | ability list-campaigns                    | level read                         | view       |
| get-campaign      | ability get-campaign                      | level read                         | view       |
| draft-campaign    | route fluentcrm/campaign                  | level drafts; another person's     | draft      |
|                   |                                           | draft asks with a card (A31)       |            |
| preview-campaign  | route fluentcrm/preview, then the proof   | level drafts                       | draft      |
| send-test         | route fluentcrm/test                      | level drafts                       | test_email |
| schedule-campaign | ability change-campaign-status, schedule  | card every time, never unattended  | send       |

Not in the pack: the other change-campaign-status actions (D36), send-email-to-contact, and every contact,
list, tag and automation write.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .. import OpSpec, PackSpec, card_text_cut as cut
from . import compose, render

PACK_ID = "fluentcrm"
NAMESPACE = "fluent-crm/v2"
SCREEN_PATH = "Settings > WPBrücke FluentCRM"
PERMISSIONS_ROUTE = "/wp-json/wpbruecke/v1/fluentcrm/permissions"
CACHE_S = 60
DESK_TYPE = "fluentcrm_campaign"
BOXES = {"view": "View campaigns", "draft": "Write draft campaigns", "test_email": "Send test emails",
         "send": "Schedule and send campaigns"}

SCOPE = ("The FluentCRM pack drafts email campaigns in the site's design, previews them and sends test copies to "
         "the address the site's admin saved; it schedules a campaign only where the admin switched sending on, "
         "after a card every time, and it never sends to a contact, never changes another campaign status, and "
         "never writes contacts, lists, tags or automations.")
SCREEN = (f"The site's admin decides what Hermes may do in FluentCRM in wp-admin > {SCREEN_PATH} (the WPBrücke "
          "connector's screen, connector 0.5.0 or newer: pick the Hermes user, tick the boxes, Save); until that "
          "screen is saved for this user, the FluentCRM pack runs nothing.")
SCREENS = {"": SCREEN, **{k: (f"It is the box \"{label}\" for the Hermes user in wp-admin > {SCREEN_PATH}, which only "
                              "the site's admin can tick (connector 0.5.0 or newer); until that screen is saved for "
                              "this user, the FluentCRM pack runs nothing.")
                          for k, label in BOXES.items()}}


def detect(facts: dict, post: Optional[dict] = None) -> bool:
    return NAMESPACE in set((facts or {}).get("namespaces") or [])


# --- FcrmPermissions --------------------------------------------------------------------------------------

def _cache_path(rt, site: dict):
    return rt.store.path("facts", f"_fcrm-{site['id']}.json")


def read_permissions(rt, site: dict, refresh: bool = False, clock=time.time) -> dict:
    """FcrmPermissions for the site's Hermes user, cached a minute next to the facts. A read that fails
    answers ``{"error": ...}`` and is not cached."""
    from ...client import WPError
    path = _cache_path(rt, site)
    cached = rt.store.read_json(path, None)
    if not refresh and isinstance(cached, dict) and clock() - float(cached.get("_t", 0)) < CACHE_S:
        return dict(cached.get("permissions") or {})
    client = rt.client(site)
    if not client.authenticated:
        return {"error": "the site's password is not sent yet (connect and confirm the site first)"}
    try:
        got = client.get(PERMISSIONS_ROUTE)
    except WPError as e:
        if e.status == 404:
            return {"error": "the connector does not answer wpbruecke/v1/fluentcrm/permissions (it needs connector "
                             "0.5.0 or newer)", "status": 404}
        return {"error": e.message, "status": e.status}
    got = got if isinstance(got, dict) else {}
    with rt.store.lock("facts"):
        rt.store.write_json(path, {"_t": clock(), "permissions": got})
    return got


def permissions(rt, site: dict) -> dict:
    """What OpSpec.needs reads: {view, draft, test_email, send}. Nothing at all until the screen was saved
    for the user (the pack then runs nothing). A read that fails raises, so the op fails closed."""
    p = read_permissions(rt, site)
    if p.get("error") and p.get("status") != 404:
        raise RuntimeError(p["error"])
    if not p.get("saved"):
        return {}
    return {k: p.get(k) is True for k in BOXES}


def doctor_view(rt, site: dict, facts: dict, refresh: bool = True) -> Optional[dict]:
    """FcrmPermissions for wp_doctor, on a site that runs FluentCRM; None elsewhere."""
    if not detect(facts) or not site.get("key_slot"):
        return None
    p = read_permissions(rt, site, refresh=refresh)
    return {k: v for k, v in p.items() if k != "screen"}


def doctor_line(view: dict) -> str:
    if view.get("error"):
        return f"FluentCRM permissions: not read ({view['error']})"
    if not view.get("fluentcrm"):
        return "FluentCRM permissions: FluentCRM is not active"
    which = f"FluentCRM {view.get('version') or ''}".strip()
    which += f" with Pro {view['pro_version']}" if view.get("pro_version") else " (free)"
    if not view.get("saved"):
        return (f"FluentCRM permissions ({which}): never saved for this user, so the FluentCRM pack runs nothing "
                f"until the site's admin saves wp-admin > {SCREEN_PATH}")
    boxes = ", ".join(f"{label.lower()} {'on' if view.get(k) else 'off'}" for k, label in BOXES.items())
    return f"FluentCRM permissions ({which}): {boxes}"


# --- reads the gate and the cards make ---------------------------------------------------------------------

def _hermes_user_id(rt, site: dict) -> Optional[int]:
    user = (rt.site_facts(site) or {}).get("user") or {}
    if isinstance(user.get("id"), int):
        return user["id"]
    uid = read_permissions(rt, site).get("user_id")
    return int(uid) if isinstance(uid, int) else None


def campaign_owner(rt, site: dict, campaign_id: Any) -> Optional[int]:
    """created_by of a campaign (FluentCRM's own campaign read, fcrm_read_emails)."""
    got = rt.client(site).get(f"/wp-json/{NAMESPACE}/campaigns/{int(campaign_id)}")
    campaign = got.get("campaign") if isinstance(got, dict) else None
    if not isinstance(campaign, dict) or campaign.get("created_by") in (None, ""):
        return None
    return int(campaign["created_by"])


def draft_gate(rt, site: dict, data: dict) -> str:
    """A new draft, or one the Hermes user made: no card. Someone else's draft (or one whose owner cannot be
    read): a card (A31)."""
    if not data.get("campaign_id"):
        return "level"
    owner, me = campaign_owner(rt, site, data["campaign_id"]), _hermes_user_id(rt, site)
    return "level" if owner is not None and owner == me else "card"


def _campaign(rt, site: dict, campaign_id: Any) -> dict:
    """The campaign as get-campaign reads it (with FluentCRM's recipient estimate), or {"_error": why}."""
    from ... import abilities
    try:
        got = abilities.run_op(rt, site, SPEC.ops["get-campaign"], {"campaign_id": int(campaign_id),
                                                                     "include": ["recipients_estimate"],
                                                                     "body_format": "text"})
    except Exception as e:  # a card still shows, with the reason
        return {"_error": f"{type(e).__name__}"}
    return got.result if got.ok and isinstance(got.result, dict) else {"_error": got.message or got.error}


def _audience(c: dict) -> str:
    r = c.get("recipients") or {}
    names = []
    for key in ("lists", "tags"):
        for x in r.get(key) or []:
            names.append(str(x.get("title") or x.get("slug") or x.get("id")) if isinstance(x, dict) else str(x))
    if r.get("all_contacts"):
        return "every subscribed contact"
    return cut(", ".join(cut(n, 40) for n in names[:8]), 80) or "none set"


def _named(c: dict, cid: Any) -> str:
    """The campaign as a card names it: its title cut, its id and status."""
    return f"campaign \"{cut(c.get('title') or '?')}\" (id {cut(cid, 20)}, {cut(c.get('status') or 'status unknown', 30)})"


# Every card states the decision in its first sentence, before any person- or site-supplied text, and cuts
# that text (card_text_cut): a chat client may show only the card's first lines.

def draft_card(rt, site: dict, data: dict) -> str:
    cid = data.get("campaign_id")
    try:
        owner = campaign_owner(rt, site, cid)
    except Exception:
        owner = None
    c = _campaign(rt, site, cid)
    whose = (f"another person's draft (WordPress user id {owner}, not the Hermes user)" if owner is not None else
             "a draft whose owner WPBrücke could not read (it may be another person's, not the Hermes user's)")
    lines = [f"Overwrites {whose}: {_named(c, cid)}.",
             "Saving replaces that person's draft with this one"
             + (f" (new subject: {cut(data['email_subject'])})" if data.get("email_subject") else "") + "."]
    return "\n".join(lines)


def schedule_card(rt, site: dict, data: dict) -> str:
    cid = data.get("campaign_id")
    c = _campaign(rt, site, cid)
    est = (c.get("recipients_estimate") or {}).get("count") if isinstance(c.get("recipients_estimate"), dict) else None
    when = cut(data.get("scheduled_at") or "", 40)
    who = (f"about {cut(est, 12)} subscribed contacts" if est is not None else "an unknown number of contacts "
           "(FluentCRM gave no estimate)")
    at = f"at {when} (site time unless it names an offset)" if when else "immediately, as soon as you approve"
    lines = [f"Sends real email to {who} ({_audience(c)}) {at}: {_named(c, cid)}.",
             "To stop it, unschedule it in FluentCRM before the send time.",
             f"Subject: {cut(c.get('email_subject') or '?')}",
             "The count is FluentCRM's estimated_recipients now."]
    if c.get("_error"):
        lines.insert(1, f"(WPBrücke could not read the campaign: {cut(c['_error'])})")
    return "\n".join(lines)


# --- run hooks ---------------------------------------------------------------------------------------------

def _refusal(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message, "sent": False}


def editor_url(site: dict, campaign_id: int) -> str:
    return f"{site['url'].rstrip('/')}/wp-admin/admin.php?page=fluentcrm-admin#/email/campaigns/{int(campaign_id)}"


def draft_payload(rt, site: dict, data: dict) -> "tuple[dict, dict]":
    """(what the campaign route gets, the design used). Raises compose.ComposeError."""
    from .. import design_tokens
    payload = {k: data[k] for k in ("campaign_id", "title", "email_subject", "email_pre_header", "recipients",
                                    "exclude_recipients") if data.get(k) not in (None, "")}
    template = data.get("design_template") or "simple"
    if template not in compose.DESIGN_TEMPLATES:
        raise compose.ComposeError(f"design_template is one of {', '.join(compose.DESIGN_TEMPLATES)} (block markup "
                                   "FluentCRM's editor keeps; raw HTML is not written)")
    if data.get("sections") and data.get("email_body"):
        raise compose.ComposeError("give sections (WPBrücke builds the email in the site's design) or email_body "
                                   "(block markup), not both")
    if not data.get("campaign_id") and not data.get("title"):
        raise compose.ComposeError("a new draft needs a title")
    design: dict = {}
    if data.get("sections") or data.get("email_body"):
        tokens = design_tokens(rt, site)
        r = compose.roles(tokens)
        if data.get("sections"):
            facts = rt.site_facts(site) or {}
            headline = data.get("headline") or data.get("title") or data.get("email_subject")
            if not headline and data.get("campaign_id"):
                headline = _campaign(rt, site, data["campaign_id"]).get("title")
            logo = {"url": tokens["logo"], "alt": facts.get("name") or ""} if tokens.get("logo") else None
            payload["email_body"] = compose.body(
                headline=headline or "",
                sections=data["sections"], r=r, site_name=str(facts.get("name") or ""), site_url=site["url"],
                intro=data.get("intro") or "", logo=logo)
        else:
            problems = compose.check_markup(data["email_body"])
            if problems:
                raise compose.ComposeError("email_body: " + "; ".join(problems[:4]))
            payload["email_body"] = data["email_body"]
        payload["design_template"] = template
        payload["settings"] = {"template_config": compose.template_config(r)}
        design = {"source": tokens.get("source"), "overridden": tokens.get("overridden") or [],
                  **{k: r[k] for k in ("text", "content_bg", "page_bg", "accent", "on_accent", "heading", "link",
                                       "body_font", "heading_font")}}
        if tokens.get("error"):
            design["tokens_error"] = tokens["error"]
    elif not data.get("campaign_id"):
        raise compose.ComposeError("a new draft needs sections (or email_body as block markup)")
    return payload, design


def _desk_item(rt, site: dict, campaign_id: int, title: str) -> Optional[str]:
    from ...desk import Desk
    desk = Desk(rt.store)
    found = next((i for i in desk.list(site=site["id"], kind="campaign")
                  if str(i.get("wp_id")) == str(campaign_id) and i.get("state") != "discarded"), None)
    if found:
        return desk.update(found["id"], title=str(title or found.get("title") or "")[:200])["id"]
    return desk.create(site=site["id"], site_url=site["url"], kind="campaign", wp_type=DESK_TYPE, wp_id=campaign_id,
                       title=title or f"Campaign {campaign_id}", note="drafted through the FluentCRM pack")["id"]


def run_draft(rt, site: dict, data: dict, send) -> dict:
    try:
        payload, design = draft_payload(rt, site, data)
    except compose.ComposeError as e:
        return _refusal("route_invalid_input", f"{e}. Nothing was sent.")
    out = send(payload)
    res = out.get("result") if isinstance(out.get("result"), dict) else {}
    cid = res.get("campaign_id")
    if out.get("ok") and isinstance(cid, int):
        try:
            out["desk_item"] = _desk_item(rt, site, cid, str(res.get("title") or data.get("title") or ""))
        except Exception:  # the draft is saved; a desk failure must not hide that
            out["desk_item"] = None
        out["editor_url"] = editor_url(site, cid)
        out["design"] = design
        out["next"] = ("preview-campaign renders the proof; the person opens the draft in FluentCRM's editor from "
                       "the desk item.")
    return out


def run_preview(rt, site: dict, data: dict, send) -> dict:
    out = send({"campaign_id": data.get("campaign_id")})
    res = out.get("result") if isinstance(out.get("result"), dict) else {}
    if not out.get("ok"):
        return out
    # render-email-preview with body_format=both answers text and html at the top level (3.3.5)
    body = res.get("body") if isinstance(res.get("body"), dict) else res
    page = str(body.get("html") or "")
    cid = int(res.get("campaign_id") or data.get("campaign_id") or 0)
    item = None
    try:
        item = _desk_item(rt, site, cid, str(res.get("title") or ""))
    except Exception:
        item = None
    target = {"kind": "desk_item" if item else "url", "url": editor_url(site, cid), "site": site["id"], "wp_id": cid,
              "item": item}
    proof = render.prove(rt, site, page, target) if page else None
    if proof and item:
        from ...desk import Desk
        Desk(rt.store).update(item, proof_id=proof["id"])
    out["result"] = {
        "campaign_id": cid, "title": res.get("title"), "subject": res.get("subject"), "pre_header": res.get("pre_header"),
        "text": str(body.get("text") or "")[:4000], "withheld_smartcodes": res.get("withheld_smartcodes"),
        "proof": None if proof is None else {
            "proof_id": proof["id"], "verdict": proof.get("verdict"),
            "checks": [{k: c.get(k) for k in ("check", "verdict", "detail", "width") if c.get(k) is not None}
                       for c in proof.get("checks") or []],
            "shots": [{k: s.get(k) for k in ("width", "file", "height")} for s in proof.get("shots") or []],
            **({"error": proof["error"]} if proof.get("error") else {})},
    }
    if proof is None:
        out["result"]["proof_note"] = "FluentCRM answered no HTML, so nothing was rendered"
    out["desk_item"] = item
    return out


def run_schedule(rt, site: dict, data: dict, send) -> dict:
    out = send(data)
    if out.get("ok"):
        from ...desk import Desk
        desk = Desk(rt.store)
        for item in desk.list(site=site["id"], kind="campaign"):
            if str(item.get("wp_id")) == str(data.get("campaign_id")) and item.get("state") != "discarded":
                try:
                    desk.read_back(rt.client(site), item)
                except Exception:
                    pass
    return out


# --- the spec ----------------------------------------------------------------------------------------------

_ID = {"type": "integer", "description": "the campaign's id (list-campaigns shows them)"}
_LINK = {"type": "object", "properties": {"text": {"type": "string"}, "url": {"type": "string"}},
         "required": ["text", "url"]}
_IMAGE = {"type": "object", "properties": {"url": {"type": "string"}, "alt": {"type": "string"}},
          "required": ["url", "alt"]}
_AUDIENCE = {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": ["string", "integer"]}},
                                              "lists": {"type": "array", "items": {"type": ["string", "integer"]}}},
             "description": "tags and lists by id, slug or title (crm-context include=[\"segments\"] names them)"}
DRAFT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "campaign_id": {"type": "integer", "description": "update this draft; omit to create a new one"},
    "title": {"type": "string", "description": "the campaign's name in FluentCRM (needed for a new draft)"},
    "email_subject": {"type": "string"}, "email_pre_header": {"type": "string"},
    "headline": {"type": "string", "description": "the email's first heading (default: title)"},
    "intro": {"type": "string", "description": "paragraphs under the headline (blank line between paragraphs)"},
    "sections": {"type": "array", "description": "the email's sections, built in the site's design", "items": {
        "type": "object", "required": ["heading"], "properties": {
            "heading": {"type": "string"}, "text": {"type": "string", "description": "plain text; blank line between "
                                                                                     "paragraphs; SmartCodes work"},
            "image": _IMAGE, "button": _LINK}}},
    "email_body": {"type": "string", "description": "instead of sections: the whole body as block markup (from "
                                                    "get-campaign body_format=html, edited); never raw HTML"},
    "design_template": {"type": "string", "enum": list(compose.DESIGN_TEMPLATES), "description": "default simple"},
    "recipients": _AUDIENCE, "exclude_recipients": _AUDIENCE}}
CAMPAIGN_ONLY = {"type": "object", "additionalProperties": False, "required": ["campaign_id"],
                 "properties": {"campaign_id": _ID}}
SCHEDULE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["campaign_id"], "properties": {
    "campaign_id": _ID,
    "scheduled_at": {"type": "string", "description": "when FluentCRM sends it: ISO 8601 with offset, or "
                                                      "\"YYYY-MM-DD HH:MM:SS\" in site time, at least a minute ahead; "
                                                      "omit to send right after the person approves"}}}

OPS = {
    "crm-context": OpSpec(ability="fluent-crm/get-crm-context", level="read", needs="view",
                          default_input={"include_segment_counts": False}, permission="fcrm_read_emails",
                          description="who Hermes is in FluentCRM, the sender, tag and list names "
                                      "(include=[\"segments\",\"sender\",\"email_blocks\"])"),
    "list-campaigns": OpSpec(ability="fluent-crm/list-campaigns", level="read", needs="view",
                             default_input={"per_page": 20}, permission="fcrm_read_emails",
                             description="campaigns with status and send times"),
    "get-campaign": OpSpec(ability="fluent-crm/get-campaign", level="read", needs="view", permission="fcrm_read_emails",
                           description="one campaign: content, sender, audience, schedule (body_format=html to edit it)"),
    "draft-campaign": OpSpec(route="fluentcrm/campaign", level="drafts", needs="draft", input_schema=DRAFT_SCHEMA,
                             gate_for=draft_gate, card=draft_card, run=run_draft,
                             description="create a draft campaign, or update a draft, built from sections in the "
                                         "site's colours and fonts; another person's draft asks the person first"),
    "preview-campaign": OpSpec(route="fluentcrm/preview", level="drafts", needs="draft", input_schema=CAMPAIGN_ONLY,
                               run=run_preview,
                               description="the proof: FluentCRM's rendered email in Chromium at 600 and 390 px "
                                           "(links, images, contrast), on the campaign's desk item"),
    "send-test": OpSpec(route="fluentcrm/test", level="drafts", needs="test_email", input_schema=CAMPAIGN_ONLY,
                        description="one test copy to the address the site's admin saved (no other address)"),
    "schedule-campaign": OpSpec(ability="fluent-crm/change-campaign-status", level="drafts", gate="card", needs="send",
                                fixed_input={"action": "schedule"}, input_schema=SCHEDULE_SCHEMA,
                                permission="fcrm_manage_emails", card=schedule_card, run=run_schedule,
                                description="schedule (send) a draft campaign: the person approves a card with the "
                                            "campaign, the send time and the recipient count every time"),
}

SPEC = PackSpec(
    id=PACK_ID, label="FluentCRM", kind="plugin", detect=detect,
    looks_for="FluentCRM active on the site (its fluent-crm/v2 REST namespace)",
    requires={"connector": "0.5.0", "namespaces": [NAMESPACE],
              "abilities": sorted({op.ability for op in OPS.values() if op.ability})},
    scope=SCOPE, ops=OPS, skill="references/pack-fluentcrm.md", permissions=permissions, screens=SCREENS,
)
