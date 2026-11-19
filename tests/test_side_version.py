"""Owner 19.11.2026: the panel shows no version anywhere. A small line at the bottom of the sidebar:
'AgentKennel v<version> · Site · GitHub'. The version comes from web/version.json, which must match
the newest CHANGELOG heading, so a release cannot forget it.
Owner 19.11.2026 (later): the line shows ONLY the version ("v2.7.2"), and that text is the one link,
to https://agentkennel.com (a new tab): no product name, no other words or links."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def newest_changelog_version():
    m = re.search(r"^## v(\d+\.\d+\.\d+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert m, "no '## vX.Y.Z' heading in CHANGELOG.md"
    return m.group(1)


def test_version_json_matches_the_changelog():
    v = json.loads((WEB / "version.json").read_text())["version"]
    assert v == newest_changelog_version()


FOOT_LINK = ('<a id="akVer" href="https://agentkennel.com" target="_blank" rel="noopener"></a>')


def test_sidebar_footer_is_only_the_version_linking_to_the_site():
    for page in ("index.html", "index-lib.html"):
        html = (WEB / page).read_text()
        foot = re.search(r'<div class="side-foot"[^>]*>([\s\S]*?)</div>', html)
        assert foot, f"{page}: no side-foot"
        body = foot.group(1).strip()
        # exactly one link, to the site; its text is filled from version.json
        assert body == FOOT_LINK, f"{page}: the footer holds only the version link: {body!r}"
        assert "fetch('/version.json'" in html or 'fetch("/version.json"' in html
        assert "textContent='v'+j.version" in html


def test_footer_link_text_is_v_and_the_version_in_a_browser():
    """The real page in Chromium: the footer's only link reads v<version.json> and goes to the site."""
    import http.server, threading
    from functools import partial
    pw = __import__("pytest").importorskip("playwright.sync_api")
    v = json.loads((WEB / "version.json").read_text())["version"]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                         partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB)))
    srv.RequestHandlerClass.log_message = lambda *a, **k: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.route(re.compile(r"/api/"), lambda r: r.abort())
            pg.route(re.compile(r"^https://fonts\."), lambda r: r.abort())
            pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
            pg.wait_for_function("document.getElementById('akVer').textContent.length > 0")
            links = pg.eval_on_selector_all("#sideFoot a", "els => els.map(a => [a.href, a.textContent, a.target])")
            assert links == [["https://agentkennel.com/", "v" + v, "_blank"]]
            assert pg.inner_text("#sideFoot").strip() == "v" + v
            b.close()
    finally:
        srv.shutdown()
