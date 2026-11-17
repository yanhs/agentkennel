"""Which web server carries the guests' addresses (sb-<name>, Open, their sites, sbx.):
nginx + certbot (the owner's server since 1.x) or the script install's own Caddy.

GUESTS_WEB=nginx|caddy in guests/.env wins; otherwise the server decides: Caddy when
AgentKennel's Caddy is what serves 443, nginx when nginx runs. A Caddy without a public
443 (--http, self-signed on :8765, a name on :8443) can't give guests an address: that is
said plainly, with the fix (install.sh --https).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

GUESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUESTS))
import webchoice as wc  # noqa: E402

SSLIP = "203-0-113-7.sslip.io"


# ── what AgentKennel's Caddy serves (install.sh's AGENTKENNEL_SITE) ───────────────
@pytest.mark.parametrize("site,kind", [
    (SSLIP, "443"),
    ("deck.example.org", "443"),
    (f"{SSLIP}:8443", "alt-port"),
    ("https://203.0.113.7:8765, https://:8765", "self-signed"),
    ("https://:8765", "self-signed"),
    (":8765", "http"),
    ("", "unknown"),
    ("  ", "unknown"),
])
def test_site_kind(site, kind):
    assert wc.site_kind(site) == kind


# ── the decision ──────────────────────────────────────────────────────────────────
def test_owners_server_nginx_running_no_caddy_on_443():
    c = wc.decide("", nginx_active=True, caddy_active=False, caddy_site="")
    assert c.backend == "nginx" and c.works
    assert "nginx" in c.why


def test_script_install_caddy_on_443():
    c = wc.decide("", nginx_active=False, caddy_active=True, caddy_site=SSLIP)
    assert c.backend == "caddy" and c.works
    assert "443" in c.why and "agentkennel-caddy" in c.why


def test_caddy_on_443_wins_over_an_nginx_that_runs_elsewhere():
    """nginx can't have 443 when our Caddy has it: Caddy is the one browsers reach."""
    c = wc.decide("", nginx_active=True, caddy_active=True, caddy_site=SSLIP)
    assert c.backend == "caddy" and c.works


def test_caddy_without_443_and_nginx_running_uses_nginx():
    c = wc.decide("", nginx_active=True, caddy_active=True, caddy_site=f"{SSLIP}:8443")
    assert c.backend == "nginx" and c.works


@pytest.mark.parametrize("site,mode", [(":8765", "--http"),
                                       ("https://:8765", "self-signed"),
                                       (f"{SSLIP}:8443", "8443")])
def test_caddy_without_a_public_443_says_why_and_how_to_fix(site, mode):
    c = wc.decide("", nginx_active=False, caddy_active=True, caddy_site=site)
    assert c.backend == "caddy" and not c.works
    assert mode in c.why
    assert "install.sh --https" in c.fix


def test_nothing_serving_web():
    c = wc.decide("", nginx_active=False, caddy_active=False, caddy_site="")
    assert not c.works and c.fix
    assert "install.sh --https" in c.fix


@pytest.mark.parametrize("value", ["nginx", "NGINX", " nginx "])
def test_setting_nginx_wins(value):
    c = wc.decide(value, nginx_active=False, caddy_active=True, caddy_site=SSLIP)
    assert c.backend == "nginx" and "GUESTS_WEB=nginx" in c.why


def test_setting_caddy_wins():
    c = wc.decide("caddy", nginx_active=True, caddy_active=True, caddy_site=SSLIP)
    assert c.backend == "caddy" and c.works and "GUESTS_WEB=caddy" in c.why


def test_setting_caddy_on_a_caddy_without_443_still_says_it_cant_work():
    c = wc.decide("caddy", nginx_active=True, caddy_active=True, caddy_site=":8765")
    assert c.backend == "caddy" and not c.works and "install.sh --https" in c.fix


def test_a_wrong_setting_is_named():
    c = wc.decide("apache", nginx_active=True, caddy_active=False, caddy_site="")
    assert not c.works and "GUESTS_WEB=apache" in c.why and "nginx or caddy" in c.fix


def test_caddy_that_does_not_import_the_sites_folder():
    """An install.sh from before this change wrote no AGENTKENNEL_CADDY_SITES: Caddy would
    never see the guests' addresses — run install.sh again."""
    c = wc.decide("", nginx_active=False, caddy_active=True, caddy_site=SSLIP, caddy_imports=False)
    assert c.backend == "caddy" and not c.works
    assert "install.sh" in c.fix and "AGENTKENNEL_CADDY_SITES" in c.why


def test_as_dict_is_json():
    c = wc.decide("", nginx_active=True, caddy_active=False, caddy_site="")
    assert json.loads(json.dumps(c.as_dict())) == {"backend": "nginx", "works": True,
                                                   "why": c.why, "fix": ""}


# ── reading the server (every outside call swapped) ───────────────────────────────
def test_caddy_env_from_systemctl_show():
    line = ('HOME=/home/u LANG=C.UTF-8 "AGENTKENNEL_SITE=https://1.2.3.4:8765, https://:8765" '
            'AGENTKENNEL_CADDY_SITES=/home/u/agentkennel/.sessions/caddy-sites/*.caddy')
    env = wc.parse_unit_env(line)
    assert env["AGENTKENNEL_SITE"] == "https://1.2.3.4:8765, https://:8765"
    assert env["AGENTKENNEL_CADDY_SITES"].endswith("caddy-sites/*.caddy")


def test_probe_reads_the_setting_and_the_units(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("GUESTS_DOMAIN=example.test\n")
    monkeypatch.setenv("GUESTS_ENV_FILE", str(env))
    active = {"agentkennel-caddy.service"}
    c = wc.probe(is_active=lambda u: u in active,
                 unit_env=lambda u: {"AGENTKENNEL_SITE": SSLIP,
                                     "AGENTKENNEL_CADDY_SITES": "/x/*.caddy"})
    assert c.backend == "caddy" and c.works
    env.write_text("GUESTS_DOMAIN=example.test\nGUESTS_WEB=nginx\n")
    assert wc.probe(is_active=lambda u: u in active, unit_env=lambda u: {}).backend == "nginx"


def test_probe_on_an_nginx_server(monkeypatch, tmp_path):
    monkeypatch.setenv("GUESTS_ENV_FILE", str(tmp_path / "none"))
    c = wc.probe(is_active=lambda u: u == "nginx.service", unit_env=lambda u: {})
    assert c.backend == "nginx" and c.works


def test_cli_prints_the_choice(monkeypatch, capsys):
    monkeypatch.setattr(wc, "probe", lambda: wc.decide("", False, True, ":8765"))
    assert wc.main([]) == 1
    out = capsys.readouterr().out
    assert "caddy" in out and "install.sh --https" in out
    monkeypatch.setattr(wc, "probe", lambda: wc.decide("", True, False, ""))
    assert wc.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "nginx"
