"""The guests' addresses as Caddy site blocks — the Caddy side of sitegate.render_vhost,
render_gone, render_moved and nginx/sbx.conf.template (guests/webchoice.py picks one).

Each block is one file, <AgentKennel>/.sessions/caddy-sites/<host>.caddy, imported at the
top level of the script install's Caddyfile (AGENTKENNEL_CADDY_SITES). Caddy gets each
name's certificate itself and redirects http to https, so the nginx port-80 servers and
the certbot webroot have no counterpart here.

nginx -> Caddy, directive by directive:
  listen 443 ssl; ssl_certificate ...; server_name H   site address H (automatic HTTPS)
  listen 80 + acme-challenge + 301 to https            Caddy's own HTTP->HTTPS redirect
                                                       and ACME challenges
  client_max_body_size 50m                             request_body { max_size 50MiB }
  location = /_owner-entry { proxy_pass P/path; }      handle /_owner-entry { rewrite *
                                                       /path?{query}; reverse_proxy P }
  proxy_pass http://ip:port                            reverse_proxy ip:port
  proxy_http_version 1.1; Upgrade/Connection           built in (reverse_proxy passes
                                                       websockets through)
  proxy_set_header Host $host                          built in (Host is passed as is)
  proxy_set_header X-Real-IP $remote_addr              header_up X-Real-IP {remote_host}
  proxy_set_header X-Forwarded-For ...                 built in (Caddy sets it; a client's
                                                       own value is kept only from
                                                       trusted proxies)
  proxy_set_header X-Forwarded-Proto https             built in (https on this listener)
  if ($http_origin = here) Origin <- known, not on     @sbx_origin {header Origin here;
  a websocket                                          not header Upgrade *}
                                                       request_header @sbx_origin Origin
  proxy_read_timeout 300s                              none: Caddy has no read timeout to
                                                       a backend by default (>= 300 s)
  (old workers keep open websockets on reload)         stream_close_delay 10m
  return 404 (stub / pending)                          respond 404
  return 301 https://to$request_uri                    redir https://to{uri} permanent
  sbx: alias + autoindex off + disable_symlinks on     reverse_proxy to publicfiles.py
                                                       (Caddy's file_server follows
                                                       symlinks, so it is not used)
  add_header ... always                                header_down ... (every answer)
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlsplit

HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
IPV4_RE = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$")
ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")
BODY_MAX = "50MiB"             # nginx: client_max_body_size 50m
# open terminals outlive a reload (nginx: old workers) by this much; short, because each
# reload keeps the old handlers until then (the site gate rate-limits reloads too)
STREAM_CLOSE_DELAY = "10m"


def _host(host: str) -> str:
    if not isinstance(host, str) or not HOST_RE.fullmatch(host) or ".." in host:
        raise ValueError(f"bad host {host!r}")
    return host


def _ip(ip: str) -> str:
    if not isinstance(ip, str) or not IPV4_RE.fullmatch(ip):
        raise ValueError(f"bad address {ip!r}")
    return ip


def _port(port) -> int:
    if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(f"bad port {port!r}")
    return int(port)


def _origin(value: str) -> str:
    if not isinstance(value, str) or not ORIGIN_RE.fullmatch(value):
        raise ValueError(f"bad origin {value!r}")
    return value


def _entry_block(path: str, upstream: str) -> str:
    """The owner's way in (guestctl.OWNER_ENTRY): exactly one path to the panel."""
    if not PATH_RE.fullmatch(path or ""):
        raise ValueError(f"bad entry path {path!r}")
    u = urlsplit(upstream)
    if u.scheme != "http" or not u.hostname or not PATH_RE.fullmatch(u.path or "/"):
        raise ValueError(f"bad entry upstream {upstream!r}")
    target = f"{_ip(u.hostname)}:{_port(u.port or 80)}"
    return (
        f"\thandle {path} {{\n"
        f"\t\trewrite * {u.path or '/'}?{{query}}\n"
        f"\t\treverse_proxy {target}\n"
        "\t}\n"
    )


def render_site(host: str, ip: str, port, tls: bool = True,
                entry: Optional[Tuple[str, str]] = None,
                origin: Optional[Tuple[str, str]] = None) -> str:
    """A guest's dashboard or published site: everything to the sandbox's ip:port.
    tls=False: the name answers 404 while its certificate comes (sitegate's "pending").
    entry=(path, upstream url): the owner's Open, before the catch-all.
    origin=(this address's origin, the one the app knows): the browser's Origin is swapped
    for the known one, except on a websocket (the terminal checks Origin against Host)."""
    host, ip, port = _host(host), _ip(ip), _port(port)
    if not tls:
        return (f"# guest site {host} (certificate pending)\n"
                f"{host} {{\n\trespond 404\n}}\n")
    swap = ""
    if origin:
        here, known = _origin(origin[0]), _origin(origin[1])
        swap = (
            "\t\t@sbx_origin {\n"
            f'\t\t\theader Origin "{here}"\n'
            "\t\t\tnot header Upgrade *\n"
            "\t\t}\n"
            f'\t\trequest_header @sbx_origin Origin "{known}"\n'
        )
    return (
        f"# guest site {host} -> sandbox {ip}:{port} (managed by sitegate)\n"
        f"{host} {{\n"
        "\trequest_body {\n"
        f"\t\tmax_size {BODY_MAX}\n"
        "\t}\n"
        + (_entry_block(*entry) if entry else "")
        + "\thandle {\n"
        + swap
        + f"\t\treverse_proxy {ip}:{port} {{\n"
        "\t\t\theader_up X-Real-IP {remote_host}\n"
        f"\t\t\tstream_close_delay {STREAM_CLOSE_DELAY}\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )


def render_gone(host: str) -> str:
    """A taken-down address: 404 on its own name (http and https) until the name is gone."""
    host = _host(host)
    return (f"# guest site {host} (removed; stub until DNS is gone)\n"
            f"http://{host}, https://{host} {{\n\trespond 404\n}}\n")


def render_moved(host: str, to: str) -> str:
    """An address that moved: every path goes to the same one on `to`."""
    host, to = _host(host), _host(to)
    return (f"# guest site {host} (moved to {to})\n"
            f"{host} {{\n\tredir https://{to}{{uri}} permanent\n}}\n")


def render_files(host: str, files_port) -> str:
    """sbx.<domain>: /f/<guest>-<key>/<file> from the symlink-safe file server
    (sitegate/publicfiles.py on 127.0.0.1), nothing else."""
    host, files_port = _host(host), _port(files_port)
    return (
        f"# guests' shared files {host} -> 127.0.0.1:{files_port} (sitegate/publicfiles.py)\n"
        f"{host} {{\n"
        "\thandle /f/* {\n"
        f"\t\treverse_proxy 127.0.0.1:{files_port} {{\n"
        '\t\t\theader_down Content-Security-Policy "sandbox"\n'
        '\t\t\theader_down X-Content-Type-Options "nosniff"\n'
        '\t\t\theader_down X-Robots-Tag "noindex, nofollow"\n'
        '\t\t\theader_down Cache-Control "no-cache"\n'
        "\t\t}\n"
        "\t}\n"
        "\thandle {\n"
        "\t\trespond 404\n"
        "\t}\n"
        "}\n"
    )


def serves(text: str, port) -> bool:
    """Is this a live block proxying to 127.0.0.1:<port> (not pending, not a stub)?"""
    return f"\t\treverse_proxy 127.0.0.1:{port} {{\n" in text


def has_entry(text: str, path: str) -> bool:
    return f"\thandle {path} {{\n" in text
