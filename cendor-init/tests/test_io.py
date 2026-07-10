from cendor_init.io import MARKER_BEGIN, MARKER_END, upsert_managed


def test_creates_lone_block_when_no_file():
    content, kind = upsert_managed(None, "BODY")
    assert kind == "created"
    assert MARKER_BEGIN in content
    assert "BODY" in content
    assert content.endswith("\n")


def test_appends_after_existing_user_content():
    content, kind = upsert_managed("# My notes\n\nkeep me", "BODY")
    assert kind == "appended"
    assert content.startswith("# My notes")
    assert "keep me" in content
    assert MARKER_BEGIN in content


def test_replaces_only_managed_region_preserving_surrounding_text():
    first, _ = upsert_managed("TOP\n\n_footer_", "V1")
    assert "V1" in first
    second, kind = upsert_managed(first, "V2")
    assert kind == "updated"
    assert "V2" in second and "V1" not in second
    assert "TOP" in second and "_footer_" in second
    assert second.count(MARKER_BEGIN) == 1
    assert second.count(MARKER_END) == 1


def test_idempotent_same_body_twice():
    once, _ = upsert_managed("x", "BODY")
    twice, _ = upsert_managed(once, "BODY")
    assert twice == once
