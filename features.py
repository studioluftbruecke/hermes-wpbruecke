"""FEATURES: which plugin feature a site's setup lights, and why the others stay dark.

One registry read by the doctor, the tools, the routes and the page. Each
rule names the rights the key needs, whether the connector or a pack is needed, and the setting
that switches it. ``evaluate`` returns ``available`` and ``unavailable`` lists; every unavailable
line says what would light it, never just "no".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .facts import can_change_live


@dataclass(frozen=True)
class FeatureRule:
    key: str
    label: str
    rights: tuple = ()            # every right needed (facts.RIGHTS keys)
    keyed: bool = False           # needs a key at all
    connector: bool = False       # needs the WPBrücke connector on the site
    pack: str = ""                # needs this pack switched on in the site file
    fix: str = ""                 # what lights it, in plain words


FEATURES = (
    FeatureRule("public_facts", "Fleet card with public facts (version, theme, REST time, SEO basics)"),
    FeatureRule("seo_site", "SEO site check (indexing, sitemap, robots.txt, description, speed)"),
    FeatureRule("proof_published", "Proof of published pages at three widths"),
    FeatureRule("read_content", "Read posts, pages, drafts and comments", keyed=True,
                fix="add an application password in a key slot and name the slot in the site file"),
    FeatureRule("drafts", "Write drafts (posts and pages)", rights=("draft",), keyed=True,
                fix="use a WordPress user that can edit posts (Contributor or the connector's drafter role)"),
    FeatureRule("media", "Upload images with alt text", rights=("upload",), keyed=True,
                fix="Contributor cannot upload; install the connector and give the user its drafter role"),
    FeatureRule("staged_changes", "Stage changes to live posts as draft copies", rights=("draft",), keyed=True,
                fix="use a WordPress user that can edit posts"),
    FeatureRule("apply_staged", "Apply a staged copy with one click on the review screen", connector=True,
                fix="install the WPBrücke connector (the click runs on your own WordPress login)"),
    FeatureRule("proof_drafts", "Proof of drafts (signed preview links)", keyed=True, connector=True,
                fix="install the WPBrücke connector; application passwords cannot open a draft preview"),
    FeatureRule("seo_fields", "Write SEO title and description", keyed=True, connector=True,
                fix="install the WPBrücke connector (or Yoast, Rank Math, SEOPress with the connector)"),
    FeatureRule("review_screen", "Publish and schedule from the desk (your own login, connector review screen)",
                connector=True, fix="install the WPBrücke connector and turn off the site's hard rule"),
    FeatureRule("updates", "Updates of WordPress, plugins and themes", rights=("install",), keyed=True,
                fix="plugin lists need an administrator key; premium plugins need the connector"),
    FeatureRule("counts", "Drafts, pending posts and held comments", rights=("draft",), keyed=True,
                fix="add a key of a user that can edit posts"),
    FeatureRule("build_run", "GeneratePress pack: build runs as GenerateBlocks drafts", rights=("draft",), keyed=True,
                pack="generatepress", fix="switch the GeneratePress pack on for this site (packs.generatepress: true)"),
)
BY_KEY = {f.key: f for f in FEATURES}


def _connector(facts: dict) -> bool:
    return bool(facts.get("connector") or facts.get("connector_ns"))


def check(rule: FeatureRule, site: dict, facts: dict) -> Optional[str]:
    """None when available, else the reason (one sentence, with the fix)."""
    if not facts.get("reachable"):
        return "the site did not answer"
    if rule.keyed and not site.get("key_slot"):
        return f"no key for this site; {rule.fix or 'add a key slot'}"
    if rule.keyed and not facts.get("rights"):
        return "the key did not authenticate; run the doctor"
    missing = [r for r in rule.rights if not (facts.get("rights") or {}).get(r)]
    if missing:
        fix = rule.fix
        if rule.key == "media" and _connector(facts):
            fix = "give the key's user the connector's WPBrücke drafter role (wp-admin, Users, the user, Role)"
        return f"the key's user lacks {', '.join(missing)}; {fix}"
    if rule.connector and not _connector(facts):
        return f"the connector is not installed; {rule.fix}"
    if rule.pack and not (site.get("packs") or {}).get(rule.pack):
        return f"the {rule.pack} pack is off; {rule.fix}"
    if rule.key == "review_screen" and site.get("never_publish_never_send", True):
        return "the hard rule \"never publish, never send\" is on for this site (your default); turn it off in the site's settings to offer desk publishing"
    if rule.key == "review_screen" and site.get("publishing", "off") == "off":
        return "publishing is off for this site, so the desk links to the WordPress editor"
    return None


def evaluate(site: dict, facts: dict) -> dict:
    available, unavailable = [], []
    for rule in FEATURES:
        why = check(rule, site, facts)
        if why is None:
            available.append({"feature": rule.key, "label": rule.label})
        else:
            unavailable.append({"feature": rule.key, "label": rule.label, "because": why})
    return {"available": available, "unavailable": unavailable}


def rights_words(facts: dict) -> list:
    """The doctor's plain chips: can draft, cannot publish, ..."""
    r = facts.get("rights") or {}
    if not r:
        return []
    words = ["can draft" if r.get("draft") else "cannot draft",
             "can upload" if r.get("upload") else "cannot upload",
             "can publish" if r.get("publish") else "cannot publish"]
    if r.get("edit_published"):
        words.append("can edit live posts")
    if r.get("manage"):
        words.append("can change settings")
    if r.get("install"):
        words.append("can install plugins")
    return words


def key_warnings(facts: dict) -> list:
    r = facts.get("rights") or {}
    out = []
    if r.get("install") or r.get("manage"):
        out.append("This key can publish, install plugins and change settings. Hermes asks before every write. "
                   "Recommended only for setup and test sites.")
    elif can_change_live(r):
        out.append("This key can publish or edit live posts. Hermes asks before every write; WordPress will not "
                   "stop a publish.")
    return out


def access_label(facts: dict, site: dict) -> str:
    if not site.get("key_slot"):
        return "not set up"
    r = facts.get("rights") or {}
    if not r:
        return "key not working"
    if can_change_live(r):
        return "admin: asks first" if r.get("manage") else "can publish: asks first"
    return "drafts only" if r.get("draft") else "read only"
