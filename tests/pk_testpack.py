"""A test-only plugin pack ("testpack") and a FakeWP that answers the Abilities API like WordPress 7.1.

Not shipped as a pack: tests put it into packs.REGISTRY for their own run (``installed`` fixture) and take
it out again. Its ops cover every gate, level and route shape wp_pack knows; round 2's WooCommerce and
FluentCRM packs declare the same fields.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl

from wpb_fakes import ROLE_CAPS, FakeWP, Settings, create_paired, mod, secrets

PACK_ID = "testpack"
PW = "pw pw pw"
SCREEN = "The site's admin ticks Send on the Test plugin screen in wp-admin (WPBrücke > Test plugin)."
BASE = "/wp-json/wp-abilities/v1/abilities"


def ability(name, readonly=False, destructive=False, idempotent=False, props=None):
    schema = {"type": "object", "properties": props} if props is not None else {}
    return {"name": name, "label": name.split("/")[1].replace("-", " ").title(), "description": f"{name} for tests",
            "category": "test", "input_schema": schema, "output_schema": {},
            "meta": {"annotations": {"readonly": readonly, "destructive": destructive, "idempotent": idempotent},
                     "show_in_rest": True}}


LISTING = [
    ability("testplug/items-query", readonly=True, idempotent=True, props={"per_page": {"type": "integer"}}),
    ability("testplug/item-save", props={"title": {"type": "string"}}),
    ability("testplug/item-publish", props={"id": {"type": "integer"}}),
    ability("testplug/send", props={"id": {"type": "integer"}, "action": {"type": "string"}}),
    ability("testplug/purge", destructive=True, idempotent=True, props={"id": {"type": "integer"}}),
    ability("testplug/undeclared", props={"id": {"type": "integer"}}),
    ability("other/thing", readonly=True),
]


class State:
    """What the pack's permission reader reports (the site's switches), per test."""
    permissions: dict = {}


def build_pack():
    packs = mod("packs")
    op = packs.OpSpec
    return packs.PackSpec(
        id=PACK_ID, label="Test plugin", kind="plugin", detect=lambda facts, post=None: True,
        looks_for="the test plugin", requires={"connector": None, "namespaces": [], "abilities": []},
        scope="The test pack lists, saves and publishes test items; it never purges.",
        ops={
            "items": op(ability="testplug/items-query", level="read", default_input={"per_page": 5},
                        permission="read_test_items", description="list items"),
            "save": op(ability="testplug/item-save", level="drafts", description="save a draft item"),
            "publish": op(ability="testplug/item-publish", level="publish", description="publish an item"),
            "send": op(ability="testplug/send", level="drafts", gate="card", needs="send", fixed_input={"action": "send"},
                       card=lambda rt, site, data: f"item {data.get('id')} goes to every subscriber"),
            "purge": op(ability="testplug/purge", level="drafts", gate="person_only"),
            "export": op(route="test/export", level="read", description="export through the connector"),
        },
        permissions=lambda rt, site: dict(State.permissions), screens={"send": SCREEN},
    )


class AbilitiesWP(FakeWP):
    """FakeWP plus the Abilities API: the listing (two pages) and one run route per listed ability."""

    def __init__(self, role="editor", listing=None, answers=None, **kw):
        super().__init__(user="hermes", caps=dict(ROLE_CAPS[role]), roles=[role], password=PW, **kw)
        self.listing = list(LISTING if listing is None else listing)
        self.answers = dict(answers or {})   # ability name -> (status, body)
        self.runs: list = []

    def default(self, method, parsed, headers, body):
        p = parsed.path
        if p == BASE and method == "GET" and self.authed(headers):
            q = dict(parse_qsl(parsed.query))
            per, page = int(q.get("per_page", 50)), int(q.get("page", 1))
            items = self.listing[(page - 1) * per:page * per]
            pages = max(1, -(-len(self.listing) // per))
            return self.j(200, items, {"X-WP-Total": str(len(self.listing)), "X-WP-TotalPages": str(pages)})
        if p.startswith(BASE + "/") and p.endswith("/run"):
            name = p[len(BASE) + 1:-len("/run")]
            data = json.loads(body.decode()) if body else None
            self.runs.append({"name": name, "method": method, "query": parsed.query, "body": data})
            if name in self.answers:
                status, answer = self.answers[name]
                return self.j(status, answer)
            if not any(a["name"] == name for a in self.listing):
                return self.j(404, {"code": "rest_ability_not_found", "message": "Ability not found.",
                                    "data": {"status": 404}})
            return self.j(200, {"ran": name, "method": method})
        if p == "/wp-json/wpbruecke/v1/test/export" and method == "GET":
            self.runs.append({"name": "route:test/export", "method": method, "query": parsed.query, "body": None})
            return self.j(200, {"exported": 3})
        return super().default(method, parsed, headers, body)


def site_with(tmp_path, role="editor", packs=None, **kw):
    """(fake, runtime) for one paired, confirmed site ``s`` whose user has ``role``."""
    fake = AbilitiesWP(role=role, **kw)
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1=PW), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"version": "1"})
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "hermes", "key_slot": 1,
                       "packs": {PACK_ID: True} if packs is None else packs}, "person")
    return fake, rt
