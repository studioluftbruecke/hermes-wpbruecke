"""The connection check (contract § 9): every state, what it sends, and what it records."""
import json
import xmlrpc.client

import pytest

from wpb_fakes import FakeWP, Multi, Settings, mod, secrets

DRAFTER = {"read": True, "edit_posts": True, "delete_posts": True, "upload_files": True, "level_1": True,
           "wpbruecke_drafter": True}
CONTRIB = {"read": True, "edit_posts": True, "delete_posts": True, "level_1": True, "contributor": True}
ADMIN = {"read": True, "edit_posts": True, "manage_options": True, "install_plugins": True, "administrator": True}
PW = "pw pw pw"


def setup(tmp_path, fake=None, url="https://test.example", user="hermes", password=PW, **settings):
    fake = fake if fake is not None else FakeWP(user="hermes", caps=DRAFTER, roles=["wpbruecke_drafter"])
    values = {"site1_url": url, "site1_user": user, **settings}
    rt = mod("runtime").Runtime(Settings(values), secrets(WPBRUECKE_KEY1=password, WPBRUECKE_KEY2=password),
                                tmp_path, transport=fake)
    return fake, rt


def check(rt, n=1):
    return mod("connect").run_check(rt, n, "/settings?tab=plugins&agent=wpbruecke&profile=p")


def keyed(fake):
    return [r for r in getattr(fake, "requests", []) if r["auth"]]


def test_check_result_shape_and_the_happy_path_records_the_pairing(tmp_path):
    fake, rt = setup(tmp_path)
    res = check(rt)
    assert set(res) == {"group", "site_id", "host", "state", "ok", "message", "next", "username", "role", "level",
                        "level_words", "caps_fp", "uploads", "connector", "access", "effective", "migrated",
                        "checked_at"}
    assert res["state"] == "needs_confirm" and res["ok"] and res["effective"] == "unconfirmed"
    assert res["message"] == ("Connected as hermes at test.example. WordPress lets this user write drafts with images. "
                              "Hermes will do exactly that.")
    assert res["next"] == {"label": "Confirm", "action": "confirm"}
    assert (res["group"], res["site_id"], res["host"]) == (1, "test-example", "test.example")
    assert (res["level"], res["role"], res["uploads"], res["username"]) == ("drafts", "wpbruecke_drafter", True, "hermes")
    assert len(res["caps_fp"]) == 12 and res["access"] is None and res["migrated"] is None
    rec = rt.records.get(1)
    assert rec.host == "test.example" and rec.fp == mod("groups").fingerprint(PW) and rec.app_password_uuid == "u"
    assert rt.site("test-example")["username"] == "hermes"
    assert rt.records.last_check(1)["state"] == "needs_confirm"
    assert rt.pairing(rt.site("test-example")) == "paired"
    assert PW not in json.dumps(res) and PW not in json.dumps(rec.to_dict())


def test_contributor_level_words_say_no_images(tmp_path):
    fake, rt = setup(tmp_path, FakeWP(user="hermes", caps=CONTRIB, roles=["contributor"]))
    res = check(rt)
    assert res["level_words"] == "write drafts (no images: Contributor cannot upload)" and res["uploads"] is False


def test_an_administrator_is_refused_with_the_way_out(tmp_path):
    fake, rt = setup(tmp_path, FakeWP(user="hermes", caps=ADMIN, roles=["administrator"]))
    res = check(rt)
    assert res["state"] == "refused" and not res["ok"] and res["level"] is None
    assert "Full admin access is a separate plan" in res["message"]
    assert res["next"]["action"] == "settings" and res["next"]["href"].endswith("profile=p")


def test_the_reader_role_needs_connector_021(tmp_path):
    reader = {"read": True, "level_0": True, "read_private_posts": True, "read_private_pages": True,
              "wpbruecke_reader": True}
    fake = FakeWP(user="hermes", caps=reader, roles=["wpbruecke_reader"], namespaces=["wp/v2", "wpbruecke/v1"])
    fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.2.1"})
    _, rt = setup(tmp_path, fake)
    res = check(rt)
    assert (res["state"], res["level"], res["connector"]) == ("needs_confirm", "read", "0.2.1")
    fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.2.0"})
    res = check(rt)
    assert res["state"] == "refused" and "0.2.1 or newer" in res["message"]


def test_a_reader_with_a_second_role_is_refused_at_the_check(tmp_path):
    caps = {"read": True, "level_0": True, "read_private_posts": True, "read_private_pages": True,
            "edit_posts": True, "publish_posts": True, "publish_pages": True, "editor": True, "wpbruecke_reader": True}
    fake = FakeWP(user="hermes", caps=caps, roles=["wpbruecke_reader", "editor"], namespaces=["wp/v2", "wpbruecke/v1"])
    fake.routes[("GET", "/wp-json/wpbruecke/v1/info")] = FakeWP.j(200, {"version": "0.2.1"})
    _, rt = setup(tmp_path, fake)
    res = check(rt)
    assert (res["state"], res["level"]) == ("refused", None)
    assert "reader role and another role. Give it one role." in res["message"]


def test_not_https_sends_nothing(tmp_path):
    fake, rt = setup(tmp_path, url="http://test.example")
    res = check(rt)
    assert res["state"] == "not_https" and fake.requests == []


def test_loopback_http_may_carry_the_key(tmp_path):
    fake = FakeWP(host="127.0.0.1", user="hermes", caps=DRAFTER, roles=["wpbruecke_drafter"])
    _, rt = setup(tmp_path, fake, url="http://127.0.0.1")
    assert check(rt)["state"] == "needs_confirm" and keyed(fake)


def route_404_html(*_):
    return 404, {"Content-Type": "text/html"}, b"<html>not found</html>"


@pytest.mark.parametrize("name,routes,state,extra", [
    ("unreachable", None, "unreachable", None),
    ("http_auth", {("GET", "/wp-json/"): (401, {"WWW-Authenticate": 'Basic realm="staging"'}, b"")}, "http_auth", None),
    ("redirect", {("GET", "/wp-json/"): (301, {"Location": "https://www.test.example/wp-json/"}, b"")},
     "use_address", "https://www.test.example"),
    ("index_url", {("GET", "/wp-json/"): FakeWP.j(200, {"url": "https://www.test.example", "authentication":
                                                         {"application-passwords": {}}})},
     "use_address", "https://www.test.example"),
    ("app_passwords_off", {("GET", "/wp-json/"): FakeWP.j(200, {"name": "x", "authentication": []})},
     "app_passwords_off", None),
    ("no_rest", {("GET", "/wp-json/"): route_404_html}, "no_rest", None),
])
def test_states_before_the_key_send_nothing_with_it(tmp_path, name, routes, state, extra):
    fake = FakeWP(user="hermes", caps=DRAFTER)
    if routes is None:
        fake.down = True
    else:
        fake.routes.update(routes)
    _, rt = setup(tmp_path, fake)
    res = check(rt)
    assert res["state"] == state and not res["ok"] and res["message"]
    if extra:
        assert extra in res["message"]
    assert not keyed(fake) and rt.records.get(1) is None


def test_plain_permalinks_is_told_from_no_rest(tmp_path):
    fake = FakeWP(user="hermes", caps=DRAFTER)
    fake.routes[("GET", "/wp-json/")] = route_404_html

    def home(method, parsed, headers, body):
        if "rest_route" in parsed.query:
            return FakeWP.j(200, {"name": "x", "routes": {}})
        return 200, {}, b"<html></html>"
    fake.routes[("GET", "/")] = home
    _, rt = setup(tmp_path, fake)
    res = check(rt)
    assert res["state"] == "plain_permalinks" and "except Plain" in res["message"]


def test_group_states_without_a_full_group(tmp_path):
    fake, rt = setup(tmp_path, user="")
    assert check(rt)["state"] == "no_user"
    fake, rt = setup(tmp_path / "b", password=None)
    assert check(rt)["state"] == "no_password" and not keyed(fake)
    fake, rt = setup(tmp_path / "c", url="")
    assert check(rt)["state"] == "free"
    assert check(rt, 2)["next"]["action"] == "settings"


def test_a_second_group_on_the_same_host_is_a_duplicate(tmp_path):
    fake, rt = setup(tmp_path, site2_url="https://test.example", site2_user="hermes")
    res = check(rt, 2)
    assert res["state"] == "duplicate" and "Site 1" in res["message"] and not keyed(fake)


def test_wrong_credentials(tmp_path):
    fake, rt = setup(tmp_path, FakeWP(user="hermes", caps=DRAFTER, password="other password"))
    res = check(rt)
    assert res["state"] == "wrong_credentials" and rt.records.get(1) is None


def stripped(fake, xmlrpc_answer):
    fake.routes[("GET", "/wp-json/wp/v2/users/me")] = FakeWP.j(401, {"code": "rest_not_logged_in", "message": "no"})
    fake.routes[("POST", "/xmlrpc.php")] = xmlrpc_answer
    return fake


def test_header_stripped_is_told_from_a_wrong_password_by_xml_rpc(tmp_path):
    ok = (200, {"Content-Type": "text/xml"}, xmlrpc.client.dumps(({"user_id": "2"},), methodresponse=True).encode())
    fake, rt = setup(tmp_path, stripped(FakeWP(user="hermes", caps=DRAFTER), ok))
    res = check(rt)
    assert res["state"] == "header_stripped" and "SetEnvIf Authorization" in res["message"]
    call = next(r for r in fake.requests if r["path"] == "/xmlrpc.php")
    assert not call["auth"] and b"wp.getProfile" in call["body"]  # the credentials ride in the body

    fault = (200, {}, xmlrpc.client.dumps(xmlrpc.client.Fault(403, "Incorrect username or password."),
                                          methodresponse=True).encode())
    _, rt = setup(tmp_path / "b", stripped(FakeWP(user="hermes", caps=DRAFTER), fault))
    assert check(rt)["state"] == "wrong_credentials"

    off = (405, {}, b"XML-RPC server accepts POST requests only.")
    _, rt = setup(tmp_path / "c", stripped(FakeWP(user="hermes", caps=DRAFTER), off))
    res = check(rt)
    assert res["state"] == "cannot_tell" and "SetEnvIf" in res["message"] and "Site 1" in res["message"]


def test_no_role_on_this_site_of_a_network(tmp_path):
    fake, rt = setup(tmp_path, FakeWP(user="hermes", caps={}, roles=[]))
    assert check(rt)["state"] == "no_role_here"


def test_states_and_next_actions_live_in_one_table():
    connect = mod("connect")
    actions = {"settings", "check", "confirm", "open", "remove", "none"}
    assert all(s.action in actions for s in connect.STATES.values())
    for state in ("free", "no_user", "no_password", "host_changed", "not_https", "unreachable", "http_auth",
                  "plain_permalinks", "no_rest", "use_address", "app_passwords_off", "duplicate",
                  "wrong_credentials", "header_stripped", "cannot_tell", "no_role_here", "refused", "needs_confirm",
                  "confirmed", "lowered", "stopped", "group_empty"):
        assert state in connect.STATES


def test_a_password_change_is_checked_again_and_a_new_host_with_a_new_password_too(tmp_path):
    a, b = FakeWP(host="a.example", user="hermes", caps=DRAFTER), FakeWP(host="b.example", user="hermes", caps=DRAFTER)
    rt = mod("runtime").Runtime(Settings({"site1_url": "https://a.example", "site1_user": "hermes"}),
                                secrets(WPBRUECKE_KEY1=PW), tmp_path, transport=Multi(a, b))
    assert check(rt)["state"] == "needs_confirm"
    b.password = "new pw"
    rt2 = mod("runtime").Runtime(Settings({"site1_url": "https://b.example", "site1_user": "hermes"}),
                                 secrets(WPBRUECKE_KEY1="new pw"), tmp_path, transport=Multi(a, b))
    assert rt2.pairing(rt2.site("a-example")) == "password_changed"
    assert check(rt2)["state"] == "needs_confirm" and rt2.records.get(1).host == "b.example"
    assert rt2.pairing(rt2.site("a-example")) == "paired"
