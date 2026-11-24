from wpb_fakes import FakeWP, Settings, mod, secrets


def make(tmp_path, fake, clock):
    return mod("runtime").Runtime(Settings(), secrets(WPBRUECKE_KEY1="pw pw pw"), tmp_path, transport=fake,
                                  wporg_fetch=lambda u: {"offers": [{"current": "7.1.4"}]} if "core" in u else {"version": "5.3"},
                                  clock=lambda: clock[0])


def test_clean_tick_is_silent_and_a_down_site_is_reported_once_then_back(tmp_path):
    fake, clock = FakeWP(), [1000.0]
    rt = make(tmp_path, fake, clock)
    rt.sites.create({"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1, "label": "Test"}, "person")
    tick = mod("tick")
    first = tick.run_tick(rt, now=clock[0])
    assert first["clean"] and tick.format_text(first) == ""
    fake.down = True
    clock[0] += 900
    down = tick.run_tick(rt, now=clock[0])
    assert len(down["lines"]) == 1 and down["lines"][0].startswith("Test is down since") and "did not answer" in down["lines"][0]
    clock[0] += 900
    assert tick.run_tick(rt, now=clock[0])["clean"]  # still down: no repeat within 6 h
    clock[0] += 7 * 3600
    assert "is down since" in tick.run_tick(rt, now=clock[0])["lines"][0]
    fake.down = False
    clock[0] += 900
    back = tick.run_tick(rt, now=clock[0])
    assert back["lines"][0].startswith("Test is back")
    assert tick.format_text(back).startswith("WPBrücke:\n- Test is back")


def test_updates_and_desk_items_are_reported_when_they_grow(tmp_path):
    fake, clock = FakeWP(), [1000.0]
    rt = make(tmp_path, fake, clock)
    rt.sites.create({"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1}, "person")
    tick = mod("tick")
    assert tick.run_tick(rt, now=1000)["clean"]
    rt.wporg = mod("facts").WporgVersions(rt.store, fetch=lambda u: {"offers": [{"current": "7.2.0"}]} if "core" in u else {"version": "9.0"},
                                          clock=lambda: 1e12)
    post = fake.add_post(status="draft", title="Draft")
    mod("desk").Desk(rt.store).create(site="s", site_url="https://test.example", kind="new_post", wp_type="post",
                                      wp_id=post["id"], title="Draft")
    lines = tick.run_tick(rt, now=2000)["lines"]
    assert any("3 update(s) available (WordPress 7.2.0, akismet, twentytwentyfive)" in l for l in lines), lines
    assert any("1 item(s) wait for you" in l for l in lines)
    assert tick.run_tick(rt, now=3000)["clean"]


def test_tick_reads_back_a_publish_done_in_wordpress(tmp_path):
    fake, clock = FakeWP(), [1000.0]
    rt = make(tmp_path, fake, clock)
    rt.sites.create({"id": "s", "url": "https://test.example", "login": "admin", "key_slot": 1}, "person")
    post = fake.add_post(status="draft")
    desk = mod("desk").Desk(rt.store)
    item = desk.create(site="s", site_url="https://test.example", kind="new_post", wp_type="post", wp_id=post["id"], title="x")
    fake.posts[post["id"]]["status"] = "publish"
    mod("tick").run_tick(rt, now=1000)
    assert desk.get(item["id"])["state"] == "published"
    assert not [r for r in fake.requests if r["method"] != "GET"]  # the tick only reads


def test_slow_site_and_digest_off(tmp_path):
    fake, clock = FakeWP(), [1000.0]
    rt = make(tmp_path, fake, clock)
    rt.sites.create({"id": "s", "url": "https://test.example"}, "person")
    tick = mod("tick")
    state = rt.store.path("tick", "state.json")
    rt.store.write_json(state, {"s": {"reachable": True, "rest_hist": [100, 120, 110], "updates": 0, "waiting": 0}})
    import time as _t
    real = fake.default

    def slow(method, parsed, headers, body):
        if parsed.path == "/wp-json/":
            _t.sleep(1.6)
        return real(method, parsed, headers, body)
    fake.default = slow
    lines = tick.run_tick(rt, now=1000)["lines"]
    assert lines and "is slow: REST" in lines[0] and "usually 110 ms" in lines[0]
    rt.store.write_json(state, {"s": {"reachable": True, "rest_hist": [100], "updates": 0, "waiting": 0}})
    assert tick.run_tick(rt, now=2000, digest=False)["clean"]


def test_busy_tick_returns_at_once(tmp_path):
    fake, clock = FakeWP(), [1000.0]
    rt = make(tmp_path, fake, clock)
    with rt.store.lock("tick"):
        r = mod("tick").run_tick(rt, now=1)
    assert r["busy"] and mod("tick").format_text(r) == ""
