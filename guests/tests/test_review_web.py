"""After review (17.11.2026), guestctl and webchoice:

* a dashboard address can't be a published site's address — checked when a guest is added
  and when its address changes (the site gate checks the other way);
* webchoice reads the install's .env the way systemd does (EnvironmentFile wins over
  Environment=), and Caddy's own CA (AGENTKENNEL_TLS_INTERNAL=1) is a warning: it works, but
  browsers won't trust the guests' certificates;
* the doctor says when addresses made for the other web server are left over (moving a
  guest set from nginx to Caddy, or back, is not automatic)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

GUESTS = Path(__file__).resolve().parents[1]
for p in (GUESTS, GUESTS / "sitegate", GUESTS / "turnstile"):
    sys.path.insert(0, str(p))
import guestctl as gc  # noqa: E402
import sitegate  # noqa: E402
import webchoice as wc  # noqa: E402

SSLIP = "203-0-113-7.sslip.io"


# ── dashboards vs sites, from guestctl's side ───────────────────────────────────────
@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "DATA", tmp_path)
    monkeypatch.setattr(gc, "REGISTRY", tmp_path / "sandboxes.json")
    (tmp_path / "sites.json").write_text(json.dumps(
        {"sb-mila.example.test": {"guest": "old", "site": "sb", "port": 3000}}))
    return tmp_path


def test_an_address_that_is_a_site_is_refused(data):
    with pytest.raises(SystemExit) as e:
        gc.check_address("anna", "mila", {"anna": {"port": 1}})
    assert "sb-mila.example.test" in str(e.value)
    assert gc.check_address("anna", "nika", {"anna": {"port": 1}}) == "nika"


def test_a_new_guest_whose_dashboard_is_a_site_is_refused(data, monkeypatch):
    monkeypatch.setattr(gc, "_docker", lambda *a, **k: pytest.fail("nothing is created"))
    args = type("A", (), {"name": "mila", "tg": 1, "limit": None, "image": "x", "disk_gb": 0})()
    with pytest.raises(SystemExit) as e:
        gc.cmd_add(args)
    assert "sb-mila.example.test" in str(e.value)


# ── webchoice: the install's .env, Caddy's own CA ───────────────────────────────────
def test_unit_env_reads_the_environment_files_too(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENTKENNEL_SITE=deck.example.org\n# x\nOTHER=1\n")

    def run(argv, **kw):
        prop = argv[argv.index("-p") + 1]
        out = {"Environment": f"AGENTKENNEL_SITE={SSLIP} AGENTKENNEL_CADDY_SITES=/x/*.caddy",
               "EnvironmentFiles": f"{env} (ignore_errors=yes)\n{tmp_path}/missing (ignore_errors=yes)"}[prop]
        return type("R", (), {"returncode": 0, "stdout": out + "\n"})()
    got = wc.unit_env("agentkennel-caddy.service", run=run)
    assert got["AGENTKENNEL_SITE"] == "deck.example.org"          # the file wins, as in systemd
    assert got["AGENTKENNEL_CADDY_SITES"] == "/x/*.caddy" and got["OTHER"] == "1"


def test_caddys_own_ca_works_with_a_warning():
    c = wc.decide("", False, True, SSLIP, caddy_global="local_certs\nskip_install_trust\n")
    assert c.works and c.backend == "caddy"
    assert "trust" in c.warning and "--https" in c.warning
    assert wc.decide("", False, True, SSLIP, caddy_global="").warning == ""


def test_probe_reads_caddys_global_options(tmp_path, monkeypatch):
    monkeypatch.setenv("GUESTS_ENV_FILE", str(tmp_path / "none"))
    g = tmp_path / "caddy-global.caddy"
    g.write_text("# written by install.sh\nlocal_certs\n")
    c = wc.probe(is_active=lambda u: u == wc.CADDY_UNIT,
                 unit_env=lambda u: {"AGENTKENNEL_SITE": SSLIP, "AGENTKENNEL_CADDY_SITES": "/x",
                                     "AGENTKENNEL_CADDY_GLOBAL": str(g)})
    assert c.works and c.warning


def test_cli_prints_the_warning(monkeypatch, capsys):
    monkeypatch.setattr(wc, "probe", lambda: wc.decide("", False, True, SSLIP, caddy_global="local_certs"))
    assert wc.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("caddy: ") and out[1].startswith("warning: ")


# ── the doctor: addresses left for the other web server ────────────────────────────
def test_doctor_names_nginx_addresses_left_on_a_caddy_server(tmp_path, monkeypatch):
    monkeypatch.setattr(sitegate.RealOps, "VHOSTS", tmp_path / "nginx")
    monkeypatch.setattr(sitegate.CaddyOps, "VHOSTS", tmp_path / "caddy")
    (tmp_path / "nginx").mkdir()
    for h in ("sb-anna.example.test", "sbx.example.test"):
        (tmp_path / "nginx" / f"{h}.conf").write_text("x")
    c = gc.check_web(wc.decide("", False, True, SSLIP))
    assert c["ok"] is False
    assert "sb-anna.example.test" in c["detail"] and "nginx" in c["detail"]
    assert "guestctl.py web" in c["fix"] and "files-web" in c["fix"]


def test_doctor_quiet_when_nothing_is_left(tmp_path, monkeypatch):
    monkeypatch.setattr(sitegate.RealOps, "VHOSTS", tmp_path / "nginx")
    monkeypatch.setattr(sitegate.CaddyOps, "VHOSTS", tmp_path / "caddy")
    c = gc.check_web(wc.decide("", False, True, SSLIP))
    assert c["ok"] is True and c["detail"].startswith("caddy: ")
    w = gc.check_web(wc.decide("", False, True, SSLIP, caddy_global="local_certs"))
    assert w["ok"] is True and "warning:" in w["detail"]
