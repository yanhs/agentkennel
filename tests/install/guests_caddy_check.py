#!/usr/bin/env python3
"""HTTP checks for tests/install/guests_caddy_e2e.sh, run INSIDE the test container as the
installing user (stdlib only). Every name resolves to 127.0.0.1 through /etc/hosts; TLS is
verified against Caddy's own CA (~/.local/share/caddy/pki/authorities/local/root.crt).

    guests_caddy_check.py login MAIN PW SETUP      first run: set the password, keep the cookie
    guests_caddy_check.py main MAIN                 the dashboard still answers (cookie kept)
    guests_caddy_check.py guest MAIN HOST           a guest's dashboard + the owner's Open
    guests_caddy_check.py stub HOST                 404 on HOST (web-off's stub), waits up to 60 s
    guests_caddy_check.py gone HOST                 HOST no longer served (TLS refused)
    guests_caddy_check.py site URL TEXT             a published site answers with TEXT
    guests_caddy_check.py files URL_PREFIX          shared files: a file, a symlink, no listing

Each check prints PASS/FAIL lines; the exit code is the number of failures (0 = all passed).
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_CA = Path.home() / ".local/share/caddy/pki/authorities/local/root.crt"
JAR = Path("/tmp/guests-caddy-e2e-cookie")
FAILED = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}", flush=True)
    if not ok:
        FAILED.append(name)
    return ok


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def ctx():
    c = ssl.create_default_context()
    c.load_verify_locations(cafile=str(ROOT_CA))
    return c


OPENER = None


def req(method, url, data=None, headers=None):
    global OPENER
    if OPENER is None:
        OPENER = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx()))
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with OPENER.open(r, timeout=30) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def cookie_of(headers, name="agents_session"):
    for v in headers.get_all("Set-Cookie") or []:
        first = v.split(";", 1)[0]
        if first.startswith(name + "="):
            return first
    return ""


def login(main, pw, setup):
    token = setup.split("setup=", 1)[-1]
    body = urllib.parse.urlencode({"pass": pw, "pass2": pw, "setup": token}).encode()
    code, h, _ = req("POST", f"{main}/login", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
    c = cookie_of(h)
    check("main dashboard: the password is set, a session cookie comes back", bool(c),
          f"status {code}")
    JAR.write_text(c)
    main_ok(main)


def main_ok(main):
    c = JAR.read_text().strip()
    code, _, body = req("GET", f"{main}/api/library", headers={"Cookie": c})
    check("main dashboard: /api/library 200 through Caddy", code == 200, f"got {code}")
    code, h, _ = req("GET", f"{main}/")
    check("main dashboard: / without a cookie -> /login",
          code == 302 and "/login" in (h.get("Location") or ""), f"{code}")


def guest(main, host):
    base = f"https://{host}"
    for _ in range(60):        # the sandbox's own dashboard takes a moment after a restart
        code, _, body = req("GET", f"{base}/login")
        if code == 200:
            break
        time.sleep(2)
    check(f"{host}: /login is the guest's own dashboard (200, sign-in form)",
          code == 200 and b'name="pass"' in body, f"got {code}")
    code, h, _ = req("GET", f"{base}/")
    check(f"{host}: / without a cookie -> /login (the sandbox's gate)",
          code == 302 and "/login" in (h.get("Location") or ""), f"{code}")
    # the entry path goes to the OWNER's panel, not the sandbox: a bad ticket gets the
    # panel's "link used" page (the sandbox would redirect to its /login)
    code, _, body = req("GET", f"{base}/_owner-entry?t=not-a-ticket")
    check(f"{host}: /_owner-entry is answered by the owner's panel", code == 403, f"got {code}")
    # Open: the owner's panel signs a login and hands it over on the guest's address
    c = JAR.read_text().strip()
    code, _, body = req("POST", f"{main}/api/guests/open",
                        json.dumps({"name": host.split(".")[0][3:]}).encode(),
                        {"Content-Type": "application/json", "Origin": main, "Cookie": c})
    url = ""
    try:
        url = json.loads(body).get("url", "")
    except ValueError:
        pass
    check("Open: the panel gives a one-time link on the guest's address",
          code == 200 and url.startswith(f"{base}/_owner-entry?t="), f"{code} {body[:200]!r}")
    if not url:
        return
    code, h, _ = req("GET", url)
    owner = cookie_of(h)
    check("Open: the link signs the owner in on the guest's address (302 + cookie)",
          code == 302 and owner.startswith("agents_session=owner."), f"{code} {owner[:40]}")
    code, _, _ = req("GET", f"{base}/api/library", headers={"Cookie": owner})
    check("Open: the guest's dashboard accepts the owner's login", code == 200, f"got {code}")
    code, _, _ = req("GET", url)
    check("Open: the link works once", code == 403, f"got {code}")


def stub(host):
    code = 0
    for _ in range(30):
        try:
            code, _, _ = req("GET", f"https://{host}/login")
        except OSError:
            code = -1
        if code == 404:
            break
        time.sleep(2)
    check(f"{host}: web off — the 404 stub answers on the name", code == 404, f"got {code}")


def gone(host):
    try:
        code, _, _ = req("GET", f"https://{host}/login")
        check(f"{host}: no longer served", False, f"still answers {code}")
    except (OSError, ssl.SSLError) as e:
        check(f"{host}: no longer served (TLS refused)", True, type(e).__name__)


def site(url, text):
    code, _, body = req("GET", url)
    check(f"published site {url} answers through Caddy", code == 200 and text.encode() in body,
          f"{code} {body[:80]!r}")
    http = "http://" + url.split("://", 1)[1]
    code, h, _ = req("GET", http)
    check(f"{http} redirects to https", code in (301, 308)
          and (h.get("Location") or "").startswith("https://"), f"{code}")


def files(prefix):
    code, h, body = req("GET", f"{prefix}/hello.txt")
    check("sbx: a guest's shared file is served", code == 200 and body.strip() == b"FILES-OK",
          f"{code} {body[:60]!r}")
    for k, v in (("Content-Security-Policy", "sandbox"), ("X-Content-Type-Options", "nosniff"),
                 ("X-Robots-Tag", "noindex, nofollow"), ("Cache-Control", "no-cache")):
        check(f"sbx: {k}: {v}", h.get(k) == v and len(h.get_all(k) or []) == 1, str(h.get_all(k)))
    code, _, body = req("GET", f"{prefix}/pw.txt")
    check("sbx: a symlink a guest planted is not followed", code == 404 and b"root:" not in body,
          f"got {code}")
    code, _, _ = req("GET", f"{prefix}/")
    check("sbx: no folder listing", code == 404, f"got {code}")
    host = prefix.split("/f/", 1)[0]
    code, _, _ = req("GET", f"{host}/")
    check("sbx: anything but /f/ is 404", code == 404, f"got {code}")


def main(argv):
    what, args = argv[0], argv[1:]
    {"login": login, "main": main_ok, "guest": guest, "stub": stub, "gone": gone,
     "site": site, "files": files}[what](*args)
    return len(FAILED)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
