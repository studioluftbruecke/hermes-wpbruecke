"""Access levels from the WordPress role (contract § 6), with the capability sets measured in P20."""
import pytest

from wpb_fakes import mod

SUBSCRIBER = {"read": True, "level_0": True, "subscriber": True}
CONTRIBUTOR = {**SUBSCRIBER, "edit_posts": True, "delete_posts": True, "level_1": True, "contributor": True}
DRAFTER = {**CONTRIBUTOR, "upload_files": True, "wpbruecke_drafter": True}
AUTHOR = {**CONTRIBUTOR, "upload_files": True, "publish_posts": True, "edit_published_posts": True,
          "delete_published_posts": True, "level_2": True, "author": True}
EDITOR = {**AUTHOR, "publish_pages": True, "edit_others_posts": True, "delete_others_posts": True,
          "unfiltered_html": True, "manage_categories": True, "moderate_comments": True, "read_private_posts": True,
          "edit_pages": True, "editor": True}
ADMIN = {**EDITOR, "manage_options": True, "edit_users": True, "install_plugins": True, "edit_theme_options": True,
         "administrator": True}
READER = {"read": True, "level_0": True, "read_private_posts": True, "read_private_pages": True,
          "wpbruecke_reader": True}       # connector 0.2.1 stores read caps only
READER_020 = {**READER, "edit_posts": True, "edit_others_posts": True}  # what 0.2.0 stored


@pytest.mark.parametrize("roles,caps,connector,level", [
    (["subscriber"], SUBSCRIBER, None, "read_public"),
    (["contributor"], CONTRIBUTOR, None, "drafts"),
    (["wpbruecke_drafter"], DRAFTER, None, "drafts"),
    (["author"], AUTHOR, None, "publish"),
    (["editor"], EDITOR, None, "publish"),
    (["administrator"], ADMIN, None, "refused"),
    (["editor"], {**EDITOR, "edit_theme_options": True}, None, "refused"),
    (["wpbruecke_reader"], READER, "0.2.1", "read"),
    (["wpbruecke_reader"], READER, "0.3.0", "read"),
    (["wpbruecke_reader"], READER_020, "0.2.0", "refused"),
    (["wpbruecke_reader"], READER, "0.1.0", "refused"),
    (["wpbruecke_reader"], READER, None, "refused"),
    ([], {}, None, "none"),
])
def test_classify_each_measured_role(roles, caps, connector, level):
    assert mod("access").classify(roles, caps, connector)[0] == level


def test_refusals_name_the_way_out():
    access = mod("access")
    assert access.classify(["administrator"], ADMIN)[1] == ("Use a user with one of the roles above. Full admin "
                                                             "access is a separate plan.")
    assert "connector 0.2.1 or newer" in access.classify(["wpbruecke_reader"], READER, "0.2.0")[1]


@pytest.mark.parametrize("other,caps", [("editor", EDITOR), ("author", AUTHOR), ("contributor", CONTRIBUTOR),
                                        ("subscriber", SUBSCRIBER)])
def test_a_reader_with_a_second_role_is_refused_before_its_publish_caps(other, caps):
    access = mod("access")
    level, reason = access.classify(["wpbruecke_reader", other], {**READER, **caps}, "0.2.1")
    assert (level, reason) == ("refused", "The Hermes user has the WPBrücke reader role and another role. "
                                          "Give it one role.")


def test_a_reader_with_admin_caps_is_still_refused_as_admin():
    access = mod("access")
    assert access.classify(["wpbruecke_reader", "administrator"], {**READER, **ADMIN}, "0.2.1")[1] == \
        access.REFUSED_ADMIN


def test_level_words_tell_contributor_from_drafter_and_author_from_editor():
    access = mod("access")
    assert access.level_words("drafts", DRAFTER) == "write drafts with images"
    assert access.level_words("drafts", CONTRIBUTOR) == "write drafts (no images: Contributor cannot upload)"
    assert access.level_words("publish", AUTHOR) == "write and publish its own posts"
    assert access.level_words("publish", EDITOR) == "write and publish all posts and pages"
    assert access.level_words("read") == "read posts, pages and drafts, never write"
    assert access.level_words("read_public") == "read what the public sees"


def test_caps_fingerprint_is_order_free_ignores_false_caps_and_role_names():
    access = mod("access")
    fp = access.caps_fingerprint(CONTRIBUTOR, ["contributor"])
    assert len(fp) == 12 and int(fp, 16) >= 0
    shuffled = dict(reversed(list(CONTRIBUTOR.items())))
    assert access.caps_fingerprint({**shuffled, "publish_posts": False}, ["contributor"]) == fp
    assert access.caps_fingerprint(DRAFTER, ["wpbruecke_drafter"]) != fp


def granted(level, caps, roles):
    return {"level": level, "role": roles[0], "caps_fingerprint": mod("access").caps_fingerprint(caps, roles),
            "confirmed_at": "2026-11-24T00:00:00Z"}


def test_effective_every_row():
    access = mod("access")
    fp = lambda caps, roles: access.caps_fingerprint(caps, roles)
    drafts = granted("drafts", DRAFTER, ["wpbruecke_drafter"])
    assert access.effective(None, "drafts") == ("unconfirmed", None)
    assert access.effective(drafts, "refused") == ("stopped", None)
    assert access.effective(drafts, "none") == ("stopped", None)
    assert access.effective(drafts, "publish", fp(AUTHOR, ["author"])) == ("stopped", None)  # wider
    assert access.effective(drafts, "drafts", fp(CONTRIBUTOR, ["contributor"])) == ("stopped", None)  # caps changed
    assert access.effective(drafts, "read_public", fp(SUBSCRIBER, ["subscriber"])) == ("lowered", "read_public")
    assert access.effective(drafts, "drafts", fp(DRAFTER, ["wpbruecke_drafter"])) == ("confirmed", "drafts")
    assert "more rights" in access.stop_reason(drafts, "publish", fp(AUTHOR, ["author"]))
    assert "changed" in access.stop_reason(drafts, "drafts", fp(CONTRIBUTOR, ["contributor"]))


def test_required_levels_and_refusal_codes():
    access = mod("access")
    assert access.required_level("public_read") is None
    assert access.required_level("keyed_read") == "read"
    assert access.required_level("draft_create") == access.required_level("upload_media") == "drafts"
    assert access.required_level("publish") == access.required_level("apply") == "publish"
    with pytest.raises(KeyError):
        access.required_level("delete_site")
    assert access.allows(None, "unconfirmed", "draft_create") == "level_unconfirmed"
    assert access.allows(None, "stopped", "keyed_read") == "level_stopped"
    assert access.allows("read", "confirmed", "draft_create") == "level_read"
    assert access.allows("drafts", "confirmed", "publish") == "level_drafts"
    assert access.allows("drafts", "confirmed", "upload_media", can_upload=False) == "level_no_uploads"
    assert access.allows("publish", "confirmed", "schedule") is None
    assert access.allows(None, "unconfirmed", "public_read") is None
    assert access.allows("read_public", "lowered", "keyed_read") == "level_public"
    assert set(access.REFUSALS) == {"level_public", "level_read", "level_drafts", "level_unconfirmed", "level_stopped",
                                    "level_no_uploads"}
