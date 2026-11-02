"""Forgot the dashboard password (owner, 02.11.2026): one command gives a new one-time setup
link, and the old password and every signed-in browser stop working at once. The install.sh
way: `install.sh --reset-password`; Docker: `docker compose exec agentkennel python3
status_server.py --reset-password`. The Docker setup link is printed whole, with the server's
address, so `docker compose up -d --wait && docker compose logs agentkennel | grep setup`
hands it over right after the start.
"""
import http.client
import importlib
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.parse
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LINK = re.compile(r"/login\?setup=[A-Za-z0-9_-]{20,}")


def _free_port():
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cli(tmp_path, *args, **env):
    # no nginx htpasswd to seed from (the test machine may have one): a fresh install
    e = dict(os.environ, AGENTKENNEL_PASSFILE=str(tmp_path / ".dashpass"),
             AGENTKENNEL_AUTH_SECRET=str(tmp_path / ".secret"),
             AGENTKENNEL_HTPASSWD=str(tmp_path / "no-htpasswd"), **env)
    return subprocess.run([sys.executable, str(ROOT / "status_server.py"), *args],
                          capture_output=True, text=True, timeout=60, env=e, cwd=str(ROOT))


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKENNEL_PASSFILE", str(tmp_path / ".dashpass"))
    monkeypatch.setenv("AGENTKENNEL_AUTH_SECRET", str(tmp_path / ".secret"))
    sys.modules.pop("status_server", None)
    m = importlib.import_module("status_server")
    monkeypatch.setattr(m, "HTPASSWD_FILE", str(tmp_path / "none"))
    port = _free_port()
    s = ThreadingHTTPServer(("127.0.0.1", port), m.AuthHandler)
    threading.Thread(target=s.serve_forever, daemon=True).start()

    def req(method, path, form=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        hdrs = {"Content-Type": "application/x-www-form-urlencoded"} if body else {}
        if cookie:
            hdrs["Cookie"] = cookie
        c.request(method, path, body=body, headers=hdrs)
        r = c.getresponse()
        data = r.read().decode()
        c.close()
        return r, data

    yield m, req, tmp_path
    s.shutdown()
    sys.modules.pop("status_server", None)


def login(req, pw):
    r, _ = req("POST", "/login", {"pass": pw})
    cookie = (r.getheader("Set-Cookie") or "").split(";", 1)[0]
    return r.status, cookie


def test_reset_password_gives_a_new_link_and_drops_password_and_sessions(srv):
    m, req, tmp = srv
    m._pw_store("old-password-1")
    status, cookie = login(req, "old-password-1")
    assert status == 302 and cookie.startswith("agents_session=")
    assert req("GET", "/check", cookie=cookie)[0].status == 200

    r = cli(tmp, "--reset-password")
    assert r.returncode == 0, r.stderr
    link = LINK.search(r.stdout).group(0)

    # the running server sees it at once, no restart: the old cookie and password are gone
    assert req("GET", "/check", cookie=cookie)[0].status == 401
    assert login(req, "old-password-1")[0] != 302
    # the new link sets a new password, and that one works
    tok = link.split("=", 1)[1]
    r2, _ = req("POST", "/login", {"setup": tok, "pass": "new-password-2", "pass2": "new-password-2"})
    assert r2.status == 302
    assert login(req, "new-password-2")[0] == 302


def test_reset_twice_gives_a_fresh_link_each_time(tmp_path):
    a = LINK.search(cli(tmp_path, "--reset-password").stdout).group(0)
    b = LINK.search(cli(tmp_path, "--reset-password").stdout).group(0)
    assert a != b
    assert cli(tmp_path, "--setup-link").stdout.strip() == b       # the newest is the one


def test_reset_outside_password_file_mode_says_so(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.endswith("PASSFILE")}
    r = subprocess.run([sys.executable, str(ROOT / "status_server.py"), "--reset-password"],
                       capture_output=True, text=True, timeout=60, env=e, cwd=str(ROOT))
    assert r.returncode != 0 and "password file" in (r.stderr + r.stdout)


def test_the_sign_in_page_says_what_to_do_when_the_password_is_forgotten(srv):
    m, req, _ = srv
    m._pw_store("x-password-1")
    _, page = req("GET", "/login")
    assert "Forgot" in page
    assert "install.sh --reset-password" in page
    assert "docker compose exec agentkennel python3 status_server.py --reset-password" in page


# ── the whole link, with the server's address (Docker logs) ──────────────────────────
@pytest.mark.parametrize("site,port,want", [
    ("https://:8765", "8765", "https://203.0.113.7:8765/login?setup="),
    ("https://:8765", "127.0.0.1:18480", "https://203.0.113.7:18480/login?setup="),
    (":8765", "9000", "http://203.0.113.7:9000/login?setup="),
    ("agents.example.com", "8765", "https://agents.example.com/login?setup="),
])
def test_setup_url_is_the_whole_address(tmp_path, site, port, want):
    r = cli(tmp_path, "--setup-url", AGENTKENNEL_SITE=site, AGENTKENNEL_PUBLIC_PORT=port,
            AGENTKENNEL_PUBLIC_IP="203.0.113.7")
    assert r.returncode == 0 and r.stdout.strip().startswith(want), r.stdout


def test_setup_url_without_a_known_ip_says_where_it_goes(tmp_path):
    r = cli(tmp_path, "--setup-url", AGENTKENNEL_SITE="https://:8765",
            AGENTKENNEL_PUBLIC_PORT="8765", AGENTKENNEL_PUBLIC_IP="none")
    assert r.stdout.strip().startswith("https://<your-server-ip>:8765/login?setup=")


def test_no_setup_url_once_the_password_is_set(tmp_path):
    (tmp_path / ".dashpass").write_text("aa$bb")
    r = cli(tmp_path, "--setup-url", AGENTKENNEL_PUBLIC_IP="203.0.113.7")
    assert r.returncode == 1 and r.stdout.strip() == ""


# ── Docker: --wait needs a health check; exec needs the paths; the log line is whole ────
def test_the_image_has_a_health_check_on_the_sign_in_page():
    """Not through Caddy: its port depends on the mode (:8765, or a domain on 443 after
    ./https.sh); the sign-in service itself and a running Caddy are true in every mode."""
    df = (ROOT / "Dockerfile").read_text()
    m = re.search(r"^HEALTHCHECK .*$(\n\s+.*)*", df, re.M)
    assert m and "127.0.0.1:3046/login" in m.group(0) and "pgrep -x caddy" in m.group(0)


def test_the_image_knows_where_the_password_lives_for_docker_exec():
    df = (ROOT / "Dockerfile").read_text()
    assert "AGENTKENNEL_PASSFILE=/app/.sessions/.dashpass" in df
    assert "AGENTKENNEL_AUTH_SECRET=/app/.sessions/.agents_auth_secret" in df


def test_compose_tells_the_container_its_public_port():
    dc = (ROOT / "docker-compose.yml").read_text()
    assert 'AGENTKENNEL_PUBLIC_PORT: "${AGENTKENNEL_PORT:-${AGENTDECK_PORT:-8765}}"' in dc


def test_the_container_log_prints_the_whole_link_on_one_line():
    ep = (ROOT / "docker" / "entrypoint.sh").read_text()
    assert "status_server.py --setup-url" in ep
    line = [x for x in ep.splitlines() if "--setup-url" in x][0]
    assert "setup" in line.split("$(")[0]            # `grep setup` finds that very line


# ── install.sh --reset-password ─────────────────────────────────────────────────────
def test_install_sh_reset_password(tmp_path):
    d = tmp_path / "agentkennel"
    (d / ".sessions").mkdir(parents=True)
    for f in ROOT.glob("*.py"):
        (d / f.name).symlink_to(f)
    (d / ".sessions" / ".dashpass").write_text("aa$bb")
    script = f"""
      AGENTKENNEL_SOURCE_ONLY=1 . {ROOT / 'install.sh'}
      DIR={d} SITE=":8765" SELF_IP=203.0.113.7 PORT=8765
      reset_password
    """
    env = dict(os.environ, AGENTKENNEL_HTPASSWD=str(tmp_path / "no-htpasswd"))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    assert re.search(r"http://203\.0\.113\.7:8765/login\?setup=[A-Za-z0-9_-]{20,}", r.stdout), r.stdout
    assert not (d / ".sessions" / ".dashpass").exists()


def test_install_sh_lists_the_flag():
    src = (ROOT / "install.sh").read_text()
    assert "--reset-password" in src.split("Env:", 1)[0]       # in the --help text
    assert re.search(r"--reset-password\)\s*RESET_PW=1", src)


def test_readme_says_both_ways():
    md = (ROOT / "README.md").read_text()
    assert "docker compose up -d --wait && docker compose logs agentkennel | grep setup" in md
    assert "install.sh --reset-password" in md
    assert "docker compose exec agentkennel python3 status_server.py --reset-password" in md
