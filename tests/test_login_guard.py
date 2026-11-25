"""Login attempts are limited per visitor (owner 25.11.2026: the panel's password could be
guessed endlessly, and behind it are terminals with full power over the server).

After MAX_FAILS wrong passwords within WINDOW seconds the visitor's address is refused for
BAN seconds: the password is not even checked (a correct one is refused too). Other
visitors are not affected."""
import importlib
import http.client
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.login_guard import LoginGuard, client_key  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


# ── the counter ─────────────────────────────────────────────────────────────

def test_ban_after_max_fails():
    c = Clock()
    g = LoginGuard(max_fails=5, window=900, ban=900, clock=c)
    for _ in range(4):
        g.failed("1.2.3.4")
    assert g.banned_for("1.2.3.4") == 0
    g.failed("1.2.3.4")
    assert g.banned_for("1.2.3.4") > 0


def test_ban_expires():
    c = Clock()
    g = LoginGuard(max_fails=5, window=900, ban=900, clock=c)
    for _ in range(5):
        g.failed("1.2.3.4")
    c.t += 901
    assert g.banned_for("1.2.3.4") == 0


def test_other_visitors_unaffected():
    g = LoginGuard(max_fails=5, window=900, ban=900, clock=Clock())
    for _ in range(5):
        g.failed("1.2.3.4")
    assert g.banned_for("5.6.7.8") == 0


def test_old_failures_fall_out_of_the_window():
    c = Clock()
    g = LoginGuard(max_fails=5, window=900, ban=900, clock=c)
    for _ in range(4):
        g.failed("1.2.3.4")
    c.t += 901
    g.failed("1.2.3.4")
    assert g.banned_for("1.2.3.4") == 0


def test_success_clears_the_count():
    g = LoginGuard(max_fails=5, window=900, ban=900, clock=Clock())
    for _ in range(4):
        g.failed("1.2.3.4")
    g.succeeded("1.2.3.4")
    for _ in range(4):
        g.failed("1.2.3.4")
    assert g.banned_for("1.2.3.4") == 0


def test_memory_stays_bounded():
    g = LoginGuard(max_fails=5, window=900, ban=900, clock=Clock(), max_keys=100)
    for i in range(1000):
        g.failed(f"10.0.{i // 256}.{i % 256}")
    assert g.size() <= 100


# ── whose attempt it is ─────────────────────────────────────────────────────

def test_public_peer_is_the_visitor_and_its_header_is_ignored():
    # a visitor talking to us directly could write any X-Forwarded-For
    assert client_key("93.184.216.34", "1.1.1.1") == "93.184.216.34"


def test_behind_our_proxy_the_header_names_the_visitor():
    assert client_key("127.0.0.1", "93.184.216.34") == "93.184.216.34"


def test_internal_hops_are_skipped():
    # host Caddy -> the sandbox's own Caddy -> here: "visitor, docker gateway"
    assert client_key("127.0.0.1", "93.184.216.34, 172.17.0.1") == "93.184.216.34"


def test_rightmost_public_address_wins():
    # whatever the visitor wrote sits on the left; our proxy appended the real one
    assert client_key("127.0.0.1", "1.1.1.1, 93.184.216.34") == "93.184.216.34"


def test_no_or_bad_header_falls_back_to_the_peer():
    assert client_key("127.0.0.1", "") == "127.0.0.1"
    assert client_key("127.0.0.1", "garbage, ???") == "127.0.0.1"


# ── the real login page ─────────────────────────────────────────────────────

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKENNEL_PASSFILE", str(tmp_path / ".dashpass"))
    monkeypatch.setenv("AGENTKENNEL_AUTH_SECRET", str(tmp_path / ".secret"))
    sys.modules.pop("status_server", None)
    for name in [n for n in sys.modules if n == "backend.auth"]:
        sys.modules.pop(name)
    m = importlib.import_module("status_server")
    auth = importlib.import_module("backend.auth")
    auth._pw_store("right-password-1")
    monkeypatch.setattr(auth, "LOGIN_GUARD", LoginGuard(max_fails=5, window=900, ban=900))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.AuthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    sys.modules.pop("status_server", None)


def _login(port, pw, visitor):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = f"pass={pw}"
    conn.request("POST", "/login", body, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)), "X-Forwarded-For": visitor})
    r = conn.getresponse()
    out = (r.status, r.getheader("Set-Cookie"), r.read().decode())
    conn.close()
    return out


def test_correct_password_logs_in(server):
    status, cookie, _ = _login(server, "right-password-1", "93.184.216.34")
    assert status == 302 and cookie


def test_banned_visitor_is_refused_even_with_the_right_password(server):
    for _ in range(5):
        assert _login(server, "wrong", "93.184.216.34")[0] == 401
    status, cookie, page = _login(server, "right-password-1", "93.184.216.34")
    assert status == 429 and not cookie
    assert "Too many failed attempts" in page


def test_another_visitor_still_logs_in_during_a_ban(server):
    for _ in range(5):
        _login(server, "wrong", "93.184.216.34")
    status, cookie, _ = _login(server, "right-password-1", "151.101.1.1")
    assert status == 302 and cookie
