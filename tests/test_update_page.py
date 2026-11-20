"""The panel's side of updates in a real browser (web/js/update.js against a fake
/api/update): the line above the version, the dialog with what changed, the Update button
refused while agents work, the result of the last update, phones (the ⋯ menu) and Docker."""
import http.server
import json
import re
import threading
from functools import partial
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
WEB = Path(__file__).resolve().parents[1] / "web"

NOTES = [{"version": "2.8.0", "title": "updates from the panel",
          "body": "### Panel\n- **Update available** next to the version.\n- One `click`.\n- <script>x</script>"},
         {"version": "2.7.3", "title": "a fix", "body": "- Fixed."}]
BASE = {"kind": "git", "current": "2.7.2", "running": "2.7.2", "latest": "2.8.0", "tag": "v2.8.0",
        "available": True, "patch": False, "notes": NOTES, "checked_at": 1, "error": "",
        "auto": False, "pending": None, "job": None, "last": None}


@pytest.fixture(scope="module")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                         partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB)))
    srv.RequestHandlerClass.log_message = lambda *a, **k: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


class Api:
    def __init__(self, state, answers=None):
        self.state, self.answers, self.posts = state, answers or {}, []

    def handle(self, route):
        req = route.request
        path = re.sub(r"^https?://[^/]+", "", req.url).split("?")[0]
        if path == "/api/update" and req.method == "GET":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
        if path.startswith("/api/update/") and req.method == "POST":
            what = path.rsplit("/", 1)[1]
            self.posts.append((what, json.loads(req.post_data or "{}")))
            status, body = self.answers.get(what, (200, self.state))
            if status == 200:
                self.state = body
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        return route.abort()


def open_page(browser, site, api, width=1440, height=900, mobile=False):
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile)
    pg = ctx.new_page()
    pg.route(re.compile(r"/api/"), api.handle)
    pg.route(re.compile(r"^https://fonts\."), lambda r: r.abort())
    pg.goto(site)
    return ctx, pg


def test_no_update_no_line(browser, site):
    api = Api({**BASE, "available": False, "latest": "2.7.2", "notes": []})
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_timeout(600)
        assert not pg.is_visible("#sideUpd")
        assert pg.query_selector("#tUpd") is None
        assert pg.inner_text("#sideFoot").startswith("v")          # the version line as before
    finally:
        ctx.close()


def test_update_available_line_and_dialog(browser, site):
    api = Api(dict(BASE))
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_selector("#updLine:visible")
        assert pg.inner_text("#updLine") == "Update available: v2.8.0"
        # right above the version, inside the sidebar
        up, foot = pg.eval_on_selector("#sideUpd", "e => e.getBoundingClientRect().bottom"), \
            pg.eval_on_selector("#sideFoot", "e => e.getBoundingClientRect().top")
        assert abs(up - foot) < 2
        pg.click("#updLine")
        pg.wait_for_selector("#updDlg[open]")
        assert pg.inner_text("#updTitle") == "AgentKennel v2.8.0"
        assert "You have v2.7.2" in pg.inner_text("#updSub")
        heads = pg.eval_on_selector_all("#updNotes h3", "els => els.map(e => e.textContent)")
        assert heads == ["v2.8.0 — updates from the panel", "v2.7.3 — a fix"]
        assert pg.inner_text("#updNotes b") == "Update available"
        assert pg.query_selector("#updNotes script") is None          # escaped, shown as text
        assert "<script>x</script>" in pg.inner_text("#updNotes")
        assert pg.is_visible("#updGo") and not pg.is_visible("#updIdle")
        assert pg.is_visible("#updAutoRow") and "2.7.x" in pg.inner_text("#updAutoRow")
        pg.screenshot(path=str(Path(__file__).parent / "artifacts" / "update-dialog.png"))
    finally:
        ctx.close()


def test_busy_agents_offer_update_when_idle(browser, site):
    api = Api(dict(BASE), answers={"start": (409, {"error": "agents are working",
                                                   "busy": ["blog fixes", "4f2a"]})})
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_selector("#updLine:visible")
        pg.click("#updLine")
        pg.click("#updGo")
        pg.wait_for_selector("#updIdle:visible")
        assert api.posts[0] == ("start", {"tag": "v2.8.0"})
        assert "2 agents are working: blog fixes, 4f2a" in pg.inner_text("#updMsg")
        assert not pg.is_visible("#updGo")
        api.answers["start"] = (200, {**BASE, "pending": {"tag": "v2.8.0", "why": "idle"}})
        pg.click("#updIdle")
        pg.wait_for_selector("#updCancel:visible")
        assert api.posts[1] == ("start", {"tag": "v2.8.0", "when_idle": True})
        assert "Waiting until no agent is working" in pg.inner_text("#updMsg")
        assert pg.inner_text("#updLine") == "Update to v2.8.0 waits for idle agents"
    finally:
        ctx.close()


def test_a_running_update_and_its_result(browser, site):
    job = {"state": "running", "from": "2.7.2", "to": "2.8.0", "step": "restart",
           "steps": [{"step": "checks", "ok": True, "detail": ""}, {"step": "backup", "ok": True, "detail": ""}]}
    api = Api({**BASE, "job": job})
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_selector("#updLine:visible")
        assert pg.inner_text("#updLine") == "Updating to v2.8.0…"
        assert "run" in pg.get_attribute("#updLine", "class")
        api.state = {**BASE, "available": False, "current": "2.7.2", "latest": "2.8.0", "job": None,
                     "last": {"state": "rolled-back", "from": "2.7.2", "to": "2.8.0", "step": "health check",
                              "error": "the panel is not healthy after 90 s", "steps": job["steps"]}}
        pg.click("#updLine")
        pg.wait_for_function("document.getElementById('updLine').textContent === 'Update to v2.8.0 failed'")
        msg = pg.inner_text("#updMsg")
        assert "failed at health check" in msg and "Rolled back: v2.7.2 runs as before." in msg
        assert "bad" in pg.get_attribute("#updLine", "class")
        pg.click("#updClose")                                  # read: the notice goes
        pg.wait_for_function("() => true")
        pg.wait_for_timeout(300)
        assert ("seen", {}) in api.posts
    finally:
        ctx.close()


def test_phones_get_it_in_the_menu(browser, site):
    api = Api(dict(BASE))
    ctx, pg = open_page(browser, site, api, width=400, height=800, mobile=True)
    try:
        pg.wait_for_selector("#tUpd", state="attached")
        assert not pg.is_visible("#sideUpd")
        pg.tap("#tMenu")
        assert pg.inner_text("#tUpd") == "Update available: v2.8.0"
        items = pg.eval_on_selector_all("#tMenuList [role=menuitem]", "els => els.map(e => e.id)")
        assert items == ["tTg", "tChpw", "tTheme", "tUpd", "tLogout"]
        pg.tap("#tUpd")
        pg.wait_for_selector("#updDlg[open]")
        assert pg.is_visible("#updGo")
        box = pg.eval_on_selector("#updDlg", "e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }")
        assert box[0] >= 15 and box[1] <= 385                    # fits the phone with a gutter
    finally:
        ctx.close()


def test_docker_shows_the_command_and_no_button(browser, site):
    api = Api({**BASE, "kind": "docker", "command": "docker compose pull && docker compose up -d"})
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_selector("#updLine:visible")
        pg.click("#updLine")
        pg.wait_for_selector("#updCmd:visible")
        assert pg.inner_text("#updCmdText") == "docker compose pull && docker compose up -d"
        assert not pg.is_visible("#updGo") and not pg.is_visible("#updAutoRow")
    finally:
        ctx.close()


def test_the_night_setting_is_saved(browser, site):
    api = Api(dict(BASE), answers={"auto": (200, {**BASE, "auto": True})})
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_selector("#updLine:visible")
        pg.click("#updLine")
        pg.check("#updAuto")
        pg.wait_for_timeout(300)
        assert ("auto", {"enabled": True}) in api.posts
        assert pg.is_checked("#updAuto")
    finally:
        ctx.close()


def test_off_shows_nothing(browser, site):
    api = Api({"kind": "off", "running": "2.7.2"})
    ctx, pg = open_page(browser, site, api)
    try:
        pg.wait_for_timeout(600)
        assert not pg.is_visible("#sideUpd") and pg.query_selector("#tUpd") is None
    finally:
        ctx.close()
