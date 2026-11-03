"""The product's name inside the dashboard (01.11.2026): before, "AgentKennel" was only in
the tab title and on the login page; a person using it saw no name anywhere. Now the paw and
the name head the sidebar, and the empty screen (no terminal open) carries both."""
from __future__ import annotations

from tests.test_index_lib import _open, api, browser, shot, site  # noqa: F401

NAME = "AgentKennel"


def visible(pg, sel):
    return pg.eval_on_selector(sel, "e => !!(e.offsetWidth || e.offsetHeight) && getComputedStyle(e).visibility !== 'hidden'")


def test_desktop_sidebar_is_headed_by_the_paw_and_the_name(browser, site, api):  # noqa: F811
    ctx, pg = _open(browser, site, api)
    try:
        assert visible(pg, "#brand")
        assert pg.get_attribute("#brand img", "src") == "/favicon.svg"
        assert pg.inner_text("#brand").strip() == NAME
        top = pg.eval_on_selector("#brand", "e => e.getBoundingClientRect().top")
        head = pg.eval_on_selector(".sidebar-header", "e => e.getBoundingClientRect().top")
        assert top < head                                   # the first thing in the sidebar
        shot(pg, "brand-desktop.png")
    finally:
        ctx.close()


def test_empty_screen_carries_the_name_and_says_where_to_pick(browser, site, api):  # noqa: F811
    ctx, pg = _open(browser, site, api)
    try:
        assert NAME in pg.inner_text("#ph")
        assert visible(pg, "#ph img")
        assert visible(pg, "#ph .hint-wide") and not visible(pg, "#ph .hint-narrow")
        assert "on the left" in pg.inner_text("#ph .hint-wide")
    finally:
        ctx.close()


def test_phone_shows_the_name_in_the_topbar(browser, site, api):  # noqa: F811
    """The phone's top row (＋ New, cmd, search, T, gauge, CPU) has no room left: the search
    must stay usable. The row below it, the topbar, is empty on its left: the name goes there."""
    ctx, pg = _open(browser, site, api, width=390, height=844, mobile=True)
    try:
        assert not visible(pg, "#brand")
        assert visible(pg, "#brandTop img") and pg.inner_text("#brandTop").strip() == NAME
        assert pg.eval_on_selector("#search", "e => e.getBoundingClientRect().width") > 100
        # the empty screen has the name, and points up: the list is above on a phone
        assert NAME in pg.inner_text("#ph")
        assert visible(pg, "#ph .hint-narrow") and not visible(pg, "#ph .hint-wide")
        assert "above" in pg.inner_text("#ph .hint-narrow")
        shot(pg, "brand-phone.png")
        pg.tap("#list .card[data-sid] .proj")
        pg.wait_for_selector("#wrap iframe")
        assert visible(pg, "#brandTop img")
        assert not visible(pg, "#brandTop .brand-word")     # the terminal's name needs the room
        info = pg.eval_on_selector("#tInfo", "e => [e.scrollWidth, e.clientWidth]")
        assert info[0] <= info[1] + 1, f"the terminal's name is cut: {info}"
        over = pg.eval_on_selector(".topbar", "e => e.scrollWidth - e.clientWidth")
        assert over <= 1, f"the phone's topbar overflows by {over}px"
        shot(pg, "brand-phone-open.png")
    finally:
        ctx.close()


def test_a_narrow_phone_keeps_the_paw_and_drops_the_word(browser, site, api):  # noqa: F811
    ctx, pg = _open(browser, site, api, width=360, height=780, mobile=True)
    try:
        pg.tap("#list .card[data-sid] .proj")
        pg.wait_for_selector("#wrap iframe")
        assert visible(pg, "#brandTop img")
        over = pg.eval_on_selector(".topbar", "e => e.scrollWidth - e.clientWidth")
        assert over <= 1, f"the 360px topbar overflows by {over}px"
    finally:
        ctx.close()


def test_desktop_has_no_second_name_in_the_topbar(browser, site, api):  # noqa: F811
    ctx, pg = _open(browser, site, api)
    try:
        assert not visible(pg, "#brandTop")
    finally:
        ctx.close()


def test_the_name_comes_back_when_the_terminal_closes(browser, site, api):  # noqa: F811
    ctx, pg = _open(browser, site, api)
    try:
        pg.click("#list .card[data-sid] .proj")
        pg.wait_for_selector("#wrap iframe")
        assert pg.query_selector("#ph") is None or not visible(pg, "#ph")
        pg.evaluate("showPlaceholder()")
        assert NAME in pg.inner_text("#ph") and visible(pg, "#ph img")
        assert visible(pg, "#brand")                        # the sidebar's stays all along
    finally:
        ctx.close()
