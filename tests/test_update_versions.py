"""Updates (owner 19.11.2026): which release is newer, which one is a patch, and what changed
since the running version (the CHANGELOG.md sections the panel shows before an update)."""
from updater import versions as V

LOG = """# Changelog

All notable changes. Newest first.

## v2.8.1 — a fix

- **Fixed** the thing.

## v2.8.0 — updates from the panel

### Panel
- **Update available** next to the version.
- One click.

## v2.7.2 — the Server tab opens at once

- Faster.

## v2.7.1 — docs

- Docs.
"""


def test_parse_takes_release_tags_only():
    assert V.parse("v2.7.2") == (2, 7, 2)
    assert V.parse("2.10.0") == (2, 10, 0)
    for bad in ("2.7", "v2.7.2-rc1", "latest", "", None, "v2.7.2.1", "vx.1.2"):
        assert V.parse(bad) is None, bad


def test_newer_compares_numbers_not_text():
    assert V.newer("2.10.0", "2.9.9")
    assert V.newer("v3.0.0", "2.99.99")
    assert not V.newer("2.7.2", "2.7.2")
    assert not V.newer("2.7.1", "2.7.2")
    assert not V.newer("garbage", "2.7.2") and not V.newer("2.7.3", "garbage")


def test_a_patch_is_the_same_minor_with_a_higher_last_number():
    assert V.is_patch("2.7.2", "2.7.3")
    assert V.is_patch("2.7.2", "v2.7.9")
    assert not V.is_patch("2.7.2", "2.8.0")
    assert not V.is_patch("2.7.2", "3.0.0")
    assert not V.is_patch("2.7.2", "2.7.2")
    assert not V.is_patch("2.7.2", "2.7.1")


def test_sections_are_read_from_the_headings():
    s = V.sections(LOG)
    assert [x["version"] for x in s] == ["2.8.1", "2.8.0", "2.7.2", "2.7.1"]
    assert s[1]["title"] == "updates from the panel"
    assert "### Panel" in s[1]["body"] and "One click." in s[1]["body"]
    assert "v2.7.2" not in s[1]["body"]


def test_excerpt_is_what_changed_since_the_running_version_newest_first():
    ex = V.excerpt(LOG, current="2.7.2", latest="2.8.1")
    assert [x["version"] for x in ex] == ["2.8.1", "2.8.0"]


def test_excerpt_stops_at_the_latest_release():
    # master's CHANGELOG may already name a version that is not released yet
    ex = V.excerpt(LOG, current="2.7.1", latest="2.8.0")
    assert [x["version"] for x in ex] == ["2.8.0", "2.7.2"]


def test_excerpt_is_cut_to_a_size_the_panel_can_show():
    long_log = "## v2.8.0 — big\n\n" + "- line of text\n" * 2000 + "\n## v2.7.2 — x\n\n- y\n"
    ex = V.excerpt(long_log, current="2.7.2", latest="2.8.0", max_chars=500)
    assert len(ex) == 1
    assert len(ex[0]["body"]) <= 520 and ex[0]["body"].endswith("…")
    assert ex[0].get("cut") is True


def test_excerpt_of_nothing_is_empty():
    assert V.excerpt("", "2.7.2", "2.8.0") == []
    assert V.excerpt(None, "2.7.2", "2.8.0") == []
    assert V.excerpt(LOG, "2.8.1", "2.8.1") == []
