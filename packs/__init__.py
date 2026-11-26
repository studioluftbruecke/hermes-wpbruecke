"""Packs: what WPBrücke can do with a theme, page builder or plugin beyond core WordPress.

A pack is data (``PackSpec``) plus code in ``packs/<id>/``, behind one contract. The site file
switches packs on per site (``packs: {<id>: true}``), and only ids in ``REGISTRY`` are accepted: a
pack that does not exist cannot be switched on, so it widens nothing. Switching a pack on widens
what Hermes may write on that site, so ``packs`` is a guarded site-file field (sites.py).

Builder packs plug into ``wp_content`` and ``wp_draft`` (builders.py sets a page's ``pack`` only when
the pack exists and is on); plugin packs will declare ``ops``. Design tokens are read live from the
site (``design_tokens``): never copied into a file; the site file's ``design:`` block holds only the
person's overrides, merged key by key.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .elementor import widgets as _elementor_widgets

KINDS = ("builder", "plugin", "theme")
LEVELS = ("read", "drafts", "publish")
GATES = ("level", "card", "person_only")
TOKEN_MAX_AGE_S = 300
# A card shows a person- or site-supplied string (a title, a list name, a subject) cut to this many
# characters, so the decision in the card's first sentence stays within a chat client's first lines.
CARD_TEXT_MAX = 120


def card_text_cut(value: Any, limit: int = CARD_TEXT_MAX) -> str:
    """``value`` as one line of at most ``limit`` characters (an ellipsis marks the cut)."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class UnknownPack(ValueError):
    code = "unknown_pack"


@dataclass(frozen=True)
class OpSpec:
    """One operation a plugin pack offers through ``wp_pack`` (v0.5). It runs exactly one ability or one
    connector route, at a level the site's WordPress role must allow:

    - ``gate``: ``"level"`` (reads need no card, writes inside the level need none, v0.2 D6), ``"card"``
      (a card every time; refused unattended), ``"person_only"`` (refused at every level; the person does it
      in wp-admin).
    - ``needs``: a permission the pack's ``permissions`` reader must report true for the site (e.g.
      ``"send"``); when it does not, the op is refused with the pack's sentence naming the wp-admin screen.
    - ``default_input``: sent when a read-only ability gets no input (P30); ``fixed_input``: keys forced
      over the caller's input (``{"action": "schedule"}`` keeps a status ability to one action).
    - ``permission``: the WordPress capability or plugin permission WordPress checks, named in the refusal
      when WordPress' own answer names none.
    - ``input_schema``: what the op accepts, shown by ``describe``; None shows the ability's own schema.
    - ``card``: (rt, site, input) -> lines for the card, content first; None uses the input itself.
    - ``gate_for``: (rt, site, input) -> ``"level"`` or ``"card"``, for a ``"level"`` op whose gate depends on
      the input (FluentCRM: another person's draft asks the person, A31). It can only add a card; when it
      fails, the op asks.
    - ``run``: (rt, site, input, send) -> result, for an op that shapes its input or reads its answer (a
      template filled in, a proof rendered); ``send(input)`` is the op's one ability or route call and
      answers what an op without ``run`` would.
    """
    ability: Optional[str] = None
    route: Optional[str] = None
    level: str = "read"
    gate: str = "level"
    needs: Optional[str] = None
    default_input: dict = field(default_factory=dict)
    description: str = ""
    fixed_input: dict = field(default_factory=dict)
    permission: Optional[str] = None
    input_schema: Optional[dict] = None
    method: Optional[str] = None      # routes only: GET for read ops, POST otherwise when None
    card: Optional[Callable[[Any, dict, dict], str]] = None
    gate_for: Optional[Callable[[Any, dict, dict], str]] = None
    run: Optional[Callable[[Any, dict, dict, Callable[[dict], dict]], dict]] = None

    def __post_init__(self) -> None:
        if bool(self.ability) == bool(self.route):
            raise ValueError("an OpSpec runs exactly one ability or one route")
        if self.level not in LEVELS:
            raise ValueError(f"OpSpec level {self.level!r} is not one of {', '.join(LEVELS)}")
        if self.gate not in GATES:
            raise ValueError(f"OpSpec gate {self.gate!r} is not one of {', '.join(GATES)}")
        if self.method is not None and (self.ability or self.method not in ("GET", "POST", "PUT", "DELETE")):
            raise ValueError("OpSpec method is for routes only: GET, POST, PUT or DELETE")

    @property
    def writes(self) -> bool:
        return self.level != "read"

    @property
    def through(self) -> str:
        return f"ability {self.ability}" if self.ability else f"connector route wpbruecke/v1/{self.route}"

    def route_method(self) -> str:
        return self.method or ("GET" if self.level == "read" else "POST")


@dataclass(frozen=True)
class PackFeature:
    """A feature the pack lights, read by features.py like its own rules."""
    key: str
    label: str
    rights: tuple = ()
    keyed: bool = False
    connector: bool = False


@dataclass(frozen=True)
class PackSpec:
    id: str
    label: str
    kind: str
    detect: Callable[[dict, Optional[dict]], bool]   # (facts, post|None) -> bool
    looks_for: str                                    # what detect looks for, in plain words
    requires: dict                                    # {"connector": "0.3.0"|None, "namespaces": [], "abilities": []}
    scope: str                                        # one sentence: what it edits, shown on refusals
    ops: dict = field(default_factory=dict)           # name -> OpSpec (plugin packs)
    tokens: Optional[Callable[[Any, dict], dict]] = None   # (client, facts) -> DesignTokens
    skill: str = ""                                   # skills/wpbruecke/references/pack-<id>.md
    builder: Optional[str] = None                     # the builders.py id this pack edits (builder packs)
    block_namespaces: tuple = ()                      # blocks Hermes knows while the pack is on
    features: tuple = ()
    # plugin packs (v0.5): what the site reports for OpSpec.needs, and where the admin changes it
    permissions: Optional[Callable[[Any, dict], dict]] = None   # (rt, site) -> {permission: bool}
    screens: dict = field(default_factory=dict)       # permission -> the sentence naming the wp-admin screen;
                                                      # key "" is the fallback for any permission


def _theme_slugs(facts: dict) -> list:
    out = [str(facts.get("theme") or "")]
    out += [str(t) for t in (facts.get("seo_public") or {}).get("themes_in_html") or []]
    return [s.lower() for s in out if s]


def _gp_detect(facts: dict, post: Optional[dict] = None) -> bool:
    return any(s == "generatepress" or s.startswith("generatepress-") or s.startswith("generatepress_")
               for s in _theme_slugs(facts or {}))


def _gp_tokens(client, facts: dict) -> dict:
    """GeneratePress' global colours through the connector's read-only ability ``gp-brand-get``."""
    got = client.get("/wp-json/wp-abilities/v1/abilities/wpbruecke/gp-brand-get/run")
    colors = [{"slug": str(c.get("slug") or ""), "name": str(c.get("name") or c.get("slug") or ""),
               "value": str(c.get("color") or "")}
              for c in (got or {}).get("colors") or [] if isinstance(c, dict) and c.get("slug")]
    return {"colors": colors, "fonts": {"body": "", "headings": ""}, "logo": None, "source": "generatepress"}


def _elementor_detect(facts: dict, post: Optional[dict] = None) -> bool:
    from .elementor import pack
    return pack.detect(facts, post)


def _elementor_tokens(client, facts: dict) -> dict:
    from .elementor import pack
    return pack.tokens(client, facts)


from .woocommerce import pack as _woocommerce  # noqa: E402 (it builds its PackSpec from the classes above)
from .fluentcrm import pack as _fluentcrm  # noqa: E402  (it builds on OpSpec and PackSpec above)

REGISTRY: dict = {
    "generatepress": PackSpec(
        id="generatepress", label="GeneratePress", kind="theme", detect=_gp_detect,
        looks_for="GeneratePress (or a child theme of it) as the active theme",
        requires={"connector": None, "namespaces": [], "abilities": []},
        scope=("The GeneratePress pack builds new pages as drafts (GenerateBlocks, else core blocks) in a build run "
               "and stages brand colours for the person's click; it does not edit existing pages or theme settings."),
        tokens=_gp_tokens, skill="references/pack-generatepress.md",
        block_namespaces=("generateblocks", "generatepress"),
        features=(PackFeature("build_run", "GeneratePress pack: build runs as GenerateBlocks drafts", rights=("draft",),
                              keyed=True),),
    ),
    "elementor": PackSpec(
        id="elementor", label="Elementor", kind="builder", detect=_elementor_detect,
        looks_for="Elementor active on the site (its elementor/v1 REST namespace)",
        requires={"connector": "0.4.0", "namespaces": ["elementor/v1"], "abilities": []},
        scope=_elementor_widgets.SCOPE, tokens=_elementor_tokens, skill="references/pack-elementor.md",
        builder="elementor",
    ),
    "woocommerce": _woocommerce.PACK,
    "fluentcrm": _fluentcrm.SPEC,
}


def ids() -> list:
    return sorted(REGISTRY)


def get(pack_id: str) -> PackSpec:
    spec = REGISTRY.get(str(pack_id))
    if spec is None:
        raise UnknownPack(f"unknown pack {pack_id!r}; known packs: {', '.join(ids())}")
    return spec


def unknown(ids_: Any) -> list:
    return sorted(str(k) for k in (ids_ or {}) if str(k) not in REGISTRY)


def validate_site_packs(raw: Any, to_bool: Callable[[Any, str], bool]) -> dict:
    """The site file's ``packs`` mapping: registry ids only, each true or false (missing ones false)."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise UnknownPack(f"packs is a mapping of pack ids to true or false; known packs: {', '.join(ids())}")
    bad = unknown(raw)
    if bad:
        raise UnknownPack(f"unknown pack(s) {', '.join(bad)}: WPBrücke has no such pack; known packs: {', '.join(ids())}")
    return {pid: to_bool(raw.get(pid, False), f"packs.{pid}") for pid in ids()}


def is_on(site: Optional[dict], pack_id: str) -> bool:
    return pack_id in REGISTRY and bool(((site or {}).get("packs") or {}).get(pack_id))


def for_builder(builder_id: str) -> Optional[PackSpec]:
    return next((s for s in REGISTRY.values() if s.builder == builder_id), None)


def known_block_namespaces(site: Optional[dict]) -> set:
    """Block namespaces an edit may drop without being named: core, and those of the site's packs that are on."""
    out = {"core"}
    for spec in REGISTRY.values():
        if is_on(site, spec.id):
            out.update(spec.block_namespaces)
    return out


def _version(v: Any) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])


def needs(spec: PackSpec, facts: dict, detected: Optional[bool] = None) -> Optional[str]:
    """What the site still lacks for this pack, in one sentence, or None."""
    facts = facts or {}
    missing = []
    if detected is None:
        detected = bool(spec.detect(facts, None))
    if not detected:
        missing.append(spec.looks_for)
    want = (spec.requires or {}).get("connector")
    if want:
        have = (facts.get("connector") or {}).get("version")
        if not have and not facts.get("connector_ns"):
            missing.append(f"the WPBrücke connector {want} or newer")
        elif have and _version(have) < _version(want):
            missing.append(f"the WPBrücke connector {want} or newer (the site has {have})")
    ns = set(facts.get("namespaces") or [])
    for n in (spec.requires or {}).get("namespaces") or []:
        if n not in ns:
            missing.append(f"the REST namespace {n}")
    return ("needs " + "; ".join(missing)) if missing else None


def status(site: dict, facts: dict) -> dict:
    """Per pack: {label, detected, on, needs} (the /overview and wp_doctor shape)."""
    out = {}
    for pid in ids():
        spec = REGISTRY[pid]
        try:
            detected = bool(spec.detect(facts or {}, None))
        except Exception:
            detected = False
        out[pid] = {"label": spec.label, "detected": detected, "on": is_on(site, pid),
                    "needs": needs(spec, facts or {}, detected)}
    return out


# --- design tokens ------------------------------------------------------------------------------------

def _resolve_font(value: Any, families: dict) -> str:
    text = str(value or "")
    m = re.search(r"var:preset\|font-family\|([\w-]+)", text) or re.search(r"--wp--preset--font-family--([\w-]+)", text)
    if m:
        fam = families.get(m.group(1)) or {}
        return str(fam.get("fontFamily") or fam.get("name") or m.group(1))
    return text


def _by_origin(value: Any) -> list:
    """theme.json presets come as a list, or keyed by origin (theme, default, custom): theme first."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        out = []
        for origin in ("custom", "theme", "default"):
            out += [v for v in value.get(origin) or [] if isinstance(v, dict)]
        return out
    return []


def theme_json_tokens(client, facts: dict) -> dict:
    """Global styles of the active theme (``wp/v2/global-styles/themes/<stylesheet>``)."""
    stylesheet = str((facts or {}).get("theme") or "")
    if not re.match(r"^[A-Za-z0-9_.-]{1,80}$", stylesheet):
        raise ValueError("the active theme is not known yet; run wp_doctor")
    data = client.get(f"/wp-json/wp/v2/global-styles/themes/{stylesheet}")
    settings, styles = (data or {}).get("settings") or {}, (data or {}).get("styles") or {}
    seen, colors = set(), []
    for c in _by_origin((settings.get("color") or {}).get("palette")):
        slug = str(c.get("slug") or "")
        if slug and slug not in seen:
            seen.add(slug)
            colors.append({"slug": slug, "name": str(c.get("name") or slug), "value": str(c.get("color") or "")})
    families = {str(f.get("slug")): f for f in _by_origin((settings.get("typography") or {}).get("fontFamilies"))
                if f.get("slug")}
    body = _resolve_font((styles.get("typography") or {}).get("fontFamily"), families)
    heading = _resolve_font((((styles.get("elements") or {}).get("heading") or {}).get("typography") or {})
                            .get("fontFamily"), families)
    return {"colors": colors, "fonts": {"body": body, "headings": heading or body}, "logo": None, "source": "theme.json"}


def merge_design(live: dict, overrides: Optional[dict]) -> dict:
    """The site file's ``design:`` block over the live tokens, key by key: one colour slug, one font, the
    logo. Each key the file replaced or added is listed in ``overridden`` (``colors.<slug>``,
    ``fonts.<key>``, ``logo``); everything else is the theme's."""
    out = {"colors": [dict(c) for c in live.get("colors") or []],
           "fonts": dict(live.get("fonts") or {"body": "", "headings": ""}),
           "logo": live.get("logo"), "source": live.get("source"), "overridden": []}
    ov = overrides or {}
    by_slug = {c["slug"]: c for c in out["colors"]}
    for c in ov.get("colors") or []:
        slug = c["slug"]
        if slug in by_slug:
            by_slug[slug].update({k: v for k, v in c.items() if v not in (None, "")})
        else:
            out["colors"].append(dict(c))
        out["overridden"].append(f"colors.{slug}")
    for key, value in (ov.get("fonts") or {}).items():
        if value:
            out["fonts"][key] = value
            out["overridden"].append(f"fonts.{key}")
    if ov.get("logo"):
        out["logo"] = ov["logo"]
        out["overridden"].append("logo")
    return out


def design_tokens(rt, site: dict, refresh: bool = False, clock: Callable[[], float] = time.time) -> dict:
    """DesignTokens for a site: GeneratePress' colours where the theme is GeneratePress and the connector
    answers, Elementor's global colours and fonts where the Elementor pack is on, else the theme's global
    styles; then the site file's overrides. Cached five minutes with the
    facts' rhythm, never written into the site file."""
    path = rt.store.path("facts", f"_design-{site['id']}.json")
    cached = rt.store.read_json(path, None)
    live, error = None, None
    if not refresh and isinstance(cached, dict) and clock() - float(cached.get("_t", 0)) < TOKEN_MAX_AGE_S:
        live = cached.get("tokens")
    if live is None:
        facts = rt.site_facts(site)
        client = rt.client(site)
        try:
            gp, el = REGISTRY["generatepress"], REGISTRY["elementor"]
            if gp.detect(facts, None) and (facts.get("connector") or facts.get("connector_ns")) and client.authenticated:
                live = gp.tokens(client, facts)
            elif is_on(site, "elementor") and el.detect(facts, None) and client.authenticated:
                live = el.tokens(client, facts)
            else:
                live = theme_json_tokens(client, facts)
        except Exception as e:  # a read that fails leaves the overrides standing, with the reason
            error = f"{type(e).__name__}: {e}"[:300]
            live = {"colors": [], "fonts": {"body": "", "headings": ""}, "logo": None, "source": None}
        if error is None:
            with rt.store.lock("facts"):
                rt.store.write_json(path, {"_t": clock(), "tokens": live})
    out = merge_design(live, site.get("design"))
    if error:
        out["error"] = error
    return out
