import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claudekeys as ck  # noqa: E402

API = "sk-ant-api03-" + "A1b2C3d4E5" * 4
OAT = "sk-ant-oat01-" + "Z9y8X7w6V5" * 4


def test_classify_variants():
    assert ck.classify(API) == ("api", API)
    assert ck.classify(f"  {API}\n") == ("api", API)
    assert ck.classify(f"here is my key {API} thanks") == ("api", API)
    assert ck.classify(OAT) == ("oauth", OAT)
    assert ck.classify("sk-proj-" + "a" * 40) is None
    assert ck.classify("sk-ant-api03-short") is None
    assert ck.classify("") is None


def test_last4():
    assert ck.last4(API) == API[-4:]
    assert ck.last4("abc") == "…"
    assert ck.last4("") == "…"


def test_store_roundtrip_and_modes(tmp_path):
    s = ck.KeyStore(tmp_path)
    assert s.get("vlad") is None
    s.put("vlad", "api", API)
    assert s.get("vlad") == ("api", API)
    f = tmp_path / "keys" / "vlad.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert set(json.loads(f.read_text())) == {"kind", "secret", "saved"}
    s.put("vlad", "oauth", OAT)
    assert s.get("vlad") == ("oauth", OAT)
    s.drop("vlad")
    assert s.get("vlad") is None
    s.drop("vlad")  # no error


def test_owner_key(tmp_path):
    s = ck.KeyStore(tmp_path)
    assert s.owner() is None
    s.set_owner(API)
    assert s.owner() == API
    f = tmp_path / "keys" / "_owner.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    s.drop_owner()
    assert s.owner() is None
    s.drop_owner()


@pytest.mark.parametrize("bad", ["a/b", "..", "../x", "_owner", "_x", "", "a\\b"])
def test_bad_names(tmp_path, bad):
    s = ck.KeyStore(tmp_path)
    with pytest.raises(ValueError):
        s.put(bad, "api", API)
    with pytest.raises(ValueError):
        s.get(bad)


def test_bad_kind(tmp_path):
    with pytest.raises(ValueError):
        ck.KeyStore(tmp_path).put("v", "password", API)


class Fake:
    def __init__(self, status=200, body=b"{}"):
        self.requests = []
        self.status, self.body = status, body
        up = self

        class H(BaseHTTPRequestHandler):
            def _go(self):
                n = int(self.headers.get("content-length") or 0)
                up.requests.append({"method": self.command, "path": self.path,
                                    "headers": dict(self.headers),
                                    "body": self.rfile.read(n) if n else b""})
                self.send_response(up.status)
                self.send_header("content-length", str(len(up.body)))
                self.end_headers()
                self.wfile.write(up.body)
            do_GET = do_POST = _go

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def up(self):
        return ("http", "127.0.0.1", self.port)


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.server.shutdown()


def test_check_api_ok(fake):
    assert ck.check("api", API, upstream=fake.up) == (True, "")
    r = fake.requests[0]
    assert r["method"] == "GET" and r["path"] == "/v1/models?limit=1"
    assert r["headers"]["x-api-key"] == API
    assert r["headers"]["anthropic-version"] == "2023-06-01"


def test_check_oauth_ok(fake):
    assert ck.check("oauth", OAT, upstream=fake.up) == (True, "")
    r = fake.requests[0]
    assert r["method"] == "POST" and r["path"] == "/v1/messages"
    assert r["headers"]["Authorization"] == f"Bearer {OAT}"
    assert r["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    body = json.loads(r["body"])
    assert body["model"] == "claude-haiku-4-5" and body["max_tokens"] == 1
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_check_error_message(fake):
    fake.status = 401
    fake.body = b'{"error":{"message":"invalid x-api-key"}}'
    assert ck.check("api", API, upstream=fake.up) == (False, "invalid x-api-key")


def test_check_error_without_json(fake):
    fake.status, fake.body = 503, b"<html>nope</html>"
    assert ck.check("api", API, upstream=fake.up) == (False, "HTTP 503")


def test_check_error_never_echoes_secret(fake):
    fake.status = 401
    fake.body = json.dumps({"error": {"message": f"bad key {API} given"}}).encode()
    ok, msg = ck.check("api", API, upstream=fake.up)
    assert not ok and "sk-ant-" not in msg and API not in msg


def test_check_unreachable():
    ok, msg = ck.check("api", API, upstream=("http", "127.0.0.1", 1), timeout=2)
    assert not ok and msg.startswith("can't reach Anthropic: ") and API not in msg


def test_check_bad_kind():
    with pytest.raises(ValueError):
        ck.check("x", API)
