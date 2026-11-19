"""The Server tab's Guests block (/api/guests): drives the optional guests module.

Part of status_server (the facade re-exports every name here)."""

import json
import subprocess
import sys
import threading
import time
import os
import re
import hmac
import secrets
from urllib.parse import urlparse
from pathlib import Path

from backend import _HERE
from backend.telegram import tg_load
from backend.library_api import LibError
from backend.server_tab import SERVER_COLLECTOR


# ── Server page, Guests block: /api/guests (2.0) ────────────────────────────
# The optional guests module (<repo>/guests, GUESTS_DIR overrides) does the work; the
# panel drives its command guests/guestctl.py. Every call runs in a background thread
# (this server answers one request at a time; `remove` alone takes ~90 s): GET returns
# the last list / checks / job results, refreshing the list when it is older than
# GUESTS_LIST_TTL. Its .env holds the bot token: the page only learns whether one is set.
GUESTS_ROUTE = "/api/guests"
GUESTS_LIST_TTL = 10
GUESTS_RULES_TIMEOUT = 45   # `guestctl rules` saves, then locks the sandbox (its exec: 20 s)
GUEST_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
GUESTS_ACTIONS = ("stop", "start", "remove", "limit", "web", "web-off")   # jobs
# Open (decision 29.10.2026): the owner signs in to a guest's dashboard without the guest's
# password. POST /api/guests/open gets a login signed by `guestctl.py session` and returns a
# one-time link to https://sb-<name>.<domain>/_owner-entry; the guest's nginx sends that one
# path here (GET /api/guests/enter), and the link is spent for the cookie on that address.
GUEST_ENTRY_PATH = "/_owner-entry"          # guestctl.OWNER_ENTRY
GUEST_ENTRY_ROUTE = "/api/guests/enter"     # guestctl.OWNER_PANEL_ENTRY
GUEST_ENTRY_TTL = 60                        # seconds an Open link stays good
GUEST_ENTRY_COOKIE_RE = re.compile(r"agents_session=[A-Za-z0-9_-]{1,64}\.[0-9]{1,12}\.[0-9a-f]{64}")
GUEST_ENTRY_SPENT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentKennel</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0d1117;color:#e6edf3;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
p{max-width:30em;padding:0 24px;text-align:center}</style></head><body>
<p>This sign-in link has already been used or has expired. Press <b>Open</b> next to the
guest on your AgentKennel panel to get a new one.</p></body></html>"""
# what the wizard sets; OWNER_TG_ID is not asked: the guests' admin is the owner of
# "Your bot" (the dashboard's Telegram page) — "Let in?" goes there (decision 27.10.2026)
_GUESTS_ENV_KEYS = {"bot_token": "GUEST_BOT_TOKEN", "domain": "GUESTS_DOMAIN"}
_GUESTS_NEEDED = ("GUEST_BOT_TOKEN", "OWNER_TG_ID", "GUESTS_DOMAIN")
_GUESTS_VALID = {
    "bot_token": re.compile(r"^[0-9]{5,15}:[A-Za-z0-9_-]{30,60}$"),
    "domain": re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"),
}


GUESTS_DEFAULT_DIR = Path(_HERE).resolve() / "guests"
DOCKERENV = Path("/.dockerenv")


def guests_dir():
    return Path(os.environ.get("GUESTS_DIR") or GUESTS_DEFAULT_DIR)


def guests_installed():
    """The module is here, and this is a server: inside Docker (a guest's own sandbox
    included) sandboxes can't be run, so no Guests block unless GUESTS_DIR says so."""
    if DOCKERENV.exists() and not os.environ.get("GUESTS_DIR"):
        return False
    return (guests_dir() / "guestctl.py").is_file()


def guests_env():
    """KEY=value lines of guests/.env (comments and order kept by guests_write_env)."""
    out = {}
    try:
        text = (guests_dir() / ".env").read_text()
    except OSError:
        return out
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def guests_write_env(values):
    path = guests_dir() / ".env"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = ["# AgentKennel guests module (written by the dashboard's Guests setup)"]
    left = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in left:
            lines[i] = f"{key}={left.pop(key)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    tmp = path.with_name(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def guests_admin():
    """The owner of "Your bot": {"bot_set", "owner_id"} — no token, no owner = not set."""
    cfg = tg_load()
    owner = str(cfg.get("owner") or "").strip() if isinstance(cfg, dict) else ""
    token = str(cfg.get("token") or "").strip() if isinstance(cfg, dict) else ""
    ok = bool(token) and bool(re.fullmatch(r"[1-9][0-9]{0,14}", owner))
    return {"bot_set": ok, "owner_id": owner if ok else ""}


class GuestsPanel:
    def __init__(self):
        self.lock = threading.Lock()
        self.guests = None            # last `list --json` result
        self.owner_key = {"set": False, "last4": None}
        self.owner_key_week_usd = 0.0
        self.settings = None          # last `defaults` result (may carry "error")
        self.list_at = 0.0
        self.list_error = None
        self.listing = False
        self.checks = None            # last `doctor --json` result
        self.checking = False
        self.jobs = {}                # name -> {action, started, done, ok, output}
        self.entries = {}             # Open links: ticket -> {host, cookie, max_age, until}

    @staticmethod
    def run(*args, timeout=180, input=None):
        """guestctl <args>; `input` is its stdin (a key goes there, never in argv)."""
        r = subprocess.run([sys.executable, str(guests_dir() / "guestctl.py"), *args],
                           capture_output=True, text=True, timeout=timeout, input=input,
                           cwd=str(guests_dir()))
        return r.returncode, r.stdout, r.stderr

    def _spawn(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _list(self):
        try:
            code, out, err = self.run("list", "--json", timeout=60)
            if code != 0:
                raise RuntimeError((err or out).strip()[-300:] or f"exit {code}")
            listed = json.loads(out)
            guests = listed.get("guests")
            if not isinstance(guests, list):
                raise RuntimeError("guestctl list: no guests list")
            ok = listed.get("owner_key")
            ok = ok if isinstance(ok, dict) else {}
            owner_key = {"set": ok.get("set") is True,           # only the known fields are copied
                         "last4": ok["last4"] if isinstance(ok.get("last4"), str) else None}
            spent = listed.get("owner_key_week_usd")
            spent = spent if isinstance(spent, (int, float)) and not isinstance(spent, bool) else 0.0
            try:
                dcode, dout, derr = self.run("defaults", timeout=30)
                if dcode != 0:
                    raise RuntimeError(self._scrub((derr or dout).strip()[-300:] or f"exit {dcode}"))
                settings = self._settings_view(json.loads(dout))
            except Exception as e:
                settings = {"error": self._scrub(str(e))[:300] or "defaults unreadable"}
            with self.lock:
                self.guests, self.list_error = guests, None
                self.owner_key, self.owner_key_week_usd = owner_key, spent
                self.settings = settings
        except Exception as e:
            with self.lock:
                self.list_error = str(e)[:300]
        finally:
            with self.lock:
                self.list_at, self.listing = time.time(), False

    def _doctor(self):
        try:
            code, out, err = self.run("doctor", "--json", timeout=60)
            checks = json.loads(out).get("checks")
            if not isinstance(checks, list):
                raise ValueError
        except Exception:
            checks = [{"id": "module", "title": "Guests module", "ok": False,
                       "detail": "guestctl doctor did not answer", "fix": ""}]
        with self.lock:
            self.checks, self.checking = checks, False

    def check(self):
        with self.lock:
            if self.checking:
                return
            self.checking = True
        self._spawn(self._doctor)

    def state(self):
        installed = guests_installed()
        env = guests_env() if installed else {}
        configured = installed and all(env.get(k) for k in _GUESTS_NEEDED)
        with self.lock:
            if configured and not self.listing and time.time() - self.list_at > GUESTS_LIST_TTL:
                self.listing = True
                self._spawn(self._list)
            return {"installed": installed, "configured": configured, "dir": str(guests_dir()),
                    "env": {"bot_token_set": bool(env.get("GUEST_BOT_TOKEN")),
                            "owner_id": env.get("OWNER_TG_ID", ""),
                            "domain": env.get("GUESTS_DOMAIN", "")},
                    "admin": guests_admin(),
                    "guests": self.guests if configured else None,
                    "list_error": self.list_error if configured else None,
                    "owner_key": dict(self.owner_key) if configured else None,
                    "owner_key_week_usd": self.owner_key_week_usd if configured else 0,
                    "settings": dict(self.settings) if configured and self.settings else None,
                    "agents": self.agents() if configured else {},
                    "checks": self.checks, "checking": self.checking,
                    "jobs": {n: dict(j) for n, j in self.jobs.items()}}

    def agents(self):
        """{guest: {"agents", "working"}}: the claude processes in each sandbox, counted by
        the server page's collector; a guest whose container it hasn't seen is left out."""
        try:
            seen = SERVER_COLLECTOR.container_agents()
        except Exception:
            return {}
        out = {}
        for g in self.guests or []:
            c = g.get("container") if isinstance(g, dict) else None
            if isinstance(c, str) and c in seen:
                out[g.get("name")] = seen[c]
        return out

    def setup(self, body):
        if not guests_installed():
            raise LibError(409, "the guests module is not in this AgentKennel")
        admin = guests_admin()
        if not admin["bot_set"]:
            raise LibError(409, "Set up your own Telegram bot first (the dashboard's Telegram "
                                "page): guests' requests to join go there")
        values = {"OWNER_TG_ID": admin["owner_id"]}
        for field, key in _GUESTS_ENV_KEYS.items():
            v = body.get(field)
            if v is None or v == "":
                continue                          # empty = keep what is there
            v = str(v).strip().lower() if field == "domain" else str(v).strip()
            if not _GUESTS_VALID[field].fullmatch(v):
                raise LibError(400, {"bot_token": "That doesn't look like a bot token (digits:letters, from @BotFather)",
                                     "domain": "A domain like example.com — no http://, no path"}[field])
            values[key] = v
        guests_write_env(values)
        self.check()
        return {"ok": True}

    def act(self, action, body):
        if action not in GUESTS_ACTIONS:
            raise LibError(404, "not found")
        name = body.get("name")
        if not isinstance(name, str) or not GUEST_NAME_RE.match(name):
            raise LibError(400, "bad guest name")
        args = [action, name]
        if action == "limit":
            limit = body.get("limit")
            if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= 10**10:
                raise LibError(400, "the limit is a positive number of tokens per week")
            args.append(str(limit))
        if not self.state()["configured"]:
            raise LibError(409, "the guests module is not set up")
        with self.lock:
            job = self.jobs.get(name)
            if job and not job["done"]:
                raise LibError(409, f"{name}: {job['action']} is still running")
            self.jobs[name] = job = {"action": action, "started": time.time(), "done": False,
                                     "ok": None, "output": ""}

        def work():
            try:
                code, out, err = self.run(*args)
                ok, output = code == 0, (out + err).strip()[-500:]
            except Exception as e:
                ok, output = False, str(e)[:500]
            with self.lock:
                job.update(done=True, ok=ok, output=output)
            self._list()                          # the list shows the result at once
        self._spawn(work)
        return {"ok": True}

    def _guest_name(self, body):
        name = body.get("name")
        if not isinstance(name, str) or not GUEST_NAME_RE.match(name):
            raise LibError(400, "bad guest name")
        if not self.state()["configured"]:
            raise LibError(409, "the guests module is not set up")
        return name

    def rules(self, body):
        """POST /api/guests/rules {name, rules}: what the guest's agent may do (owner,
        12.11.2026), web access on or off (17.11.2026: guestctl then makes the sandbox's
        terminals Claude-only). guestctl checks them; its refusal is said."""
        name = self._guest_name(body)
        rules = body.get("rules")
        if not isinstance(rules, dict):
            raise LibError(400, "rules: an object")
        code, out, err = self.run("rules", name, json.dumps(rules, ensure_ascii=False), timeout=GUESTS_RULES_TIMEOUT)
        if code != 0:
            raise LibError(400, (err or out).strip()[-300:] or "rules not saved")
        self._spawn(self._list)
        return {"ok": True}

    SETTINGS_VIEW = ("claude_mode", "usd_week", "notify_at", "week_limit", "web", "rules", "error")

    @staticmethod
    def _scrub(text, *secrets):
        for sec in secrets:
            if isinstance(sec, str) and sec.strip():
                text = text.replace(sec.strip(), "…")
        return re.sub(r"sk-ant[\w-]*", "…", text)

    @classmethod
    def _settings_view(cls, d):
        """Only the known fields of `guestctl defaults` go to the page."""
        if not isinstance(d, dict):
            raise ValueError("defaults: not an object")
        out = {k: d[k] for k in cls.SETTINGS_VIEW if k in d}
        if isinstance(out.get("error"), str):
            out["error"] = cls._scrub(out["error"])[:300]
        return out

    @staticmethod
    def _ctl_error(out, err, fallback, *secrets):
        """guestctl's refusal: the "error" of its JSON answer, else its last words. A key is
        never part of it."""
        text = ""
        for stream in (out, err):
            try:
                e = json.loads((stream or "").strip().splitlines()[-1]).get("error")
            except (ValueError, IndexError, AttributeError):
                continue
            if isinstance(e, str) and e:
                text = e
                break
        text = text or (err or out or "").strip()[-300:] or fallback
        return GuestsPanel._scrub(text, *secrets)[:300]

    def _ctl(self, *args, input=None, fallback="not saved", secrets=()):
        """Run guestctl for a write; its JSON answer, or LibError 400 (504 on a timeout)."""
        try:
            code, out, err = self.run(*args, timeout=60, input=input)
        except subprocess.TimeoutExpired:
            raise LibError(504, "guestctl did not answer in time")
        if code != 0:
            raise LibError(400, self._ctl_error(out, err, fallback, *secrets))
        try:
            res = json.loads(out.strip().splitlines()[-1])      # the LAST line is the JSON
        except (ValueError, IndexError):
            res = {"ok": True}
        return res if isinstance(res, dict) else {"ok": True}

    def claude(self, body):
        """POST /api/guests/claude {name, mode?, usd_week?, notify_at?, secret?, understood?}.
        A secret (the guest's own key) goes to guestctl on stdin only."""
        name = self._guest_name(body)
        if "owner_mode" in body:
            raise LibError(400, "owner_mode is not accepted: `mode` sets it")
        secret = body.get("secret")
        spec = {k: body[k] for k in ("mode", "usd_week", "notify_at", "understood") if k in body}
        stdin = None
        if secret is not None and secret != "":
            if not isinstance(secret, str):
                raise LibError(400, "secret: a string")
            spec["secret"], stdin = True, secret
        res = self._ctl("claude", name, json.dumps(spec), input=stdin, fallback="not saved",
                        secrets=(stdin,))
        self._spawn(self._list)
        out = {"ok": True}
        for k in ("claude", "message", "warning"):
            if k in res:
                out[k] = res[k]
        return out

    SETTINGS_FIELDS = ("claude_mode", "usd_week", "notify_at", "week_limit", "web", "rules",
                       "understood")

    def settings_set(self, body):
        """POST /api/guests/settings {claude_mode?, usd_week?, ...}: what new guests get."""
        if not self.state()["configured"]:
            raise LibError(409, "the guests module is not set up")
        patch = {k: body[k] for k in self.SETTINGS_FIELDS if k in body}
        if not patch or set(patch) == {"understood"}:
            raise LibError(400, "settings: nothing to save")
        res = self._ctl("defaults", "--set", json.dumps(patch), fallback="settings not saved")
        self._spawn(self._list)
        return {"ok": True, "settings": self._settings_view(res)}

    def owner_key_set(self, body):
        """POST /api/guests/owner-key {key} | {remove: true}: the owner's API key. Only
        {ok, last4} comes back."""
        if not self.state()["configured"]:
            raise LibError(409, "the guests module is not set up")
        if body.get("remove") is True:
            self._ctl("owner-key", "--remove", fallback="not removed")
            out = {"ok": True}
            with self.lock:
                self.owner_key = {"set": False, "last4": None}
        else:
            key = body.get("key")
            if not isinstance(key, str) or not key.strip():
                raise LibError(400, "key: your Anthropic API key")
            res = self._ctl("owner-key", input=key.strip(), fallback="key not saved",
                            secrets=(key,))
            out = {"ok": True}
            if isinstance(res.get("last4"), str):
                out["last4"] = res["last4"]
            with self.lock:                       # the next GET is not stale
                self.owner_key = {"set": True, "last4": out.get("last4")}
        return out

    def folders(self, body):
        """POST /api/guests/folders {name}: the sandbox's folders in /work (Rules form)."""
        name = self._guest_name(body)
        code, out, _ = self.run("folders", name, timeout=30)
        try:
            folders = json.loads(out).get("folders") if code == 0 else None
        except ValueError:
            folders = None
        if not isinstance(folders, list):
            raise LibError(502, "the sandbox's folders could not be read")
        return {"folders": [f for f in folders if isinstance(f, str)]}

    def open(self, body):
        """A one-time link that opens the guest's dashboard signed in as the owner."""
        name = body.get("name")
        if not isinstance(name, str) or not GUEST_NAME_RE.match(name):
            raise LibError(400, "bad guest name")
        if not self.state()["configured"]:
            raise LibError(409, "the guests module is not set up")
        if self.guests is None:
            self._list()
        with self.lock:
            g = next((x for x in self.guests or [] if isinstance(x, dict) and x.get("name") == name), None)
        if g is None:
            raise LibError(404, f"no guest {name}")
        url = g.get("url") if isinstance(g.get("url"), str) else ""
        host = (urlparse(url).hostname or "").lower() if url.startswith("https://") else ""
        if not host:
            raise LibError(409, f"{name} has no web address yet")
        if g.get("state") != "running":
            raise LibError(409, f"{name}'s sandbox is not running")
        try:
            code, out, err = self.run("session", name, timeout=30)
        except Exception as e:
            raise LibError(502, f"guestctl session: {str(e)[:200]}")
        if code != 0:
            raise LibError(502, (err or out).strip()[-300:] or f"guestctl session: exit {code}")
        try:
            s = json.loads(out)
            cookie, max_age = s["cookie"], s["max_age"]
            ok = (isinstance(cookie, str) and GUEST_ENTRY_COOKIE_RE.fullmatch(cookie) is not None
                  and s.get("host") == host and type(max_age) is int and 0 < max_age <= 31 * 86400)
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:      # it goes into a header on the guest's address: exactly a login or nothing
            raise LibError(502, "guestctl session: the answer doesn't look like a login")
        ticket, now = secrets.token_urlsafe(32), time.time()
        with self.lock:
            self.entries = {t: e for t, e in self.entries.items() if e["until"] > now}
            self.entries[ticket] = {"host": host, "cookie": cookie, "max_age": max_age,
                                    "until": now + GUEST_ENTRY_TTL}
        return {"url": f"{url.rstrip('/')}{GUEST_ENTRY_PATH}?t={ticket}"}

    def enter(self, ticket, host):
        """Spend an Open link: its entry, or None — unknown, spent, expired, or asked for on
        another address (a wrong try spends it too)."""
        with self.lock:
            e = self.entries.pop(ticket, None) if ticket else None
        if not e or e["until"] < time.time():
            return None
        host = (host or "").split(":", 1)[0].strip().lower()
        return e if hmac.compare_digest(host.encode(), e["host"].encode()) else None


GUESTS = GuestsPanel()
