"""A proxy of your own in front of AgentKennel is believed about https (01.11.2026).

The owner's test server ran install.sh --http behind nginx with HTTPS: "+ New" got 403.
nginx said X-Forwarded-Proto: https, but Caddy trusts no proxy by default and replaced
it with its own "http"; status_server then expected the Origin http://<host>, the
browser sent https://<host>. Caddy now passes the header on from a proxy on this
machine or its private network (nginx on the host, Docker's port forwarding). Only the
dashboard's own origin and the cookie's Secure flag read it, so a forged header gains
nothing: a browser can't send it cross-site without a preflight we never answer.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def global_block(text: str) -> str:
    """The Caddyfile's global options: the first top-level { ... } block."""
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    start = body.index("{")
    assert body[:start].strip() == "", "the global options come first"
    depth = 0
    for i, ch in enumerate(body[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            return body[start:i + 1]
    raise AssertionError("unbalanced braces")


@pytest.mark.parametrize("caddyfile", ["Caddyfile", "docker/Caddyfile"])
def test_caddy_believes_a_proxy_on_this_machine_or_its_private_network(caddyfile):
    block = global_block((REPO / caddyfile).read_text())
    assert re.search(r"servers\s*\{[^{}]*trusted_proxies\s+static\s+private_ranges[^{}]*\}",
                     block), block


def test_no_other_servers_block_is_added_by_the_installer():
    # install.sh imports its own global options; a second `servers` block would clash
    text = (REPO / "install.sh").read_text()
    conf = text.split("caddy_global_conf() {", 1)[1].split("\n}\n", 1)[0]
    assert "servers" not in conf
