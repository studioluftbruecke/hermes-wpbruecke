"""Elementor's own update steps add capabilities to the Editor role; that must not stop a confirmed site
(finding of the v0.4 AE2 live box), while any other new capability still does."""
from test_v02_enforce import clock, handlers, setup, writes  # noqa: F401  (the enforce tests' fixture and helpers)
from wpb_fakes import mod

ELEMENTOR_UPDATE_CAPS = {"elementor_global_classes_update_class": True, "elementor_atomic_widgets_edit": True}


def create(rt):
    return handlers(rt)[0]({"site": "s", "action": "create", "title": "x"})


def test_capabilities_elementor_adds_on_update_do_not_stop_a_confirmed_editor(tmp_path, clock):
    fake, rt = setup(tmp_path, "editor")
    fake.caps = {**fake.caps, **ELEMENTOR_UPDATE_CAPS}
    clock[0] += 61
    out = create(rt)
    assert out.get("ok"), out


def test_a_site_confirmed_by_0_3_with_elementor_capabilities_stays_confirmed(tmp_path, clock):
    fake, rt = setup(tmp_path, "editor", confirmed=False)
    fake.caps = {**fake.caps, **ELEMENTOR_UPDATE_CAPS}
    access = mod("access")
    legacy = access.legacy_caps_fingerprint(fake.caps, fake.roles)
    assert legacy != access.caps_fingerprint(fake.caps, fake.roles)
    rt.sites.update("s", {"access": {"level": "publish", "role": "editor", "caps_fingerprint": legacy,
                                     "confirmed_at": "2026-11-25T00:00:00Z"}}, "person", confirm=True)
    clock[0] += 61
    out = create(rt)
    assert out.get("ok"), out


def test_any_other_new_capability_still_stops_the_site(tmp_path, clock):
    fake, rt = setup(tmp_path, "editor")
    fake.caps = {**fake.caps, **ELEMENTOR_UPDATE_CAPS, "list_users": True}
    clock[0] += 61
    out = create(rt)
    assert out["error"] == "level_stopped" and "rights changed" in out["message"] and not writes(fake)
