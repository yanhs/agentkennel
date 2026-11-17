"""sbx.<domain>/f/<guest>-<key>/<file> on a Caddy server (sitegate/publicfiles.py): the
same promises as the nginx template — no listing, a symlink a guest plants never reaches
a host file, guest HTML runs sandboxed — and nothing outside guests/public is ever read."""

import http.client
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publicfiles as pf  # noqa: E402

HEADERS = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff",
           "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-cache"}


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("pf")
    r = tmp_path / "public"
    key = r / "anna-k3y"
    (key / "site").mkdir(parents=True)
    (key / "a.txt").write_text("hello")
    (key / "page.html").write_text("<h1>hi</h1>")
    (key / "site" / "index.html").write_text("index!")
    (key / "with space.txt").write_text("spaced")
    (key / "big.bin").write_bytes(bytes(range(256)) * 40)
    (tmp_path / "secret.txt").write_text("host secret")
    os.symlink(tmp_path / "secret.txt", key / "link.txt")
    os.symlink(tmp_path, key / "dirlink")
    os.symlink("a.txt", key / "rel-link.txt")
    os.mkfifo(key / "pipe")
    return r


@pytest.fixture(scope="module")
def server(root):
    srv = pf.make_server("127.0.0.1", 0, root)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def get(server, path, method="GET", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    c.request(method, path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def test_a_file_is_served_with_the_nginx_headers(server):
    r, body = get(server, "/f/anna-k3y/a.txt")
    assert r.status == 200 and body == b"hello"
    assert r.getheader("Content-Type").startswith("text/plain")
    for k, v in HEADERS.items():
        assert r.getheader(k) == v, k


def test_html_is_served_sandboxed(server):
    r, body = get(server, "/f/anna-k3y/page.html")
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/html")
    assert r.getheader("Content-Security-Policy") == "sandbox"


def test_percent_encoded_names(server):
    assert get(server, "/f/anna-k3y/with%20space.txt")[1] == b"spaced"


@pytest.mark.parametrize("path", ["/f/anna-k3y/link.txt", "/f/anna-k3y/dirlink/secret.txt",
                                  "/f/anna-k3y/rel-link.txt"])
def test_symlinks_are_never_followed(server, path):
    r, body = get(server, path)
    assert r.status == 404 and b"host secret" not in body and b"hello" not in body


@pytest.mark.parametrize("path", ["/f/../secret.txt", "/f/anna-k3y/../../secret.txt",
                                  "/f/%2e%2e/secret.txt", "/f/anna-k3y/%2e%2e/%2e%2e/secret.txt",
                                  "/f/anna-k3y/a.txt%00", "/f//etc/passwd", "/f/./anna-k3y/a.txt"])
def test_nothing_outside_the_folder(server, path):
    r, body = get(server, path)
    assert r.status == 404 and b"host secret" not in body


def test_no_listing(server):
    assert get(server, "/f/")[0].status == 404
    assert get(server, "/f/anna-k3y/")[0].status == 404


def test_a_folder_with_an_index(server):
    r, _ = get(server, "/f/anna-k3y/site")
    assert r.status == 301 and r.getheader("Location") == "/f/anna-k3y/site/"
    r, body = get(server, "/f/anna-k3y/site/")
    assert r.status == 200 and body == b"index!"


def test_a_pipe_is_not_read(server):
    r, _ = get(server, "/f/anna-k3y/pipe")
    assert r.status == 404


def test_only_under_f(server):
    r, _ = get(server, "/anna-k3y/a.txt")
    assert r.status == 404
    for k, v in HEADERS.items():                      # "always": on a 404 too
        assert r.getheader(k) == v


def test_head_and_range(server):
    r, body = get(server, "/f/anna-k3y/big.bin", method="HEAD")
    assert r.status == 200 and body == b"" and r.getheader("Content-Length") == "10240"
    assert r.getheader("Accept-Ranges") == "bytes"
    r, body = get(server, "/f/anna-k3y/big.bin", headers={"Range": "bytes=256-511"})
    assert r.status == 206 and body == bytes(range(256))
    assert r.getheader("Content-Range") == "bytes 256-511/10240"
    r, body = get(server, "/f/anna-k3y/big.bin", headers={"Range": "bytes=-16"})
    assert r.status == 206 and body == bytes(range(240, 256))
    r, _ = get(server, "/f/anna-k3y/big.bin", headers={"Range": "bytes=99999-"})
    assert r.status == 416


def test_only_reading(server):
    assert get(server, "/f/anna-k3y/a.txt", method="POST")[0].status == 405
    assert get(server, "/f/anna-k3y/a.txt", method="DELETE")[0].status == 405


def test_not_modified(server):
    r, _ = get(server, "/f/anna-k3y/a.txt")
    lm = r.getheader("Last-Modified")
    assert lm
    r, body = get(server, "/f/anna-k3y/a.txt", headers={"If-Modified-Since": lm})
    assert r.status == 304 and body == b""


def test_bind_failure_is_not_fatal(root):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    try:
        assert pf.start_in_background("127.0.0.1", s.getsockname()[1], root,
                                      log=lambda m: None) is None
    finally:
        s.close()
