"""Where a post's meta description is written.

WordPress core has no description field. The WPBrücke connector registers ``_wpbruecke_description``
for REST and prints it in ``<head>``; when Yoast SEO, Rank Math or SEOPress is active the connector
registers that plugin's own key for REST instead, so the description lands where the site's SEO
plugin prints it (one description, never two).
"""
from __future__ import annotations

CONNECTOR_NS = "wpbruecke/v1"
CONNECTOR_KEY = "_wpbruecke_description"

SEO_PLUGINS = {
    "yoast": {"name": "Yoast SEO", "namespace": "yoast/v1", "meta_key": "_yoast_wpseo_metadesc"},
    "rankmath": {"name": "Rank Math", "namespace": "rankmath/v1", "meta_key": "rank_math_description"},
    "seopress": {"name": "SEOPress", "namespace": "seopress/v1", "meta_key": "_seopress_titles_desc"},
}


def has_connector(facts: dict) -> bool:
    return bool(facts.get("connector")) or CONNECTOR_NS in (facts.get("namespaces") or [])


def seo_plugin(facts: dict) -> str | None:
    """'yoast' | 'rankmath' | 'seopress' | None, from the connector's report or the REST namespaces."""
    said = (facts.get("connector") or {}).get("seo_plugin") if isinstance(facts.get("connector"), dict) else None
    if said in SEO_PLUGINS:
        return said
    namespaces = facts.get("namespaces") or []
    for key, info in SEO_PLUGINS.items():
        if info["namespace"] in namespaces:
            return key
    return None


def description_target(facts: dict) -> dict:
    """{"where": "connector"|"yoast"|"rankmath"|"seopress"|None, "meta_key": str|None, "reason": str}"""
    plugin = seo_plugin(facts)
    if has_connector(facts):
        if plugin:
            info = SEO_PLUGINS[plugin]
            return {"where": plugin, "meta_key": info["meta_key"],
                    "reason": f"{info['name']} is active and prints the description; the WPBrücke connector "
                              f"opens its key {info['meta_key']} to REST, so the draft sets it there."}
        return {"where": "connector", "meta_key": CONNECTOR_KEY,
                "reason": "The WPBrücke connector's description field; it prints the description in the page head."}
    if plugin:
        name = SEO_PLUGINS[plugin]["name"]
        return {"where": None, "meta_key": None,
                "reason": f"{name} is active, but its description key is not writable over REST; "
                          "install the WPBrücke connector to set it from a draft."}
    return {"where": None, "meta_key": None,
            "reason": "WordPress core has no description field; install the WPBrücke connector."}
