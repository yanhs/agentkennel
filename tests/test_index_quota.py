"""Browser contract: the Claude allowance next to the cmd button (owner, 30.10.2026).

Approved look (pr/tgfiles/guest-view-1030/new-row-mockup.png): "＋ New terminal" becomes
"＋ New" (title "New terminal"); to the right of cmd two tiny stacked lines "5h 7%" /
"wk 9%" — amber from 70 %, red from 90 % (each line on its own); the title says when each
window resets and, for the owner, which terminals spent the most today. Hidden on phones
(≤ 700 px) and whenever GET /api/quota says {"available": false}.

GET /api/quota and /api/quota/terminals (tests/test_quota_api.py) are faked here.
Times are computed inside each test (a suite run takes minutes).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from tests.test_index_lib import FakeAPI, _open, browser, shot, site  # noqa: F401

CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def quota(u5=0.07, u7=0.09, source="subscription", r5=None, r7=None):
    now = int(time.time())
    return {"available": True, "source": source,
            "five_hour": {"utilization": u5, "resets_at": now + 2 * 3600 if r5 is None else r5},
            "seven_day": {"utilization": u7, "resets_at": now + 3 * 86400 if r7 is None else r7}}


TERMS = {"terminals": [{"id": "aaaa0001", "name": "Appeals", "tokens_today": 12_400_000},
                       {"id": "bbbb0002", "name": "AgentKennel", "tokens_today": 5_100_000},
                       {"id": "cccc0003", "name": "Small one", "tokens_today": 640_000}]}


def open_with(browser, site, answer, terms=TERMS, clock=False, **kw):  # noqa: F811
    """answer: the /api/quota body, or a callable returning it (changes between polls)."""
    ctx, pg = _open(browser, site, FakeAPI(), init_script=None, **kw)
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    calls = {"quota": 0, "terminals": 0}

    def handle(route):
        calls["quota"] += 1
        body = answer() if callable(answer) else answer
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def handle_terms(route):
        calls["terminals"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(terms))
    pg.route(re.compile(r"/api/quota(\?|$)"), handle)
    pg.route(re.compile(r"/api/quota/terminals(\?|$)"), handle_terms)
    pg.claude_posts = []                          # POST /api/quota/claude, held until answered
    pg.route(re.compile(r"/api/quota/claude(\?|$)"), lambda r: pg.claude_posts.append(r))
    if clock:
        pg.clock.install()
    pg.reload()
    pg.wait_for_selector(".card[data-sid]")
    pg.calls = calls
    return ctx, pg


def chip_lines(pg):
    return pg.eval_on_selector_all("#quotaChip > span", "els => els.map(e => e.innerText.trim())")


def test_new_is_short_and_says_what_it_does(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, {"available": False})
    assert pg.inner_text("#newBtn").strip() == "＋ New"
    assert pg.get_attribute("#newBtn", "title") == "New terminal"
    ctx.close()


def test_without_numbers_there_is_no_chip(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, {"available": False})
    pg.wait_for_timeout(400)
    assert pg.calls["quota"] >= 1, "the page asks /api/quota"
    assert pg.is_hidden("#quotaChip")
    assert pg.calls["terminals"] == 0
    assert pg.evaluate("document.getElementById('quota') === null"), "the old line is gone"
    assert pg.errors == []
    ctx.close()


def test_the_chip_sits_right_of_cmd_with_two_stacked_lines(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, quota())
    pg.wait_for_selector("#quotaChip:not([hidden])")
    assert chip_lines(pg) == ["5h 7%", "wk 9%"]
    assert pg.evaluate("document.getElementById('quotaChip').parentElement.id") == "newRow"
    assert pg.evaluate("document.getElementById('cmdBtn').nextElementSibling.id") == "quotaChip"
    box = lambda sel: pg.eval_on_selector(sel, "e => e.getBoundingClientRect().toJSON()")  # noqa: E731
    cmd, chip, row = box("#cmdBtn"), box("#quotaChip"), box("#newRow")
    five, week = (pg.eval_on_selector_all("#quotaChip > span",
                                          "els => els.map(e => e.getBoundingClientRect().toJSON())"))
    assert chip["left"] >= cmd["right"] - 0.5                     # right of cmd …
    assert chip["top"] >= row["top"] - 1 and chip["bottom"] <= row["bottom"] + 1   # … in its row
    assert week["top"] >= five["bottom"] - 1                      # stacked: 5h above wk
    assert abs(chip["right"] - row["right"]) < 1.5                # flush right
    side = pg.eval_on_selector(".sidebar", "e => e.getBoundingClientRect().right")
    assert chip["right"] <= side + 0.5
    over = pg.eval_on_selector(".lib-tools", "e => e.scrollWidth - e.clientWidth")
    assert over <= 1
    assert pg.eval_on_selector("#quotaChip", "e => e.scrollWidth <= e.clientWidth + 1")
    assert pg.errors == []
    ctx.close()


def test_the_title_says_when_each_window_resets(browser, site):  # noqa: F811
    answer = quota(source="guest")
    ctx, pg = open_with(browser, site, answer)
    pg.wait_for_selector("#quotaChip:not([hidden])")
    title = pg.get_attribute("#quotaChip", "title")
    want = pg.evaluate("""([r5, r7]) => {
      const p = n => String(n).padStart(2, '0');
      const d5 = new Date(r5 * 1000), d7 = new Date(r7 * 1000);
      const day = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
      const t5 = (d5.toDateString() === new Date().toDateString() ? '' : day[d5.getDay()] + ' ')
                 + p(d5.getHours()) + ':' + p(d5.getMinutes());
      return [t5, day[d7.getDay()] + ' ' + p(d7.getHours()) + ':' + p(d7.getMinutes())];
    }""", [answer["five_hour"]["resets_at"], answer["seven_day"]["resets_at"]])
    assert title.startswith(f"5 hours: 7% · resets {want[0]}; week: 9% · resets {want[1]}"), title
    assert "Today by terminal" not in title                       # a guest: no terminal list
    assert not CYRILLIC.search(title)
    pg.wait_for_timeout(300)
    assert pg.calls["terminals"] == 0                             # never asked for a guest
    ctx.close()


def test_a_window_not_open_yet_says_no_reset(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, quota(u5=0, r5=0, source="guest"))
    pg.wait_for_selector("#quotaChip:not([hidden])")
    assert chip_lines(pg)[0] == "5h 0%"
    assert pg.get_attribute("#quotaChip", "title").startswith("5 hours: 0%; week: 9% · resets ")
    ctx.close()


def test_the_owner_sees_today_by_terminal(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, quota())
    pg.wait_for_function("(document.getElementById('quotaChip').title || '').includes('Today by')")
    title = pg.get_attribute("#quotaChip", "title")
    assert "Today by terminal: Appeals 12M, AgentKennel 5.1M, Small one 640K" in title, title
    assert pg.calls["terminals"] == 1
    ctx.close()


@pytest.mark.parametrize("u5,u7,c5,c7", [(0.69, 0.5, "", ""), (0.70, 0.89, "warn", "warn"),
                                         (0.95, 0.2, "crit", ""), (0.3, 0.90, "", "crit")],
                         ids=["calm", "amber", "red-5h", "red-week"])
def test_each_line_turns_amber_then_red(browser, site, u5, u7, c5, c7):  # noqa: F811
    ctx, pg = open_with(browser, site, quota(u5, u7))
    pg.wait_for_selector("#quotaChip:not([hidden])")
    cls = pg.eval_on_selector_all("#quotaChip > span", "els => els.map(e => e.className)")
    assert [("crit" if "crit" in c else "warn" if "warn" in c else "") for c in cls] == [c5, c7]
    colors = pg.eval_on_selector_all("#quotaChip > span b", "els => els.map(e => getComputedStyle(e).color)")
    amber, red = "rgb(251, 191, 36)", "rgb(248, 113, 113)"
    for got, c in zip(colors, (c5, c7)):
        assert (got == amber) == (c == "warn") and (got == red) == (c == "crit"), (got, c)
    ctx.close()


def test_hidden_on_a_phone_where_new_leaves_room_to_search(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, quota(), width=390, height=844, mobile=True)
    pg.wait_for_timeout(600)
    assert pg.calls["quota"] >= 1
    assert not pg.is_visible("#quotaChip")
    assert pg.inner_text("#newBtn").strip() == "＋ New"
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    search = pg.eval_on_selector("#search", "e => e.getBoundingClientRect().width")
    new = pg.eval_on_selector("#newBtn", "e => e.getBoundingClientRect().width")
    assert new < 70 and search > 100, (new, search)   # was 111 and 64 px
    ctx.close()


def test_polls_quota_each_minute_terminals_at_most_every_five(browser, site):  # noqa: F811
    state = {"answer": {"available": False}}
    ctx, pg = open_with(browser, site, lambda: state["answer"], clock=True)
    pg.clock.run_for(1000)
    pg.wait_for_timeout(200)
    assert pg.calls["quota"] == 1 and pg.is_hidden("#quotaChip")
    state["answer"] = quota()                    # the server's first probe is in now
    pg.clock.run_for(6000)                       # a first "nothing yet" is asked again soon
    pg.wait_for_selector("#quotaChip:not([hidden])")
    assert pg.calls["quota"] == 2
    pg.wait_for_function("(document.getElementById('quotaChip').title || '').includes('Today by')")
    assert pg.calls["terminals"] == 1
    for _ in range(5):                           # polls at 1, 2, 3, 4, 5 min: the list was
        pg.clock.run_for(60_000)                 # asked at 7 s, so not again yet
        pg.wait_for_timeout(150)
    assert pg.calls["quota"] == 7 and pg.calls["terminals"] == 1
    pg.clock.run_for(60_000)                     # the 6-minute poll: over 5 minutes now
    pg.wait_for_timeout(300)
    assert pg.calls["quota"] == 8 and pg.calls["terminals"] == 2
    ctx.close()


def test_the_dashboard_pages_stay_the_same_file():
    web = Path(__file__).resolve().parents[1] / "web"
    assert (web / "index.html").read_text() == (web / "index-lib.html").read_text()


# ── a guest's own Claude: the label and the "Your Claude" dialog (task 11, 13.11.2026) ──
# GET /api/quota carries the turnstile's `claude` for a guest. The label: "Claude · owner's
# key $6.10/$20" (owner_key) or "Claude · own key …x4Fk" (own) in place of the chip; for
# owner_sub today's chip stays and a small "Claude" sits beside it; a broken setup reads
# "Claude · needs the owner". Without `claude` (an older turnstile, the owner) nothing new.
# The dialog (mockup part 5) posts {"secret"} / {"back": true} to POST /api/quota/claude
# and never keeps the key: the input is cleared whatever the answer.
KEY = "sk-ant-api03-" + "k" * 60 + "x4Fk"
OWNER_KEY = {"mode": "owner_key", "owner_mode": None, "has_key": False, "last4": None,
             "usd": {"used": 6.1, "limit": 20, "resets_at": 1_900_000_000}, "usd_week": 20}
OWN = {"mode": "own", "owner_mode": "owner_key", "has_key": True, "last4": "x4Fk",
       "usd": None, "usd_week": 20}
OWNER_SUB = {"mode": "owner_sub", "owner_mode": None, "has_key": False, "last4": None,
             "usd": None, "usd_week": 20}
BROKEN = dict(OWNER_SUB, error=True)


def with_claude(claude):
    return dict(quota(source="guest"), claude=claude)


def answer_post(pg, body, status=200, wait=True):
    """Answer the held POST /api/quota/claude (the page shows "Checking…" until then)."""
    if wait:
        pg.wait_for_function("() => document.getElementById('claudeSave').disabled")
    deadline = time.time() + 10
    while not pg.claude_posts and time.time() < deadline:
        pg.wait_for_timeout(50)
    route = pg.claude_posts.pop(0)
    sent = route.request
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
    return json.loads(sent.post_data), sent.headers


def open_dialog(pg, btn="#claudeBtn"):
    pg.wait_for_selector(btn + ":not([hidden])")
    pg.click(btn)
    pg.wait_for_selector("#claudeDlg[open]")


@pytest.mark.parametrize("claude,label,chip", [
    (OWNER_KEY, "Claude · owner's key $6.10/$20", False),
    (dict(OWNER_KEY, usd={"used": 0, "limit": 12.5, "resets_at": 1}), "Claude · owner's key $0.00/$12.50", False),
    (OWN, "Claude · own key …x4Fk", False),
    (OWNER_SUB, "Claude", True),
    (BROKEN, "Claude · needs the owner", False)],
    ids=["owner-key", "owner-key-cents", "own", "owner-sub", "broken"])
def test_the_label_says_whose_claude_runs(browser, site, claude, label, chip):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(claude))
    pg.wait_for_selector("#claudeBtn:not([hidden])")
    assert pg.inner_text("#claudeBtn").strip() == label
    assert pg.is_visible("#quotaChip") is chip
    assert not pg.is_visible("#claudeTop")                      # the phone's one
    if chip:
        assert chip_lines(pg) == ["5h 7%", "wk 9%"]            # today's chip, unchanged
    side = pg.eval_on_selector(".sidebar", "e => e.getBoundingClientRect().right")
    assert pg.eval_on_selector("#claudeBtn", "e => e.getBoundingClientRect().right") <= side + 0.5
    assert pg.eval_on_selector(".lib-tools", "e => e.scrollWidth - e.clientWidth") <= 1
    assert pg.eval_on_selector("#claudeBtn", "e => e.scrollWidth <= e.clientWidth + 1")
    assert not CYRILLIC.search(pg.inner_text("#claudeBtn") + (pg.get_attribute("#claudeBtn", "title") or ""))
    assert pg.errors == []
    ctx.close()


@pytest.mark.parametrize("answer", [quota(source="guest"), quota()], ids=["older-turnstile", "owner"])
def test_without_claude_nothing_new_shows(browser, site, answer):  # noqa: F811
    ctx, pg = open_with(browser, site, answer)
    pg.wait_for_selector("#quotaChip:not([hidden])")
    assert pg.is_hidden("#claudeBtn")
    assert chip_lines(pg) == ["5h 7%", "wk 9%"]
    ctx.close()


def test_the_dialog_posts_the_key_and_forgets_it(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(OWNER_KEY))
    open_dialog(pg)
    assert pg.inner_text("#claudeDlg h2").strip() == "Your Claude"
    assert "The owner's key · $6.10 of $20 this week" in pg.inner_text("#claudeNow")
    attrs = pg.eval_on_selector("#claudeKey", """e => ({type: e.type, ac: e.getAttribute('autocomplete'),
        sc: e.getAttribute('spellcheck'), cap: e.getAttribute('autocapitalize'),
        mask: getComputedStyle(e).webkitTextSecurity})""")
    assert attrs == {"type": "text", "ac": "off", "sc": "false", "cap": "off", "mask": "disc"}
    assert pg.is_hidden("#claudeBack")
    pg.fill("#claudeKey", KEY)
    shot(pg, "claude-dialog-desktop.png")
    pg.click("#claudeSave")
    pg.wait_for_function("() => document.getElementById('claudeSave').disabled")
    assert pg.inner_text("#claudeMsg").strip() == "Checking with Anthropic…"
    sent, headers = answer_post(pg, {"ok": True, "message": "<b>Checked</b>: the key works.",
                                     "claude": OWN})
    assert sent == {"secret": KEY}
    assert headers["content-type"].startswith("application/json")
    pg.wait_for_function("() => !document.getElementById('claudeSave').disabled")
    assert pg.inner_text("#claudeMsg").strip() == "<b>Checked</b>: the key works."
    assert pg.query_selector("#claudeMsg b") is None              # text, never HTML
    assert pg.input_value("#claudeKey") == ""
    assert pg.inner_text("#claudeBtn").strip() == "Claude · own key …x4Fk"   # at once
    assert "…x4Fk" in pg.inner_text("#claudeNow")
    assert pg.is_visible("#claudeBack")                            # own + owner_mode now
    assert pg.evaluate(f"!document.documentElement.outerHTML.includes({json.dumps(KEY)})")
    assert pg.errors == []
    ctx.close()


def test_a_refusal_clears_the_key_too(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(OWNER_KEY))
    open_dialog(pg)
    pg.fill("#claudeKey", KEY)
    pg.press("#claudeKey", "Enter")                                 # Enter checks too
    answer_post(pg, {"type": "error", "error": {"type": "rate_limit_error",
                                                "message": "Too many tries; wait an hour."}}, 429)
    pg.wait_for_function("() => !document.getElementById('claudeSave').disabled")
    assert pg.inner_text("#claudeMsg").strip() == "Too many tries; wait an hour."
    assert "bad" in pg.get_attribute("#claudeMsg", "class")
    assert pg.input_value("#claudeKey") == ""
    assert pg.inner_text("#claudeBtn").strip() == "Claude · owner's key $6.10/$20"
    ctx.close()


def test_an_empty_key_is_not_sent(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(OWNER_KEY))
    open_dialog(pg)
    pg.click("#claudeSave")
    pg.wait_for_timeout(300)
    assert pg.claude_posts == []
    assert pg.inner_text("#claudeMsg").strip() == "Paste the key first."
    ctx.close()


@pytest.mark.parametrize("claude,back", [
    (OWN, True), (dict(OWN, owner_mode="owner_sub"), True), (dict(OWN, owner_mode=None), False),
    (OWNER_KEY, False), (OWNER_SUB, False)],
    ids=["own-from-key", "own-from-sub", "own-no-owner-mode", "owner-key", "owner-sub"])
def test_back_only_when_on_their_own_key_with_an_owner_mode(browser, site, claude, back):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(claude))
    open_dialog(pg)
    assert pg.is_visible("#claudeBack") is back
    if back:
        assert pg.inner_text("#claudeBack").strip() == "Back to the owner's key"
        pg.click("#claudeBack")
        sent, _ = answer_post(pg, {"ok": True, "message": "Back on the owner's key.",
                                   "claude": OWNER_KEY})
        assert sent == {"back": True}
        pg.wait_for_function("() => document.getElementById('claudeMsg').textContent.includes('Back on')")
        assert pg.inner_text("#claudeBtn").strip() == "Claude · owner's key $6.10/$20"
        assert pg.is_hidden("#claudeBack")
    ctx.close()


def test_the_subscription_choice_says_how_to_get_a_token(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(OWNER_KEY))
    open_dialog(pg)
    labels = pg.eval_on_selector_all("#claudeDlg .cl-radio", "els => els.map(e => e.innerText.trim())")
    assert labels == ["API key", "Your Claude subscription"]
    assert pg.is_checked("#claudeKindApi")
    hint = "Run claude setup-token on your computer and paste the token"
    assert hint not in pg.inner_text("#claudeDlg")
    pg.click("text=Your Claude subscription")
    pg.wait_for_function(f"() => document.getElementById('claudeDlg').innerText.includes({json.dumps(hint)})")
    text = pg.inner_text("#claudeDlg")
    assert "on your subscription" not in text.lower()
    assert not CYRILLIC.search(text)
    ctx.close()


def test_a_broken_setup_only_says_the_owner_was_told(browser, site):  # noqa: F811
    ctx, pg = open_with(browser, site, with_claude(BROKEN))
    open_dialog(pg)
    assert "The owner has been told" in pg.inner_text("#claudeDlg")
    for sel in ("#claudeKey", "#claudeSave", "#claudeBack", "#claudeKindApi"):
        assert not pg.is_visible(sel), sel
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#claudeDlg:not([open])", state="attached")
    assert pg.claude_posts == []
    ctx.close()


def test_on_a_phone_the_label_and_the_dialog_fit(browser, site):  # noqa: F811
    """The phone's tools row has no room (the search must stay usable): the label sits
    in the topbar beside ⋯ — whole on the empty page, just "Claude" over a terminal."""
    ctx, pg = open_with(browser, site, with_claude(OWNER_KEY), width=390, height=844, mobile=True)
    pg.wait_for_selector("#claudeTop:not([hidden])")
    assert pg.is_visible("#claudeTop") and not pg.is_visible("#claudeBtn")
    assert pg.inner_text("#claudeTop").strip() == "Claude · owner's key $6.10/$20"
    top = pg.eval_on_selector("#claudeTop", "e => e.getBoundingClientRect().toJSON()")
    menu = pg.eval_on_selector("#tMenu", "e => e.getBoundingClientRect().toJSON()")
    assert top["right"] <= menu["left"] and abs(top["top"] - menu["top"]) < 6, (top, menu)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    search = pg.eval_on_selector("#search", "e => e.getBoundingClientRect().width")
    new = pg.eval_on_selector("#newBtn", "e => e.getBoundingClientRect().width")
    assert new < 70 and search > 100, (new, search)
    open_dialog(pg, "#claudeTop")
    pg.fill("#claudeKey", KEY)
    box = pg.eval_on_selector("#claudeDlg", "e => e.getBoundingClientRect().toJSON()")
    assert box["left"] >= 0 and box["right"] <= 390 and box["bottom"] <= 844, box
    assert pg.eval_on_selector("#claudeKey", "e => parseFloat(getComputedStyle(e).fontSize)") >= 16
    shot(pg, "claude-dialog-phone.png")
    ctx.close()
