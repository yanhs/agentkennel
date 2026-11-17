#!/usr/bin/env python3
"""Which web server carries the guests' addresses: nginx + certbot, or AgentKennel's Caddy.

    python3 guests/webchoice.py [--json]     the choice, why, and the fix when it can't work

The guests' four browser-facing things — a guest's dashboard sb-<name>.<domain>, the
owner's Open button on it, the sites guests publish (<site>-<guest>.<domain>) and the
shared files address sbx.<domain> — are virtual hosts on ports 80/443. Two ways:

  nginx   site files in guests/nginx/sites, linked into /etc/nginx/sites-enabled, a
          certificate per name from certbot (needs sudo; the owner's server since 1.x)
  caddy   site files in <AgentKennel>/.sessions/caddy-sites/<host>.caddy, imported by the
          script install's Caddy (agentkennel-caddy.service, AGENTKENNEL_CADDY_SITES); Caddy
          gets the certificates itself; nothing needs sudo

GUESTS_WEB=nginx|caddy (guests/.env) wins. Otherwise: Caddy when AgentKennel's Caddy is
what serves 443, else nginx when nginx runs. Both can't own 443, so a Caddy on 443 is the
one browsers reach. A Caddy without a public 443 (--http, self-signed on :8765, a name on
:8443) can't give guests addresses: decide() says so, with the fix.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guestenv  # noqa: E402

NGINX, CADDY = "nginx", "caddy"
CADDY_UNIT = "agentkennel-caddy.service"
NGINX_UNIT = "nginx.service"
HTTPS_FIX = ("run ./install.sh --https (ports 80 and 443 free, or --https your-domain.com): "
             "AgentKennel's Caddy then serves 443 and gives every guest address its own "
             "certificate")


@dataclass(frozen=True)
class Choice:
    backend: str          # "nginx" | "caddy"
    works: bool           # guests' addresses can be served this way right now
    why: str              # how the choice was made, in plain words
    fix: str = ""         # what to do when it can't work
    warning: str = ""     # works, but something a person should know

    def as_dict(self) -> Dict:
        return asdict(self)


def site_kind(site: str) -> str:
    """What install.sh's AGENTKENNEL_SITE says Caddy serves:
    "443" (a name, trusted certificate on 443), "alt-port" (a name on another port, 443 is
    someone else's), "self-signed" (https://...:8765), "http" (:8765, --http), "unknown"."""
    first = (site or "").split(",")[0].strip()
    if not first:
        return "unknown"
    if first.startswith(":"):
        return "http"
    if first.startswith("https://"):
        return "self-signed"
    if ":" in first:
        return "alt-port"
    return "443"


_MODE_WORDS = {"http": "plain http on :8765 (--http)",
               "self-signed": "a self-signed certificate on :8765",
               "alt-port": "HTTPS on :8443 (port 443 is another program's)",
               "unknown": "no known address"}


INTERNAL_CA_WARNING = (
    "AgentKennel's Caddy uses its own certificate authority (AGENTKENNEL_TLS_INTERNAL=1, "
    "local_certs): browsers won't trust the guests' addresses — for real certificates run "
    "./install.sh --https without AGENTKENNEL_TLS_INTERNAL")


def decide(setting: str, nginx_active: bool, caddy_active: bool, caddy_site: str,
           caddy_imports: bool = True, caddy_global: str = "") -> Choice:
    """The choice, from what the server shows (pure: every input is passed in)."""
    s = (setting or "").strip().lower()
    kind = site_kind(caddy_site) if caddy_active else "unknown"
    on_443 = caddy_active and kind == "443"

    def caddy(why: str) -> Choice:
        if not on_443:
            mode = _MODE_WORDS.get(kind, kind) if caddy_active else "not running"
            return Choice(CADDY, False, f"{why}; but AgentKennel's Caddy has no public 443 "
                                        f"({mode}), so guests' addresses can't be served",
                          HTTPS_FIX)
        if not caddy_imports:
            return Choice(CADDY, False, f"{why}; but {CADDY_UNIT} has no "
                                        "AGENTKENNEL_CADDY_SITES (installed before guests "
                                        "could use Caddy), so it would not see their sites",
                          "run ./install.sh again (as you, in the AgentKennel folder): it "
                          "adds the guests' sites folder to Caddy")
        own_ca = any(line.strip() == "local_certs" for line in (caddy_global or "").splitlines())
        return Choice(CADDY, True, why, warning=INTERNAL_CA_WARNING if own_ca else "")

    if s == NGINX:
        return Choice(NGINX, True, "GUESTS_WEB=nginx in guests/.env")
    if s == CADDY:
        return caddy("GUESTS_WEB=caddy in guests/.env")
    if s:
        return Choice(NGINX, False, f"GUESTS_WEB={setting.strip()} in guests/.env is not a "
                                    "web server this module knows",
                      "set GUESTS_WEB to nginx or caddy in guests/.env, or remove the line "
                      "(then the server decides)")
    if on_443:
        return caddy(f"{CADDY_UNIT} serves port 443 ({caddy_site.split(',')[0].strip()})")
    if nginx_active:
        return Choice(NGINX, True, "nginx is running (sites in guests/nginx/sites, "
                                   "certificates from certbot)")
    if caddy_active:
        return caddy(f"{CADDY_UNIT} is AgentKennel's web server and nginx is not running")
    return Choice(NGINX, False, "neither nginx nor AgentKennel's Caddy is running",
                  HTTPS_FIX + "; or install nginx and certbot on 80/443")


# --------------------------------------------------------------------------- #
def unit_active(unit: str, run=subprocess.run) -> bool:
    try:
        return run(["systemctl", "is-active", "--quiet", unit], capture_output=True,
                   timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def parse_unit_env(line: str) -> Dict[str, str]:
    """`systemctl show -p Environment --value` -> {name: value} (values with spaces come
    quoted)."""
    out: Dict[str, str] = {}
    try:
        words = shlex.split(line or "")
    except ValueError:
        words = (line or "").split()
    for word in words:
        key, sep, value = word.partition("=")
        if sep:
            out[key] = value
    return out


def _show(unit: str, prop: str, run) -> str:
    try:
        r = run(["systemctl", "show", "-p", prop, "--value", unit],
                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def unit_env(unit: str, run=subprocess.run) -> Dict[str, str]:
    """The unit's environment as its processes get it: Environment=, then every
    EnvironmentFile= on top (systemd lets the files win; install.sh's units read the
    install's .env that way)."""
    env = parse_unit_env(_show(unit, "Environment", run))
    for line in _show(unit, "EnvironmentFiles", run).splitlines():
        path = line.split(" (", 1)[0].strip()
        if not path:
            continue
        try:
            env.update(guestenv.parse(Path(path).read_text(encoding="utf-8")))
        except OSError:
            pass
    return env


def probe(is_active: Callable[[str], bool] = unit_active,
          unit_env: Callable[[str], Dict[str, str]] = unit_env) -> Choice:
    """The choice on this server: GUESTS_WEB, nginx.service, agentkennel-caddy.service."""
    caddy_active = is_active(CADDY_UNIT)
    env = unit_env(CADDY_UNIT) if caddy_active else {}
    site = env.get("AGENTKENNEL_SITE") or env.get("AGENTDECK_SITE") or ""
    try:
        global_opts = Path(env["AGENTKENNEL_CADDY_GLOBAL"]).read_text(encoding="utf-8")
    except (KeyError, OSError):
        global_opts = ""
    return decide(guestenv.get("GUESTS_WEB") or "", is_active(NGINX_UNIT), caddy_active,
                  site, caddy_imports=bool(env.get("AGENTKENNEL_CADDY_SITES")),
                  caddy_global=global_opts)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    c = probe()
    if "--json" in args:
        print(json.dumps(c.as_dict(), ensure_ascii=False))
        return 0
    print(f"{c.backend}: {c.why}")
    if c.warning:
        print(f"warning: {c.warning}")
    if not c.works:
        print(f"guests' web addresses can't work: {c.fix}")
    return 0 if c.works else 1


if __name__ == "__main__":
    sys.exit(main())
