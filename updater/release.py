"""The latest release of AgentKennel, checked at most once a day.

On GitHub: the releases API (the release the maintainer published, not just any tag),
without a token. The answer's ETag is kept and sent back, so an unchanged answer (304)
does not count against GitHub's limit of 60 requests an hour per address. After an error
the next try is an hour later; after a rate limit, when GitHub says it resets. The
release's CHANGELOG.md comes from raw.githubusercontent.com (no API limit).

Any other remote (a mirror, a local bare repo in the tests): `git ls-remote --tags`,
the highest vX.Y.Z tag, and its CHANGELOG.md after fetching that tag.

check() takes the cached answer (a dict, kept in .sessions/update/latest.json) and returns
the new one; it never raises."""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request

from updater import versions

DEFAULT_REPO = "https://github.com/yanhs/agentkennel.git"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
CHECK_EVERY = 24 * 3600          # the daily check
RETRY_AFTER_ERROR = 3600
MIN_GAP = 600                    # "Check now" by hand: at most once in ten minutes
CHANGELOG_MAX = 256 * 1024
UA = "AgentKennel-updater"
_SLUG = re.compile(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


class CheckError(Exception):
    def __init__(self, msg, retry_at=None):
        super().__init__(msg)
        self.retry_at = retry_at


def github_slug(url):
    m = _SLUG.match((url or "").strip())
    return m.group(1) if m else None


def http_get(url, headers):
    """(status, headers, body) — urllib, 15 s, never raises on an HTTP status."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read(CHANGELOG_MAX + 1)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""


def _git(args, run=None, timeout=60):
    run = run or subprocess.run
    r = run(["git", *args], capture_output=True, text=True, timeout=timeout,
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/local/bin:/usr/bin:/bin",
                 "HOME": "/nonexistent"})
    if r.returncode != 0:
        raise CheckError(f"git {args[0]} failed: {(r.stderr or '').strip()[:200]}")
    return r.stdout


def latest_git(url, run=None):
    """The highest vX.Y.Z tag on a git remote."""
    tags = []
    for line in _git(["ls-remote", "--tags", "--refs", url], run).splitlines():
        ref = line.split("\t")[-1]
        tag = ref[len("refs/tags/"):] if ref.startswith("refs/tags/") else ""
        if versions.parse(tag) and tag.startswith("v"):
            tags.append(tag)
    if not tags:
        raise CheckError("no release tags on the remote")
    return max(tags, key=versions.parse)


def changelog_git(repo_dir, url, tag, run=None):
    _git(["-C", repo_dir, "fetch", "-q", "--no-tags", url, f"+refs/tags/{tag}:refs/tags/{tag}"], run)
    return _git(["-C", repo_dir, "show", f"{tag}:CHANGELOG.md"], run)[:CHANGELOG_MAX]


def _status_error(status, headers, what):
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if status in (403, 429) and (h.get("x-ratelimit-remaining") == "0" or status == 429):
        reset = h.get("x-ratelimit-reset", "")
        return CheckError("GitHub's rate limit for this address; trying again later",
                          int(reset) if reset.isdigit() else None)
    return CheckError(f"{what}: HTTP {status}")


def latest_github(slug, etag, cached_tag, get):
    """(tag, etag); the cached tag when GitHub answers 304; None when there is no release."""
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if etag and cached_tag:
        headers["If-None-Match"] = etag
    status, h, body = get(f"{API}/repos/{slug}/releases/latest", headers)
    if status == 304:
        return cached_tag, etag
    if status == 404:
        return None, None
    if status != 200:
        raise _status_error(status, h, "GitHub's releases API")
    try:
        tag = json.loads(body or b"{}").get("tag_name") or ""
    except ValueError:
        raise CheckError("GitHub's answer is not JSON")
    if not versions.parse(tag):
        raise CheckError(f"the latest release is not a version number ({tag[:40]!r})")
    return tag, {k.lower(): v for k, v in h.items()}.get("etag")


def changelog_github(slug, tag, get):
    status, h, body = get(f"{RAW}/{slug}/{tag}/CHANGELOG.md", {"User-Agent": UA})
    if status != 200:
        raise _status_error(status, h, "CHANGELOG.md")
    return body[:CHANGELOG_MAX].decode("utf-8", "replace")


def due(cache, now, by_hand=False):
    if by_hand:
        return now - (cache.get("checked_at") or 0) >= MIN_GAP and now >= (cache.get("hold_until") or 0)
    return now >= (cache.get("next_at") or 0)


def check(cache, repo_url, install_dir, now=None, get=None, run=None, by_hand=False):
    """The cache after asking, when due (else unchanged). Never raises."""
    now = time.time() if now is None else now
    cache = dict(cache or {})
    if not due(cache, now, by_hand):
        return cache
    get = get or http_get
    slug = github_slug(repo_url)
    try:
        if slug:
            tag, etag = latest_github(slug, cache.get("etag"), cache.get("tag"), get)
            if tag is None:                       # no published release: the tags
                tag, etag = latest_git(repo_url, run), None
        else:
            tag, etag = latest_git(repo_url, run), None
        if tag != cache.get("tag") or not cache.get("changelog"):
            cache["changelog"] = (changelog_github(slug, tag, get) if slug
                                  else changelog_git(install_dir, repo_url, tag, run))
        cache.update(tag=tag, version=versions.plain(tag), etag=etag, error="",
                     checked_at=now, next_at=now + CHECK_EVERY, hold_until=0)
    except (CheckError, OSError, ValueError, subprocess.SubprocessError) as e:
        retry = getattr(e, "retry_at", None)
        nxt = max(retry or 0, now + RETRY_AFTER_ERROR)
        cache.update(error=str(e)[:300] or type(e).__name__, checked_at=now, next_at=nxt,
                     hold_until=retry or 0)
    return cache
