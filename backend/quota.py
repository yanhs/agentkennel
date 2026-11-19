"""The Claude allowance (/api/quota), a guest's own Claude key (/api/quota/claude) and today's
tokens by terminal (/api/quota/terminals).

Part of status_server (the facade re-exports every name here)."""

import http.client
import json
import threading
import time
import os
import re
import secrets
import glob
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse
from datetime import datetime
import library

from backend.library_api import lib_path, LibError


# ── The Claude allowance: the 5-hour window and the week (GET /api/quota, 30.10.2026) ──
# Like Claude's /usage, for whoever runs this dashboard:
# - a guest's sandbox: Claude goes through the host's turnstile (ANTHROPIC_BASE_URL) with
#   the guest's pass (ANTHROPIC_AUTH_TOKEN); the turnstile's GET /guest/usage has the
#   guest's own numbers. The pass never goes to the page.
# - the owner's install: one tiny request to Anthropic with the subscription's OAuth token
#   (~/.claude/oauth.env) answers with anthropic-ratelimit-unified-* headers — the same
#   numbers /usage shows. At most once per QUOTA_PROBE_TTL, in a background thread: this
#   server answers one request at a time and the page must never wait for Anthropic.
# Anywhere else: {"available": false}. The shared contract (turnstile, dashboard, page):
# pr/terminal-dev/.progress-usage-limits-disk.md.
QUOTA_ROUTE = "/api/quota"
QUOTA_TERMS_ROUTE = "/api/quota/terminals"
QUOTA_TTL = 20                  # seconds a guest's answer is reused (the page asks once a minute)
QUOTA_PROBE_TTL = 600           # the owner's probe: at most once per 10 minutes
QUOTA_RETRY = 120               # after a failed fetch, try again this soon
QUOTA_PROBE_URL = "https://api.anthropic.com/v1/messages"
QUOTA_PROBE_MODEL = "claude-haiku-4-5"
QUOTA_OAUTH_ENV = os.path.join("~", ".claude", "oauth.env")
_QUOTA_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
_OAUTH_LINE = re.compile(r"^\s*(?:export\s+)?CLAUDE_CODE_OAUTH_TOKEN\s*=\s*(.*?)\s*$")


def quota_oauth_token():
    """The subscription's token: CLAUDE_CODE_OAUTH_TOKEN in ~/.claude/oauth.env (a shell
    file, lines may start with `export `), else the environment's. None when neither."""
    try:
        with open(os.path.expanduser(QUOTA_OAUTH_ENV), encoding="utf-8") as f:
            for line in f:
                m = _OAUTH_LINE.match(line)
                if m:
                    v = m.group(1)
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                        v = v[1:-1]
                    if v.strip():
                        return v.strip()
    except (OSError, UnicodeDecodeError):
        pass
    return (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip() or None


def quota_probe(token, url=None):
    """One token of Haiku with the subscription's token -> (HTTP status, headers). The
    headers carry the subscription's usage even on a 429 (at the limit). OSError when
    Anthropic cannot be reached."""
    body = json.dumps({"model": QUOTA_PROBE_MODEL, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "."}]}).encode()
    req = urllib.request.Request(url or QUOTA_PROBE_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}


def _quota_number(v, kind):
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v < 1e12):
        return None
    return float(v) if kind is float else int(v)


def _quota_window(utilization, resets_at):
    u, r = _quota_number(utilization, float), _quota_number(resets_at, int)
    return None if u is None or r is None else {"utilization": u, "resets_at": r}


def quota_from_headers(headers):
    """The anthropic-ratelimit-unified-{5h,7d}-{utilization,reset} headers ->
    {"five_hour": {...}, "seven_day": {...}}, or None when the week is missing or anything is
    garbage. No 5h headers at all = no 5-hour window open: 0, no reset."""
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    out = {}
    for key, tag in _QUOTA_WINDOWS:
        u = h.get(f"anthropic-ratelimit-unified-{tag}-utilization")
        r = h.get(f"anthropic-ratelimit-unified-{tag}-reset")
        if key == "five_hour" and u is None and r is None:
            # no 5-hour window open: Anthropic sends only the week (seen 02.11.2026)
            out[key] = {"utilization": 0.0, "resets_at": 0}
            continue
        w = _quota_window(u, r)
        if w is None:
            return None
        out[key] = w
    return out


def quota_from_turnstile(d):
    """GET /guest/usage's body -> {"five_hour": {...}, "seven_day": {...}}, or None."""
    if not isinstance(d, dict):
        return None
    out = {}
    for key, _tag in _QUOTA_WINDOWS:
        w = d.get(key)
        if not isinstance(w, dict) or isinstance(w.get("utilization"), str) \
                or isinstance(w.get("resets_at"), str):
            return None
        w = _quota_window(w.get("utilization"), w.get("resets_at"))
        if w is None:
            return None
        out[key] = w
    return out


QUOTA_CLAUDE_ROUTE = "/api/quota/claude"
QUOTA_CLAUDE_TIMEOUT = 30          # the turnstile checks a key with Anthropic: up to 20 s
QUOTA_CLAUDE_BODY_MAX = 8 * 1024   # the turnstile's own cap on POST /guest/claude
_CLAUDE_MODES = ("owner_key", "own", "owner_sub")


def quota_claude_view(c):
    """The turnstile's claude_view, known fields only (it never carries a secret, and
    this keeps it so whatever the turnstile adds), or None when there is none."""
    if not isinstance(c, dict) or c.get("mode") not in _CLAUDE_MODES:
        return None
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    usd = c.get("usd")
    if isinstance(usd, dict) and num(usd.get("used")) is not None and num(usd.get("limit")) is not None:
        usd = {"used": usd["used"], "limit": usd["limit"], "resets_at": num(usd.get("resets_at"))}
    else:
        usd = None
    last4 = c.get("last4")
    out = {"mode": c["mode"],
           "owner_mode": c.get("owner_mode") if c.get("owner_mode") in _CLAUDE_MODES else None,
           "has_key": c.get("has_key") is True,
           "last4": last4 if isinstance(last4, str) and len(last4) <= 4 else None,
           "usd": usd, "usd_week": num(c.get("usd_week"))}
    if c.get("error") is True:
        out["error"] = True
    return out


def quota_claude_body(raw):
    """The dialog's body, checked like the turnstile checks it: exactly {"secret": str}
    or {"back": true}. The secret itself (for scrubbing), or LibError(400)."""
    try:
        body = json.loads(raw)
    except ValueError:
        raise LibError(400, "body is not JSON")
    if isinstance(body, dict) and set(body) == {"back"} and body["back"] is True:
        return None
    if isinstance(body, dict) and set(body) == {"secret"} and isinstance(body["secret"], str):
        return body["secret"]
    raise LibError(400, 'send exactly {"secret": "..."} or {"back": true}')


def quota_claude_post(base, token, raw, secret):
    """POST <turnstile>/guest/claude with the pass -> (status, JSON-able answer). Neither
    the pass nor the secret ever comes back, and nothing here is logged."""
    req = urllib.request.Request(base + "/guest/claude", data=raw, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=QUOTA_CLAUDE_TIMEOUT) as r:
            status, text = r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read(65536)
    except (OSError, ValueError, http.client.HTTPException) as e:
        return 502, {"error": f"the turnstile did not answer ({type(e).__name__})"}
    if status == 204 or not 200 <= status <= 599:
        return 502, {"error": f"the turnstile answered HTTP {status}"}
    try:
        answer = json.loads(text)
    except ValueError:
        return 502, {"error": f"the turnstile's answer ({status}) is not JSON"}
    if isinstance(answer, dict) and "claude" in answer:     # the same view as GET /api/quota
        answer = dict(answer, claude=quota_claude_view(answer["claude"]))
    out = json.dumps(answer, ensure_ascii=False)
    for s in (token, secret):
        if s and len(s) >= 8:
            out = out.replace(json.dumps(s, ensure_ascii=False)[1:-1], "…")
    try:
        return status, json.loads(out)
    except ValueError:
        return 502, {"error": "the turnstile's answer could not be passed on"}


class ClaudeJobs:
    """The key checks behind POST /api/quota/claude. The panel's server has one thread and
    a check takes up to 20 s, so each runs in its own daemon thread and the page asks
    GET /api/quota/claude?job=<id>. A job record holds only {at, done, status, body}: the
    secret lives in the thread's locals alone. Results are kept JOB_TTL seconds; at most
    MAX_PENDING checks run at once."""
    JOB_TTL = 120
    MAX_PENDING = 3

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.clock = time.monotonic       # tests put a fake clock here
        self.post = quota_claude_post     # … and may put a fake turnstile call here

    def _prune(self, now):
        for k in [k for k, j in self.jobs.items() if now - j["at"] > self.JOB_TTL]:
            del self.jobs[k]

    def start(self, base, token, raw, secret):
        """A new job's id, or None when MAX_PENDING checks already run."""
        with self.lock:
            now = self.clock()
            self._prune(now)
            if sum(1 for j in self.jobs.values() if not j["done"]) >= self.MAX_PENDING:
                return None
            job = secrets.token_urlsafe(16)
            self.jobs[job] = {"at": now, "done": False, "status": None, "body": None}
        threading.Thread(target=self._run, args=(job, base, token, raw, secret),
                         daemon=True, name="quota-claude").start()
        return job

    def _run(self, job, base, token, raw, secret):
        try:
            status, body = self.post(base, token, raw, secret)
        except Exception as e:                       # never the body: only what failed
            status, body = 502, {"error": f"the check failed ({type(e).__name__})"}
        del raw, secret
        if status == 200 and isinstance(body, dict):
            QUOTA.put_claude(body.get("claude"))
        with self.lock:
            if job in self.jobs:
                self.jobs[job].update(at=self.clock(), done=True, status=status, body=body)

    def get(self, job):
        """{"pending": True}, {"status", "body"}, or None (unknown or expired)."""
        with self.lock:
            self._prune(self.clock())
            j = self.jobs.get(job or "")
            if j is None:
                return None
            return {"status": j["status"], "body": j["body"]} if j["done"] else {"pending": True}


CLAUDE_JOBS = ClaudeJobs()


class Quota:
    """GET /api/quota. get() never waits for the network: it answers what is known and,
    when that is stale, starts one background fetch (never two at once)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.probe = quota_probe          # tests put a fake here
        self.clock = time.monotonic       # … and a fake clock here
        self.clear()

    def clear(self):
        with self.lock:
            self.key, self.at, self.data, self.busy = None, 0.0, None, False

    @staticmethod
    def source():
        """("guest", base, pass) behind a turnstile; ("proxy",) for a proxy without a
        pass; None when Claude talks to Anthropic itself (the owner's subscription)."""
        base = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
        token = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        host = (urlparse(base).hostname or "").lower()
        if not base or host == "anthropic.com" or host.endswith(".anthropic.com"):
            return None
        return ("guest", base, token) if token else ("proxy",)

    @staticmethod
    def fetch_guest(base, token):
        req = urllib.request.Request(base + "/guest/usage",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read(65536))
        except urllib.error.HTTPError as e:
            return {"available": False, "error": f"the turnstile answered HTTP {e.code}"}
        except (OSError, ValueError) as e:
            return {"available": False, "error": f"the turnstile did not answer ({type(e).__name__})"}
        w = quota_from_turnstile(d)
        if w is None:
            return {"available": False, "error": "the turnstile's answer has no usage numbers"}
        out = {"available": True, "source": "guest", **w}
        c = quota_claude_view(d.get("claude"))
        if c is not None:                  # an older turnstile says nothing: nothing new shows
            out["claude"] = c
        return out

    def put_claude(self, view):
        """After a switch in the dialog: the page sees the new Claude at once, and the
        numbers (they follow the mode) are fetched again on the next ask."""
        c = quota_claude_view(view)
        with self.lock:
            if c is not None and self.data is not None and self.data.get("available"):
                self.data = dict(self.data, claude=c)
                self.at = float("-inf")

    def fetch_subscription(self, token):
        try:
            status, headers = self.probe(token)
        except Exception as e:
            return {"available": False, "error": f"Anthropic did not answer ({type(e).__name__})"}
        w = quota_from_headers(headers)
        if w is None:
            if status not in (200, 429):
                return {"available": False, "error": f"Anthropic answered HTTP {status}"}
            return {"available": False, "error": f"Anthropic's answer ({status}) has no usage numbers"}
        return {"available": True, "source": "subscription", **w}

    def _run(self, key, fn):
        try:
            data = fn()
        except Exception as e:                       # never leave busy set
            data = {"available": False, "error": f"quota failed ({type(e).__name__})"}
        with self.lock:
            if self.key == key:
                self.data = data
            self.busy = False

    def get(self):
        src = self.source()
        if src is not None and src[0] == "proxy":
            return {"available": False}
        if src is None:
            token = quota_oauth_token()
            if not token:
                return {"available": False}
            key, ttl, fn = ("subscription",), QUOTA_PROBE_TTL, lambda: self.fetch_subscription(token)
        else:
            key, ttl, fn = src[:2], QUOTA_TTL, lambda: self.fetch_guest(src[1], src[2])
        with self.lock:
            if self.key != key:                      # another source: forget the old numbers
                self.key, self.at, self.data = key, 0.0, None
            data, now = self.data, self.clock()
            if data is not None and not data.get("available"):
                ttl = min(ttl, QUOTA_RETRY)
            if not self.busy and (data is None or now - self.at >= ttl):
                self.busy, self.at = True, now
                threading.Thread(target=self._run, args=(key, fn), daemon=True,
                                 name="quota-fetch").start()
        return dict(data) if data is not None else {"available": False}


QUOTA = Quota()


# ── Today by terminal (GET /api/quota/terminals, the owner's only) ──────────────────────
# Which terminals spent the most of the subscription today: weighted tokens of each
# terminal's conversation (and its subagents) since local midnight, each assistant
# message once (Claude Code writes one line per content block, all with the usage).
# Weights: roughly what each kind of token costs against the allowance.
QUOTA_TERMS_TTL = 300
QUOTA_TERMS_TOP = 8
QUOTA_WEIGHTS = (("input_tokens", 1.0), ("cache_creation_input_tokens", 1.25),
                 ("cache_read_input_tokens", 0.1), ("output_tokens", 5.0))


def _local_midnight(now):
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _record_time(s):
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo else None


def _weighted(usage):
    n = 0.0
    for k, w in QUOTA_WEIGHTS:
        v = usage.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            n += w * v
    return n


class QuotaTerminals:
    """Counts today's weighted tokens per terminal. Remembers how far each conversation
    file was read, so the next count reads only what was appended since."""

    def __init__(self):
        self.lock = threading.Lock()
        self.files = {}                   # path -> per-file state, for one day
        self.clock = time.monotonic       # tests put a fake clock here
        self.at, self.data, self.busy = 0.0, None, False

    def _file(self, path, day):
        try:
            st = os.stat(path)
        except OSError:
            self.files.pop(path, None)
            return 0.0
        if st.st_mtime < day:                        # untouched since midnight
            self.files.pop(path, None)
            return 0.0
        f = self.files.get(path)
        if f is None or f["day"] != day or f["ino"] != st.st_ino or st.st_size < f["pos"]:
            f = self.files[path] = {"day": day, "ino": st.st_ino, "pos": 0,
                                    "seen": set(), "total": 0.0}
        if st.st_size > f["pos"]:
            with open(path, "rb") as fh:
                fh.seek(f["pos"])
                chunk = fh.read(st.st_size - f["pos"])
            end = chunk.rfind(b"\n") + 1             # a half-written last line waits
            f["pos"] += end
            for line in chunk[:end].splitlines():
                if b'"assistant"' not in line or b'"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                msg = d.get("message") if isinstance(d, dict) else None
                if d.get("type") != "assistant" or not isinstance(msg, dict):
                    continue
                mid, usage = msg.get("id"), msg.get("usage")
                if not isinstance(mid, str) or not isinstance(usage, dict) or mid in f["seen"]:
                    continue
                t = _record_time(d.get("timestamp"))
                if t is None or t < day:
                    continue
                f["seen"].add(mid)
                f["total"] += _weighted(usage)
        return f["total"]

    def compute(self, lib_file, projects_root, now):
        day = _local_midnight(now)
        try:
            lib = library.load(lib_file)
        except Exception:
            return []
        rows, live, known = [], set(), set()
        for e in lib.get("sessions") or []:
            u = e.get("uuid")
            if not isinstance(u, str) or not e.get("cwd"):
                continue
            main = library.transcript_file(projects_root, e["cwd"], u)
            paths = [main] + sorted(glob.glob(os.path.join(
                glob.escape(os.path.join(os.path.dirname(main), u)), "subagents", "*.jsonl")))
            known.update(paths)
            if library.is_trashed(e):
                continue
            live.update(paths)
            n = round(sum(self._file(p, day) for p in paths))
            if n > 0:
                rows.append({"id": e.get("id"), "name": e.get("name") or e.get("id"),
                             "tokens_today": n})
        # everything else under the projects folder — a Telegram bot, headless helpers, other
        # tools — as one row (30.10.2026: the list was empty while the bot spent the most)
        root = glob.escape(str(projects_root))
        others = [p for p in glob.glob(os.path.join(root, "*", "*.jsonl"))
                  + glob.glob(os.path.join(root, "*", "*", "subagents", "*.jsonl")) if p not in known]
        live.update(others)
        n = round(sum(self._file(p, day) for p in others))
        if n > 0:
            rows.append({"id": "other", "name": "Other conversations", "tokens_today": n})
        for p in set(self.files) - live:             # gone or trashed: forget
            del self.files[p]
        rows.sort(key=lambda r: -r["tokens_today"])
        return rows[:QUOTA_TERMS_TOP]

    def _run(self):
        try:
            data = self.compute(lib_path(), library.claude_projects_root(), time.time())
        except Exception:
            data = []
        with self.lock:
            self.data, self.busy = data, False

    def get(self):
        with self.lock:
            now = self.clock()
            if not self.busy and (self.data is None or now - self.at >= QUOTA_TERMS_TTL):
                self.busy, self.at = True, now
                threading.Thread(target=self._run, daemon=True, name="quota-terminals").start()
            return {"terminals": list(self.data or [])}


QUOTA_TERMS = QuotaTerminals()


def quota_terminal_tokens(lib_file, projects_root, now):
    """One count from scratch: [{"id", "name", "tokens_today"}], the largest first."""
    return QuotaTerminals().compute(lib_file, projects_root, now)


def quota_terminals():
    """The owner's only: a guest's sandbox (or any proxy) gets an empty list."""
    if Quota.source() is not None:
        return {"terminals": []}
    return QUOTA_TERMS.get()
