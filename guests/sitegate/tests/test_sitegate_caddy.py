"""Site gate on a Caddy server: the same Gate, the same order of steps, Caddy site blocks
instead of nginx vhosts, and CaddyOps instead of nginx + certbot + sudo.

CaddyOps writes <host>.caddy files into the sites folder the script install's Caddyfile
imports, and reloads the way a reload of agentkennel-caddy does — the Caddyfile Caddy runs
on (.sessions/Caddyfile.run) is POSTed to its admin API, where Caddy adapts it with its
own environment — so nothing needs sudo. A reload Caddy refuses changes nothing: the files
are put back as they were. Then the live test: a real caddy (PATH or CADDY_BIN) with its own
CA serving a guest's dashboard, the owner's entry, a stub, a redirect and shared files."""

import http.server
import json
import os
import shutil
import socket
import subprocess
import warnings
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "turnstile"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import turnstile as ts  # noqa: E402
import sitegate as sg  # noqa: E402
import caddyconf  # noqa: E402
import publicfiles  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import webchoice  # noqa: E402


class FakeOps:
    KIND = "caddy"

    def __init__(self):
        self.log = []

    def __getattr__(self, name):
        return lambda *a: self.log.append((name, *a))


@pytest.fixture
def gate(tmp_path):
    book = ts.GuestBook(tmp_path / "guests.json")
    book.add("anna", 10, telegram_id=1)
    ops = FakeOps()
    return sg.Gate(book, ops, tmp_path / "sites.json", ip_of={"anna": "172.31.250.11"}.get,
                   cleanup_delay=0), ops


# ── which renderer goes with which ops ─────────────────────────────────────────────
def test_ops_without_a_kind_are_nginx():
    class Plain:
        pass
    assert sg.conf_for(Plain()) is sg.NginxConf
    assert sg.conf_for(sg.RealOps) is sg.NginxConf
    assert sg.conf_for(FakeOps()) is sg.CaddyConf


def test_the_nginx_renderer_is_the_old_text_byte_for_byte():
    h = "blog-anna.example.test"
    assert sg.NginxConf.site(h, "172.31.250.11", 3000, tls=True) == \
        sg.render_vhost(h, "172.31.250.11", 3000, tls=True)
    assert sg.NginxConf.site(h, "172.31.250.11", 3000, tls=False) == \
        sg.render_vhost(h, "172.31.250.11", 3000, tls=False)
    assert sg.NginxConf.gone(h) == sg.render_gone(h)
    assert sg.NginxConf.moved(h, "x.example.test") == sg.render_moved(h, "x.example.test")


# ── the Gate, unchanged in its steps ───────────────────────────────────────────────
def test_publish_on_caddy_writes_caddy_blocks(gate):
    g, ops = gate
    res = g.publish("anna", "blog", 3000)
    assert res["url"] == "https://blog-anna.example.test"
    names = [e[0] for e in ops.log]
    assert names == ["problem", "vhost_exists", "dns_create", "write_vhost", "reload",
                     "cert_issue", "write_vhost", "reload", "notify"]
    pending, final = [e[2] for e in ops.log if e[0] == "write_vhost"]
    assert "respond 404" in pending and "reverse_proxy" not in pending
    assert "reverse_proxy 172.31.250.11:3000 {" in final and "listen 443" not in final


def test_unpublish_on_caddy_writes_the_caddy_stub(gate):
    g, ops = gate
    g.publish("anna", "blog", 3000)
    ops.log.clear()
    g.unpublish("anna", "blog")
    first = ops.log[0]
    assert first[0] == "write_vhost" and "respond 404" in first[2]
    assert [e[0] for e in ops.log] == ["write_vhost", "reload", "dns_delete", "remove_vhost",
                                       "reload", "cert_delete", "notify"]


def test_publish_is_refused_when_guest_web_cant_work(tmp_path):
    book = ts.GuestBook(tmp_path / "guests.json")
    book.add("anna", 10, telegram_id=1)

    class Ops(FakeOps):
        def problem(self):
            return "AgentKennel's Caddy has no public 443 — run ./install.sh --https"
    ops = Ops()
    g = sg.Gate(book, ops, tmp_path / "sites.json", ip_of={"anna": "172.31.250.11"}.get,
                cleanup_delay=0)
    with pytest.raises(sg.GateError) as e:
        g.publish("anna", "blog", 3000)
    assert "install.sh --https" in str(e.value)
    assert not [x for x in ops.log if x[0] != "problem"]


# ── make_ops: what a choice gives ─────────────────────────────────────────────────
def test_make_ops_follows_the_choice(monkeypatch, tmp_path):
    monkeypatch.setattr(sg.RealOps, "VHOSTS", tmp_path / "nginx")
    monkeypatch.setattr(sg.CaddyOps, "VHOSTS", tmp_path / "caddy")
    nginx = sg.make_ops(webchoice.Choice("nginx", True, "nginx is running"))
    assert type(nginx) is sg.RealOps and nginx.choice.backend == "nginx"
    caddy = sg.make_ops(webchoice.Choice("caddy", True, "caddy on 443"))
    assert isinstance(caddy, sg.CaddyOps) and caddy.KIND == "caddy"
    assert caddy.problem() == ""
    broken = sg.make_ops(webchoice.Choice("caddy", False, "no 443", "run ./install.sh --https"))
    assert "no 443" in broken.problem() and "install.sh --https" in broken.problem()


def test_auto_ops_asks_again_later_and_keeps_its_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(sg.CaddyOps, "VHOSTS", tmp_path / "caddy")
    monkeypatch.setattr(sg.RealOps, "VHOSTS", tmp_path / "nginx")
    answers = [webchoice.Choice("nginx", False, "nothing yet", "fix"),
               webchoice.Choice("caddy", True, "caddy on 443"),
               webchoice.Choice("caddy", True, "caddy on 443")]
    now = [0.0]
    auto = sg.AutoOps(probe=lambda: answers.pop(0), clock=lambda: now[0], ttl=30)
    assert auto.KIND == "nginx"
    assert auto.KIND == "nginx"                      # within the TTL: not asked again
    now[0] = 31
    assert auto.KIND == "caddy"
    first = auto.current()
    now[0] = 62
    assert auto.current() is first                   # same backend: same instance (its undo list)


# ── CaddyOps against a fake admin API ──────────────────────────────────────────────
class Admin:
    """Caddy's admin endpoint as far as CaddyOps uses it: POST /load, GET /config/."""

    def __init__(self):
        self.loads, self.refuse, self.config, self.slow = [], None, {}, 0
        admin = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                admin.loads.append((self.path, self.headers["Content-Type"], body))
                if admin.slow:
                    time.sleep(admin.slow)
                if admin.refuse:
                    data = json.dumps({"error": admin.refuse}).encode()
                    self.send_response(400)
                else:
                    data = b""
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                data = json.dumps(admin.config).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def serve_hosts(self, *hosts):
        self.config = {"srv0": {"routes": [{"match": [{"host": list(hosts)}]}]}}


@pytest.fixture
def admin():
    a = Admin()
    yield a
    a.srv.shutdown()
    a.srv.server_close()


@pytest.fixture
def cops(tmp_path, admin, monkeypatch):
    monkeypatch.setattr(sg.CaddyOps, "VHOSTS", tmp_path / "caddy-sites")
    run = tmp_path / "Caddyfile.run"
    run.write_text("{\n}\n:8765 {\n}\nimport \"{$AGENTKENNEL_CADDY_SITES:/dev/null}\"\n")

    def no_commands(*a, **k):
        raise AssertionError(f"CaddyOps ran a command: {a}")
    monkeypatch.setattr(sg.subprocess, "run", no_commands)    # no sudo, no nginx, no certbot
    return sg.CaddyOps(admin=admin.url, caddyfile=run)


def test_write_and_reload_without_sudo(cops, admin, tmp_path):
    h = "sb-anna.example.test"
    cops.write_vhost(h, caddyconf.render_site(h, "127.0.0.1", 18101))
    f = tmp_path / "caddy-sites" / f"{h}.caddy"
    assert f.exists() and cops.vhost_exists(h)
    admin.serve_hosts(h)
    cops.reload()
    path, ctype, body = admin.loads[-1]
    assert path == "/load" and ctype == "text/caddyfile"
    assert body == (tmp_path / "Caddyfile.run").read_text()


def test_a_refused_reload_puts_every_file_back(cops, admin, tmp_path):
    keep, new, gone = "sb-keep.example.test", "sb-new.example.test", "sb-gone.example.test"
    for h in (keep, gone):
        cops.write_vhost(h, caddyconf.render_site(h, "127.0.0.1", 18101))
    admin.serve_hosts(keep, gone)
    cops.reload()
    before = {p.name: p.read_text() for p in (tmp_path / "caddy-sites").iterdir()}
    cops.write_vhost(keep, "broken {")
    cops.write_vhost(new, "also broken {")
    cops.remove_vhost(gone)
    admin.refuse = "adapting config using caddyfile: unexpected EOF"
    with pytest.raises(RuntimeError) as e:
        cops.reload()
    assert "unexpected EOF" in str(e.value)
    after = {p.name: p.read_text() for p in (tmp_path / "caddy-sites").iterdir()}
    assert after == before


def test_an_unchanged_write_is_no_write_and_no_reload(cops, admin, tmp_path):
    h = "sb-anna.example.test"
    text = caddyconf.render_site(h, "127.0.0.1", 18101)
    cops.write_vhost(h, text)
    admin.serve_hosts(h)
    cops.reload()
    assert len(admin.loads) == 1
    f = tmp_path / "caddy-sites" / f"{h}.caddy"
    before = f.stat().st_mtime_ns
    for _ in range(5):
        cops.write_vhost(h, text)
        cops.reload()
    assert len(admin.loads) == 1 and f.stat().st_mtime_ns == before
    cops.remove_vhost("sb-never.example.test")          # nothing there: nothing to do
    cops.reload()
    assert len(admin.loads) == 1


def test_a_timed_out_load_that_caddy_applied_is_kept(cops, admin, tmp_path):
    h = "sb-anna.example.test"
    cops.load_timeout = 0.3
    admin.slow = 1
    admin.serve_hosts(h)                                 # Caddy did take it, just slowly
    cops.write_vhost(h, caddyconf.render_site(h, "127.0.0.1", 18101))
    cops.reload()
    assert (tmp_path / "caddy-sites" / f"{h}.caddy").exists()


def test_a_timed_out_load_caddy_didnt_apply_is_put_back(cops, admin, tmp_path):
    h = "sb-anna.example.test"
    cops.load_timeout = 0.3
    admin.slow = 1
    admin.serve_hosts("other.example.test")
    cops.write_vhost(h, caddyconf.render_site(h, "127.0.0.1", 18101))
    with pytest.raises(RuntimeError):
        cops.reload()
    assert not (tmp_path / "caddy-sites" / f"{h}.caddy").exists()


def test_a_caddy_that_does_not_import_the_folder_is_named(cops, admin):
    h = "sb-anna.example.test"
    cops.write_vhost(h, caddyconf.render_site(h, "127.0.0.1", 18101))
    admin.serve_hosts("203-0-113-7.sslip.io")             # loaded, but without our file
    with pytest.raises(RuntimeError) as e:
        cops.reload()
    assert "AGENTKENNEL_CADDY_SITES" in str(e.value) and "install.sh" in str(e.value)


def test_no_running_caddy_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sg.CaddyOps, "VHOSTS", tmp_path / "s")
    run = tmp_path / "Caddyfile.run"
    run.write_text(":1 {\n}\n")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ops = sg.CaddyOps(admin=f"http://127.0.0.1:{port}", caddyfile=run)
    ops.write_vhost("a.example.test", caddyconf.render_gone("a.example.test"))
    with pytest.raises(RuntimeError) as e:
        ops.reload()
    assert "agentkennel-caddy" in str(e.value)
    assert not ops.vhost_exists("a.example.test")          # put back: there was none


def test_no_caddyfile_run_no_reload(tmp_path, monkeypatch, admin):
    monkeypatch.setattr(sg.CaddyOps, "VHOSTS", tmp_path / "s")
    ops = sg.CaddyOps(admin=admin.url, caddyfile=tmp_path / "missing")
    ops.write_vhost("a.example.test", caddyconf.render_gone("a.example.test"))
    with pytest.raises(RuntimeError) as e:
        ops.reload()
    assert "Caddyfile.run" in str(e.value) and not admin.loads


def test_hosts_are_checked_before_they_name_a_file(cops):
    for bad in ("../x", "a/b", "", "X.test"):
        with pytest.raises(ValueError):
            cops.write_vhost(bad, "x")


def test_certificate_wait(cops, monkeypatch):
    tries = []
    monkeypatch.setattr(cops, "_tls_ok", lambda host: tries.append(host) or len(tries) >= 3)
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    cops.cert_issue("sb-anna.example.test")
    assert tries == ["sb-anna.example.test"] * 3
    cops.cert_wait = 0
    monkeypatch.setattr(cops, "_tls_ok", lambda host: False)
    with pytest.raises(RuntimeError) as e:
        cops.cert_issue("sb-anna.example.test")
    assert "certificate" in str(e.value) and "journalctl -u agentkennel-caddy" in str(e.value)


def test_cert_delete_removes_only_that_name(cops, tmp_path, monkeypatch):
    data = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    for issuer in ("local", "acme-v02.api.letsencrypt.org-directory"):
        for h in ("sb-anna.example.test", "sb-annabel.example.test"):
            d = data / "caddy" / "certificates" / issuer / h
            d.mkdir(parents=True)
            (d / f"{h}.crt").write_text("x")
    cops.cert_delete("sb-anna.example.test")
    left = sorted(p.name for p in (data / "caddy" / "certificates").glob("*/*"))
    assert left == ["sb-annabel.example.test", "sb-annabel.example.test"]


# ── the real thing ──────────────────────────────────────────────────────────────────
def caddy_bin():
    """The caddy the real-Caddy tests use: CADDY_BIN, PATH, or where install.sh puts it."""
    for c in (os.environ.get("CADDY_BIN"), shutil.which("caddy"), "/usr/local/bin/caddy",
              "/usr/bin/caddy"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


NO_CADDY = ("real-Caddy tests SKIPPED: no caddy binary in CADDY_BIN, PATH, /usr/local/bin "
            "or /usr/bin (set CADDY_BIN=/path/to/caddy to run them)")
if not caddy_bin():
    warnings.warn(NO_CADDY)            # shown in pytest's warnings summary, not only with -rs


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Upstream:
    """A sandbox stand-in: echoes what it was sent as JSON; answers a websocket upgrade
    with 101 and then echoes raw bytes."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(20)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        try:
            f = c.makefile("rb")
            line = f.readline().decode()
            if not line:
                return
            headers = {}
            while True:
                h = f.readline().decode()
                if h in ("\r\n", "\n", ""):
                    break
                k, _, v = h.partition(":")
                headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length") or 0)
            body = f.read(n) if n else b""
            if headers.get("upgrade", "").lower() == "websocket":
                c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          b"Connection: Upgrade\r\n\r\n")
                data = c.recv(100)
                c.sendall(b"echo:" + data)
                time.sleep(0.5)
                return
            out = json.dumps({"line": line.strip(), "headers": headers, "body": len(body)}).encode()
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                      + str(len(out)).encode() + b"\r\nConnection: close\r\n\r\n" + out)
        finally:
            c.close()


@pytest.fixture
def live(tmp_path, monkeypatch):
    if not caddy_bin():
        pytest.skip(NO_CADDY)
    http_port, https_port, admin_port = free_port(), free_port(), free_port()
    data = tmp_path / "xdg"
    sites = tmp_path / "caddy-sites"
    sites.mkdir()
    run = tmp_path / "Caddyfile.run"
    run.write_text(
        "{\n"
        f"\tadmin 127.0.0.1:{admin_port}\n"
        f"\thttp_port {http_port}\n\thttps_port {https_port}\n"
        "\tlocal_certs\n\tskip_install_trust\n"
        "}\n"
        f":{free_port()} {{\n\trespond \"main dashboard\"\n}}\n"
        'import "{$AGENTKENNEL_CADDY_SITES:/dev/null}"\n')
    env = {**os.environ, "XDG_DATA_HOME": str(data), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "AGENTKENNEL_CADDY_SITES": f"{sites}/*.caddy", "HOME": str(tmp_path)}
    proc = subprocess.Popen([caddy_bin(), "run", "--config", str(run), "--adapter", "caddyfile"],
                            env=env, cwd=tmp_path, stdout=subprocess.DEVNULL,
                            stderr=open(tmp_path / "caddy.log", "w"))
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", admin_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        monkeypatch.setenv("XDG_DATA_HOME", str(data))
        monkeypatch.setattr(sg.CaddyOps, "VHOSTS", sites)
        ops = sg.CaddyOps(admin=f"http://127.0.0.1:{admin_port}", caddyfile=run,
                          https_port=https_port, cert_wait=30)
        yield {"ops": ops, "https": https_port, "http": http_port, "data": data,
               "tmp": tmp_path}
    finally:
        proc.terminate()
        proc.wait(10)
        print((tmp_path / "caddy.log").read_text()[-3000:])


def curl(live, host, path="/", *args):
    root = live["data"] / "caddy" / "pki" / "authorities" / "local" / "root.crt"
    r = subprocess.run(["curl", "-sS", "--max-time", "10", "--cacert", str(root),
                        "--resolve", f"{host}:{live['https']}:127.0.0.1",
                        "-o", "-", "-w", "\n%{http_code} %{redirect_url}", *args,
                        f"https://{host}:{live['https']}{path}"],
                       capture_output=True, text=True, timeout=30)
    body, _, tail = r.stdout.rpartition("\n")
    code, _, redirect = tail.partition(" ")
    return int(code or 0), body, redirect


def test_live_caddy_serves_a_guest(live):
    ops, host = live["ops"], "sb-anna.example.test"
    up, panel = Upstream(), Upstream()
    text = caddyconf.render_site(
        host, "127.0.0.1", up.port,
        entry=("/_owner-entry", f"http://127.0.0.1:{panel.port}/api/guests/enter"),
        origin=(f"https://{host}", "https://sb-anna.old.test"))
    ops.write_vhost(host, caddyconf.render_site(host, "127.0.0.1", up.port, tls=False))
    ops.reload()
    ops.cert_issue(host)                              # Caddy's own CA: a moment
    assert curl(live, host)[0] == 404                 # pending: 404 on its own name
    ops.write_vhost(host, text)
    ops.reload()

    code, body, _ = curl(live, host, "/x?y=1")
    seen = json.loads(body)
    assert code == 200 and seen["line"].startswith("GET /x?y=1 ")
    assert seen["headers"]["host"].startswith(host)
    assert seen["headers"]["x-real-ip"] == "127.0.0.1"
    assert seen["headers"]["x-forwarded-proto"] == "https"
    # the Origin stand-in, not on a websocket
    seen = json.loads(curl(live, host, "/", "-H", f"Origin: https://{host}")[1])
    assert seen["headers"]["origin"] == "https://sb-anna.old.test"
    seen = json.loads(curl(live, host, "/", "-H", "Origin: https://elsewhere.test")[1])
    assert seen["headers"]["origin"] == "https://elsewhere.test"
    # the owner's entry: one path, to the panel, with its query
    code, body, _ = curl(live, host, "/_owner-entry?t=abc")
    seen = json.loads(body)
    assert code == 200 and seen["line"].startswith("GET /api/guests/enter?t=abc ")
    assert json.loads(curl(live, host, "/_owner-entry/x")[1])["line"].startswith("GET /_owner-entry/x")
    # 50 MiB body limit
    big = live["tmp"] / "big"
    big.write_bytes(b"x" * (50 * 1024 * 1024 + 10))
    assert curl(live, host, "/up", "--data-binary", f"@{big}")[0] == 413
    small = live["tmp"] / "small"
    small.write_bytes(b"x" * 1024)
    assert json.loads(curl(live, host, "/up", "--data-binary", f"@{small}")[1])["body"] == 1024

    # a websocket goes through
    import ssl
    ctx = ssl.create_default_context(cafile=str(live["data"] / "caddy" / "pki" / "authorities"
                                                / "local" / "root.crt"))
    with socket.create_connection(("127.0.0.1", live["https"]), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as s:
            s.sendall(f"GET /sess/ws HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                      f"Sec-WebSocket-Version: 13\r\nOrigin: https://{host}\r\n\r\n".encode())
            head = b""
            while b"\r\n\r\n" not in head:
                head += s.recv(1000)
            assert head.startswith(b"HTTP/1.1 101")
            s.sendall(b"ping")
            assert s.recv(100) == b"echo:ping"

    # http goes to https
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}",
                        "--resolve", f"{host}:{live['http']}:127.0.0.1",
                        f"http://{host}:{live['http']}/a"], capture_output=True, text=True)
    assert r.stdout.startswith(("301 ", "308 ")) and f"https://{host}" in r.stdout

    # web off: the stub, then nothing
    ops.write_vhost(host, caddyconf.render_gone(host))
    ops.reload()
    assert curl(live, host)[0] == 404
    # moved
    new = "sb-mila.example.test"
    ops.write_vhost(new, caddyconf.render_site(new, "127.0.0.1", up.port))
    ops.write_vhost(host, caddyconf.render_moved(host, new))
    ops.reload()
    ops.cert_issue(new)
    code, _, loc = curl(live, host, "/a/b?c=1")
    assert code == 301 and loc == f"https://{new}/a/b?c=1"
    # a broken block is refused and the rest keeps working
    with pytest.raises(RuntimeError):
        ops.write_vhost("bad.example.test", "bad.example.test {\n\tnot_a_directive\n}\n")
        ops.reload()
    assert not ops.vhost_exists("bad.example.test")
    assert curl(live, new)[0] == 200
    ops.remove_vhost(host)
    ops.reload()
    ops.cert_delete(host)
    assert not list((live["data"] / "caddy" / "certificates").glob(f"*/{host}"))
    assert curl(live, host)[0] == 0          # no block, no certificate: the TLS handshake fails
    assert curl(live, new)[0] == 200


def test_live_caddy_serves_shared_files(live):
    ops, host = live["ops"], "sbx.example.test"
    root = live["tmp"] / "public"
    (root / "anna-k3y").mkdir(parents=True)
    (root / "anna-k3y" / "a.txt").write_text("hello")
    (live["tmp"] / "secret").write_text("host secret")
    os.symlink(live["tmp"] / "secret", root / "anna-k3y" / "s.txt")
    files = publicfiles.start_in_background("127.0.0.1", 0, root, log=lambda m: None)
    try:
        ops.write_vhost(host, caddyconf.render_files(host, files.server_address[1]))
        ops.reload()
        ops.cert_issue(host)
        code, body, _ = curl(live, host, "/f/anna-k3y/a.txt", "-D", str(live["tmp"] / "h"))
        assert code == 200 and body == "hello"
        h = (live["tmp"] / "h").read_text().lower()
        for want in ("content-security-policy: sandbox", "x-content-type-options: nosniff",
                     "x-robots-tag: noindex, nofollow", "cache-control: no-cache"):
            assert h.count(want) == 1, want
        assert curl(live, host, "/f/anna-k3y/s.txt")[0] == 404
        assert curl(live, host, "/f/")[0] == 404
        assert curl(live, host, "/")[0] == 404
    finally:
        files.shutdown()
        files.server_close()


def test_live_a_reload_doesnt_cut_an_open_terminal(live):
    """Without stream_close_delay Caddy ends every websocket with 1001 on a reload (seen
    17.11.2026); a guest's change must not cut the terminals that are open."""
    import ssl
    ops, host = live["ops"], "sb-anna.example.test"
    up = Upstream()
    ops.write_vhost(host, caddyconf.render_site(host, "127.0.0.1", up.port))
    ops.reload()
    ops.cert_issue(host)
    ctx = ssl.create_default_context(cafile=str(live["data"] / "caddy" / "pki" / "authorities"
                                                / "local" / "root.crt"))
    with socket.create_connection(("127.0.0.1", live["https"]), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as s:
            s.sendall(f"GET /ws HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n".encode())
            head = b""
            while b"\r\n\r\n" not in head:
                head += s.recv(1000)
            other = "blog-anna.example.test"
            ops.write_vhost(other, caddyconf.render_site(other, "127.0.0.1", up.port))
            ops.reload()                                  # someone else's change
            time.sleep(1)
            s.sendall(b"ping")
            assert s.recv(100) == b"echo:ping"
