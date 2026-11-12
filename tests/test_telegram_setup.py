"""TDD for the dashboard's Telegram-bot setup (status_server tg_* helpers).

The dashboard lets you configure the optional Telegram bridge from the browser:
enter a bot token + your Telegram user id -> the config is saved to the volume and
the bridge process is (re)started. On container restart it auto-starts if enabled.
These tests cover the config store, status, start, and autostart logic — without
actually spawning the bridge or talking to Telegram.
"""
from __future__ import annotations

import importlib
import re
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

TERMINAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TERMINAL_DIR))


def _load():
    sys.modules.pop("status_server", None)
    mod = importlib.import_module("status_server")
    # the real host runs the bridge as a systemd service; tests decide explicitly
    mod.tg_systemd_active = lambda: False
    return mod


def test_config_roundtrip(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TG_CONF", str(tmp_path / "telegram.json"))
    assert mod.tg_load() == {}                       # nothing saved yet
    mod.tg_save({"token": "T", "owner": "1", "enabled": True})
    assert mod.tg_load() == {"token": "T", "owner": "1", "enabled": True}


def test_running_zero_without_pidfile(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TG_PID", str(tmp_path / "tg.pid"))
    assert mod.tg_running() == 0


def test_render_shows_status_and_saved_values(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TG_CONF", str(tmp_path / "telegram.json"))
    monkeypatch.setattr(mod, "TG_PID", str(tmp_path / "tg.pid"))
    mod.tg_save({"token": "123:ABC", "owner": "777", "enabled": True})
    html = mod.tg_render()
    assert "stopped" in html                         # no live pid -> stopped
    assert "123:ABC" in html and "777" in html       # form is pre-filled
    assert 'action="/telegram"' in html


def test_start_passes_token_and_owner_via_env(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TG_PID", str(tmp_path / "tg.pid"))
    fake = MagicMock()
    fake.pid = os.getpid()                            # pretend the bridge = a live pid
    with patch.object(mod.subprocess, "Popen", return_value=fake) as pop, \
         patch.object(mod.os, "makedirs"):
        assert mod.tg_start("TOKEN", "777") is True
        env = pop.call_args.kwargs["env"]
        assert env["TG_BRIDGE_TOKEN"] == "TOKEN"
        assert env["TG_BRIDGE_OWNER"] == "777"
    assert mod.tg_running() == os.getpid()            # pidfile written + process alive


def test_autostart_starts_only_when_enabled(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TG_CONF", str(tmp_path / "telegram.json"))
    monkeypatch.setattr(mod, "tg_running", lambda: 0)

    # enabled -> tg_start called with the saved creds
    mod.tg_save({"token": "T", "owner": "1", "enabled": True})
    called = {}
    monkeypatch.setattr(mod, "tg_start", lambda t, o: called.update(t=t, o=o))
    mod.tg_autostart()
    assert called == {"t": "T", "o": "1"}

    # disabled -> tg_start must NOT be called
    mod.tg_save({"token": "T", "owner": "1", "enabled": False})
    monkeypatch.setattr(mod, "tg_start",
                        lambda *a: pytest.fail("must not start when disabled"))
    mod.tg_autostart()


# ── bridge managed by systemd (this server: claude-tg-bridge.service) ───────
def test_render_shows_running_when_systemd_bridge_is_active(tmp_path, monkeypatch):
    # the page only knew its own pidfile and said "stopped" while the real
    # bridge ran as a systemd user service (owner 2026-09-25)
    mod = _load()
    monkeypatch.setattr(mod, "TG_PID", str(tmp_path / "tg.pid"))
    monkeypatch.setattr(mod, "tg_systemd_active", lambda: True)
    html = mod.tg_render()
    assert "running" in html and "stopped" not in html
    assert "claude-tg-bridge" in html
    assert 'value="start"' not in html and 'value="stop"' not in html   # no duplicate-start buttons


def test_start_and_stop_refuse_when_systemd_bridge_is_active(tmp_path, monkeypatch):
    # a second bridge on the same token would fight the live one for updates
    mod = _load()
    monkeypatch.setattr(mod, "tg_systemd_active", lambda: True)
    monkeypatch.setattr(mod, "tg_start", lambda *a: pytest.fail("must not start a second bridge"))
    monkeypatch.setattr(mod, "tg_stop", lambda *a: pytest.fail("must not touch the systemd bridge"))
    assert "systemd" in mod.tg_action("start", "T", "1")
    assert "systemd" in mod.tg_action("stop", "", "")


def test_autostart_skips_when_systemd_bridge_is_active(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TG_CONF", str(tmp_path / "telegram.json"))
    monkeypatch.setattr(mod, "tg_running", lambda: 0)
    monkeypatch.setattr(mod, "tg_systemd_active", lambda: True)
    mod.tg_save({"token": "T", "owner": "1", "enabled": True})
    monkeypatch.setattr(mod, "tg_start", lambda *a: pytest.fail("must not start a second bridge"))
    mod.tg_autostart()


# ── a second bot for notifications (owner 12.11.2026) ─────────────────────────
# Warnings (a guest's spending, a strange request, a site published) can go to a bot of
# their own, so they don't get lost in the chat with the agents. "Let them in?" stays in
# the main bot: its buttons are answered there.

def _conf(mod, tmp_path, monkeypatch, **cfg):
    monkeypatch.setattr(mod, "TG_CONF", str(tmp_path / "telegram.json"))
    monkeypatch.setattr(mod, "TG_PID", str(tmp_path / "tg.pid"))
    if cfg:
        mod.tg_save(cfg)


def test_render_has_the_notifications_bot_field(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:MAIN", owner="777", enabled=True, notify_token="2:NOTE")
    html = mod.tg_render()
    assert 'name="notify_token"' in html and "2:NOTE" in html
    assert 'value="notify"' in html


def test_the_notifications_field_stays_when_systemd_runs_the_bridge(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:MAIN", owner="777", enabled=True)
    monkeypatch.setattr(mod, "tg_systemd_active", lambda: True)
    html = mod.tg_render()
    assert 'name="notify_token"' in html and 'value="notify"' in html


def test_saving_the_notifications_bot_sends_a_test_and_keeps_the_rest(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:MAIN", owner="777", enabled=True)
    sent = []
    monkeypatch.setattr(mod, "tg_send", lambda token, chat, text: (sent.append((token, chat, text)), (True, ""))[1])
    msg = mod.tg_notify_action("2:NOTE")
    assert sent and sent[0][:2] == ("2:NOTE", "777")
    assert mod.tg_load() == {"token": "1:MAIN", "owner": "777", "enabled": True, "notify_token": "2:NOTE"}
    assert "notifications" in msg.lower()


def test_a_bot_that_cant_write_to_you_is_not_saved(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:MAIN", owner="777", enabled=True)
    monkeypatch.setattr(mod, "tg_send", lambda *a: (False, "Forbidden: bot can't initiate conversation with a user"))
    msg = mod.tg_notify_action("2:NOTE")
    assert "notify_token" not in mod.tg_load()
    assert "Start" in msg and "initiate" in msg


def test_notifications_bot_needs_the_main_one_first_and_must_differ(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "tg_send", lambda *a: pytest.fail("nothing to send to"))
    assert "main bot" in mod.tg_notify_action("2:NOTE").lower()
    mod.tg_save({"token": "1:MAIN", "owner": "777", "enabled": True})
    assert "second bot" in mod.tg_notify_action("1:MAIN").lower()
    assert "token" in mod.tg_notify_action("not a token").lower()
    assert mod.tg_load() == {"token": "1:MAIN", "owner": "777", "enabled": True}


def test_an_empty_field_sends_notifications_to_the_main_bot_again(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:MAIN", owner="777", enabled=True, notify_token="2:NOTE")
    monkeypatch.setattr(mod, "tg_send", lambda *a: pytest.fail("nothing to test"))
    msg = mod.tg_notify_action("")
    assert "notify_token" not in mod.tg_load()
    assert "main bot" in msg.lower()


def test_starting_the_main_bot_keeps_the_notifications_bot(tmp_path, monkeypatch):
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:OLD", owner="777", enabled=False, notify_token="2:NOTE")
    monkeypatch.setattr(mod, "tg_start", lambda *a: True)
    mod.tg_action("start", "1:MAIN", "777")
    assert mod.tg_load()["notify_token"] == "2:NOTE" and mod.tg_load()["token"] == "1:MAIN"


def test_enter_in_the_notifications_field_doesnt_restart_the_main_bot(tmp_path, monkeypatch):
    """Enter submits a form with its first button: the field and its button belong to a
    form of their own, or Enter there would press "Save & start"."""
    mod = _load()
    _conf(mod, tmp_path, monkeypatch, token="1:MAIN", owner="777", enabled=True)
    html = mod.tg_render()
    assert re.search(r'<form[^>]*id="tgn"[^>]*action="/telegram"', html)
    assert re.search(r'<input[^>]*name="notify_token"[^>]*form="tgn"', html)
    assert re.search(r'<button[^>]*form="tgn"[^>]*value="notify"', html)
