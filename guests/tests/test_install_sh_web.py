"""guests/install.sh --check says which web server carries the guests' addresses and why
(guests/webchoice.py), and no longer asks for nginx + certbot on a Caddy server."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# by path: tests/ and guests/tests/ both have a test_install_sh.py (the sandboxed guests/install.sh)
_spec = importlib.util.spec_from_file_location(
    "_guests_install_sh", Path(__file__).resolve().parent / "test_install_sh.py")
_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lib)
Box = _lib.Box
SHIM = _lib.SHIM
BASH = _lib.BASH

PY = ('case "$*" in *webchoice.py*) printf "%b" "$FAKE_WEB"; exit "${FAKE_WEB_RC:-0}";; '
      '*image-tag*) echo agentkennel-guest:v9.9.9;; esac; exit 0')


@pytest.fixture
def box(tmp_path):
    b = Box(tmp_path)
    b._exe(b.bin / "python3", SHIM.format(bash=BASH, name="python3", body=PY))
    return b


def test_caddy_server_is_ok_without_nginx(box):
    for tool in ("nginx", "certbot"):
        (box.bin / tool).unlink()
    box.fill_env()
    r = box.run("--check", FAKE_WEB="caddy: agentkennel-caddy.service serves port 443 (x.sslip.io)\n")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "web addresses: caddy: agentkennel-caddy.service serves port 443" in out
    assert "nginx and certbot are needed" not in out
    assert any(c.startswith("python3 ") and "webchoice.py" in c for c in box.calls())


def test_a_caddy_without_443_is_explained_but_not_fatal(box):
    box.fill_env()
    r = box.run("--check", FAKE_WEB_RC=1,
                FAKE_WEB="caddy: ... no public 443 (plain http on :8765 (--http))\n"
                         "guests' web addresses can't work: run ./install.sh --https ...\n")
    assert r.returncode == 0, r.stdout + r.stderr          # Telegram still works
    err = r.stderr
    assert "install.sh --https" in err and "Telegram works without them" in err


def test_nginx_without_certbot_is_named(box):
    (box.bin / "certbot").unlink()
    box.fill_env()
    r = box.run("--check", FAKE_WEB="nginx: nginx is running\n")
    assert "web addresses: nginx: nginx is running" in r.stdout
    assert "certbot" in r.stderr


def test_a_warning_is_passed_on(box):
    box.fill_env()
    r = box.run("--check", FAKE_WEB="caddy: agentkennel-caddy.service serves port 443 (x)\n"
                                     "warning: AgentKennel's Caddy uses its own certificate authority\n")
    assert r.returncode == 0
    assert "web addresses: caddy: agentkennel-caddy.service" in r.stdout
    assert "warning: web addresses: AgentKennel's Caddy uses its own certificate authority" in r.stderr
