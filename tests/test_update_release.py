"""The daily check for a newer release: GitHub's releases API without a token (an ETag, so an
unchanged answer costs no rate limit), at most once a day, an hour's pause after an error and
until GitHub's reset after a rate limit; any other git remote is read with `git ls-remote`."""
import json
import subprocess

import pytest

from updater import release as R

SLUG = "yanhs/agentkennel"
URL = "https://github.com/yanhs/agentkennel.git"
CHANGELOG = "# Changelog\n\n## v2.8.0 — new\n\n- New.\n\n## v2.7.2 — old\n\n- Old.\n"


class FakeGitHub:
    def __init__(self, tag="v2.8.0", etag='"e1"', changelog=CHANGELOG):
        self.tag, self.etag, self.changelog = tag, etag, changelog
        self.calls = []
        self.status = None            # force an answer: (status, headers, body)

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.status:
            return self.status
        if url == f"{R.API}/repos/{SLUG}/releases/latest":
            if headers.get("If-None-Match") == self.etag:
                return 304, {}, b""
            return 200, {"ETag": self.etag}, json.dumps({"tag_name": self.tag}).encode()
        if url == f"{R.RAW}/{SLUG}/{self.tag}/CHANGELOG.md":
            return 200, {}, self.changelog.encode()
        return 404, {}, b"{}"


def test_slug_is_read_from_github_addresses_only():
    assert R.github_slug("https://github.com/yanhs/agentkennel.git") == SLUG
    assert R.github_slug("https://github.com/yanhs/agentkennel") == SLUG
    assert R.github_slug("git@github.com:yanhs/agentkennel.git") == SLUG
    assert R.github_slug("/srv/git/agentkennel.git") is None
    assert R.github_slug("https://gitlab.com/x/y.git") is None


def test_a_check_finds_the_release_and_its_changelog():
    gh = FakeGitHub()
    c = R.check({}, URL, "/nowhere", now=1000, get=gh)
    assert c["version"] == "2.8.0" and c["tag"] == "v2.8.0"
    assert c["changelog"] == CHANGELOG
    assert c["checked_at"] == 1000 and c["next_at"] == 1000 + R.CHECK_EVERY
    assert not c.get("error")
    ua = gh.calls[0][1]
    assert "User-Agent" in ua and "Authorization" not in ua      # no token, ever


def test_an_unchanged_release_is_asked_with_its_etag_and_not_downloaded_again():
    gh = FakeGitHub()
    c = R.check({}, URL, "/nowhere", now=1000, get=gh)
    gh.calls.clear()
    c2 = R.check(c, URL, "/nowhere", now=1000 + R.CHECK_EVERY, get=gh)
    assert [u for u, _ in gh.calls] == [f"{R.API}/repos/{SLUG}/releases/latest"]
    assert gh.calls[0][1]["If-None-Match"] == '"e1"'
    assert c2["version"] == "2.8.0" and c2["changelog"] == CHANGELOG


def test_not_due_means_no_request_at_all():
    gh = FakeGitHub()
    c = R.check({}, URL, "/nowhere", now=1000, get=gh)
    gh.calls.clear()
    assert R.check(c, URL, "/nowhere", now=2000, get=gh) == c
    assert gh.calls == []


def test_asking_by_hand_is_allowed_after_ten_minutes():
    gh = FakeGitHub()
    c = R.check({}, URL, "/nowhere", now=1000, get=gh)
    gh.calls.clear()
    assert R.check(c, URL, "/nowhere", now=1000 + 60, get=gh, by_hand=True) == c
    assert gh.calls == []
    R.check(c, URL, "/nowhere", now=1000 + R.MIN_GAP, get=gh, by_hand=True)
    assert gh.calls


def test_an_error_keeps_the_last_answer_and_waits_an_hour():
    gh = FakeGitHub()
    c = R.check({}, URL, "/nowhere", now=1000, get=gh)
    gh.status = (500, {}, b"")
    t = 1000 + R.CHECK_EVERY
    c2 = R.check(c, URL, "/nowhere", now=t, get=gh)
    assert c2["version"] == "2.8.0"                       # still known
    assert "500" in c2["error"]
    assert c2["next_at"] == t + R.RETRY_AFTER_ERROR


def test_a_rate_limit_waits_for_githubs_reset():
    gh = FakeGitHub()
    t = 5000
    gh.status = (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(t + 7200)}, b"{}")
    c = R.check({}, URL, "/nowhere", now=t, get=gh)
    assert "rate limit" in c["error"]
    assert c["next_at"] == t + 7200


def test_a_network_failure_is_an_error_not_a_crash():
    def broken(url, headers):
        raise OSError("no route to host")
    c = R.check({}, URL, "/nowhere", now=10, get=broken)
    assert "no route to host" in c["error"] and not c.get("version")


def test_no_published_release_falls_back_to_the_tags(monkeypatch):
    gh = FakeGitHub()
    gh.status = None
    monkeypatch.setattr(R, "latest_git", lambda url, run=None: "v2.7.9")
    def get(url, headers):
        if "/releases/latest" in url:
            return 404, {}, b"{}"
        return gh(url.replace("v2.7.9", "v2.8.0"), headers)
    c = R.check({}, URL, "/nowhere", now=10, get=get)
    assert c["version"] == "2.7.9"


# ── another remote: git ls-remote (the end-to-end tests use a local bare repo) ──────────
def git(*a, cwd=None):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd or "/tmp"), "PATH": "/usr/bin:/bin"})


@pytest.fixture
def remote(tmp_path):
    src, bare = tmp_path / "src", tmp_path / "remote.git"
    src.mkdir()
    git("init", "-q", "-b", "master", cwd=src)
    for v in ("2.7.2", "2.10.0", "2.9.1"):
        (src / "CHANGELOG.md").write_text(f"## v{v} — t\n\n- x\n")
        git("add", "CHANGELOG.md", cwd=src)
        git("commit", "-q", "-m", v, cwd=src)
        git("tag", f"v{v}", cwd=src)
    git("tag", "v3.0.0-rc1", cwd=src)                     # not a release
    git("tag", "nightly", cwd=src)
    git("clone", "-q", "--bare", str(src), str(bare), cwd=tmp_path)
    clone = tmp_path / "install"
    git("clone", "-q", str(bare), str(clone), cwd=tmp_path)
    return bare, clone


def test_ls_remote_picks_the_highest_release_tag(remote):
    bare, _ = remote
    assert R.latest_git(str(bare)) == "v2.10.0"


def test_a_check_against_a_plain_git_remote(remote):
    bare, clone = remote
    c = R.check({}, str(bare), str(clone), now=10)
    assert c["version"] == "2.10.0"
    assert c["changelog"].startswith("## v2.10.0")
