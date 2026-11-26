"""v0.3 watcher: update offers by key with a first-tick summary (WG1), health from the public home page
with broken / updating / back (WG2)."""
import pytest

from wpb_fakes import (CONNECTOR_NAMESPACES, ROLE_CAPS, FakeWP, Settings, WporgFake, connector_updates,
                       create_paired, home_fault, mod, secrets)

STEP = 900  # `hermes wpbruecke watch-setup` schedules the tick every 15 minutes


def make(tmp_path, connector=False, core="7.1.4"):
    fake = FakeWP(caps=ROLE_CAPS["editor"], roles=["editor"], namespaces=CONNECTOR_NAMESPACES if connector else None)
    clock = [1_000_000.0]
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                clock=lambda: clock[0])
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1, "label": "Test"},
                  "person")
    wp = WporgFake(core=core).attach(rt)
    return rt, fake, wp, clock


def tick(rt, clock, **kw):
    clock[0] += STEP
    return mod("tick").run_tick(rt, now=clock[0], **kw)["lines"]


def state(rt):
    return rt.store.read_json(rt.store.path("tick", "state.json"), {})["s"]


# --- WG1: update offers by key ------------------------------------------------------------------

def test_a_newer_release_after_one_already_reported_is_reported_again(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    assert tick(rt, clock) == []
    wp.core = "7.2.0"
    first = tick(rt, clock)
    assert len(first) == 1 and "1 update(s) available (WordPress 7.2.0)" in first[0], first
    assert tick(rt, clock) == []
    wp.core = "7.3.0"
    second = tick(rt, clock)
    assert len(second) == 1 and "1 update(s) available (WordPress 7.3.0)" in second[0], second
    assert tick(rt, clock) == []


def test_plugin_a_updated_while_plugin_b_appears_reports_b(tmp_path):
    rt, fake, wp, clock = make(tmp_path, connector=True)
    connector_updates(fake, plugins={"plugin-a": "2.0"})
    assert tick(rt, clock) == ["Test: 1 update(s) pending (plugin-a 2.0); you update in wp-admin"]
    connector_updates(fake, plugins={"plugin-b": "1.1"})  # A was updated, B is new: the count stays 1
    lines = tick(rt, clock)
    assert lines == ["Test: 1 update(s) available (plugin-b 1.1); you update in wp-admin"]
    assert tick(rt, clock) == []


def test_an_offer_from_wordpress_org_and_the_connector_counts_once_and_the_connector_wins(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    wp.plugins = {"gp-premium": "2.5.0", "akismet": "5.4"}
    facts = {"wp_version": "7.1.4",
             "plugins": [{"plugin": "gp-premium/gp-premium.php", "version": "2.4"},
                         {"plugin": "akismet/akismet.php", "version": "5.3"}],
             "connector": {"updates": {"core": None, "themes": [], "count": 2, "plugins": [
                 {"slug": "gp-premium", "installed": "2.4", "latest": "2.5.1"},
                 {"slug": "akismet", "installed": "5.3", "latest": "5.4"}]}}}
    ups = mod("facts").updates(facts, rt.wporg)
    assert ups["count"] == 2 and ups["complete"] is True
    assert ups["offers"] == [
        {"key": "plugin:akismet", "installed": "5.3", "offered": "5.4", "source": "connector"},
        {"key": "plugin:gp-premium", "installed": "2.4", "offered": "2.5.1", "source": "connector"}]


def test_first_tick_with_two_pending_prints_one_summary_line_then_silence(tmp_path):
    rt, fake, wp, clock = make(tmp_path, connector=True, core="7.2.0")
    connector_updates(fake, plugins={"plugin-a": "2.0"})
    lines = tick(rt, clock)
    assert lines == ["Test: 2 update(s) pending (WordPress 7.2.0, plugin-a 2.0); you update in wp-admin"]
    assert tick(rt, clock) == []
    assert tick(rt, clock) == []


def test_a_state_file_from_0_2_gets_one_summary_then_silence(tmp_path):
    rt, fake, wp, clock = make(tmp_path, core="7.2.0")
    old = {"s": {"reachable": True, "rest_hist": [100, 110], "updates": 1, "down_since": None,
                 "last_down_alert": None, "slow": False, "waiting": 0}}
    rt.store.write_json(rt.store.path("tick", "state.json"), old)
    assert tick(rt, clock) == ["Test: 1 update(s) pending (WordPress 7.2.0); you update in wp-admin"]
    assert tick(rt, clock) == []
    assert state(rt)["baseline_sent"] is True and state(rt)["reported"] == {"core:wordpress": "7.2.0"}


def test_a_failed_wordpress_org_read_keeps_what_was_reported_and_prints_nothing(tmp_path):
    rt, fake, wp, clock = make(tmp_path, connector=True, core="7.2.0")
    connector_updates(fake, plugins={"plugin-a": "2.0"})
    assert len(tick(rt, clock)) == 1
    wp.fail = True
    WporgFake.forget(rt)  # nothing cached either: the core offer is missing from this read
    connector_updates(fake, plugins={"plugin-a": "2.0", "plugin-b": "1.1"})
    assert tick(rt, clock) == []
    assert rt.facts.read("s")["updates"]["complete"] is False
    assert state(rt)["reported"] == {"core:wordpress": "7.2.0", "plugin:plugin-a": "2.0"}
    wp.fail = False
    assert tick(rt, clock) == ["Test: 1 update(s) available (plugin-b 1.1); you update in wp-admin"]
    assert tick(rt, clock) == []


def test_repro_2026_11_25_first_tick_with_one_update_pending_prints_the_summary(tmp_path):
    fake, clock = FakeWP(caps=ROLE_CAPS["editor"], roles=["editor"]), [1000.0]
    rt = mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                wporg_fetch=lambda u: {"offers": [{"current": "7.2.0"}]} if "core" in u else {"version": "5.3"},
                                clock=lambda: clock[0])
    create_paired(rt, {"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1, "label": "Test"}, "person")
    lines = mod("tick").run_tick(rt, now=clock[0])["lines"]
    assert lines == ["Test: 1 update(s) pending (WordPress 7.2.0); you update in wp-admin"]


def test_digest_off_announces_no_updates_but_keeps_the_baseline(tmp_path):
    rt, fake, wp, clock = make(tmp_path, core="7.2.0")
    assert tick(rt, clock, digest=False) == []
    assert tick(rt, clock) == []


# --- WG2: health from the public home page -------------------------------------------------------

@pytest.mark.parametrize("mode,verdict,signals", [
    ("off", "ok", {"home_status": 200}),
    ("fatal", "broken", {"home_status": 500, "error_page": True}),
    ("plain500", "broken", {"home_status": 500}),
    ("plain503", "broken", {"home_status": 503}),
    ("die200", "broken", {"home_status": 200, "error_page": True}),
    ("maintenance", "updating", {"home_status": 503, "retry_after": "600", "error_page": True}),
])
def test_health_per_signal(tmp_path, mode, verdict, signals):
    rt, fake, wp, clock = make(tmp_path)
    home_fault(fake, mode)
    health = rt.site_facts(rt.site("s"), refresh=True)["health"]
    assert health["verdict"] == verdict
    assert {s["name"]: s["value"] for s in health["signals"]} == signals
    homes = [r for r in fake.requests if r["path"] == "/"]
    assert homes and not any(r["auth"] for r in homes)  # the public page: never the key


def test_health_no_answer_is_unknown_and_a_redirect_is_not_followed(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    fake.routes[("GET", "/")] = (301, {"Location": "https://other.example/"}, b"")
    assert rt.site_facts(rt.site("s"), refresh=True)["health"]["verdict"] == "ok"
    fake.down = True
    assert rt.site_facts(rt.site("s"), refresh=True)["health"] == {"verdict": "unknown", "signals": []}


@pytest.mark.parametrize("html,verdict", [
    ('<body id="error-page"><div class="wp-die-message">x</div></body>', "broken"),
    ("<body id='error-page'><div class='wp-die-message'>x</div></body>", "broken"),
    ('<body id="error-page"><p>a theme that names its body so</p></body>', "ok"),
    ('<p>a post that quotes the class wp-die-message</p>', "ok"),
])
def test_error_page_needs_both_markers(html, verdict):
    assert mod("health").classify(200, None, html)["verdict"] == verdict


def test_maintenance_on_one_tick_is_silent_on_two_ticks_one_line(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    assert tick(rt, clock) == []
    home_fault(fake, "maintenance")
    assert tick(rt, clock) == []
    home_fault(fake, "off")  # the update finished between two ticks
    assert tick(rt, clock) == []
    home_fault(fake, "maintenance")
    assert tick(rt, clock) == []
    lines = tick(rt, clock)
    assert len(lines) == 1 and lines[0].startswith("Test is broken since") and "maintenance mode" in lines[0], lines
    assert tick(rt, clock) == []
    home_fault(fake, "off")
    assert tick(rt, clock) == ["Test is back: the home page answers HTTP 200 again"]
    assert tick(rt, clock) == []


def test_broken_is_reported_once_again_after_six_hours_then_back(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    assert tick(rt, clock) == []
    home_fault(fake, "fatal")
    rounds = [tick(rt, clock) for _ in range(30)]  # 7.5 h, one tick per 15 minutes as the cron runs it
    alerted = [i for i, lines in enumerate(rounds) if lines]
    assert alerted == [0, 25], alerted  # 25 * 900 s is the first tick past 6 h after the first alert
    for i in alerted:
        assert len(rounds[i]) == 1 and rounds[i][0].startswith("Test is broken since")
        assert "HTTP 500 with WordPress' error page" in rounds[i][0]
    home_fault(fake, "off")
    assert tick(rt, clock) == ["Test is back: the home page answers HTTP 200 again"]
    assert tick(rt, clock) == []


def test_repro_2026_11_25_home_500_while_rest_answers_now_reports(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    assert tick(rt, clock) == []
    home_fault(fake, "fatal")
    lines = tick(rt, clock, digest=False)  # an outage: reported with the digest off too
    assert len(lines) == 1 and lines[0].startswith("Test is broken since"), lines
    row = mod("api").overview(rt)[1]["sites"][0]
    assert row["health"] == {"verdict": "broken", "since": clock[0]}
    doctor = mod("tools.doctor").run(rt, "s")["sites"][0]
    assert doctor["health"]["verdict"] == "broken"
    assert any(l.startswith("health: broken (the home page answers HTTP 500") for l in doctor["lines"])


def test_a_down_site_stays_down_not_broken_and_comes_back_once(tmp_path):
    rt, fake, wp, clock = make(tmp_path)
    assert tick(rt, clock) == []
    fake.down = True
    down = tick(rt, clock)
    assert len(down) == 1 and down[0].startswith("Test is down since")
    fake.down = False
    back = tick(rt, clock)
    assert len(back) == 1 and back[0].startswith("Test is back (REST")
    assert mod("api").overview(rt)[1]["sites"][0]["health"] == {"verdict": "ok", "since": None}


def test_a_site_flapping_between_broken_and_down_is_announced_broken_once_per_six_hours(tmp_path):
    # Review 2026-11-25 (row e2): each return from down announced "broken" again with a new "since".
    rt, fake, wp, clock = make(tmp_path)
    assert tick(rt, clock) == []
    home_fault(fake, "fatal")
    broken, sinces = [], set()
    for i in range(30):  # 7.5 h; every third tick the site does not answer at all
        fake.down = i % 3 == 2
        for line in tick(rt, clock):
            if " is broken since " in line:
                broken.append(i)
                sinces.add(line.split(" is broken since ")[1][:9])
    assert broken == [0, 25], broken
    assert len(sinces) == 1, sinces
    fake.down = False
    home_fault(fake, "off")
    back = tick(rt, clock)
    assert back == ["Test is back: the home page answers HTTP 200 again"], back
