"""Browser contract for the Guests block and its setup wizard on web/server.html (2.0).

/api/server, /api/services and /api/guests are faked by route(); see test_guests_api.py
for the API itself. Screenshots go to tests/artifacts/ (untracked).
"""
from __future__ import annotations

import copy
import json
import re

import pytest

from tests.test_server_page import FakeServerAPI, browser, shot, site  # noqa: F401

# `guestctl list --json` rows: a 5-hour and a weekly limit, like the subscription's (30.10.2026)
GUESTS = [
    {"name": "masha", "telegram_id": 111, "port": 18101, "state": "running", "enabled": True,
     "five_hour": {"used": 8_000_000, "limit": 20_000_000, "resets_at": 1793430000},
     "seven_day": {"used": 16_800_000, "limit": 140_000_000, "resets_at": 1793689200},
     "url": "https://sb-masha.example.com", "created": "2026-10-20 10:00:00"},
    {"name": "petya", "telegram_id": 222, "port": 18102, "state": "exited", "enabled": False,
     "five_hour": {"used": 0, "limit": 10_000_000, "resets_at": 0},
     "seven_day": {"used": 35_000_000, "limit": 70_000_000, "resets_at": 1793689200},
     "url": None, "created": "2026-10-21 10:00:00"},
]
CHECKS_BAD = [
    {"id": "docker", "title": "Docker", "ok": True, "detail": "Docker 26.1 works", "fix": ""},
    {"id": "bot", "title": "Guest bot", "ok": False, "detail": "No bot token yet",
     "fix": "Create a bot with @BotFather and paste its token"},
    {"id": "owner", "title": "Your Telegram ID", "ok": False, "detail": "Not set", "fix": "Paste your numeric ID"},
    {"id": "domain", "title": "Domain", "ok": False, "detail": "Not set", "fix": "Point *.example.com here"},
    {"id": "services", "title": "Services", "ok": False, "detail": "not installed",
     "fix": "Run guests/install.sh"},
]

CHECKS_OK = {"bot": "@my_guests_bot answers", "owner": "Telegram ID 42",
             "domain": "sb-test.example.com points to this server (203.0.113.7)"}


class FakeGuests:
    def __init__(self, configured=True, admin=True):
        self.configured = configured
        self.admin = {"bot_set": admin, "owner_id": "42" if admin else ""}
        self.guests = copy.deepcopy(GUESTS)
        self.agents = {"masha": {"agents": 2, "working": 0}}    # the collector's count per sandbox
        self.checks = None
        self.jobs = {}
        self.posts = []
        self.open_error = None
        self.rules_error = None
        self.env = ({"bot_token_set": True, "owner_id": "42", "domain": "example.com"}
                    if configured else {"bot_token_set": False, "owner_id": "", "domain": ""})

    def handle(self, route):
        req = route.request
        path = re.sub(r"^https?://[^/]+", "", req.url).split("?")[0]
        if req.method == "POST":
            body = json.loads(req.post_data or "{}")
            self.posts.append((path, body, req.headers.get("content-type", "")))
            what = path.rsplit("/", 1)[1]
            if what == "check":
                self.checks = CHECKS_BAD
            elif what == "setup":
                if body.get("bot_token") == "bad":
                    return route.fulfill(status=400, content_type="application/json",
                                         body=json.dumps({"error": "That doesn't look like a bot token"}))
                self.checks = [dict(c, ok=c["id"] != "services", detail=CHECKS_OK.get(c["id"], c["detail"]))
                               for c in CHECKS_BAD]
                self.env = {"bot_token_set": True, "owner_id": body.get("owner_id", ""),
                            "domain": body.get("domain", "")}
            elif what == "folders":
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps({"folders": ["/work/angels-help", "/work/public"]}))
            elif what == "rules":
                if self.rules_error:
                    return route.fulfill(status=400, content_type="application/json",
                                         body=json.dumps({"error": self.rules_error}))
                g = next(x for x in self.guests if x["name"] == body["name"])
                g["rules"] = body["rules"]
                g["rules_active"] = bool(body["rules"].get("on")) and not g.get("web", True)
            elif what == "open":
                if self.open_error:
                    return route.fulfill(status=409, content_type="application/json",
                                         body=json.dumps({"error": self.open_error}))
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"url": f"https://sb-{body['name']}.example.com/_owner-entry?t=T0KEN"}))
            else:
                g = next(x for x in self.guests if x["name"] == body["name"])
                if what == "stop":
                    g.update(state="exited", enabled=False)
                elif what == "start":
                    g.update(state="running", enabled=True)
                elif what == "remove":
                    self.guests.remove(g)
                elif what == "web":
                    g.update(web=True, url=f"https://sb-{g['name']}.example.com", rules_active=False)
                elif what == "web-off":
                    g.update(web=False, url=None,
                             rules_active=bool((g.get("rules") or {}).get("on")))
                elif what == "limit":            # guestctl: the week; 5 hours = a seventh
                    g["seven_day"]["limit"] = body["limit"]
                    g["five_hour"]["limit"] = round(body["limit"] / 7)
                self.jobs[body["name"]] = {"action": what, "done": True, "ok": True, "output": ""}
            return route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
        state = {"installed": True, "configured": self.configured, "dir": "/home/me/agentkennel/guests",
                 "env": self.env, "admin": self.admin, "guests": self.guests if self.configured else None,
                 "list_error": None, "checks": self.checks, "checking": False, "jobs": self.jobs}
        if self.agents is not None:
            state["agents"] = self.agents
        route.fulfill(status=200, content_type="application/json", body=json.dumps(state))


def services_none(route):
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"available": True, "services": {}, "dir": "/w/.services", "now": 0}))


def open_page(browser, site, fake, width=1280, height=900, mobile=False):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": height},
                              is_mobile=mobile, has_touch=mobile)
    ctx.set_default_timeout(10000)
    ctx.route(re.compile(r"/api/services(/|\?|$)"), services_none)
    ctx.route(re.compile(r"/api/guests(/|\?|$)"), fake.handle)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda msg: pg.errors.append(msg.text) if msg.type == "error"
          and "Failed to load resource" not in msg.text else None)
    pg.route(re.compile(r"^https://fonts\.(googleapis|gstatic)\.com/"), lambda r: r.abort())
    pg.route(re.compile(r"/api/server(\?|$)"), FakeServerAPI().handle)
    pg.goto(site + "?poll=300")
    pg.wait_for_selector(".row[data-id]")
    return ctx, pg


@pytest.fixture
def fake():
    return FakeGuests()


@pytest.fixture
def page(browser, site, fake):  # noqa: F811
    ctx, pg = open_page(browser, site, fake)
    yield pg
    assert pg.errors == []
    ctx.close()


def guest_rows(pg):
    pg.wait_for_selector("#guests .guest[data-name]")
    return pg.eval_on_selector_all("#guests .guest[data-name]",
                                   "els => els.map(e => [e.dataset.name, e.innerText])")


def acts(pg, name):
    return pg.eval_on_selector_all(f'#guests .guest[data-name="{name}"] button[data-act]',
                                   "els => els.map(e => e.dataset.act)")


def bar_width(pg, name):
    return pg.eval_on_selector(f'#guests .guest[data-name="{name}"] .gspend .bar i',
                               "e => parseFloat(e.style.width)")


def test_lists_guests_with_their_five_hours_and_week(page):
    got = dict(guest_rows(page))
    assert set(got) == {"masha", "petya"}
    assert "5h 40% · wk 12%" in got["masha"]
    assert "5h 0% · wk 50%" in got["petya"]
    assert "sb-masha.example.com" in got["masha"]
    assert "off" in got["petya"].lower()
    assert page.get_attribute('#guests .guest[data-name="masha"] a', "href") == "https://sb-masha.example.com"
    assert "2 guests" in page.inner_text("#gSum")
    assert "week 52M" in page.inner_text("#gSum")          # the summary counts the week
    shot(page, "server-guests-desktop.png")


def dot(pg, name):
    return pg.eval_on_selector(f'#guests .guest[data-name="{name}"] .dot',
                               "e => [e.className, e.title]")


def note(pg, name):
    return pg.inner_text(f'#guests .guest[data-name="{name}"] .gnote')


def wait_dot(pg, name, cls):
    pg.wait_for_function(
        "([n, c]) => { const d = document.querySelector(`#guests .guest[data-name='${n}'] .dot`);"
        " return d && d.classList.contains(c); }", arg=[name, cls])


def test_the_dot_says_whether_agents_are_loaded(page, fake):
    """01.11.2026: green only while agents are loaded, amber while one works, grey when
    the sandbox runs with none, a hollow ring when it is off."""
    guest_rows(page)
    assert dot(page, "masha")[0] == "dot running"
    assert "2 agents loaded" in dot(page, "masha")[1]
    assert note(page, "masha") == "2 agents"
    assert dot(page, "petya")[0] == "dot off"                  # stopped sandbox: a hollow ring
    assert note(page, "petya") == "off"
    assert "agents 2" in page.inner_text("#gSum")

    fake.agents = {"masha": {"agents": 2, "working": 1}}
    wait_dot(page, "masha", "working")
    assert note(page, "masha") == "2 agents · 1 working"

    fake.agents = {"masha": {"agents": 1, "working": 0}}
    wait_dot(page, "masha", "running")
    assert note(page, "masha") == "1 agent"

    fake.agents = {"masha": {"agents": 0, "working": 0}}
    wait_dot(page, "masha", "unloaded")
    assert dot(page, "masha")[0] == "dot unloaded"
    assert page.eval_on_selector('#guests .guest[data-name="masha"] .dot',
                                 "e => [e.offsetWidth, e.offsetHeight]") == [8, 8]
    assert note(page, "masha") == "no agents loaded"
    assert "unloaded" in dot(page, "masha")[1]
    assert "agents 0" in page.inner_text("#gSum")
    shot(page, "server-guests-no-agents.png")


def test_without_a_count_the_dot_only_says_the_sandbox_runs(page, fake):
    """An older server (no "agents" in /api/guests): as before, green = running."""
    guest_rows(page)
    fake.agents = None                                         # from the next poll on
    page.wait_for_function("() => !document.querySelector('#gSum').innerText.includes('agents')")
    assert dot(page, "masha")[0] == "dot running"
    assert note(page, "masha") == ""
    assert "agents" not in page.inner_text("#gSum")


def test_the_bar_is_the_fuller_of_the_two(page):
    guest_rows(page)
    assert bar_width(page, "masha") == 40                  # 5 hours 40%, week 12%
    assert bar_width(page, "petya") == 50                  # 5 hours 0%, week 50%


def test_the_numbers_behind_the_percents_are_in_the_title(page):
    guest_rows(page)
    title = page.get_attribute('#guests .guest[data-name="masha"] .gspend', "title")
    assert "8M of 20M" in title and "17M of 140M" in title, title


def test_buttons_match_the_state(page):
    guest_rows(page)
    # Open only where there is a running dashboard with an address of its own
    assert acts(page, "masha") == ["open", "web", "rules", "limit", "stop", "remove"]
    assert acts(page, "petya") == ["web", "rules", "limit", "start", "remove"]


def test_spend_column_lines_up(page):
    """Rows have different buttons (Open only on a running guest with an address): the
    spend column still starts at one x on every row."""
    guest_rows(page)
    xs = page.eval_on_selector_all("#guests .guest .gspend",
                                   "els => els.map(e => Math.round(e.getBoundingClientRect().left))")
    assert len(xs) == 2 and len(set(xs)) == 1, xs


def guest_page(route):
    route.fulfill(status=200, content_type="text/html", body="<title>masha</title><p>guest dashboard</p>")


def test_open_signs_in_to_the_guest_dashboard(page, fake):
    """Decision 29.10.2026: the owner opens a guest's dashboard without their password."""
    guest_rows(page)
    page.context.route(re.compile(r"^https://sb-masha\.example\.com/"), guest_page)
    with page.expect_popup() as pop:
        page.click('#guests .guest[data-name="masha"] button[data-act="open"]')
    tab = pop.value
    tab.wait_for_url("https://sb-masha.example.com/_owner-entry?t=T0KEN")
    assert fake.posts[-1][:2] == ("/api/guests/open", {"name": "masha"})
    assert tab.evaluate("window.opener") is None       # the guest's page can't reach back
    title = page.get_attribute('#guests .guest[data-name="masha"] button[data-act="open"]', "title")
    assert "password" in title.lower() and "masha" in title
    tab.close()


def test_open_failure_says_why_and_closes_the_empty_tab(page, fake):
    guest_rows(page)
    fake.open_error = "masha's sandbox is not running"
    with page.expect_popup() as pop:
        page.click('#guests .guest[data-name="masha"] button[data-act="open"]')
    page.wait_for_selector("#gErr:not([hidden])")
    assert "masha's sandbox is not running" in page.inner_text("#gErr")
    page.wait_for_timeout(1000)                         # a few polls later: still there
    assert "masha's sandbox is not running" in page.inner_text("#gErr")
    tab = pop.value
    tab.wait_for_event("close", timeout=3000) if not tab.is_closed() else None
    assert tab.is_closed()


def test_stop_and_start(page, fake):
    guest_rows(page)
    page.click('#guests .guest[data-name="masha"] button[data-act="stop"]')
    page.wait_for_selector('#guests .guest[data-name="masha"] button[data-act="start"]')
    assert fake.posts[0][0] == "/api/guests/stop" and fake.posts[0][1] == {"name": "masha"}
    assert fake.posts[0][2].startswith("application/json")


def test_remove_asks_first(page, fake):
    guest_rows(page)
    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    page.click('#guests .guest[data-name="petya"] button[data-act="remove"]')
    page.wait_for_timeout(300)
    assert seen and "petya" in seen[0] and fake.posts == []
    page.once("dialog", lambda d: d.accept())
    page.click('#guests .guest[data-name="petya"] button[data-act="remove"]')
    page.wait_for_selector('#guests .guest[data-name="petya"]', state="detached")
    assert fake.posts[-1][:2] == ("/api/guests/remove", {"name": "petya"})


def test_change_the_weekly_limit(page, fake):
    guest_rows(page)
    page.click('#guests .guest[data-name="masha"] button[data-act="limit"]')
    inp = page.wait_for_selector('#guests .guest[data-name="masha"] input.glimit-in')
    assert inp.input_value() == "140"
    editor = page.inner_text('#guests .guest[data-name="masha"] .glimit')
    assert "Weekly limit" in editor and "5 hours: a seventh" in editor, editor
    inp.fill("210")
    page.click('#guests .guest[data-name="masha"] button.glimit-save')
    page.wait_for_function(
        "() => document.querySelector('#guests .guest[data-name=\"masha\"]').innerText.includes('5h 27% · wk 8%')")
    assert fake.posts[-1][:2] == ("/api/guests/limit", {"name": "masha", "limit": 210_000_000})


def test_off_offers_the_wizard(browser, site):  # noqa: F811
    fake = FakeGuests(configured=False)
    ctx, pg = open_page(browser, site, fake)
    pg.wait_for_selector("#gOff:not([hidden])")
    assert "other people" in pg.inner_text("#gOff").lower()
    pg.click("#gSetup")
    pg.wait_for_selector("#gWizard:not([hidden])")
    # opening the wizard runs the checks
    pg.wait_for_selector('#gWizard .gstep[data-step="docker"][data-ok="1"]')
    assert fake.posts[0][0] == "/api/guests/check"
    assert pg.get_attribute('#gWizard .gstep[data-step="bot"]', "data-ok") == "0"
    assert "@BotFather" in pg.inner_text('#gWizard .gstep[data-step="bot"]')
    assert "Paste your numeric ID" not in pg.inner_text('#gWizard .gstep[data-step="bot"]')
    shot(pg, "server-guests-wizard.png")
    # no id to type: requests go to your own bot, whose owner is named
    assert pg.query_selector("#gOwner") is None
    assert "42" in pg.inner_text('#gWizard .gstep[data-step="bot"] .gadmin')
    # step 2 + 3: the token and the domain go in one save
    pg.fill("#gToken", "123456789:AAH" + "x" * 32)
    pg.fill("#gDomain", "example.com")
    pg.click("#gSave")
    pg.wait_for_selector('#gWizard .gstep[data-step="domain"][data-ok="1"]')
    path, body, _ = fake.posts[-1]
    assert path == "/api/guests/setup"
    assert body == {"bot_token": "123456789:AAH" + "x" * 32, "domain": "example.com"}
    assert pg.input_value("#gToken") == ""             # never kept on the page
    # step 4: the one command to run, with the module's real folder
    step4 = pg.inner_text('#gWizard .gstep[data-step="services"]')
    assert "/home/me/agentkennel/guests/install.sh" in step4 and "sudo /home" not in step4
    shot(pg, "server-guests-wizard-filled.png")
    assert pg.errors == []
    ctx.close()


def test_wizard_shows_a_refused_value(browser, site):  # noqa: F811
    fake = FakeGuests(configured=False)
    ctx, pg = open_page(browser, site, fake)
    pg.click("#gSetup")
    pg.fill("#gToken", "bad")
    pg.click("#gSave")
    pg.wait_for_selector("#gWizErr:not([hidden])")
    assert "bot token" in pg.inner_text("#gWizErr")
    assert pg.errors == []
    ctx.close()


def test_no_bot_of_your_own_sends_you_there_first(browser, site):  # noqa: F811
    fake = FakeGuests(configured=False, admin=False)
    ctx, pg = open_page(browser, site, fake)
    pg.click("#gSetup")
    step = pg.wait_for_selector('#gWizard .gstep[data-step="bot"] .gadmin a')
    assert step.get_attribute("href") == "/telegram"
    assert "your own" in pg.inner_text('#gWizard .gstep[data-step="bot"] .gadmin').lower()
    assert pg.get_attribute('#gWizard .gstep[data-step="bot"]', "data-ok") == "0"
    assert pg.is_disabled("#gSave")
    shot(pg, "server-guests-no-admin-bot.png")
    assert pg.errors == []
    ctx.close()


def test_not_installed_hides_the_block(browser, site):  # noqa: F811
    fake = FakeGuests(configured=False)
    orig = fake.handle

    def handle(route):
        if route.request.method == "GET":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"installed": False, "configured": False, "dir": "/x", "env": fake.env,
                 "guests": None, "list_error": None, "checks": None, "checking": False, "jobs": {}}))
        return orig(route)
    fake.handle = handle
    ctx, pg = open_page(browser, site, fake)
    pg.wait_for_timeout(700)
    assert pg.is_hidden("#guests")
    ctx.close()


def test_phone_width(browser, site):  # noqa: F811
    ctx, pg = open_page(browser, site, FakeGuests(), width=390, height=844, mobile=True)
    guest_rows(pg)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(pg, "server-guests-phone.png")
    assert pg.errors == []
    ctx.close()


def test_phone_wizard(browser, site):  # noqa: F811
    ctx, pg = open_page(browser, site, FakeGuests(configured=False), width=390, height=844, mobile=True)
    pg.click("#gSetup")
    pg.wait_for_selector('#gWizard .gstep[data-step="docker"][data-ok="1"]')
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(pg, "server-guests-wizard-phone.png")
    ctx.close()


# ── web on/off and the agent's rules (owner, 12.11.2026) ─────────────────────────────
def btn(pg, name, act):
    return pg.query_selector(f'#guests .guest[data-name="{name}"] button[data-act="{act}"]')


def test_web_is_a_switch_and_rules_wait_for_it_off(page):
    guest_rows(page)
    web, rules = btn(page, "masha", "web"), btn(page, "masha", "rules")
    assert web.get_attribute("aria-pressed") == "true"
    assert rules.is_disabled() and "web" in rules.get_attribute("title").lower()


def test_turning_web_off_asks_then_does_it(page, fake):
    guest_rows(page)
    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.accept()))
    btn(page, "masha", "web").click()
    page.wait_for_function(
        "() => document.querySelector('#guests .guest[data-name=\"masha\"] button[data-act=\"web\"]')"
        ".getAttribute('aria-pressed') === 'false'")
    assert "masha" in seen[0] and "bot" in seen[0].lower()
    assert fake.posts[-1][:2] == ("/api/guests/web-off", {"name": "masha"})
    assert "Telegram only" in note(page, "masha")
    assert not btn(page, "masha", "rules").is_disabled()


def test_telegram_only_note_has_no_dangling_dot(page, fake):
    """Web off with nothing known about the agents: "Telegram only", not "Telegram only · "."""
    fake.agents = None                  # the collector hasn't counted the agents yet
    fake.guests[0].update(web=False, url=None)
    page.wait_for_function(
        "() => (document.querySelector('#guests .guest[data-name=\"masha\"] .gnote') || {}).textContent"
        " && document.querySelector('#guests .guest[data-name=\"masha\"] .gnote').textContent.includes('Telegram')")
    text = note(page, "masha").strip()
    assert text.startswith("Telegram only") and not text.endswith("·"), text


def test_turning_web_on_asks_too(page, fake):
    fake.guests[0].update(web=False, url=None)
    page.wait_for_function(
        "() => (document.querySelector('#guests .guest[data-name=\"masha\"] button[data-act=\"web\"]') || {})"
        ".getAttribute && document.querySelector('#guests .guest[data-name=\"masha\"] button[data-act=\"web\"]')"
        ".getAttribute('aria-pressed') === 'false'")
    page.once("dialog", lambda d: d.accept())
    btn(page, "masha", "web").click()
    page.wait_for_timeout(400)
    assert fake.posts[-1][:2] == ("/api/guests/web", {"name": "masha"})


def open_rules(page, fake):
    fake.guests[0].update(web=False, url=None)
    page.wait_for_function(
        "() => { const b = document.querySelector('#guests .guest[data-name=\"masha\"] button[data-act=\"rules\"]');"
        " return b && !b.disabled; }")
    btn(page, "masha", "rules").click()
    return page.wait_for_selector('#guests .guest[data-name="masha"] .grules')


def test_rules_keep_the_agent_to_one_job(page, fake):
    guest_rows(page)
    box = open_rules(page, fake)
    page.wait_for_selector('#guests .grules input[data-folder="/work/angels-help"]')
    text = box.inner_text()
    for words in ("Anything in its sandbox", "Only this work", "Publish new sites",
                  "strange or illegal"):
        assert words in text, words
    box.query_selector('input[name="grmode-masha"][value="only"]').check()
    box.query_selector("textarea").fill("The website angelsrepair.com")
    box.query_selector('input[data-folder="/work/angels-help"]').check()
    box.query_selector("button.grules-save").click()
    page.wait_for_selector('#guests .guest[data-name="masha"] .grules', state="detached")
    path, body, _ = fake.posts[-1]
    assert path == "/api/guests/rules"
    assert body == {"name": "masha", "rules": {
        "on": True, "work": "The website angelsrepair.com", "folders": ["/work/angels-help"],
        "actions": {"edit": True, "restart": True, "packages": True,
                    "publish": False, "services": False},
        "flag": True}}
    page.wait_for_function(
        "() => document.querySelector('#guests .guest[data-name=\"masha\"] .gnote').textContent.includes('rules')")


def test_rules_form_desktop_screenshot(page, fake):
    guest_rows(page)
    open_rules(page, fake)
    page.wait_for_selector('#guests .grules input[data-folder="/work/angels-help"]')
    shot(page, "guests-rules-desktop.png")


def test_rules_open_as_they_were_saved(page, fake):
    fake.guests[0]["rules"] = {"on": True, "work": "Site", "folders": ["/work/public"],
                               "actions": {"edit": True, "restart": False, "packages": False,
                                           "publish": True, "services": False}, "flag": False}
    guest_rows(page)
    box = open_rules(page, fake)
    page.wait_for_selector('#guests .grules input[data-folder="/work/public"]')
    assert box.query_selector('input[name="grmode-masha"][value="only"]').is_checked()
    assert box.query_selector("textarea").input_value() == "Site"
    assert box.query_selector('input[data-folder="/work/public"]').is_checked()
    assert not box.query_selector('input[data-folder="/work/angels-help"]').is_checked()
    assert box.query_selector('input[data-action="publish"]').is_checked()
    assert not box.query_selector('input[data-action="restart"]').is_checked()
    assert not box.query_selector('input.grules-flag').is_checked()


def test_only_this_work_needs_the_work_said(page, fake):
    guest_rows(page)
    box = open_rules(page, fake)
    box.query_selector('input[name="grmode-masha"][value="only"]').check()
    box.query_selector("button.grules-save").click()
    page.wait_for_timeout(300)
    assert not any(p[0] == "/api/guests/rules" for p in fake.posts)
    assert "Say what the work is" in box.inner_text()


def test_a_refused_rule_is_said_and_the_form_stays(page, fake):
    fake.rules_error = "rules: '/etc' is not a folder under /work"
    guest_rows(page)
    box = open_rules(page, fake)
    box.query_selector('input[name="grmode-masha"][value="only"]').check()
    box.query_selector("textarea").fill("Site")
    box.query_selector("button.grules-save").click()
    page.wait_for_function(
        "() => document.querySelector('#guests .grules').innerText.includes('not a folder under /work')")


def test_rules_and_the_web_switch_on_a_phone(browser, site, fake):  # noqa: F811
    fake.guests[0].update(web=False, url=None)
    ctx, pg = open_page(browser, site, fake, width=390, height=844, mobile=True)
    try:
        guest_rows(pg)
        btn(pg, "masha", "rules").click()
        pg.wait_for_selector('#guests .guest[data-name="masha"] .grules input[data-folder]')
        assert pg.evaluate("document.documentElement.scrollWidth") <= 390
        shot(pg, "guests-rules-phone.png")
        assert pg.errors == []
    finally:
        ctx.close()


# ── whose Claude a guest runs on (owner, 13.11.2026; mockup parts 1–3) ──────────────
OWNER_WEEK = 8_700_000          # 1% of the owner's subscription week, in weighted tokens
WEEK_END = 1793689200
CLAUDE = {
    "masha": {"mode": "owner_key", "owner_mode": "owner_key", "has_key": False, "last4": None,
              "usd": {"used": 6.1, "limit": 20, "resets_at": WEEK_END}, "usd_week": 20},
    "petya": {"mode": "own", "owner_mode": "owner_key", "has_key": True, "last4": "x4Fk", "usd": None,
              "usd_week": 20},
    "yan": {"mode": "owner_sub", "owner_mode": "owner_sub", "has_key": False, "last4": None, "usd": None,
            "usd_week": 25},
}
SECRET = "sk-ant-api03-" + "Q" * 80 + "xY4k"


class FakeClaudeGuests(FakeGuests):
    """/api/guests with `claude` per guest (Task 9) and the routes behind the Claude form,
    the "For new guests" settings and the owner's key."""

    def __init__(self, key_set=True, mode="owner_key"):
        super().__init__()
        yan = copy.deepcopy(GUESTS[0])
        yan.update(name="yan", url="https://sb-yan.example.com",
                   seven_day={"used": OWNER_WEEK, "limit": 140_000_000, "resets_at": WEEK_END})
        self.guests.append(yan)
        for g in self.guests:
            g["claude"] = copy.deepcopy(CLAUDE[g["name"]])
        self.settings = {"claude_mode": mode, "usd_week": 20, "notify_at": 0.8,
                         "week_limit": 140_000_000, "web": True, "rules": {}}
        self.owner_key = {"set": key_set, "last4": "AbCd" if key_set else None}
        self.key_error = None
        self.hold = set()           # routes ("settings", "claude") answered only when released
        self.held = []

    def _json(self, route, status, body):
        return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def handle(self, route):
        req = route.request
        what = re.sub(r"^https?://[^/]+", "", req.url).split("?")[0].rsplit("/", 1)[1]
        if req.method == "POST" and what in ("claude", "settings", "owner-key"):
            body = json.loads(req.post_data or "{}")
            self.posts.append(("/api/guests/" + what, body, req.headers.get("content-type", "")))
            if what in self.hold:
                self.held.append(route)
                return None
            if what == "owner-key":
                if self.key_error:
                    return self._json(route, 400, {"error": self.key_error})
                self.owner_key = {"set": True, "last4": body["key"][-4:]}
                return self._json(route, 200, {"ok": True, "last4": body["key"][-4:]})
            if what == "settings":
                self.settings.update({k: v for k, v in body.items() if k != "understood"})
                return self._json(route, 200, {"ok": True, "settings": self.settings})
            g = next(x for x in self.guests if x["name"] == body["name"])
            c = g["claude"]
            c.pop("error", None)
            if body.get("secret"):
                c.update(mode="own", has_key=True, last4=body["secret"][-4:], usd=None)
            elif "mode" in body:
                c["mode"] = body["mode"]
                if body["mode"] != "own":
                    c["owner_mode"] = body["mode"]
                c["usd"] = ({"used": 0, "limit": body.get("usd_week", 20), "resets_at": WEEK_END}
                            if body["mode"] == "owner_key" else None)
            if "usd_week" in body:
                c["usd_week"] = body["usd_week"]
                if c.get("usd"):
                    c["usd"]["limit"] = body["usd_week"]
            res = {"ok": True, "claude": c}
            if body.get("mode") == "owner_key" and not self.owner_key["set"]:
                res["warning"] = "no_owner_key"
            return self._json(route, 200, res)
        if req.method == "GET":
            state = {"installed": True, "configured": True, "dir": "/home/me/agentkennel/guests",
                     "env": self.env, "admin": self.admin, "guests": self.guests, "list_error": None,
                     "checks": self.checks, "checking": False, "jobs": self.jobs,
                     "agents": self.agents or {}, "settings": self.settings,
                     "owner_key": self.owner_key,
                     "owner_key_week_usd": 9.4}
            return self._json(route, 200, state)
        return super().handle(route)


@pytest.fixture
def cfake():
    return FakeClaudeGuests()


@pytest.fixture
def cpage(browser, site, cfake):  # noqa: F811
    ctx, pg = open_page(browser, site, cfake)
    yield pg
    assert pg.errors == []
    ctx.close()


def row_text(pg, name):
    return pg.inner_text(f'#guests .guest[data-name="{name}"] .gspend')


def color_of(pg, sel):
    return pg.eval_on_selector(sel, "e => getComputedStyle(e).color")


def css_var(pg, name):
    return pg.evaluate("n => { const d = document.createElement('span'); d.style.color = `var(${n})`;"
                       " document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c; }",
                       name)


def test_rows_say_whose_claude_each_guest_runs_on(cpage):
    guest_rows(cpage)
    assert row_text(cpage, "masha") == "your key · $6.10 of $20"
    assert bar_width(cpage, "masha") == pytest.approx(30.5)
    assert row_text(cpage, "petya") == "own key"
    assert row_text(cpage, "yan") == "your subscription ⚠ · wk 1%"
    assert color_of(cpage, '#guests .guest[data-name="yan"] .gspend .v') == css_var(cpage, "--amber")
    assert color_of(cpage, '#guests .guest[data-name="masha"] .gspend .v') != css_var(cpage, "--amber")
    assert cpage.is_hidden('#guests .guest[data-name="petya"] .gspend .bar')
    assert cpage.is_hidden('#guests .guest[data-name="yan"] .gspend .bar')
    assert "your key this week: $9.40" in cpage.inner_text("#gSum")
    shot(cpage, "server-guests-claude.png")


def test_the_claude_button_replaces_limit(cpage):
    guest_rows(cpage)
    assert acts(cpage, "masha") == ["open", "web", "rules", "claude", "stop", "remove"]
    assert acts(cpage, "petya") == ["web", "rules", "claude", "start", "remove"]


def test_spend_column_lines_up_with_the_claude_button(cpage):
    guest_rows(cpage)
    xs = cpage.eval_on_selector_all("#guests .guest .gspend",
                                    "els => els.map(e => Math.round(e.getBoundingClientRect().left))")
    assert len(xs) == 3 and len(set(xs)) == 1, xs


def test_no_owner_key_spend_without_owner_key_guests(cpage, cfake):
    for g in cfake.guests:
        if g["claude"]["mode"] == "owner_key":
            g["claude"].update(mode="owner_sub", owner_mode="owner_sub", usd=None)
    cpage.wait_for_function("() => !document.querySelector('#gSum').innerText.includes('your key')")


def test_a_broken_claude_block_needs_attention(cpage, cfake):
    cfake.guests[0]["claude"] = {"mode": "owner_sub", "owner_mode": "owner_sub", "has_key": False,
                                 "last4": None, "usd": None, "error": True}
    cpage.wait_for_function(
        "() => (document.querySelector('#guests .guest[data-name=\"masha\"] .gspend') || {}).innerText"
        " === 'settings need attention'")
    assert color_of(cpage, '#guests .guest[data-name="masha"] .gspend .v') == css_var(cpage, "--amber")
    btn(cpage, "masha", "claude").click()
    form = cpage.wait_for_selector('#guests .guest[data-name="masha"] .gclaude')
    # nothing is pre-chosen: the owner picks again and the save sends the mode
    assert not form.query_selector("input[type=radio]:checked")
    form.query_selector('input[value="owner_key"]').check()
    form.query_selector(".gclaude-save").click()
    cpage.wait_for_selector('#guests .guest[data-name="masha"] .gclaude', state="detached")
    path, body, _ = cfake.posts[-1]
    assert path == "/api/guests/claude" and body["name"] == "masha" and body["mode"] == "owner_key"


def assert_key_field(key):
    """Minor 2 (fix round 1): masked by CSS, not type=password, so Chromium never offers to
    save it as a password; no autocomplete, spellcheck or capitals."""
    assert key.get_attribute("type") == "text"
    assert key.evaluate("e => getComputedStyle(e).webkitTextSecurity") == "disc"
    assert key.get_attribute("autocomplete") == "off" and key.get_attribute("spellcheck") == "false"
    assert key.get_attribute("autocapitalize") == "off"


def open_claude(pg, name):
    guest_rows(pg)
    btn(pg, name, "claude").click()
    return pg.wait_for_selector(f'#guests .guest[data-name="{name}"] .gclaude')


def shown(form, sel):
    el = form.query_selector(sel)
    return bool(el) and el.is_visible()


def test_the_claude_form_opens_on_the_guests_mode(cpage):
    form = open_claude(cpage, "masha")
    text = form.inner_text()
    assert "Claude for masha" in text
    for words in ("Your API key", "Their own key or login", "Your subscription ⚠",
                  "Paste it here, or they send it with /claude in the bot"):
        assert words in text, words
    assert form.query_selector('input[value="owner_key"]').is_checked()
    assert form.query_selector("input.glim-usd-in").input_value() == "20"
    assert "a week" in form.inner_text() and "spent $6.10" in text
    assert btn(cpage, "masha", "claude").get_attribute("class") == "on"
    shot(cpage, "server-guests-claude-form.png")


def test_the_limit_follows_the_mode_in_the_claude_form(cpage):
    form = open_claude(cpage, "masha")
    assert shown(form, ".glim-usd") and not shown(form, ".glim-pct")
    form.query_selector("input.glim-usd-in").fill("35")
    form.query_selector('input[value="own"]').check()
    assert not shown(form, ".glim-usd") and not shown(form, ".glim-pct")
    form.query_selector('input[value="owner_sub"]').check()
    assert shown(form, ".glim-pct") and not shown(form, ".glim-usd")
    pct = form.query_selector("input.glim-pct-in")
    assert pct.input_value() == "16.1"                    # 140M / 8.7M
    assert "% of your week" in form.query_selector(".glim-pct").inner_text()
    pct.fill("2")
    form.query_selector('input[value="owner_key"]').check()
    assert form.query_selector("input.glim-usd-in").input_value() == "35"     # both kept
    form.query_selector('input[value="owner_sub"]').check()
    assert pct.input_value() == "2"


def test_the_subscription_warns_and_needs_i_understand(cpage, cfake):
    form = open_claude(cpage, "masha")
    save = form.query_selector(".gclaude-save")
    assert not form.query_selector(".gwarn")
    form.query_selector('input[value="owner_sub"]').check()
    warn = form.wait_for_selector(".gwarn")
    assert ("Anthropic's terms don't allow sharing one Claude subscription between several people. "
            "Guests would use your Pro/Max plan and its limits. If you turn this on, it's your call.") in warn.inner_text()
    assert "I understand" in warn.inner_text()
    use = warn.query_selector(".gwarn-use")
    assert use.inner_text() == "Use my subscription"
    assert save.is_disabled() and use.is_disabled()
    shot(cpage, "server-guests-claude-warning.png")
    save.click(force=True)
    cpage.wait_for_timeout(200)
    assert not any(p[0] == "/api/guests/claude" for p in cfake.posts)
    warn.query_selector("input.gwarn-ok").check()
    assert not save.is_disabled() and not use.is_disabled()
    form.query_selector("input.glim-pct-in").fill("2")
    use.click()
    cpage.wait_for_selector('#guests .guest[data-name="masha"] .gclaude', state="detached")
    claude = [p for p in cfake.posts if p[0] == "/api/guests/claude"]
    assert claude[-1][1] == {"name": "masha", "mode": "owner_sub", "understood": True}
    assert claude[-1][2].startswith("application/json")
    assert cfake.posts[-1][:2] == ("/api/guests/limit", {"name": "masha", "limit": 17_400_000})


def test_cancel_on_the_warning_goes_back(cpage):
    form = open_claude(cpage, "masha")
    form.query_selector('input[value="owner_sub"]').check()
    form.wait_for_selector(".gwarn").query_selector(".gwarn-cancel").click()
    assert not form.query_selector(".gwarn")
    assert form.query_selector('input[value="owner_key"]').is_checked()
    assert not form.query_selector(".gclaude-save").is_disabled()


def test_a_guest_already_on_the_subscription_is_not_warned_again(cpage, cfake):
    form = open_claude(cpage, "yan")
    assert form.query_selector('input[value="owner_sub"]').is_checked()
    assert not form.query_selector(".gwarn")
    form.query_selector("input.glim-pct-in").fill("3")
    form.query_selector(".gclaude-save").click()
    cpage.wait_for_selector('#guests .guest[data-name="yan"] .gclaude', state="detached")
    assert cfake.posts[-1][:2] == ("/api/guests/limit", {"name": "yan", "limit": 26_100_000})
    assert not any(p[0] == "/api/guests/claude" for p in cfake.posts)


def test_dollar_limit_is_saved_with_the_mode(cpage, cfake):
    form = open_claude(cpage, "masha")
    form.query_selector("input.glim-usd-in").fill("35")
    form.query_selector(".gclaude-save").click()
    cpage.wait_for_selector('#guests .guest[data-name="masha"] .gclaude', state="detached")
    assert cfake.posts[-1][:2] == ("/api/guests/claude", {"name": "masha", "usd_week": 35})
    cpage.wait_for_function(
        "() => document.querySelector('#guests .guest[data-name=\"masha\"] .gspend').innerText.includes('of $35')")


def test_paste_a_key_for_a_guest(cpage, cfake):
    form = open_claude(cpage, "masha")
    form.query_selector('input[value="own"]').check()
    key = form.query_selector("input.gcl-key")
    assert key.is_visible()
    assert_key_field(key)
    key.fill(SECRET)
    form.query_selector(".gclaude-save").click()
    cpage.wait_for_selector('#guests .guest[data-name="masha"] .gclaude', state="detached")
    assert cfake.posts[-1][:2] == ("/api/guests/claude", {"name": "masha", "mode": "own", "secret": SECRET})
    cpage.wait_for_function(
        "() => document.querySelector('#guests .guest[data-name=\"masha\"] .gspend').innerText === 'own key'")
    assert SECRET not in cpage.content()
    assert SECRET[:30] not in cpage.evaluate("document.body.innerText")


def test_a_refused_key_is_said_and_the_input_is_emptied(cpage, cfake):
    form = open_claude(cpage, "masha")
    orig = cfake.handle

    def refuse(route):
        if route.request.method == "POST" and route.request.url.endswith("/claude"):
            cfake.posts.append(("/api/guests/claude", json.loads(route.request.post_data), ""))
            return cfake._json(route, 400, {"error": "Anthropic: invalid x-api-key"})
        return orig(route)
    cpage.context.unroute(re.compile(r"/api/guests(/|\?|$)"))
    cpage.context.route(re.compile(r"/api/guests(/|\?|$)"), refuse)
    form.query_selector('input[value="own"]').check()
    form.query_selector("input.gcl-key").fill(SECRET)
    form.query_selector(".gclaude-save").click()
    cpage.wait_for_function(
        "() => document.querySelector('#guests .gclaude').innerText.includes('invalid x-api-key')")
    assert form.query_selector("input.gcl-key").input_value() == ""
    assert SECRET not in cpage.content()


def test_a_guest_with_their_own_key_shows_only_its_last_four(cpage):
    form = open_claude(cpage, "petya")
    assert form.query_selector('input[value="own"]').is_checked()
    assert "…x4Fk" in form.inner_text()


# ── Settings → For new guests ──
def open_settings(pg):
    pg.wait_for_selector("#gSetup")
    pg.click("#gSetup")
    return pg.wait_for_selector("#gNew:not([hidden])")


def test_settings_for_new_guests(cpage):
    box = open_settings(cpage)
    text = box.inner_text()
    for words in ("For new guests", "Claude for new guests", "Your API key", "Their own key or login",
                  "Your subscription ⚠", "Billed to your Anthropic account", "/claude",
                  "Tell me in Telegram at 80%", "Web access", "What the agent may do",
                  "Anything in its sandbox", "Only this work", "✓ checked, works · …AbCd"):
        assert words in text, words
    assert "/key" not in text
    # it sits above the bot setup
    assert cpage.evaluate("document.querySelector('#gNew').compareDocumentPosition("
                          "document.querySelector('#gWizard .gstep[data-step=\"bot\"]')) & 4")
    assert box.query_selector('input[name="gnew-mode"][value="owner_key"]').is_checked()
    assert box.query_selector("input.glim-usd-in").input_value() == "20"
    assert box.query_selector("input.gnew-notify").is_checked()
    assert cpage.is_hidden("#gNoKey")
    shot(cpage, "server-guests-settings.png")


def test_settings_warn_before_the_subscription(cpage, cfake):
    box = open_settings(cpage)
    save = box.query_selector("#gNewSave")
    box.query_selector('input[name="gnew-mode"][value="owner_sub"]').check()
    warn = box.wait_for_selector(".gwarn")
    assert "Anthropic's terms don't allow sharing one Claude subscription" in warn.inner_text()
    assert save.is_disabled()
    warn.query_selector("input.gwarn-ok").check()
    assert not save.is_disabled()
    save.click()
    cpage.wait_for_function("() => document.querySelector('#gNewSaved') && !document.querySelector('#gNewSaved').hidden")
    path, body, _ = cfake.posts[-1]
    assert path == "/api/guests/settings"
    assert body["claude_mode"] == "owner_sub" and body["understood"] is True


def test_settings_limit_follows_the_mode(cpage, cfake):
    box = open_settings(cpage)
    usd = box.query_selector(".glim-usd")
    assert usd.is_visible() and "a week" in usd.inner_text()
    assert not box.query_selector(".glim-pct").is_visible()
    box.query_selector("input.glim-usd-in").fill("25")
    box.query_selector('input[name="gnew-mode"][value="owner_sub"]').check()
    box.wait_for_selector(".gwarn").query_selector("input.gwarn-ok").check()
    pct = box.query_selector(".glim-pct")
    assert pct.is_visible() and "% of your week" in pct.inner_text()
    assert not usd.is_visible()
    assert box.query_selector("input.glim-pct-in").input_value() == "16.1"
    box.query_selector("input.glim-pct-in").fill("10")
    box.query_selector("#gNewSave").click()
    cpage.wait_for_function("() => !document.querySelector('#gNewSaved').hidden")
    body = cfake.posts[-1][1]
    assert body["usd_week"] == 25 and body["week_limit"] == 87_000_000
    assert body["claude_mode"] == "owner_sub"


def test_unchanged_percent_does_not_drift(cpage, cfake):
    box = open_settings(cpage)
    box.query_selector("input.glim-usd-in").fill("30")
    box.query_selector("#gNewSave").click()
    cpage.wait_for_function("() => !document.querySelector('#gNewSaved').hidden")
    body = cfake.posts[-1][1]
    assert body["usd_week"] == 30
    assert "week_limit" not in body and "claude_mode" not in body and "understood" not in body


def test_check_and_save_the_owner_key(browser, site):  # noqa: F811
    fake = FakeClaudeGuests(key_set=False)
    ctx, pg = open_page(browser, site, fake)
    try:
        box = open_settings(pg)
        key = box.query_selector("#gNewKey")
        assert_key_field(key)
        key.fill(SECRET)
        box.query_selector("#gNewKeySave").click()
        pg.wait_for_function("() => document.querySelector('#gNewKeyState').innerText.includes('…xY4k')")
        assert "✓ checked, works · …xY4k" in pg.inner_text("#gNewKeyState")
        assert fake.posts[-1][:2] == ("/api/guests/owner-key", {"key": SECRET})
        assert key.input_value() == ""
        pg.wait_for_timeout(700)                      # a few polls later
        assert SECRET not in pg.content() and SECRET[-20:] not in pg.content()
        assert pg.errors == []
    finally:
        ctx.close()


def test_a_refused_owner_key_says_why(browser, site):  # noqa: F811
    fake = FakeClaudeGuests(key_set=False)
    fake.key_error = "Anthropic did not accept the key: invalid x-api-key"
    ctx, pg = open_page(browser, site, fake)
    try:
        box = open_settings(pg)
        box.query_selector("#gNewKey").fill(SECRET)
        box.query_selector("#gNewKeySave").click()
        pg.wait_for_function("() => document.querySelector('#gNewKeyState').innerText.includes('invalid x-api-key')")
        assert box.query_selector("#gNewKey").input_value() == ""
        pg.wait_for_timeout(700)
        assert "invalid x-api-key" in pg.inner_text("#gNewKeyState")      # the poll keeps it
        assert SECRET not in pg.content()
        assert pg.errors == []
    finally:
        ctx.close()


def test_no_owner_key_yet(browser, site):  # noqa: F811
    """Defaults say owner_key (the release default) but no key is saved: one line says so,
    and "Your API key" can't be chosen until a key passed the check."""
    fake = FakeClaudeGuests(key_set=False)
    ctx, pg = open_page(browser, site, fake)
    try:
        pg.wait_for_selector("#gNoKey:not([hidden])")
        assert pg.inner_text("#gNoKey") == "Add your API key in Settings — new guests run on it"
        box = open_settings(pg)
        assert "Add your API key in Settings — new guests run on it" in box.inner_text()
        assert pg.is_hidden("#gNoKey")                  # the form says it; not twice (minor 3)
        radio = box.query_selector('input[name="gnew-mode"][value="owner_key"]')
        assert radio.is_disabled()
        assert "Add and check your API key first" in box.inner_text()
        shot(pg, "server-guests-settings-no-key.png")
        # changing something else still saves, without the mode
        box.query_selector("input.glim-usd-in").fill("15")
        box.query_selector("#gNewSave").click()
        pg.wait_for_function("() => !document.querySelector('#gNewSaved').hidden")
        assert fake.posts[-1][0] == "/api/guests/settings" and "claude_mode" not in fake.posts[-1][1]
        # a checked key turns the line off and the choice on
        box.query_selector("#gNewKey").fill(SECRET)
        box.query_selector("#gNewKeySave").click()
        pg.wait_for_selector("#gNoKey", state="hidden")
        pg.wait_for_function("() => !document.querySelector('#gNew input[value=\"owner_key\"]').disabled")
        assert "Add and check your API key first" not in box.inner_text()
        assert pg.errors == []
    finally:
        ctx.close()


def test_no_line_when_new_guests_get_the_subscription(browser, site):  # noqa: F811
    fake = FakeClaudeGuests(key_set=False, mode="owner_sub")
    ctx, pg = open_page(browser, site, fake)
    try:
        guest_rows(pg)
        pg.wait_for_timeout(400)
        assert pg.is_hidden("#gNoKey")
        assert pg.errors == []
    finally:
        ctx.close()


def test_settings_web_and_rules_defaults(cpage, cfake):
    box = open_settings(cpage)
    web = box.query_selector("#gNewWeb")
    assert web.get_attribute("aria-pressed") == "true"
    web.click()
    assert web.get_attribute("aria-pressed") == "false"
    box.query_selector('input[name="gnew-rules"][value="only"]').check()
    box.query_selector("#gNewWork").fill("The bakery site")
    box.query_selector("#gNewSave").click()
    cpage.wait_for_function("() => !document.querySelector('#gNewSaved').hidden")
    body = cfake.posts[-1][1]
    assert body["web"] is False
    assert body["rules"]["on"] is True and body["rules"]["work"] == "The bakery site"


def test_the_page_never_says_guests_run_on_your_subscription():
    from tests.test_server_page import PAGE
    html = PAGE.read_text(encoding="utf-8")
    assert "on your subscription" not in html and "on your Claude subscription" not in html


def test_claude_form_and_settings_on_a_phone(browser, site):  # noqa: F811
    fake = FakeClaudeGuests()
    ctx, pg = open_page(browser, site, fake, width=390, height=844, mobile=True)
    try:
        guest_rows(pg)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        shot(pg, "server-guests-claude-phone.png")
        form = open_claude(pg, "masha")
        form.query_selector('input[value="owner_sub"]').check()
        form.wait_for_selector(".gwarn")
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        shot(pg, "server-guests-claude-form-phone.png")
        open_settings(pg)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        shot(pg, "server-guests-settings-phone.png")
        assert pg.errors == []
    finally:
        ctx.close()


# ── fix round 1 (Task 10 review) ──
def test_a_guest_switching_meanwhile_is_not_overwritten(cpage, cfake):
    """The form remembers the mode it was opened on; if the guest switched with /claude in the
    meantime, Save sends nothing (it would drop their own key or skip the subscription tick)."""
    form = open_claude(cpage, "masha")
    cfake.guests[0]["claude"].update(mode="own", has_key=True, last4="9z9z", usd=None)
    cpage.wait_for_function(
        "() => document.querySelector('#guests .guest[data-name=\"masha\"] .gspend').innerText === 'own key'")
    form.query_selector("input.glim-usd-in").fill("35")
    form.query_selector(".gclaude-save").click()
    cpage.wait_for_function(
        "() => document.querySelector('#guests .gclaude').innerText.includes('masha changed their Claude meanwhile — reopen')")
    assert not any(p[0] in ("/api/guests/claude", "/api/guests/limit") for p in cfake.posts)


def test_the_dollar_field_is_prefilled_in_every_mode(cpage):
    form = open_claude(cpage, "yan")
    form.query_selector('input[value="owner_key"]').check()
    assert form.query_selector("input.glim-usd-in").input_value() == "25"
    form = open_claude(cpage, "petya")
    assert form.query_selector("input.glim-usd-in").input_value() == "20"


def test_their_own_key_is_a_default_too(cpage, cfake):
    box = open_settings(cpage)
    own = box.query_selector('input[name="gnew-mode"][value="own"]')
    assert not own.is_disabled()
    assert ("The guest sends it to the bot with /claude: an API key, or a token from claude setup-token."
            in box.inner_text())
    own.check()
    assert not box.query_selector(".gwarn")
    assert box.query_selector(".glim-usd").is_hidden() and box.query_selector(".glim-pct").is_hidden()
    assert not box.query_selector("#gNewSave").is_disabled()
    box.query_selector("#gNewSave").click()
    cpage.wait_for_function("() => !document.querySelector('#gNewSaved').hidden")
    assert cfake.posts[-1][:2] == ("/api/guests/settings", {"claude_mode": "own"})
    # rebuilt from what was saved: the choice shows, Save is usable
    assert cpage.is_checked('#gNew input[name="gnew-mode"][value="own"]')
    assert not cpage.is_disabled("#gNewSave")
    shot(cpage, "server-guests-settings-own.png")


def test_settings_save_stays_off_while_it_is_being_saved(cpage, cfake):
    cfake.hold.add("settings")
    box = open_settings(cpage)
    box.query_selector("input.glim-usd-in").fill("31")
    box.query_selector("#gNewSave").click()
    cpage.wait_for_timeout(900)                        # a few polls: still saving
    assert cpage.is_disabled("#gNewSave")
    assert sum(p[0] == "/api/guests/settings" for p in cfake.posts) == 1
    cfake.hold.clear()
    route = cfake.held.pop()
    body = json.loads(route.request.post_data)
    cfake.settings.update(body)
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"ok": True, "settings": cfake.settings}))
    cpage.wait_for_function("() => !document.querySelector('#gNewSaved').hidden")
    assert not cpage.is_disabled("#gNewSave")


def test_claude_save_stays_off_while_it_is_being_saved(cpage, cfake):
    cfake.hold.add("claude")
    form = open_claude(cpage, "masha")
    form.query_selector("input.glim-usd-in").fill("35")
    save = form.query_selector(".gclaude-save")
    save.click()
    cpage.wait_for_timeout(300)
    form.query_selector('input[value="own"]').check()          # a change re-checks the form
    form.query_selector('input[value="owner_key"]').check()
    cpage.wait_for_timeout(600)
    assert save.is_disabled()
    cfake.hold.clear()
    cfake.held.pop().fulfill(status=200, content_type="application/json", body='{"ok": true}')
    cpage.wait_for_selector('#guests .guest[data-name="masha"] .gclaude', state="detached")


def test_moving_a_guest_to_your_key_without_one_says_so(browser, site):  # noqa: F811
    fake = FakeClaudeGuests(key_set=False)
    ctx, pg = open_page(browser, site, fake)
    try:
        form = open_claude(pg, "yan")
        form.query_selector('input[value="owner_key"]').check()
        form.query_selector(".gclaude-save").click()
        pg.wait_for_function(
            "() => (document.querySelector('#guests .guest[data-name=\"yan\"] .gclaude') || {}).innerText"
            " && document.querySelector('#guests .guest[data-name=\"yan\"] .gclaude').innerText.includes("
            "'Saved. Add your API key in Settings — this guest is refused until then.')")
        # the $ field was pre-filled with yan's kept $25 and not changed: only the mode goes
        assert fake.posts[-1][:2] == ("/api/guests/claude", {"name": "yan", "mode": "owner_key"})
        assert pg.errors == []
    finally:
        ctx.close()
