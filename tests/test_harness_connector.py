"""Opt-in: the connector on the oldest WordPress and PHP it supports (the harness `min` pair).

WordPress 6.5 on PHP 7.4 with only this checkout's connector and the harness control plugin. Proves
that the connector activates there, answers ``info`` with the plugin's own connector version, names a
post's builder in edit context only, and answers its Elementor read route with 409
``wpbruecke_no_elementor`` on a site without Elementor. Skipped unless ``WPB_HARNESS=1``
(``harness_support.py`` names the rest).
"""
from __future__ import annotations

import pytest

import harness_support as hs
from wpb_fakes import mod

pytestmark = pytest.mark.skipif(hs.skip_reason() is not None, reason=hs.skip_reason() or "")

cz = mod("connector_zip")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    yield from hs.boot("min", tmp_path_factory.mktemp("harness-min"))


def test_booted_the_oldest_supported_wordpress_and_php(harness):
    booted = harness.ready["booted"]
    assert booted["wp"] == "6.5"
    assert booted["php"].startswith("7.4.")
    assert booted["plugins"]["wpbruecke-connector"] == {"version": cz.CONNECTOR_VERSION, "active": True}


def test_info_answers_the_connector_version(harness):
    status, info = hs.rest(*harness.user("hermes_editor"), "GET", "/wpbruecke/v1/info")
    assert status == 200
    assert info["version"] == cz.CONNECTOR_VERSION == cz.header_version()


def test_builder_field_in_edit_context_only(harness):
    page = harness.seed["pages"]["blocks"]
    status, edit = hs.rest(*harness.user("hermes_editor"), "GET", f"/wp/v2/pages/{page}?context=edit")
    assert status == 200
    assert edit["wpbruecke_builder"]["id"] == "blocks"
    status, view = hs.rest(None, None, "GET", f"/wp/v2/pages/{page}")
    assert status == 200
    assert "wpbruecke_builder" not in view


def test_elementor_read_route_without_elementor_is_409(harness):
    page = harness.seed["pages"]["blocks"]
    status, body = hs.rest(*harness.user("hermes_editor"), "GET", f"/wpbruecke/v1/elementor/{page}")
    assert status == 409
    assert body["code"] == "wpbruecke_no_elementor"
