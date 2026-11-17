"""A guest's rules in the live terminal (owner, 17.11.2026): while the guests module's lock is up
in a sandbox (guests/rules_gate.py writes /etc/agentkennel/rules/on as root), the dashboard has
no plain command line — refused by the server, not only hidden — and every tmux server started
later reads the lock (no prefix key). Without the flag nothing changes (other tests).

AGENTKENNEL_RULES_FLAG points the core at a test file instead of /etc/agentkennel/rules/on."""
import os
import subprocess

from tests.test_cmd_shell import SCRIPT, SHELL, run
from tests.test_cmd_shell import deck  # noqa: F401  (fixture)
from tests.test_library_api import get
from tests.test_library_api import api  # noqa: F401  (fixture)

import library

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def flag_up(tmp_path):
    f = tmp_path / "rules-on"
    f.write_text("on\n")
    return str(f)


def test_library_knows_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKENNEL_RULES_FLAG", str(tmp_path / "missing"))
    assert library.shell_locked() is False
    monkeypatch.setenv("AGENTKENNEL_RULES_FLAG", flag_up(tmp_path))
    assert library.shell_locked() is True


def test_the_default_flag_is_the_root_owned_one(monkeypatch):
    monkeypatch.delenv("AGENTKENNEL_RULES_FLAG", raising=False)
    assert library.rules_flag() == "/etc/agentkennel/rules/on"


def test_open_session_refuses_the_command_line_while_locked(deck, tmp_path):  # noqa: F811
    r = run(deck, "shell", AGENTKENNEL_RULES_FLAG=flag_up(tmp_path))
    assert r.returncode == 2
    assert "owner" in r.stdout
    assert not deck.server_up()


def test_open_session_dry_run_refuses_too(deck, tmp_path):  # noqa: F811
    r = run(deck, "shell", DRY_RUN="1", AGENTKENNEL_RULES_FLAG=flag_up(tmp_path))
    assert r.returncode == 2 and "bash -l" not in r.stdout


def test_topics_still_open_while_locked(deck, tmp_path):  # noqa: F811
    e = deck.add("A")
    r = run(deck, e["id"], DRY_RUN="1", AGENTKENNEL_RULES_FLAG=flag_up(tmp_path))
    assert r.returncode == 0, r.stderr


def test_shell_ensure_refuses_while_locked(deck, tmp_path):  # noqa: F811
    r = deck.cli("shell-ensure", AGENTKENNEL_RULES_FLAG=flag_up(tmp_path))
    assert r.returncode != 0
    assert not deck.server_up() or not deck.has(SHELL)


def test_api_says_the_command_line_is_locked(api, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENTKENNEL_RULES_FLAG", flag_up(tmp_path))
    code, data = get(api)
    assert code == 200
    assert data["shell"]["locked"] is True and data["shell"]["active"] is False


def test_api_shell_unchanged_without_the_lock(api, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENTKENNEL_RULES_FLAG", str(tmp_path / "missing"))
    assert "locked" not in get(api)[1]["shell"]


def test_tmux_conf_reads_the_lock_last():
    lines = [l.strip() for l in open(os.path.join(REPO, "tmux.conf")) if l.strip()
             and not l.lstrip().startswith("#")]
    assert lines[-1] == ('if-shell "test -f /etc/agentkennel/rules/on -a -f '
                         '/etc/agentkennel/rules/tmux.conf" '
                         '"source-file /etc/agentkennel/rules/tmux.conf"')


def test_open_session_tells_the_owner_through_the_gate(deck, tmp_path):  # noqa: F811
    """The refusal is reported (rules_gate.py report shell -> the turnstile's /flag)."""
    gate = tmp_path / "gate.py"
    said = tmp_path / "said"
    gate.write_text(f"import sys\nopen({str(said)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    r = run(deck, "shell", AGENTKENNEL_RULES_FLAG=flag_up(tmp_path),
            AGENTKENNEL_RULES_GATE=str(gate))
    assert r.returncode == 2 and said.read_text() == "report shell"


def test_open_session_without_a_gate_still_refuses(deck, tmp_path):  # noqa: F811
    r = run(deck, "shell", AGENTKENNEL_RULES_FLAG=flag_up(tmp_path),
            AGENTKENNEL_RULES_GATE=str(tmp_path / "missing.py"))
    assert r.returncode == 2 and "owner" in r.stdout


# ── the page, in a browser (tests/test_index_lib.py's fake API) ──────────────────────
from tests.test_index_lib import FakeAPI, _open  # noqa: E402
from tests.test_index_lib import browser, site  # noqa: E402,F401  (fixtures)


def test_the_page_hides_cmd_while_locked_and_shows_it_otherwise(browser, site):  # noqa: F811
    for locked in (True, False):
        api = FakeAPI()
        api.shell = {"active": False, "attached": False, "status": "off",
                     **({"locked": True} if locked else {})}
        ctx, pg = _open(browser, site, api)
        try:
            pg.wait_for_timeout(600)              # one poll of /api/library
            assert pg.is_hidden("#cmdBtn") is locked, locked
            if locked:                            # pickShell refuses even if called
                pg.evaluate("document.getElementById('cmdBtn').click()")
                src = pg.evaluate("(document.querySelector('#wrap iframe') || {}).src || ''")
                assert "arg=shell" not in src
        finally:
            ctx.close()
