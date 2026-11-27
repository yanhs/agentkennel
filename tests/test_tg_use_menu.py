"""/use is the one menu of the Telegram bridge (owner 27.11.2026): the Claude allowance in
its head, the terminals as buttons, and under them New, Archive, Compact and Restart —
the /list, /archive, /new, /compact and /restart commands are no longer in the bot's menu
(typed, they still work)."""

import pytest

# test_tg_bridge first: it sets the bridge's environment (token, owner) before the import
from tests.test_tg_bridge import (CHAT, U1, U2, _callbacks, run_cb, run_cmd, world)  # noqa: F401
import tg_bridge as tb  # noqa: E402
import tg_use_menu as um  # noqa: E402


def _q(five, week):
    return {"available": True, "source": "subscription",
            "five_hour": {"utilization": five, "resets_at": 0},
            "seven_day": {"utilization": week, "resets_at": 1796238000}}


# --- the allowance line ---------------------------------------------------------

def test_quota_line_shows_the_session_and_the_week_in_percent():
    assert um.quota_line(_q(0.12, 0.454)) == "📊 Claude: session 12% · week 45%"


def test_quota_line_without_numbers_is_nothing():
    assert um.quota_line({"available": False}) is None
    assert um.quota_line(None) is None
    assert um.quota_line({"available": True, "five_hour": {"utilization": "x"}}) is None


def test_quota_line_keeps_a_spent_allowance_at_100():
    assert um.quota_line(_q(1.0, 1.3)) == "📊 Claude: session 100% · week 100%"


def test_fetch_quota_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(um.urllib.request, "urlopen", boom)
    assert um.fetch_quota() is None


# --- /use: the head and the buttons ----------------------------------------------

@pytest.fixture
def quota(monkeypatch):
    box = {"data": _q(0.12, 0.45)}
    monkeypatch.setattr(um, "fetch_quota", lambda: box["data"])
    return box


def _rows(markup):
    return [[b.callback_data for b in row] for row in markup.inline_keyboard]


def test_use_menu_shows_the_allowance(world, quota):
    world.add("active one", uuid=U1)
    text, _ = run_cmd(tb.cmd_use, [])[-1]
    assert "session 12% · week 45%" in text


def test_use_menu_without_the_allowance_says_nothing_of_it(world, quota):
    quota["data"] = {"available": False}
    world.add("active one", uuid=U1)
    text, _ = run_cmd(tb.cmd_use, [])[-1]
    assert "session" not in text and "📊" not in text


def test_use_menu_with_a_current_terminal_offers_its_compact_and_restart(world, quota):
    world.add("active one", uuid=U1)
    tb.set_current(CHAT, "aaaa1111")
    _, markup = run_cmd(tb.cmd_use, [])[-1]
    rows = _rows(markup)
    assert rows[0] == ["use:aaaa1111"]
    assert ["ucmp:aaaa1111", "urst:aaaa1111"] in rows
    assert ["unew"] in rows                     # no archive: no Archive button


def test_use_menu_without_a_pick_has_no_compact_or_restart(world, quota):
    world.add("active one", uuid=U1)
    cbs = _callbacks(run_cmd(tb.cmd_use, [])[-1][1])
    assert not [c for c in cbs if c.startswith(("ucmp:", "urst:"))]
    assert "unew" in cbs


def test_use_menu_counts_the_archive_on_its_button(world, quota):
    world.add("active one", uuid=U1)
    world.add("in the archive", uuid=U2, archived=True)
    text, markup = run_cmd(tb.cmd_use, [])[-1]
    buttons = {b.callback_data: b.text for row in markup.inline_keyboard for b in row}
    assert ["unew", "uarc"] in _rows(markup)
    assert "Archive" in buttons["uarc"] and "1" in buttons["uarc"]


def test_use_menu_with_no_terminals_still_offers_new(world, quota):
    text, markup = run_cmd(tb.cmd_use, [])[-1]
    assert _callbacks(markup) == ["unew"]
    assert "No terminals yet" in text


def test_use_menu_when_everything_is_archived_offers_the_archive(world, quota):
    world.add("gone", uuid=U1, archived=True)
    text, markup = run_cmd(tb.cmd_use, [])[-1]
    assert _callbacks(markup) == ["unew", "uarc"]
    assert "outside the archive" in text


# --- the buttons -----------------------------------------------------------------

def test_new_button_creates_a_terminal_and_picks_it(world, quota):
    sink = run_cb(tb.on_use_action_cb, "unew")
    sid = tb.get_current(CHAT)
    assert sid and world.ensured() == [sid]
    assert sink[0][0].startswith("🆕 ")


def test_archive_button_opens_the_archive(world, quota):
    world.add("gone", uuid=U1, archived=True)
    text, markup = run_cb(tb.on_use_action_cb, "uarc")[-1]
    assert _callbacks(markup) == ["arch:aaaa1111"] and "Archive" in text


def test_compact_button_types_compact_into_that_terminal(world, quota, monkeypatch):
    world.add("active one", uuid=U1)
    world.loaded.append("aaaa1111")
    typed = []
    monkeypatch.setattr(tb, "send_text", lambda s, t: typed.append((s, t)))
    text, _ = run_cb(tb.on_use_action_cb, "ucmp:aaaa1111")[-1]
    assert typed == [("cs-aaaa1111", "/compact")] and "compact" in text


def test_compact_button_on_a_stopped_terminal_types_nothing(world, quota, monkeypatch):
    world.add("active one", uuid=U1)
    typed = []
    monkeypatch.setattr(tb, "send_text", lambda s, t: typed.append((s, t)))
    text, _ = run_cb(tb.on_use_action_cb, "ucmp:aaaa1111")[-1]
    assert typed == [] and "isn't running" in text


def test_restart_button_restarts_an_idle_terminal(world, quota, monkeypatch):
    world.add("active one", uuid=U1)
    calls = []

    def lib_restart(sid, force=False, check=False):
        calls.append((sid, force, check))
        return {"busy": None} if check else {"ok": True}
    monkeypatch.setattr(tb.ss, "lib_restart", lib_restart)
    text, _ = run_cb(tb.on_use_action_cb, "urst:aaaa1111")[-1]
    assert calls == [("aaaa1111", False, True), ("aaaa1111", False, False)]
    assert text.startswith("⟳ Restarted")


def test_restart_button_on_a_busy_terminal_asks_first(world, quota, monkeypatch):
    world.add("active one", uuid=U1)
    calls = []

    def lib_restart(sid, force=False, check=False):
        calls.append((sid, force, check))
        return {"busy": "working"} if check else {"ok": True}
    monkeypatch.setattr(tb.ss, "lib_restart", lib_restart)
    text, markup = run_cb(tb.on_use_action_cb, "urst:aaaa1111")[-1]
    assert calls == [("aaaa1111", False, True)]
    assert _callbacks(markup) == ["rst:aaaa1111"] and "working" in text


def test_action_buttons_reject_garbage(world, quota, monkeypatch):
    typed = []
    monkeypatch.setattr(tb, "send_text", lambda s, t: typed.append((s, t)))
    for data in ("ucmp:../x", "urst:;rm", "uzzz"):
        run_cb(tb.on_use_action_cb, data)
    assert typed == [] and world.ensured() == []


# --- the bot's menu ---------------------------------------------------------------

def test_bot_menu_has_only_use_and_the_keys():
    assert [c.command for c in tb.BOT_COMMANDS] == ["use", "read", "esc", "enter", "services"]
    use = {c.command: c.description for c in tb.BOT_COMMANDS}["use"]
    assert "new" in use and "archive" in use and "restart" in use


def test_the_old_commands_still_work_when_typed():
    from telegram.ext import CallbackQueryHandler, CommandHandler
    app = tb.build_app()
    hs = [h for group in app.handlers.values() for h in group]
    cmds = {c for h in hs if isinstance(h, CommandHandler) for c in h.commands}
    assert {"list", "archive", "new", "compact", "restart"} <= cmds
    cbs = {h.callback: h for h in hs if isinstance(h, CallbackQueryHandler)}
    pat = cbs[tb.on_use_action_cb].pattern
    for data in ("unew", "uarc", "ucmp:aaaa1111", "urst:aaaa1111"):
        assert pat.match(data), data
    assert not pat.match("use:aaaa1111")


def test_need_pick_points_to_use_only():
    assert tb.NEED_PICK == "Pick a terminal first: /use"
