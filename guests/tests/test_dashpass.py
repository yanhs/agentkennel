"""TDD: the guest bot never sends a dashboard password that no longer works.

The sandbox keeps only a hash of its dashboard password (/app/.sessions/.dashpass);
the bot keeps the password it handed out (turnstile/data/<name>.dashboard-password).
Owner, 06.11.2026: /web changes nothing — it sends the bot's password while that is
the one in force, and says "you set your own" when it isn't (it can't be read back
from a hash); /newpassword puts a new one in and sends it (Sveta could not get in).
Every docker call goes through a fake here: no real sandbox is touched."""
import importlib
import os
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

GUESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUESTS))
sys.path.insert(0, str(GUESTS.parent))

import dashpass  # noqa: E402


class FakeSandbox:
    """docker exec sbx-<name> … against an in-memory /app/.sessions/.dashpass."""

    def __init__(self, stored=None, running=True, write_fails=False):
        self.stored, self.running, self.write_fails = stored, running, write_fails
        self.calls = []

    def __call__(self, argv, input=None):
        self.calls.append((argv, input))
        assert argv[:2] == ["docker", "exec"], argv
        if not self.running:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="is not running")
        script = argv[-1]
        if "cat >" in script:                          # the write
            if self.write_fails:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="disk full")
            self.stored = input
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout=(self.stored or "") + "\n", stderr="")

    def writes(self):
        return [c for c in self.calls if "cat >" in c[0][-1]]


def _bot_file(tmp_path, pw="first-pass-1234"):
    f = tmp_path / "anna.dashboard-password"
    f.write_text(pw + "\n")
    f.chmod(0o600)
    return f


def test_the_hash_is_the_one_the_dashboard_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKENNEL_PASSFILE", str(tmp_path / ".dashpass"))
    sys.modules.pop("status_server", None)
    ss = importlib.import_module("status_server")
    (tmp_path / ".dashpass").write_text(dashpass.hash_line("s3cret-pw"))
    assert ss._pw_verify("s3cret-pw") is True
    assert ss._pw_verify("other") is False
    ss._pw_store("made-by-dashboard")                 # and the other way round
    assert dashpass.verifies("made-by-dashboard", (tmp_path / ".dashpass").read_text())


def test_the_bots_password_still_works_so_web_sends_it(tmp_path):
    _bot_file(tmp_path)
    box = FakeSandbox(stored=dashpass.hash_line("first-pass-1234"))
    assert dashpass.status("anna", tmp_path, run=box) == ("first-pass-1234", False)
    assert box.writes() == []


def test_an_own_password_is_reported_and_left_alone(tmp_path):
    f = _bot_file(tmp_path)
    box = FakeSandbox(stored=dashpass.hash_line("the-guests-own"))
    before = box.stored
    assert dashpass.status("anna", tmp_path, run=box) == (None, True)
    assert box.writes() == [] and box.stored == before     # /web never resets
    assert f.read_text().strip() == "first-pass-1234"


def test_a_sandbox_never_logged_into_keeps_its_first_password(tmp_path):
    _bot_file(tmp_path)
    box = FakeSandbox(stored=None)                      # no hash yet: the first login seeds it
    assert dashpass.status("anna", tmp_path, run=box) == ("first-pass-1234", False)
    assert box.writes() == []


def test_a_stopped_sandbox_gets_the_bots_password(tmp_path):
    _bot_file(tmp_path)
    box = FakeSandbox(stored=dashpass.hash_line("x"), running=False)
    assert dashpass.status("anna", tmp_path, run=box) == ("first-pass-1234", False)


def test_no_bot_copy_no_password(tmp_path):
    assert dashpass.status("anna", tmp_path, run=FakeSandbox()) == (None, False)


def test_newpassword_puts_a_new_one_in_and_returns_it(tmp_path):
    f = _bot_file(tmp_path)
    box = FakeSandbox(stored=dashpass.hash_line("the-guests-own"))
    pw = dashpass.reset("anna", tmp_path, run=box)
    assert pw and pw not in ("first-pass-1234", "the-guests-own") and len(pw) >= 12
    assert dashpass.verifies(pw, box.stored)            # it opens the dashboard now
    assert not dashpass.verifies("the-guests-own", box.stored)
    assert f.read_text().strip() == pw                  # the bot's copy follows
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    (argv, data), = box.writes()
    assert argv[:4] == ["docker", "exec", "-i", "sbx-anna"]
    assert pw not in " ".join(argv)                     # never on a command line
    assert pw not in (data or "")                       # only its hash goes in
    assert dashpass.status("anna", tmp_path, run=box) == (pw, False)   # /web sends it now


def test_newpassword_works_without_an_earlier_copy(tmp_path):
    box = FakeSandbox(stored=dashpass.hash_line("whatever"))
    pw = dashpass.reset("anna", tmp_path, run=box)
    assert pw and dashpass.verifies(pw, box.stored)
    assert (tmp_path / "anna.dashboard-password").read_text().strip() == pw


def test_a_failed_newpassword_returns_nothing_and_keeps_the_copy(tmp_path):
    f = _bot_file(tmp_path)
    box = FakeSandbox(stored=dashpass.hash_line("the-guests-own"), write_fails=True)
    assert dashpass.reset("anna", tmp_path, run=box) is None
    assert f.read_text().strip() == "first-pass-1234"


def test_a_stopped_sandbox_gets_no_new_password(tmp_path):
    f = _bot_file(tmp_path)
    assert dashpass.reset("anna", tmp_path, run=FakeSandbox(running=False)) is None
    assert f.read_text().strip() == "first-pass-1234"


def test_a_bad_name_never_reaches_docker(tmp_path):
    box = FakeSandbox()
    for bad in ("../x", "a b", "", "x;rm"):
        assert dashpass.status(bad, tmp_path, run=box) == (None, False)
        assert dashpass.reset(bad, tmp_path, run=box) is None
    assert box.calls == []
