"""guestctl on a server where the script install's Caddy carries the guests' addresses:
the same commands and the same order of steps as on nginx (tests/test_owner_entry.py,
test_move_domain.py, test_guest_address.py), with Caddy site blocks and CaddyOps. Plus the
doctor's "Web addresses" line and the guest bot finding a dashboard's Caddy block."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

GUESTS = Path(__file__).resolve().parents[1]
for p in (GUESTS, GUESTS / "sitegate", GUESTS / "tgrelay", GUESTS / "turnstile"):
    sys.path.insert(0, str(p))
import guestctl as gc  # noqa: E402
import sitegate  # noqa: E402
import caddyconf  # noqa: E402
import tgrelay  # noqa: E402
import webchoice  # noqa: E402

HOST = "sb-anna.example.test"


class CaddyFake:
    KIND = "caddy"
    SUFFIX = ".caddy"

    def __init__(self, vhosts: Path, problem: str = ""):
        self.VHOSTS = vhosts
        self.log = []
        self._problem = problem

    def problem(self):
        return self._problem

    def dns_create(self, host):
        self.log.append(("dns+", host))

    def dns_delete(self, host):
        self.log.append(("dns-", host))

    def write_vhost(self, host, text):
        (self.VHOSTS / f"{host}.caddy").write_text(text)
        self.log.append(("write", host, text))

    def vhost_exists(self, host):
        return (self.VHOSTS / f"{host}.caddy").exists()

    def remove_vhost(self, host):
        (self.VHOSTS / f"{host}.caddy").unlink(missing_ok=True)
        self.log.append(("remove", host))

    def reload(self):
        self.log.append(("reload",))

    def cert_issue(self, host):
        self.log.append(("cert+", host))

    def cert_delete(self, host):
        self.log.append(("cert-", host))


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(gc, "DATA", d)
    monkeypatch.setattr(gc, "REGISTRY", d / "sandboxes.json")
    (d / "sandboxes.json").write_text(json.dumps({"anna": {"port": 18101, "telegram_id": 111}}))
    gc.ts.GuestBook(d / "guests.json").add("anna", 10, telegram_id=111)
    monkeypatch.setattr(gc, "sandbox_auth_secret", lambda name: b"k" * 32)
    monkeypatch.setattr(gc, "_container_env", lambda name: {"AGENTKENNEL_ORIGIN": f"https://{HOST}"})
    return d


@pytest.fixture
def ops(tmp_path, monkeypatch):
    v = tmp_path / "caddy-sites"
    v.mkdir()
    o = CaddyFake(v)
    monkeypatch.setattr(gc, "_site_ops", lambda: o)
    return o


# ── the owner entry text on nginx stays byte for byte ──────────────────────────────
def test_owner_entry_location_on_nginx_is_unchanged():
    assert gc.owner_entry_location() == (
        "    location = /_owner-entry {\n"
        "        proxy_pass http://127.0.0.1:3011/api/guests/enter;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Forwarded-Proto https;\n"
        "    }\n")


def test_dashboard_on_caddy_has_the_entry_and_the_sandbox(monkeypatch):
    monkeypatch.setattr(gc, "_container_env", lambda name: {"AGENTKENNEL_ORIGIN": "https://sb-anna.old.test"})
    text = gc.dashboard_vhost("anna", 18101, conf=sitegate.CaddyConf)
    assert f"\n{HOST} {{\n" in text
    assert "handle /_owner-entry {" in text and "rewrite * /api/guests/enter?{query}" in text
    assert "reverse_proxy 127.0.0.1:3011" in text
    assert caddyconf.serves(text, 18101)
    assert 'request_header @sbx_origin Origin "https://sb-anna.old.test"' in text
    assert "listen" not in text and "proxy_pass" not in text


# ── web / web-off / Open ───────────────────────────────────────────────────────────
def test_web_on_caddy(data, ops, capsys):
    assert gc.main(["web", "anna"]) == 0
    names = [e[0] for e in ops.log]
    assert names == ["dns+", "write", "reload", "cert+", "write", "reload"]
    writes = [e[2] for e in ops.log if e[0] == "write"]
    assert "respond 404" in writes[0]
    assert caddyconf.serves(writes[1], 18101) and caddyconf.has_entry(writes[1], gc.OWNER_ENTRY)
    assert HOST in capsys.readouterr().out


def test_web_is_refused_with_the_fix_when_caddy_cant_serve(data, tmp_path, monkeypatch):
    o = CaddyFake(tmp_path, problem="AgentKennel's Caddy has no public 443 (--http). "
                                    "Fix: run ./install.sh --https")
    monkeypatch.setattr(gc, "_site_ops", lambda: o)
    with pytest.raises(SystemExit) as e:
        gc.main(["web", "anna"])
    assert "install.sh --https" in str(e.value)
    assert o.log == []


def test_open_adds_the_entry_to_an_older_caddy_dashboard(data, ops, capsys):
    f = ops.VHOSTS / f"{HOST}.caddy"
    f.write_text(caddyconf.render_site(HOST, "127.0.0.1", 18101))
    assert gc.main(["session", "anna"]) == 0
    assert caddyconf.has_entry(f.read_text(), gc.OWNER_ENTRY)
    assert [e[0] for e in ops.log] == ["write", "reload"]
    assert gc.main(["session", "anna"]) == 0
    assert [e[0] for e in ops.log] == ["write", "reload"]          # up to date


@pytest.mark.parametrize("text", [caddyconf.render_site(HOST, "127.0.0.1", 18101, tls=False),
                                  caddyconf.render_gone(HOST)], ids=["pending", "gone"])
def test_open_leaves_a_pending_or_removed_page_alone(data, ops, text):
    f = ops.VHOSTS / f"{HOST}.caddy"
    f.write_text(text)
    gc.main(["session", "anna"])
    assert f.read_text() == text and ops.log == []


def test_web_off_on_caddy_puts_the_stub_first(ops):
    gc.take_down_web("anna", ops, delay=0)
    assert [e[0] for e in ops.log] == ["write", "reload", "dns-", "remove", "reload", "cert-"]
    assert ops.log[0][2] == caddyconf.render_gone(HOST)


def test_recreate_rewrites_the_caddy_dashboard(data, ops, monkeypatch):
    monkeypatch.setattr(gc, "web_exists", lambda name: True)
    gc.refresh_web("anna", 18102)
    assert caddyconf.serves(ops.log[0][2], 18102) and ops.log[-1] == ("reload",)


def test_web_exists_sees_a_caddy_block(tmp_path, monkeypatch):
    monkeypatch.setattr(sitegate.RealOps, "VHOSTS", tmp_path / "nginx")
    monkeypatch.setattr(sitegate.CaddyOps, "VHOSTS", tmp_path / "caddy")
    monkeypatch.setattr(gc, "_registry", lambda: {"anna": {"port": 18101}})
    (tmp_path / "caddy").mkdir()
    assert gc.web_exists("anna") is False
    (tmp_path / "caddy" / f"{HOST}.caddy").write_text("x")
    assert gc.web_exists("anna") is True


# ── address / move-domain / files ───────────────────────────────────────────────────
def test_new_address_on_caddy(monkeypatch, ops):
    reg = {"anna": {"port": 18101}}
    monkeypatch.setattr(gc, "_registry", lambda: reg)
    monkeypatch.setattr(gc, "_save_registry", lambda r: reg.update(r))
    monkeypatch.setattr(gc, "_container_env", lambda n: {"AGENTKENNEL_ORIGIN": f"https://{HOST}"})
    (ops.VHOSTS / f"{HOST}.caddy").write_text("x")
    gc.set_address("anna", "mila", ops)
    final = {e[1]: e[2] for e in ops.log if e[0] == "write"}
    assert caddyconf.serves(final["sb-mila.example.test"], 18101)
    assert final[HOST] == caddyconf.render_moved(HOST, "sb-mila.example.test")


def test_move_domain_on_caddy(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "_container_env", lambda name: {"AGENTKENNEL_ORIGIN": f"https://sb-{name}.old.test"})
    ops = CaddyFake(tmp_path)
    for h in ("sbx.old.test", "sb-anna.old.test"):
        (tmp_path / f"{h}.caddy").write_text("x")
    gc.move_domain("old.test", ops=ops, guests={"anna": {"port": 18101}},
                   fix_rules=lambda *a: None)
    final = {e[1]: e[2] for e in ops.log if e[0] == "write"}
    assert final["sbx.example.test"] == caddyconf.render_files("sbx.example.test", 8795)
    assert caddyconf.serves(final[HOST], 18101)
    assert final["sb-anna.old.test"] == caddyconf.render_moved("sb-anna.old.test", HOST)


def test_files_web_on_caddy(monkeypatch, tmp_path, ops):
    monkeypatch.setattr(gc, "PUBLIC_ROOT", str(tmp_path / "public"))
    assert gc.main(["files-web"]) == 0
    writes = [e for e in ops.log if e[0] == "write"]
    assert writes[-1][2] == caddyconf.render_files("sbx.example.test", 8795)
    assert [e[0] for e in ops.log] == ["dns+", "write", "reload", "cert+", "write", "reload"]


# ── doctor ──────────────────────────────────────────────────────────────────────────
def test_doctor_reports_the_web_server(monkeypatch, tmp_path):
    monkeypatch.setenv("GUESTS_ENV_FILE", str(tmp_path / "none"))
    kw = dict(run=lambda argv, **k: type("R", (), {"returncode": 0, "stdout": "27", "stderr": ""})(),
              get_me=lambda t: "b", resolve=lambda h: [], detect_ip=lambda: "",
              cf_zone=lambda d: None, is_active=lambda u: True)
    checks = gc.doctor(**kw, web=lambda: webchoice.decide("", False, True, ":8765"))
    web = {c["id"]: c for c in checks}["web"]
    assert web["ok"] is False and "caddy" in web["detail"] and "install.sh --https" in web["fix"]
    checks = gc.doctor(**kw, web=lambda: webchoice.decide("", True, False, ""))
    web = {c["id"]: c for c in checks}["web"]
    assert web["ok"] is True and web["detail"].startswith("nginx: ")


def test_doctor_cli_includes_the_web_line(monkeypatch, capsys):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return [{"id": "web", "title": "Web addresses", "ok": True, "detail": "caddy: x", "fix": ""}]
    monkeypatch.setattr(gc, "doctor", fake)
    gc.main(["doctor"])
    assert "web" in seen and "Web addresses: caddy: x" in capsys.readouterr().out


# ── the guest bot finds the address on Caddy too ────────────────────────────────────
def test_dashboard_info_finds_a_caddy_block(tmp_path):
    data, nginx, caddy = tmp_path / "data", tmp_path / "nginx", tmp_path / "caddy"
    for d in (data, nginx, caddy):
        d.mkdir()
    (data / "sandboxes.json").write_text(json.dumps({"anna": {"port": 18101}}))
    assert tgrelay.dashboard_info("anna", data, (nginx, caddy)) is None
    (caddy / f"{HOST}.caddy").write_text("x")
    assert tgrelay.dashboard_info("anna", data, (nginx, caddy))["url"] == f"https://{HOST}"
    assert tgrelay.dashboard_info("anna", data, nginx) is None      # one folder, as before
