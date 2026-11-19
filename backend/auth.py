"""The login gate (:3046): password storage, signed session cookie, login / setup /
change-password pages, /check for the proxy.

Part of status_server (the facade re-exports every name here)."""

from http.server import BaseHTTPRequestHandler
import subprocess
import time
import os
import re
import hmac
import hashlib
import secrets
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse
import envcompat

from backend import _HERE


# ── Cookie-session auth (replaces the nginx basic-auth browser dialog) ───────
# The basic-auth dialog re-prompts on every 401, so a burst of polling/reload
# requests showed it dozens of times ("can't even type the password"). A cookie
# session has NO native dialog: one login on a page → signed cookie → every
# request (fetch, iframe, ttyd ws) carries it automatically. Passwords are the
# SAME (verified against the existing /etc/nginx/.htpasswd_agents).

HTPASSWD_FILE = envcompat.get("HTPASSWD", "/etc/nginx/.htpasswd_agents")
# Cookie-signing key. AGENTKENNEL_AUTH_SECRET lets the entrypoint keep it in the persistent
# volume (so sessions survive a container recreate and the key is never baked into the image);
# the default sits next to this module, leaving the existing host service untouched.
AUTH_SECRET_FILE = envcompat.get("AUTH_SECRET",
    os.path.join(_HERE, ".agents_auth_secret"),
)
AUTH_COOKIE = "agents_session"
AUTH_TTL = 30 * 24 * 3600          # 30 days


def _auth_secret():
    try:
        with open(AUTH_SECRET_FILE, "rb") as f:
            s = f.read().strip()
            if s:
                return s
    except OSError:
        pass
    s = os.urandom(32).hex().encode()
    old = os.umask(0o077)
    try:
        with open(AUTH_SECRET_FILE, "wb") as f:
            f.write(s)
    finally:
        os.umask(old)
    return s


AUTH_SECRET = _auth_secret()
_SECRET_SEEN = [None, AUTH_SECRET]          # (inode, mtime, size) of the file, its key


def _secret():
    """The cookie key, re-read when its file changes: `--reset-password` writes a new one,
    and every signed-in browser is out at once, without a restart."""
    try:
        st = os.stat(AUTH_SECRET_FILE)
        seen = (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return _SECRET_SEEN[1]
    if seen != _SECRET_SEEN[0]:
        _SECRET_SEEN[0], _SECRET_SEEN[1] = seen, _auth_secret()
    return _SECRET_SEEN[1]


def _sign_token(user, exp):
    sig = hmac.new(_secret(), f"{user}.{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{user}.{exp}.{sig}"


def _verify_token(tok):
    if not tok or tok.count(".") < 2:
        return None
    user, exp, sig = tok.rsplit(".", 2)
    if not exp.isdigit() or int(exp) < int(time.time()):
        return None
    expect = hmac.new(_secret(), f"{user}.{exp}".encode(), hashlib.sha256).hexdigest()
    return user if hmac.compare_digest(expect, sig) else None


# Standalone/Docker mode: keep a single dashboard password (pbkdf2 hash) in a file the
# user SETS on first run and can change in the UI. If AGENTKENNEL_PASSFILE is unset, behaviour
# is unchanged (verify against /etc/nginx/.htpasswd_agents) — the live nginx setup is untouched.
PASSFILE = envcompat.get("PASSFILE", "")


def _pw_is_set():
    try:
        return bool(PASSFILE) and os.path.getsize(PASSFILE) > 0
    except OSError:
        return False


def _pw_store(pw):
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    old = os.umask(0o077)
    try:
        with open(PASSFILE, "w") as f:
            f.write(f"{salt.hex()}${h.hex()}")
    finally:
        os.umask(old)
    _setup_token_drop()


# First run: the password may be set only through /login?setup=<token>. Without it, the
# first stranger to open a fresh install (sslip.io names show up in public certificate
# logs within minutes) would set the password and own a shell with your sudo. The token
# sits next to the passfile (0600); install.sh and the container's log print the link
# (`status_server.py --setup-link`); setting the password deletes it.
def _setup_token_file():
    return PASSFILE + ".setup" if PASSFILE else ""


def _setup_open():
    return bool(PASSFILE) and not _pw_is_set() and not _pw_seedable()


def _setup_token():
    """The current token, made on first use; '' when there is nothing to set up."""
    if not _setup_open():
        return ""
    path = _setup_token_file()
    try:
        with open(path) as f:
            tok = f.read().strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    old = os.umask(0o077)
    try:
        with open(path, "w") as f:
            f.write(tok + "\n")
    finally:
        os.umask(old)
    return tok


def _setup_token_ok(given):
    tok = _setup_token()
    return bool(tok and given) and hmac.compare_digest(tok, given)


def _setup_token_drop():
    try:
        os.unlink(_setup_token_file())
    except OSError:
        pass


def setup_link_path():
    tok = _setup_token()
    return f"/login?setup={tok}" if tok else ""


def reset_password():
    """Forgot the password (02.11.2026): drop it, write a new cookie key (every signed-in
    browser is out) and a new setup token. -> the new link's path."""
    for path in (PASSFILE, _setup_token_file()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    tmp = AUTH_SECRET_FILE + ".new"
    old = os.umask(0o077)
    try:
        with open(tmp, "wb") as f:
            f.write(os.urandom(32).hex().encode())
    finally:
        os.umask(old)
    os.replace(tmp, AUTH_SECRET_FILE)
    return setup_link_path()


def _public_ip():
    """This server's address for the setup link: AGENTKENNEL_PUBLIC_IP ("none" = unknown),
    else one question to api.ipify.org; None when that fails."""
    ip = (envcompat.get("PUBLIC_IP") or "").strip()
    if ip:
        return None if ip == "none" else ip
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as r:
            ip = r.read(64).decode().strip()
    except (OSError, ValueError):
        return None
    return ip if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip) else None


def setup_url():
    """The whole first-run link, address included (the Docker log prints it: `docker compose
    logs agentkennel | grep setup` hands over something to open). '' once a password is set.
    AGENTKENNEL_SITE says the scheme / name; AGENTKENNEL_PUBLIC_PORT (docker-compose.yml) the
    port the server publishes."""
    path = setup_link_path()
    if not path:
        return ""
    site = (envcompat.get("SITE") or "https://:8765").strip()
    if site.startswith(":") or site.startswith("https://:") or site.startswith("http://:"):
        scheme = "http" if site.startswith((":", "http://")) else "https"
        port = (envcompat.get("PUBLIC_PORT") or "").strip().rsplit(":", 1)[-1] or site.rsplit(":", 1)[-1]
        return f"{scheme}://{_public_ip() or '<your-server-ip>'}:{port}{path}"
    return f"https://{site.split('://', 1)[-1].rstrip('/')}{path}"


def _pw_verify(pw):
    try:
        with open(PASSFILE) as f:
            salt_hex, want = f.read().strip().split("$", 1)
        h = hashlib.pbkdf2_hmac("sha256", (pw or "").encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(h.hex(), want)
    except (OSError, ValueError):
        return False


def _htpasswd_verify(user, pw):
    if not pw or len(pw) > 256 or not re.match(r'^[A-Za-z0-9_.-]{1,32}$', user or ''):
        return False
    try:
        r = subprocess.run(["htpasswd", "-vb", HTPASSWD_FILE, user, pw],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _pw_seedable():
    """Passfile mode, passfile still empty, but the old nginx htpasswd exists:
    migrate on the next successful login instead of offering an open 'set a
    password' form (the first stranger to arrive would own the dashboard)."""
    return bool(PASSFILE) and not _pw_is_set() and os.path.exists(HTPASSWD_FILE)


def _login_verify(user, pw):
    """Login check; in the seedable state a correct htpasswd login also moves the
    password into the dashboard's own passfile (nobody has to retype it anywhere)."""
    if _pw_seedable():
        if _htpasswd_verify(user, pw):
            _pw_store(pw)
            return True
        return False
    return _check_password(user, pw)


def _verify_current(user, pw):
    """'Current password' on /change-password: the passfile once seeded, else htpasswd."""
    return _htpasswd_verify(user, pw) if _pw_seedable() else _pw_verify(pw)


def _check_password(user, pw):
    if not pw or len(pw) > 256:
        return False
    if PASSFILE:                       # standalone mode: one password, username ignored
        return _pw_verify(pw)
    return _htpasswd_verify(user, pw)


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentKennel — login</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>
*{box-sizing:border-box} html,body{height:100%}
body{margin:0;display:flex;align-items:center;justify-content:center;background:#0d1117;
  color:#e6edf3;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.card{width:min(92vw,340px);background:#161b22;border:1px solid #30363d;border-radius:14px;
  padding:28px 26px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{margin:0 0 4px;font-size:19px} .sub{margin:0 0 20px;color:#8b949e;font-size:13px}
label{display:block;font-size:12px;color:#8b949e;margin:12px 0 5px}
input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #30363d;background:#0d1117;
  color:#e6edf3;font-size:15px;outline:none}
input:focus{border-color:#388bfd;box-shadow:0 0 0 3px rgba(56,139,253,.25)}
button{width:100%;margin-top:18px;padding:11px;border:0;border-radius:8px;background:#238636;
  color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#2ea043}
.err{margin:14px 0 0;color:#ff7b72;font-size:13px;text-align:center}
.forgot{margin:16px 0 0;font-size:12px;color:#8b949e} .forgot summary{cursor:pointer;text-align:center}
.forgot p{margin:8px 0 4px} .forgot code{display:block;padding:6px 8px;border-radius:6px;background:#0d1117;
  border:1px solid #30363d;color:#e6edf3;font-size:11.5px;overflow-wrap:anywhere}
</style>__BREAKOUT__</head><body>
<form class="card" method="POST" action="/login">
  <h1>🐾 AgentKennel</h1><p class="sub">Sign in to open the panel</p>
  __USER__<label for="p">Password</label>
  <input id="p" name="pass" type="password" autocomplete="current-password"__PFOCUS__>
  <button type="submit">Sign in</button>
  __ERR__
  <details class="forgot"><summary>Forgot your password?</summary>
    <p>On the server, run this. It prints a new one-time link to set a new password; the old
    one and every signed-in browser stop working.</p>
    <code>~/agentkennel/install.sh --reset-password</code>
    <p>Docker, in the AgentKennel folder:</p>
    <code>docker compose exec agentkennel python3 status_server.py --reset-password</code>
  </details>
</form></body></html>"""


_LOGIN_USER_FIELD = ('<label for="u">Username</label>\n'
                     '  <input id="u" name="user" autocomplete="username" autofocus>\n  ')


def _login_needs_user():
    """The username matters only where htpasswd checks it: no passfile (htpasswd mode),
    or the passfile-still-empty migration state. A set passfile ignores it."""
    return not PASSFILE or _pw_seedable()


# A framed sign-in page takes the whole tab: after an expired session the terminal iframe
# reloads into /login, and logging in THERE nested a second dashboard inside the pane.
_FRAME_BREAKOUT = "<script>if(window.top!==window.self)window.top.location.replace('/login')</script>"


def login_html(error=False):
    """The sign-in page; Username field only where it is checked."""
    need_user = _login_needs_user()
    err = ("Invalid username or password" if need_user else "Invalid password")
    return (LOGIN_HTML
            .replace("__USER__", _LOGIN_USER_FIELD if need_user else "")
            .replace("__PFOCUS__", "" if need_user else " autofocus")
            .replace("__BREAKOUT__", _FRAME_BREAKOUT)
            .replace("__ERR__", f'<p class="err">{err}</p>' if error else ""))


_AUTH_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
  '<meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentKennel</title>'
  '<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>'
  '*{box-sizing:border-box}html,body{height:100%}'
  'body{margin:0;display:flex;align-items:center;justify-content:center;background:#0d1117;'
  'color:#e6edf3;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}'
  '.card{width:min(92vw,340px);background:#161b22;border:1px solid #30363d;border-radius:14px;'
  'padding:28px 26px;box-shadow:0 10px 40px rgba(0,0,0,.4)}'
  'h1{margin:0 0 4px;font-size:19px}.sub{margin:0 0 20px;color:#8b949e;font-size:13px}'
  'label{display:block;font-size:12px;color:#8b949e;margin:12px 0 5px}'
  'input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #30363d;background:#0d1117;'
  'color:#e6edf3;font-size:15px;outline:none}'
  'input:focus{border-color:#388bfd;box-shadow:0 0 0 3px rgba(56,139,253,.25)}'
  'button{width:100%;margin-top:18px;padding:11px;border:0;border-radius:8px;background:#238636;'
  'color:#fff;font-size:15px;font-weight:600;cursor:pointer}button:hover{background:#2ea043}'
  '.err{margin:14px 0 0;color:#ff7b72;font-size:13px;text-align:center}'
  '.ok{margin:14px 0 0;color:#3fb950;font-size:13px;text-align:center}'
  'a{color:#58a6ff}</style>' + _FRAME_BREAKOUT + '</head><body>')


def _setup_form(error="", token=""):
    return (_AUTH_HEAD + '<form class="card" method="POST" action="/login">'
      '<h1>🐾 AgentKennel</h1><p class="sub">Set a password to protect the dashboard</p>'
      f'<input type="hidden" name="setup" value="{token}">'
      '<label for="p">New password</label>'
      '<input id="p" name="pass" type="password" autocomplete="new-password" autofocus>'
      '<label for="p2">Repeat password</label>'
      '<input id="p2" name="pass2" type="password" autocomplete="new-password">'
      '<button type="submit">Set password</button>'
      + (f'<p class="err">{error}</p>' if error else '') + '</form></body></html>')


def _setup_locked():
    return (_AUTH_HEAD + '<div class="card">'
      '<h1>🐾 AgentKennel</h1><p class="sub">No password yet</p>'
      '<p>Open the <b>setup link</b> the installer printed — only that link can set the '
      'password. To show it again, on the server run <code>~/agentkennel/install.sh</code> '
      'again, or <code>python3 status_server.py --setup-link</code> in the AgentKennel folder '
      '(Docker: <code>docker compose logs agentkennel | grep setup</code>).</p>'
      '</div></body></html>')


def _change_form(error="", ok=""):
    return (_AUTH_HEAD + '<form class="card" method="POST" action="/change-password">'
      '<h1>Change password</h1><p class="sub">Enter your current and a new password</p>'
      '<label for="c">Current password</label>'
      '<input id="c" name="cur" type="password" autocomplete="current-password" autofocus>'
      '<label for="p">New password</label>'
      '<input id="p" name="pass" type="password" autocomplete="new-password">'
      '<label for="p2">Repeat new password</label>'
      '<input id="p2" name="pass2" type="password" autocomplete="new-password">'
      '<button type="submit">Change password</button>'
      + (f'<p class="err">{error}</p>' if error else '')
      + (f'<p class="ok">{ok}</p>' if ok else '')
      + '<p style="text-align:center;margin-top:14px"><a href="/">&larr; back to dashboard</a></p></form></body></html>')


def _cookie_flags(secure=True):
    # HttpOnly: JS can't read it. Secure (HTTPS-only) is dropped on plain http so the
    # cookie actually comes back (e.g. a Docker http port).
    return "Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (AUTH_TTL, "; Secure" if secure else "")


class AuthHandler(BaseHTTPRequestHandler):
    """Cookie login. /check (auth probe), /login (form/verify; FIRST RUN in
    AGENTKENNEL_PASSFILE mode = set the password), /change-password, /logout."""

    def _secure(self):
        return self.headers.get("X-Forwarded-Proto", "") == "https"

    def _html(self, html, code=200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_login(self, error=False):
        if _setup_open():
            given = parse_qs(urlparse(self.path).query).get("setup", [""])[0]
            if not _setup_token_ok(given):
                return self._html(_setup_locked(), 403)
            return self._html(_setup_form(token=given))
        self._html(login_html(error), 401 if error else 200)

    def _session_cookie(self, user):
        tok = _sign_token(user or "admin", int(time.time()) + AUTH_TTL)
        return f"{AUTH_COOKIE}={tok}; {_cookie_flags(self._secure())}"

    def _login_cookie(self, user):
        self.send_response(302)
        self.send_header("Set-Cookie", self._session_cookie(user))
        self.send_header("Location", "/")
        self.end_headers()

    def _renew(self):
        # Sliding session: the open dashboard calls GET /login?renew=1 every few hours and a
        # still-valid cookie is reissued for another AUTH_TTL, so daily use never reaches the
        # expiry. No valid cookie → 401 and nothing set (an expired login is not revived).
        user = self._user()
        self.send_response(204 if user else 401)
        self.send_header("Cache-Control", "no-store")
        if user:
            self.send_header("Set-Cookie", self._session_cookie(user))
        self.end_headers()

    def _cookies(self):
        out = {}
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _user(self):
        return _verify_token(self._cookies().get(AUTH_COOKIE, ""))

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/check":
            self.send_response(200 if self._user() else 401)
            self.end_headers()
            return
        if path == "/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", f"{AUTH_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if path == "/change-password":
            if not (PASSFILE and self._user()):
                return self._redirect("/login")
            return self._html(_change_form())
        if path == "/login" and parse_qs(urlparse(self.path).query).get("renew") == ["1"]:
            return self._renew()
        self._send_login()      # /login (or anything else) → the form

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", "replace") if 0 < length <= 4096 else ""
        form = parse_qs(body)

        if path == "/change-password":
            if not (PASSFILE and self._user()):
                return self._redirect("/login")
            cur = form.get("cur", [""])[0]
            new, new2 = form.get("pass", [""])[0], form.get("pass2", [""])[0]
            if not _verify_current(self._user(), cur):
                return self._html(_change_form(error="Current password is wrong"))
            if len(new) < 6 or new != new2:
                return self._html(_change_form(error="New passwords must match (6+ chars)"))
            _pw_store(new)
            return self._html(_change_form(ok="Password changed."))

        # /login — FIRST RUN (passfile mode, no password yet) sets it, with the setup
        # link's token only; otherwise verify
        if _setup_open():
            given = form.get("setup", [""])[0]
            if not _setup_token_ok(given):
                return self._html(_setup_locked(), 403)
            new, new2 = form.get("pass", [""])[0], form.get("pass2", [""])[0]
            if len(new) < 6 or new != new2:
                return self._html(_setup_form("Passwords must match (6+ chars)", token=given))
            _pw_store(new)
            return self._login_cookie("admin")

        user = (form.get("user", [""])[0]).strip()
        pw = form.get("pass", [""])[0]
        if _login_verify(user, pw):
            self._login_cookie(user)
        else:
            self._send_login(error=True)

    def log_message(self, *args):
        pass
