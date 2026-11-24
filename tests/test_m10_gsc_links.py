import base64
import json

import pytest

from wpb_fakes import mod

KEY = json.dumps({"type": "service_account", "client_email": "wpb@proj.iam.gserviceaccount.com",
                  "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n", "private_key_id": "k1"})


def fake_signer(pem, data):
    assert "PRIVATE KEY" in pem
    return b"sig:" + data[:8]


class Google:
    def __init__(self, token_status=200, query_status=200, rows=None):
        self.calls = []
        self.token_status, self.query_status = token_status, query_status
        self.rows = rows if rows is not None else [{"keys": ["sourdough"], "clicks": 4, "impressions": 90, "ctr": 0.0444,
                                                    "position": 7.21}]

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.startswith("https://oauth2.googleapis.com/token"):
            return self.token_status, ({"access_token": "tok", "expires_in": 3599} if self.token_status == 200
                                       else {"error": "invalid_grant", "error_description": "Invalid JWT"})
        return self.query_status, {"rows": self.rows} if self.query_status == 200 else {"error": {"code": self.query_status}}


def test_no_key_no_property_bad_key_are_not_proven_with_reasons():
    gsc = mod("seo.gsc")
    g = Google()
    assert "no Search Console key" in gsc.row(None, "sc-domain:x.example", "https://x.example/a/", g)["detail"]
    assert "names no Search Console property" in gsc.row(KEY, "", "https://x.example/a/", g)["detail"]
    assert "not a service account JSON key" in gsc.row('{"type": "user"}', "sc-domain:x.example", "u", g)["detail"]
    assert g.calls == []


def test_jwt_shape_and_query_body():
    gsc = mod("seo.gsc")
    g = Google()
    r = gsc.row(KEY, "sc-domain:x.example", "https://x.example/a/", g, now=1_800_000_000, signer=fake_signer)
    assert r["verdict"] == "match" and "\"sourdough\" (4 clicks, 90 impressions, pos 7.2)" in r["detail"]
    _, url, headers, body = g.calls[0]
    assertion = body.decode().split("assertion=")[1]
    head, claims, sig = assertion.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    assert json.loads(base64.urlsafe_b64decode(pad(head))) == {"alg": "RS256", "typ": "JWT", "kid": "k1"}
    c = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert c["iss"] == "wpb@proj.iam.gserviceaccount.com" and c["scope"].endswith("webmasters.readonly")
    assert c["exp"] - c["iat"] == 3600
    _, qurl, qheaders, qbody = g.calls[1]
    assert qurl.endswith("/sites/sc-domain%3Ax.example/searchAnalytics/query")
    assert qheaders["Authorization"] == "Bearer tok"
    q = json.loads(qbody)
    assert q["dimensionFilterGroups"][0]["filters"][0] == {"dimension": "page", "operator": "equals",
                                                           "expression": "https://x.example/a/"}
    assert q["endDate"] < "2027-01-15" and q["startDate"] < q["endDate"]


def test_refusals_are_named():
    gsc = mod("seo.gsc")
    r = gsc.row(KEY, "sc-domain:x.example", "u", Google(token_status=400), signer=fake_signer)
    assert r["verdict"] == "not_proven" and "refused the service account sign-in (HTTP 400: Invalid JWT)" in r["detail"]
    r = gsc.row(KEY, "sc-domain:x.example", "u", Google(query_status=403), signer=fake_signer)
    assert "has no access to sc-domain:x.example; add it as a Restricted user" in r["detail"]
    r = gsc.row(KEY, "sc-domain:x.example", "u", Google(rows=[]), signer=fake_signer)
    assert r["verdict"] == "match" and r["detail"].startswith("no search queries")


def test_real_rs256_signature_verifies():
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    gsc = mod("seo.gsc")
    jwt = gsc.assertion({"client_email": "a@b", "private_key": pem}, 1_800_000_000)
    h, c, s = jwt.split(".")
    sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    pk.public_key().verify(sig, f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
    assert crypto


def test_site_fields_and_cockpit_link():
    sites = mod("sites")
    s = sites.validate({"id": "a", "url": "https://a.example", "gsc_property": "sc-domain:a.example",
                        "cockpit_app": "k0w8c4s0g8o4c"})
    assert sites.cockpit_url(s) == "hermes://open/coolifybruecke?app=k0w8c4s0g8o4c"
    assert sites.cockpit_url(sites.validate({"id": "b", "url": "https://b.example"})) is None
    with pytest.raises(sites.SiteError, match="gsc_property"):
        sites.validate({"id": "a", "url": "https://a.example", "gsc_property": "a.example"})
    with pytest.raises(sites.SiteError, match="cockpit_app"):
        sites.validate({"id": "a", "url": "https://a.example", "cockpit_app": "../x"})


def test_base64_key_is_accepted():
    gsc = mod("seo.gsc")
    assert gsc.parse_key(base64.b64encode(KEY.encode()).decode())["client_email"] == "wpb@proj.iam.gserviceaccount.com"
