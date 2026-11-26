"""v0.5 release (PK6): the connector this plugin ships satisfies every pack, and wp_doctor stops naming the
connector once a site runs it."""
from __future__ import annotations

import pytest

from test_v05_woo import shop_site
from wpb_fakes import mod

SHIPPED = mod("connector_zip").CONNECTOR_VERSION


def _facts(spec, version):
    return {"connector": {"version": version}, "namespaces": ["wp/v2", "wpbruecke/v1", *(spec.requires or {}).get("namespaces", [])]}


@pytest.mark.parametrize("pack_id", sorted(mod("packs").REGISTRY))
def test_the_shipped_connector_satisfies_every_pack(pack_id):
    packs = mod("packs")
    spec = packs.REGISTRY[pack_id]
    assert packs.needs(spec, _facts(spec, SHIPPED), detected=True) is None


@pytest.mark.parametrize("pack_id", ["woocommerce", "fluentcrm"])
def test_the_plugin_packs_name_the_connector_they_need_on_an_older_site(pack_id):
    packs = mod("packs")
    spec = packs.REGISTRY[pack_id]
    assert spec.requires["connector"] == "0.5.0"
    assert packs.needs(spec, _facts(spec, "0.4.0"), detected=True) == \
        "needs the WPBrücke connector 0.5.0 or newer (the site has 0.4.0)"


def _pack_line(rt, label):
    report = mod("tools.doctor").run(rt, "s")["sites"][0]
    return next(line for line in report["lines"] if line.startswith(f"pack {label}:"))


def test_doctor_drops_the_needs_line_for_the_shipped_connector(tmp_path):
    _, rt = shop_site(tmp_path / "old", connector="0.4.0")
    assert _pack_line(rt, "WooCommerce") == ("pack WooCommerce: detected, on; needs the WPBrücke connector 0.5.0 or "
                                             "newer (the site has 0.4.0)")
    _, rt = shop_site(tmp_path / "new", connector=SHIPPED)
    assert _pack_line(rt, "WooCommerce") == "pack WooCommerce: detected, on"
