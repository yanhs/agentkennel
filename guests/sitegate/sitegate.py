#!/usr/bin/env python3
"""Site gate — a guest publishes a site from its sandbox as <site>-<guest>.<domain>.

The guest's agent calls (from inside its sandbox, with its turnstile pass):

    curl -s -X POST http://172.31.250.1:8794/publish \\
         -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -d '{"site":"test","port":3000}'
    curl -s http://172.31.250.1:8794/sites -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN"
    curl -s -X DELETE http://172.31.250.1:8794/publish/test -H "Authorization: Bearer ..."

The host then creates the DNS record (Cloudflare, proxied), an nginx vhost that proxies
ONLY to that guest's sandbox address and port, a Let's Encrypt certificate, and tells
the owner in Telegram. A guest can have MAX_SITES sites and remove only its own.
Names and ports are validated before they reach a command line or a config file.

Settings (guests/.env, see guestenv.py): GUESTS_DOMAIN, SERVER_IP (else detected),
CF_TOKEN_FILE (default ~/.cf_token; without it DNS is left to a wildcard record),
CF_ZONE (default GUESTS_DOMAIN).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "turnstile"))
sys.path.insert(0, str(HERE))         # run with python3 -I: its own folder isn't on the path
import guestenv  # noqa: E402
from texts import t as _t  # noqa: E402
import turnstile as ts  # noqa: E402
import webchoice  # noqa: E402  (nginx or Caddy carries the guests' addresses)
import caddyconf  # noqa: E402
import publicfiles  # noqa: E402

MAX_SITES = 3
# a guest's changes reload the web server (and on Caddy cut open websockets after their
# stream_close_delay): one change per guest a minute, CHANGES_PER_HOUR in all
CHANGE_EVERY = 60
CHANGES_PER_HOUR = 20
# sb-<guest>.<domain> is a dashboard, sbx.<domain> the shared files: a site named like
# them (site `sb` of guest x = sb-x, site `sb-anna` of guest k = sb-anna-k) would take one
RESERVED_SITES = ("sb", "sbx")
FILES_PORT = 8795   # publicfiles.py on 127.0.0.1: sbx.<domain>/f/ behind Caddy
SITE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,18}[a-z0-9])?$")
IPV4_RE = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$")
IP_LOOKUPS = ("https://api.ipify.org", "https://ifconfig.me", "https://icanhazip.com")


class GateError(Exception):
    pass


def validate_site(name) -> str:
    if not isinstance(name, str) or not SITE_RE.match(name):
        raise ValueError(_t("site_bad_name"))
    if name in RESERVED_SITES or name.startswith(tuple(f"{r}-" for r in RESERVED_SITES)):
        raise ValueError(_t("site_reserved"))
    return name


def validate_port(port) -> int:
    if isinstance(port, bool) or not isinstance(port, (int, str)) or not str(port).isdigit():
        raise ValueError(_t("site_bad_port"))
    value = int(port)
    if not 1024 <= value <= 65535:
        raise ValueError(_t("site_bad_port"))
    return value


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": guestenv.USER_AGENT})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read(100).decode("ascii", "replace")


def server_ip(fetch=_fetch) -> str:
    """This server's public IPv4: SERVER_IP (guests/.env), else asked on the internet;
    "" when unknown."""
    ip = (guestenv.get("SERVER_IP") or os.environ.get("SITEGATE_SERVER_IP") or "").strip()
    if ip:
        return ip
    for url in IP_LOOKUPS:
        try:
            answer = fetch(url).strip()
        except Exception:  # noqa: BLE001 — try the next one
            continue
        if IPV4_RE.match(answer):
            return answer
    return ""


def cf_token_file() -> Path:
    return Path(guestenv.get("CF_TOKEN_FILE") or Path.home() / ".cf_token").expanduser()


def cf_zone() -> str:
    return guestenv.get("CF_ZONE") or guestenv.domain()


def cloudflare_api(method: str, path: str, data: Optional[Dict] = None,
                   timeout: float = 60) -> Dict:
    """One Cloudflare API call (curl -4: this server's IPv6 route to it is unreliable)."""
    token = cf_token_file().read_text().strip()
    argv = ["curl", "-4", "-s", "--max-time", str(int(timeout)), "-X", method,
            "-H", f"Authorization: Bearer {token}", "-H", "Content-Type: application/json",
            f"https://api.cloudflare.com/client/v4/{path}"]
    if data is not None:
        argv += ["--data", json.dumps(data)]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 10)
    if r.returncode != 0:
        raise RuntimeError(f"curl cloudflare: exit {r.returncode}")
    return json.loads(r.stdout or "{}")


def host_for(site: str, guest: str, registry: Optional[Dict] = None) -> str:
    domain = guestenv.domain()
    if not domain:
        raise GateError(_t("site_no_domain"))
    host = f"{site}-{guestenv.address_of(guest, registry or {})}.{domain}"
    if len(host.split(".")[0]) > 63:
        raise ValueError(_t("site_too_long"))
    return host


def render_vhost(host: str, ip: str, port: int, tls: bool, extra: str = "",
                 origin: Optional[Tuple[str, str]] = None) -> str:
    """nginx config for one guest site. tls=False: only what the first certificate needs.
    `extra`: locations put before the catch-all of the https block (a guest's dashboard
    has one: the owner's way in, see guestctl.owner_entry_location).
    `origin` (this address's origin, the one the app knows): the app was made for another
    address and takes writes only from that Origin — the browser's is swapped for it
    (not on a websocket: the terminal checks Origin against Host)."""
    http = (
        "server {\n"
        "    listen 80;\n    listen [::]:80;\n"
        f"    server_name {host};\n"
        "    location ^~ /.well-known/acme-challenge/ { root /var/www/html; }\n"
        + ("    location / { return 301 https://$host$request_uri; }\n" if tls
           else "    location / { return 404; }\n")
        + "}\n"
    )
    if not tls:
        return f"# guest site {host} (certificate pending)\n" + http
    https = (
        "server {\n"
        "    listen 443 ssl;\n    listen [::]:443 ssl;\n"
        f"    server_name {host};\n"
        f"    ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem;\n"
        f"    ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem;\n"
        "    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        "    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n"
        "    client_max_body_size 50m;\n"
        + extra
        + "    location / {\n"
        f"        proxy_pass http://{ip}:{port};\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_set_header Upgrade $http_upgrade;\n"
        "        proxy_set_header Connection $http_connection;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto https;\n"
        + (("        set $sbx_origin $http_origin;\n"
            f'        if ($http_origin = "{origin[0]}") {{ set $sbx_origin "{origin[1]}"; }}\n'
            # the terminal (a websocket: ttyd) checks Origin against Host — the real one
            "        if ($http_upgrade) { set $sbx_origin $http_origin; }\n"
            "        proxy_set_header Origin $sbx_origin;\n") if origin else "")
        + "        proxy_read_timeout 300s;\n"
        "    }\n"
        "}\n"
    )
    return f"# guest site {host} -> sandbox {ip}:{port} (managed by sitegate)\n" + http + "\n" + https


def render_gone(host: str) -> str:
    """A taken-down site: answers 404 on its own name until the name is gone."""
    return (
        f"# guest site {host} (removed; stub until DNS is gone)\n"
        "server {\n    listen 80;\n    listen [::]:80;\n"
        f"    server_name {host};\n    location / {{ return 404; }}\n}}\n"
        "server {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n"
        f"    server_name {host};\n"
        f"    ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem;\n"
        f"    ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem;\n"
        "    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        "    location / { return 404; }\n}\n"
    )


def render_moved(host: str, to: str) -> str:
    """An address that moved: everything goes to the same path on `to` (its certificate
    is kept, and renews over http)."""
    go = f"    location / {{ return 301 https://{to}$request_uri; }}\n"
    return (
        f"# guest site {host} (moved to {to})\n"
        "server {\n    listen 80;\n    listen [::]:80;\n"
        f"    server_name {host};\n"
        "    location ^~ /.well-known/acme-challenge/ { root /var/www/html; }\n"
        + go + "}\n"
        "server {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n"
        f"    server_name {host};\n"
        f"    ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem;\n"
        f"    ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem;\n"
        "    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        "    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n"
        + go + "}\n"
    )


def nginx_entry_location(path: str, upstream: str) -> str:
    """One exact path of a guest's address that goes to the owner's panel (guestctl's
    OWNER_ENTRY), put before the catch-all of the https block."""
    return (
        f"    location = {path} {{\n"
        f"        proxy_pass {upstream};\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Forwarded-Proto https;\n"
        "    }\n"
    )


def files_port() -> int:
    """Where publicfiles.py listens (127.0.0.1): GUESTS_FILES_PORT, default 8795."""
    value = guestenv.get("GUESTS_FILES_PORT") or ""
    return int(value) if value.isdigit() else FILES_PORT


class NginxConf:
    """The guests' addresses as nginx vhosts (the text every nginx server has had)."""
    KIND = "nginx"

    @staticmethod
    def site(host: str, ip: str, port: int, tls: bool = True,
             entry: Optional[Tuple[str, str]] = None,
             origin: Optional[Tuple[str, str]] = None) -> str:
        return render_vhost(host, ip, port, tls=tls,
                            extra=nginx_entry_location(*entry) if entry else "", origin=origin)

    gone = staticmethod(render_gone)
    moved = staticmethod(render_moved)

    @staticmethod
    def files(domain: str, public_root: str) -> str:
        text = (ROOT / "nginx" / "sbx.conf.template").read_text()
        return text.replace("@DOMAIN@", domain).replace("@PUBLIC_ROOT@", public_root)

    @staticmethod
    def serves(text: str, port: int) -> bool:
        return "listen 443" in text and f"proxy_pass http://127.0.0.1:{port};" in text

    @staticmethod
    def has_entry(text: str, path: str) -> bool:
        return f"location = {path} " in text


class CaddyConf:
    """The same addresses as Caddy site blocks (caddyconf.py)."""
    KIND = "caddy"
    site = staticmethod(caddyconf.render_site)
    gone = staticmethod(caddyconf.render_gone)
    moved = staticmethod(caddyconf.render_moved)
    serves = staticmethod(caddyconf.serves)
    has_entry = staticmethod(caddyconf.has_entry)

    @staticmethod
    def files(domain: str, public_root: str) -> str:
        return caddyconf.render_files(f"sbx.{domain}", files_port())


def conf_for(ops) -> type:
    """The renderer that goes with these ops: Caddy blocks for KIND "caddy", else nginx."""
    return CaddyConf if getattr(ops, "KIND", "nginx") == "caddy" else NginxConf


class Gate:
    def __init__(self, book: "ts.GuestBook", ops, registry: Path,
                 ip_of: Callable[[str], Optional[str]], cleanup_delay: float = 90,
                 sandboxes: Callable[[], Dict] = dict, clock=time.monotonic,
                 timer=None, limit: bool = False) -> None:
        self.sandboxes = sandboxes   # guestctl's registry: each guest's own address name
        self.book = book
        self.ops = ops
        self.path = Path(registry)
        self.ip_of = ip_of
        self.cleanup_delay = cleanup_delay
        self._lock = threading.RLock()   # the delayed clean-up takes it too
        self.clock = clock
        self.timer = timer or (lambda delay, fn: threading.Timer(delay, fn).start())
        self.limit = limit               # main() turns the rate limit on
        self._last_change: Dict[str, float] = {}
        self._changes: list = []
        self._leaving: set = set()       # taken down, its 404 stub still up (cleanup_delay)

    def _ops(self):
        """The ops for ONE operation: AutoOps (the running gate) may pick another backend
        between operations, never in the middle of one."""
        if getattr(type(self.ops), "current", None):
            return self.ops.current()
        return self.ops

    def _may_change(self, guest: str) -> None:
        if not self.limit:
            return
        now = self.clock()
        last = self._last_change.get(guest)
        if last is not None and now - last < CHANGE_EVERY:
            raise GateError(_t("site_too_often", wait=int(CHANGE_EVERY - (now - last)) + 1))
        self._changes = [t for t in self._changes if now - t < 3600]
        if len(self._changes) >= CHANGES_PER_HOUR:
            raise GateError(_t("site_too_busy"))

    def _changed(self, guest: str) -> None:
        now = self.clock()
        self._last_change[guest] = now
        self._changes.append(now)

    def _load(self) -> Dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self, reg: Dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def sites(self, guest: str) -> list:
        return [{"url": f"https://{h}", "site": v["site"], "port": v["port"]}
                for h, v in sorted(self._load().items()) if v["guest"] == guest]

    def publish(self, guest: str, site, port) -> Dict:
        site, port = validate_site(site), validate_port(port)
        g = self.book.get(guest)
        if g is None or not g.enabled:
            raise GateError(_t("site_guest_off"))
        ip = self.ip_of(guest)
        if not ip:
            raise GateError(_t("site_no_ip"))
        ops = self._ops()
        problem = getattr(ops, "problem", None)
        problem = problem() if callable(problem) else ""
        if problem:                      # e.g. a Caddy without a public 443
            raise GateError(_t("site_failed", err=problem))
        conf = conf_for(ops)
        host = host_for(site, guest, self.sandboxes())
        with self._lock:
            reg = self._load()
            if host not in reg and ts.rules_active(g) and not g.rules["actions"].get("publish"):
                raise GateError(_t("site_rules_no_publish"))   # owner's rules, 12.11.2026
            if host in reg:
                if reg[host]["guest"] != guest:
                    raise GateError(_t("site_taken"))
                if reg[host]["port"] == port:      # nothing changes: no reload either
                    return {"url": f"https://{host}", "host": host, "port": port}
                self._may_change(guest)
                ops.write_vhost(host, conf.site(host, ip, port, tls=True))
                ops.reload()
                reg[host]["port"] = port
                self._save(reg)
                self._changed(guest)
                return {"url": f"https://{host}", "host": host, "port": port}
            if sum(1 for v in reg.values() if v["guest"] == guest) >= MAX_SITES:
                raise GateError(_t("site_too_many", max=MAX_SITES))
            exists = getattr(ops, "vhost_exists", None)
            if callable(exists) and exists(host) and host not in self._leaving:
                # served, but not one of the gate's sites: a dashboard, sbx., the owner's
                raise GateError(_t("site_taken"))
            self._may_change(guest)
            try:
                ops.dns_create(host)
                ops.write_vhost(host, conf.site(host, ip, port, tls=False))
                ops.reload()
                ops.cert_issue(host)
                ops.write_vhost(host, conf.site(host, ip, port, tls=True))
                ops.reload()
            except Exception as exc:
                for undo in (lambda: ops.remove_vhost(host), ops.reload,
                             lambda: ops.dns_delete(host)):
                    try:
                        undo()
                    except Exception:
                        pass
                raise GateError(_t("site_failed", err=exc))
            reg[host] = {"guest": guest, "site": site, "port": port,
                         "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save(reg)
            self._changed(guest)
        ops.notify(_t("site_published", guest=guest, host=host, port=port))
        return {"url": f"https://{host}", "host": host, "port": port}

    def unpublish(self, guest: str, site) -> Dict:
        site = validate_site(site)
        host = host_for(site, guest, self.sandboxes())
        with self._lock:
            reg = self._load()
            if host not in reg or reg[host]["guest"] != guest:
                raise GateError(_t("site_not_yours"))
            self._may_change(guest)
            ops = self._ops()
            self._remove(host, ops)
            del reg[host]
            self._save(reg)
            self._changed(guest)
        ops.notify(_t("site_removed", guest=guest, host=host))
        return {"removed": f"https://{host}"}

    def _remove(self, host: str, ops=None) -> None:
        """Take a site down without ever falling through to another site.

        Cloudflare keeps routing the name for a short while after the DNS record is
        deleted; with no vhost, nginx would answer with its FIRST server block (another
        dashboard's login page). So: 404 stub first, then DNS, and only after
        cleanup_delay the stub and the certificate go — with the same ops (the backend that
        wrote them), and not if the site was published again meanwhile.
        """
        ops = ops or self._ops()
        ops.write_vhost(host, conf_for(ops).gone(host))
        ops.reload()
        ops.dns_delete(host)

        def finish() -> None:
            ops.remove_vhost(host)
            ops.reload()
            ops.cert_delete(host)

        def later() -> None:
            with self._lock:
                self._leaving.discard(host)
                if host in self._load():       # published again within the delay
                    return
                finish()

        if self.cleanup_delay <= 0:
            finish()
        else:
            self._leaving.add(host)
            self.timer(self.cleanup_delay, later)


# --------------------------------------------------------------------------- #
class RealOps:
    """The actual server changes. Every name here was validated by the Gate."""

    KIND = "nginx"
    SUFFIX = ".conf"
    VHOSTS = ROOT / "nginx" / "sites"
    choice: "Optional[webchoice.Choice]" = None      # how make_ops chose this backend

    def __init__(self) -> None:
        self.VHOSTS.mkdir(parents=True, exist_ok=True)
        self._zone: Optional[str] = None
        self._ip: Optional[str] = None

    @staticmethod
    def _run(*argv: str) -> str:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: "
                               f"{(r.stderr or r.stdout).strip()[-300:]}")
        return r.stdout

    def _cf(self, method: str, path: str, data: Optional[Dict] = None) -> Dict:
        res = cloudflare_api(method, path, data)
        if not res.get("success"):
            raise RuntimeError(f"cloudflare: {res.get('errors')}")
        return res

    def _zone_id(self) -> str:
        if not self._zone:
            self._zone = self._cf("GET", f"zones?name={cf_zone()}")["result"][0]["id"]
        return self._zone

    @staticmethod
    def _dns_by_cloudflare() -> bool:
        """No token file: DNS is the owner's wildcard record (*.<domain> -> this server)."""
        return cf_token_file().exists()

    def dns_create(self, host: str) -> None:
        if not self._dns_by_cloudflare():
            return
        z = self._zone_id()
        if self._cf("GET", f"zones/{z}/dns_records?name={host}")["result"]:
            return
        if self._ip is None:
            self._ip = server_ip()
        if not self._ip:
            raise RuntimeError("this server's IP is unknown: set SERVER_IP in guests/.env")
        self._cf("POST", f"zones/{z}/dns_records", {
            "type": "A", "name": host, "content": self._ip, "proxied": True, "ttl": 1,
            "comment": "AgentKennel guest site (sitegate)"})

    def dns_delete(self, host: str) -> None:
        if not self._dns_by_cloudflare():
            return
        z = self._zone_id()
        for rec in self._cf("GET", f"zones/{z}/dns_records?name={host}")["result"]:
            self._cf("DELETE", f"zones/{z}/dns_records/{rec['id']}")

    def write_vhost(self, host: str, text: str) -> None:
        conf = self.VHOSTS / f"{host}.conf"
        conf.write_text(text)
        self._run("sudo", "-n", "ln", "-sfn", str(conf), f"/etc/nginx/sites-enabled/sbx-site-{host}")

    def vhost_exists(self, host: str) -> bool:
        return (self.VHOSTS / f"{host}.conf").exists()

    def remove_vhost(self, host: str) -> None:
        self._run("sudo", "-n", "rm", "-f", f"/etc/nginx/sites-enabled/sbx-site-{host}")
        (self.VHOSTS / f"{host}.conf").unlink(missing_ok=True)

    def reload(self) -> None:
        self._run("sudo", "-n", "nginx", "-t")
        self._run("sudo", "-n", "systemctl", "reload", "nginx")

    def cert_issue(self, host: str) -> None:
        time.sleep(3)  # let nginx pick up the http vhost
        self._run("sudo", "-n", "certbot", "certonly", "--webroot", "-w", "/var/www/html",
                  "-d", host, "--non-interactive", "--keep-until-expiring")

    def cert_delete(self, host: str) -> None:
        try:
            self._run("sudo", "-n", "certbot", "delete", "--cert-name", host, "--non-interactive")
        except RuntimeError:
            pass  # no certificate yet — nothing to delete

    def notify(self, text: str) -> None:
        try:
            guestenv.notify_owner(text)
        except Exception as exc:  # noqa: BLE001 — a site is up even if the owner isn't told
            print(f"NOTIFY-FAILED {exc!r}", file=sys.stderr, flush=True)

    def problem(self) -> str:
        """Why a new address can't be served here ("" = go ahead). An installed nginx is
        left to say it itself (nginx -t, certbot), as it always was."""
        c = self.choice
        if c is None or c.works or (c.backend == webchoice.NGINX and nginx_installed()):
            return ""
        return f"{c.why}. Fix: {c.fix}" if c.fix else c.why


def nginx_installed() -> bool:
    return bool(shutil.which("nginx") or os.path.exists("/usr/sbin/nginx"))


def caddy_data_dir() -> Path:
    """Where Caddy keeps certificates: $XDG_DATA_HOME/caddy (agentkennel-caddy.service sets
    XDG_DATA_HOME=~/.local/share)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "caddy"


class CaddyOps(RealOps):
    """The same server changes on the script install's Caddy, with no sudo:
    site blocks in <AgentKennel>/.sessions/caddy-sites/<host>.caddy (the Caddyfile imports
    them: AGENTKENNEL_CADDY_SITES), reload = the Caddyfile Caddy runs on, POSTed to its
    admin API (Caddy adapts it with its own environment, as `systemctl reload
    agentkennel-caddy` would), certificates = Caddy's own. DNS and the owner's messages are
    RealOps'. A reload Caddy refuses changes nothing: every file is put back."""

    KIND = "caddy"
    SUFFIX = ".caddy"
    VHOSTS = guestenv.caddy_sites_dir()
    CADDYFILE = ROOT.parent / ".sessions" / "Caddyfile.run"   # ExecStartPre writes it

    def __init__(self, admin: Optional[str] = None, caddyfile: Optional[Path] = None,
                 https_port: Optional[int] = None, cert_wait: Optional[float] = None,
                 poll: float = 2.0) -> None:
        super().__init__()
        self.admin = (admin or guestenv.get("GUESTS_CADDY_ADMIN")
                      or "http://localhost:2019").rstrip("/")
        self.caddyfile = Path(caddyfile or self.CADDYFILE)
        self.https_port = int(https_port or 443)
        wait = guestenv.get("GUESTS_CERT_WAIT") or ""
        self.cert_wait = float(cert_wait if cert_wait is not None
                               else (wait if wait.isdigit() else 120))
        self.poll = poll
        self._undo: Dict[str, Optional[str]] = {}   # host -> its text before (None: no file)
        # the site gate's delayed clean-up runs on a timer thread next to a new publish
        self._lock = threading.RLock()

    def _file(self, host: str) -> Path:
        if not isinstance(host, str) or not caddyconf.HOST_RE.fullmatch(host) or ".." in host:
            raise ValueError(f"bad host {host!r}")
        return self.VHOSTS / f"{host}{self.SUFFIX}"

    def _remember(self, host: str) -> None:
        if host not in self._undo:
            f = self._file(host)
            self._undo[host] = f.read_text() if f.exists() else None

    def write_vhost(self, host: str, text: str) -> None:
        f = self._file(host)
        with self._lock:
            self._remember(host)
            tmp = f.with_name(f".{f.name}.tmp")
            tmp.write_text(text)
            tmp.chmod(0o644)
            tmp.replace(f)

    def vhost_exists(self, host: str) -> bool:
        return self._file(host).exists()

    def remove_vhost(self, host: str) -> None:
        f = self._file(host)
        with self._lock:
            self._remember(host)
            f.unlink(missing_ok=True)

    def _put_back(self) -> None:
        for host, text in list(self._undo.items()):
            f = self._file(host)
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(text)
        self._undo.clear()

    def _admin(self, method: str, path: str, body: Optional[bytes] = None,
               ctype: str = "") -> bytes:
        req = urllib.request.Request(self.admin + path, data=body, method=method,
                                     headers={"Content-Type": ctype} if ctype else {})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def reload(self) -> None:
        with self._lock:
            self._reload()

    def _reload(self) -> None:
        try:
            text = self.caddyfile.read_text()
        except OSError:
            self._put_back()
            raise RuntimeError(f"caddy: no {self.caddyfile} (the Caddyfile.run "
                               "agentkennel-caddy.service writes when it starts: is it "
                               "running?)")
        try:
            self._admin("POST", "/load", text.encode(), "text/caddyfile")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error") or detail
            except ValueError:
                pass
            self._put_back()
            raise RuntimeError(f"caddy refused the change, nothing changed: {detail[-400:]}")
        except (urllib.error.URLError, OSError) as exc:
            self._put_back()
            raise RuntimeError(f"caddy: its admin API {self.admin} doesn't answer ({exc}); is "
                               "agentkennel-caddy running? (systemctl status agentkennel-caddy)")
        served = [h for h, _ in self._undo.items() if self._file(h).exists()]
        self._undo.clear()
        if served:
            try:
                loaded = self._admin("GET", "/config/apps/http/servers").decode(errors="replace")
            except (urllib.error.URLError, OSError) as exc:
                raise RuntimeError(f"caddy reloaded, but its config can't be read back ({exc})")
            missing = [h for h in served if json.dumps(h) not in loaded]
            if missing:
                raise RuntimeError(
                    f"caddy reloaded but doesn't serve {', '.join(missing)}: its Caddyfile "
                    "doesn't import the guests' sites folder (AGENTKENNEL_CADDY_SITES in "
                    "agentkennel-caddy.service) — run ./install.sh again")

    def _tls_ok(self, host: str) -> bool:
        """Does Caddy hand out a valid certificate for this name on its https port?"""
        ctx = ssl.create_default_context()
        root = caddy_data_dir() / "pki" / "authorities" / "local" / "root.crt"
        if root.exists():                       # Caddy's own CA (local_certs / tests)
            ctx.load_verify_locations(cafile=str(root))
        try:
            with socket.create_connection(("127.0.0.1", self.https_port), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=host):
                    return True
        except (OSError, ssl.SSLError):
            return False

    def cert_issue(self, host: str) -> None:
        """Caddy gets the certificate by itself after the reload; wait until it is served."""
        deadline = time.monotonic() + self.cert_wait
        while True:
            if self._tls_ok(host):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"caddy has no certificate for {host} after {int(self.cert_wait)} s "
                    "(does the name point at this server, are 80 and 443 open?): "
                    "journalctl -u agentkennel-caddy")
            time.sleep(self.poll)

    def cert_delete(self, host: str) -> None:
        self._file(host)                          # the name is checked before it names a folder
        for d in (caddy_data_dir() / "certificates").glob(f"*/{host}"):
            if d.name == host and d.is_dir() and not d.is_symlink():
                shutil.rmtree(d, ignore_errors=True)


def make_ops(choice: "Optional[webchoice.Choice]" = None) -> RealOps:
    """The ops of the web server that carries the guests' addresses on this server."""
    choice = choice or webchoice.probe()
    ops = CaddyOps() if choice.backend == webchoice.CADDY else RealOps()
    ops.choice = choice
    return ops


class AutoOps:
    """make_ops for the long-running site gate: the choice is asked again after `ttl`
    seconds (at boot it may start before Caddy or nginx), the same instance kept while the
    backend stays the same."""

    def __init__(self, probe=None, clock=time.monotonic, ttl: float = 30) -> None:
        self._probe = probe or webchoice.probe
        self._clock, self._ttl = clock, ttl
        self._ops: Optional[RealOps] = None
        self._at = None

    def current(self) -> RealOps:
        now = self._clock()
        if self._ops is None or now - self._at >= self._ttl:
            choice = self._probe()
            if self._ops is None or (self._ops.KIND != choice.backend and choice.works):
                self._ops = make_ops(choice)       # a backend that works, or the first
            elif self._ops.KIND == choice.backend:
                self._ops.choice = choice          # same backend: its state now
            # else: the probe failed or nothing reports active — keep what we had
            self._at = now
        return self._ops

    @property
    def KIND(self) -> str:  # noqa: N802 — the ops attribute conf_for reads
        return self.current().KIND

    def __getattr__(self, name: str):
        return getattr(self.current(), name)


# --------------------------------------------------------------------------- #
def make_server(bind: str, port: int, gate: Gate) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: Dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _guest(self) -> Optional[str]:
            auth = self.headers.get("authorization", "")
            secret = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            g = gate.book.lookup(secret)
            return g.name if g else None

        def _body(self) -> Dict:
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}

        def _route(self, method: str) -> None:
            guest = self._guest()
            if guest is None:
                return self._json(401, {"error": _t("site_need_pass")})
            try:
                if method == "GET" and self.path == "/sites":
                    return self._json(200, {"sites": gate.sites(guest)})
                if method == "POST" and self.path == "/publish":
                    body = self._body()
                    return self._json(200, gate.publish(guest, body.get("site"), body.get("port")))
                if method == "DELETE" and self.path.startswith("/publish/"):
                    return self._json(200, gate.unpublish(guest, self.path[len("/publish/"):]))
                return self._json(404, {"error": _t("site_routes")})
            except (ValueError, GateError) as exc:
                return self._json(400, {"error": str(exc)})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None) -> int:
    data = Path(os.environ.get("TURNSTILE_DATA", ROOT / "turnstile" / "data"))
    book = ts.GuestBook(data / "guests.json")
    sandboxes = data / "sandboxes.json"

    def registry() -> Dict:
        return json.loads(sandboxes.read_text()) if sandboxes.exists() else {}

    def ip_of(name: str) -> Optional[str]:
        return (registry().get(name) or {}).get("ip")

    gate = Gate(book, AutoOps(), data / "sites.json", ip_of, sandboxes=registry, limit=True)
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["remove"] and len(args) == 2:  # owner: take any site down
        host = args[1]
        reg = gate._load()
        if host not in reg:
            print(f"no site {host}")
            return 1
        gate._remove(host)
        del reg[host]
        gate._save(reg)
        print(f"removed {host}")
        return 0
    if args[:1] == ["list"]:
        for host, v in sorted(gate._load().items()):
            print(f"https://{host}\tguest={v['guest']}\tport={v['port']}\t{v['created']}")
        return 0
    bind = os.environ.get("SITEGATE_BIND", "127.0.0.1")
    port = int(os.environ.get("SITEGATE_PORT", "8794"))
    # sbx.<domain>/f/ when Caddy carries the guests' addresses (localhost only; harmless
    # and unused behind nginx, which serves the folder itself)
    publicfiles.start_in_background("127.0.0.1", files_port(), ROOT / "public")
    print(f"sitegate on {bind}:{port}", file=sys.stderr, flush=True)
    make_server(bind, port, gate).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
