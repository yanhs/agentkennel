"""The panel page is ONE page with its CSS and JS in files of their own (owner 19.11.2026:
"isn't it sensible to split such big files?").

- web/index-lib.html is the old name of the dashboard (the session-library migration copied
  it over index.html); it stays a working URL as a symlink to index.html, never a copy.
- web/index.html keeps only the markup: the styles are web/css/panel.css, the code web/js/*.js
  (classic scripts sharing one global scope, in the page's order).
- Every asset link carries ?v=<hash of the file> (bin/stamp-assets), so a browser never mixes
  a new page with old cached scripts, and an asset change changes index.html (live reload).
- Each asset is served exactly like the page: behind the login (real Caddy, both Caddyfiles);
  the three icons stay public."""
import hashlib
import http.server
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
INDEX = WEB / "index.html"
ASSET_RE = re.compile(r'<(?:link|script)\b[^>]*?\b(?:href|src)="(/(?:css|js)/[^"]+)"')


def asset_links(html=None):
    return ASSET_RE.findall(html if html is not None else INDEX.read_text(encoding="utf-8"))


def asset_path(link):
    return WEB / link.split("?", 1)[0].lstrip("/")


# ── one page, no copies ─────────────────────────────────────────────────────
def test_index_lib_is_a_link_to_index_not_a_copy():
    lib = WEB / "index-lib.html"
    assert lib.is_symlink(), "web/index-lib.html must be a symlink to index.html, not a copy"
    assert os.readlink(lib) == "index.html"
    assert lib.read_bytes() == INDEX.read_bytes()


def test_no_two_html_files_in_web_are_the_same():
    seen = {}
    for p in sorted(WEB.glob("*.html")):
        if p.is_symlink():
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        assert h not in seen, f"{p.name} duplicates {seen[h]}"
        seen[h] = p.name


def test_git_stores_index_lib_as_a_symlink():
    r = subprocess.run(["git", "ls-files", "-s", "web/index-lib.html"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout:
        pytest.skip("not a git checkout")
    assert r.stdout.split()[0] == "120000", r.stdout       # git's mode for a symlink


# ── markup only; styles and code in files ───────────────────────────────────
def test_page_has_no_inline_styles_or_app_code():
    html = INDEX.read_text(encoding="utf-8")
    assert "<style" not in html
    inline = re.findall(r"<script>([\s\S]*?)</script>", html)
    # only the theme bootstrap stays inline: it must run before the first paint
    assert len(inline) == 1 and "agentkennel-theme" in inline[0] and len(inline[0]) < 400, inline
    assert len(html.splitlines()) < 200, "the page should be markup only"


def test_every_referenced_asset_exists_and_every_asset_is_referenced():
    links = asset_links()
    assert any(l.startswith("/css/") for l in links) and sum(l.startswith("/js/") for l in links) >= 5
    for l in links:
        assert asset_path(l).is_file(), l
    referenced = {asset_path(l).resolve() for l in links}
    on_disk = {p.resolve() for p in list((WEB / "css").glob("*.css")) + list((WEB / "js").glob("*.js"))}
    assert on_disk == referenced, f"not referenced: {on_disk - referenced}"


def test_scripts_are_classic_and_in_the_page_order():
    """Classic scripts (no type=module, no defer/async): they run in order, as the one inline
    script did, sharing its global scope."""
    tags = re.findall(r"<script\b[^>]*\bsrc=[^>]*>", INDEX.read_text(encoding="utf-8"))
    assert tags
    for t in tags:
        assert not re.search(r"\b(type=\"module\"|defer|async)\b", t), t
    names = [re.search(r"/js/([^?\"]+)", t).group(1) for t in tags]
    assert names[0] == "state.js" and names[-2:] == ["boot.js", "version.js"], names


def test_asset_links_carry_the_hash_of_the_file():
    for l in asset_links():
        m = re.search(r"\?v=([0-9a-f]{10})$", l)
        assert m, f"{l}: no ?v=<hash> (run bin/stamp-assets)"
        assert m.group(1) == hashlib.sha256(asset_path(l).read_bytes()).hexdigest()[:10], \
            f"{l}: stale hash (run bin/stamp-assets)"
    r = subprocess.run([str(ROOT / "bin" / "stamp-assets"), "--check"], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_stamp_assets_rewrites_a_stale_link(tmp_path):
    """The tool itself: an old ?v= or none at all becomes the file's hash; --check says so."""
    root = tmp_path / "co"
    (root / "bin").mkdir(parents=True)
    shutil.copy2(ROOT / "bin" / "stamp-assets", root / "bin" / "stamp-assets")
    (root / "web" / "js").mkdir(parents=True)
    (root / "web" / "js" / "a.js").write_text("var a = 1;\n")
    (root / "web" / "index.html").write_text('<script src="/js/a.js?v=0000000000"></script>\n')
    (root / "web" / "index-lib.html").symlink_to("index.html")
    tool = [str(root / "bin" / "stamp-assets")]
    assert subprocess.run(tool + ["--check"], capture_output=True).returncode == 1
    assert subprocess.run(tool, capture_output=True).returncode == 0
    h = hashlib.sha256(b"var a = 1;\n").hexdigest()[:10]
    assert (root / "web" / "index.html").read_text() == f'<script src="/js/a.js?v={h}"></script>\n'
    assert (root / "web" / "index-lib.html").is_symlink()
    assert subprocess.run(tool + ["--check"], capture_output=True).returncode == 0


# ── in a browser: every asset loads, the code runs without an error ─────────
def _free_port():
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("name", ["index.html", "index-lib.html"])
def test_page_loads_every_asset_and_runs_without_errors(name):
    pw = pytest.importorskip("playwright.sync_api")
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB)))
    port = srv.server_address[1]
    srv.RequestHandlerClass.log_message = lambda *a, **k: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    errors, assets = [], {}
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("response", lambda r: assets.__setitem__(r.url.split(str(port), 1)[1], r.status)
                  if re.search(r"/(css|js)/", r.url) else None)
            pg.route(re.compile(r"^https://fonts\."), lambda r: r.abort())
            pg.route(re.compile(r"/api/library(/|\?|$)"), lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"sessions": []}'))
            pg.route(re.compile(r"/(api/(?!library)|login|sess/)"), lambda r: r.abort())
            pg.goto(f"http://127.0.0.1:{port}/{name}")
            pg.wait_for_function("document.getElementById('akVer').textContent.length > 0")
            # the styles applied (a token from panel.css), the code ran to its last file
            assert pg.evaluate("getComputedStyle(document.documentElement)"
                               ".getPropertyValue('--fg-rgb').trim()") != ""
            assert pg.evaluate("typeof poll === 'function' && typeof pick === 'function'"
                               " && typeof enablePasteImage === 'function'")
            b.close()
    finally:
        srv.shutdown()
    assert not errors, errors
    want = {l for l in asset_links()}
    assert set(assets) == want and all(s == 200 for s in assets.values()), assets


# ── served like the page: behind the login (real Caddy) ─────────────────────
def caddy_bin():
    for c in (os.environ.get("CADDY_BIN"), shutil.which("caddy"), "/usr/local/bin/caddy",
              "/usr/bin/caddy"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


class _Auth(http.server.BaseHTTPRequestHandler):
    """The login gate's /check: 200 with the test cookie, 401 without."""
    def do_GET(self):
        ok = "agents_session=ok" in (self.headers.get("Cookie") or "")
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.mark.skipif(not caddy_bin(), reason="no caddy binary")
@pytest.mark.parametrize("caddyfile", ["Caddyfile", "docker/Caddyfile"])
def test_caddy_serves_the_assets_behind_the_login_and_the_icons_to_all(caddyfile, tmp_path):
    auth = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Auth)
    threading.Thread(target=auth.serve_forever, daemon=True).start()
    port = _free_port()
    text = (ROOT / caddyfile).read_text()
    # a private copy: the fake gate instead of status_server, this checkout's web/, no admin API
    text = text.replace("127.0.0.1:3046", f"127.0.0.1:{auth.server_address[1]}")
    text = re.sub(r"root \* \S+", f"root * {WEB}", text)
    text = text.replace("{\n", "{\n\tadmin off\n", 1)
    conf = tmp_path / "Caddyfile"
    conf.write_text(text)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "AGENTKENNEL_SITE": f"http://127.0.0.1:{port}"}
    proc = subprocess.Popen([caddy_bin(), "run", "--config", str(conf), "--adapter", "caddyfile"],
                            env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)

    def get(path, cookie=None):
        req = urllib.request.Request(base + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    try:
        deadline = time.time() + 20
        while True:
            try:
                get("/favicon.svg")
                break
            except OSError:
                assert proc.poll() is None, proc.stdout.read().decode()
                assert time.time() < deadline, "caddy did not start"
                time.sleep(0.2)
        links = asset_links()
        for l in links:
            code, hdrs, _ = get(l)
            assert code == 302 and hdrs.get("Location", "").endswith("/login"), (l, code)
            code, hdrs, body = get(l, "agents_session=ok")
            assert code == 200 and body == asset_path(l).read_bytes(), (l, code)
            want = "text/css" if l.startswith("/css/") else "javascript"
            assert want in hdrs.get("Content-Type", ""), (l, hdrs.get("Content-Type"))
        for icon in ("/favicon.svg", "/favicon.ico", "/apple-touch-icon.png"):
            assert get(icon)[0] == 200, icon
        for page in ("/", "/index.html", "/index-lib.html"):
            assert get(page)[0] == 302, page
            code, _, body = get(page, "agents_session=ok")
            assert code == 200 and body == INDEX.read_bytes(), page
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        auth.shutdown()


def test_nginx_template_serves_web_behind_the_login():
    """nginx/agents-subdomain.conf: the auth_request is server-wide, only the three icons
    switch it off, and `location /` serves the whole web/ folder (css/ and js/ included)."""
    src = (ROOT / "nginx" / "agents-subdomain.conf").read_text()
    server = src[:src.index("# Plain http")]
    assert re.search(r"^\s{4}auth_request /__auth;", server, re.M)
    public = re.findall(r"location ~ (\S+) \{ auth_request off;", server)
    assert public == [r"^/(favicon\.svg|favicon\.ico|apple-touch-icon\.png)$"], public
    block = re.search(r"# Dashboard UI\s*location / \{(.*?)\}", server, re.S).group(1)
    assert re.search(r"root \S+/web;", block) and "auth_request off" not in block
