#!/usr/bin/env python3
"""A guest's rules in the live terminal too (owner, 17.11.2026): the sandbox becomes Claude-only.

The turnstile adds the owner's rules to every request the sandbox makes of Claude; that holds
whatever happens inside. What the rules could not reach were the ways to run something WITHOUT
Claude. Found by experiment in a throwaway container (claude 2.1.282; the design note
docs/superpowers/specs/2026-11-17-guest-rules-live-terminal.md):

  * `!command` in Claude's input runs it directly: no UserPromptSubmit, no PreToolUse, no hook.
    But it — like every Bash tool call, hook, status line and stdio MCP server — is started
    through CLAUDE_CODE_SHELL_PREFIX. This file is that prefix (`prefix`): a command runs only
    when it is a hook from the settings files or when the model asked for it a moment ago (the
    PreToolUse hook `ticket` leaves a one-time ticket with its text). `!` has no ticket.
  * tmux's prefix key (Ctrl+B c: a new window with a shell; Ctrl+B : run-shell): taken away.
  * Ctrl+G / /memory open $EDITOR: EDITOR and VISUAL point at a refusal.
  * plugins (hooks and MCP servers of their own): no marketplace may be added.
  * the dashboard's plain command line (cmd-shell): refused by the core while the flag is up.

Everything here runs as root, as the agent itself does, so the agent could undo it — but only
because someone asked it to, and that request reaches it with the owner's rules attached.
What this closes is the guest's way around the agent, not the agent.

    rules_gate.py install on|off   (as root in the sandbox; guestctl pipes this file in)
    rules_gate.py prefix '<command line>'      CLAUDE_CODE_SHELL_PREFIX, via shell-gate
    rules_gate.py ticket                       PreToolUse hook (any tool), JSON on stdin

Standard library only: it runs on any sandbox image.
"""
import glob
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

RULES_DIR = "etc/agentkennel/rules"           # under the sandbox's / (root-owned)
MANAGED = "etc/claude-code/managed-settings.json"
TICKETS = "run/agentkennel-rules"
SHELL_GATE = "/etc/agentkennel/rules/shell-gate"
NO_EDITOR = "/etc/agentkennel/rules/no-editor"
TICKET_HOOK = "python3 /etc/agentkennel/rules/gate.py ticket"
TICKET_TTL = 600            # seconds a model's command may wait between PreToolUse and its run
REFUSED = 126
REFUSAL = ("The server's owner has limited this sandbox to its work: commands are run only by "
           "Claude. Write to Claude what you need instead (no `!`).")

# Ctrl+B is tmux's way to a shell (c: new window, ": run-shell"); with no prefix key the
# prefix table can't be reached. The right-click menus are already unbound by tmux.conf.
TMUX_LOCK = ("# a guest's rules are on (guests/rules_gate.py): no tmux commands from the keyboard\n"
             "set -g prefix None\n"
             "set -g prefix2 None\n")

SHELL_GATE_SH = ("#!/bin/sh\n# CLAUDE_CODE_SHELL_PREFIX (guests/rules_gate.py)\n"
                 'exec python3 /etc/agentkennel/rules/gate.py prefix "$1"\n')
NO_EDITOR_SH = ("#!/bin/sh\n# EDITOR/VISUAL while a guest's rules are on (guests/rules_gate.py)\n"
                "echo 'No editor here: ask Claude to change the file.' >&2\nexit 1\n")


def root_dir() -> Path:
    return Path(os.environ.get("AGENTKENNEL_RULES_ROOT") or "/")


def tickets_dir(root: Path) -> Path:
    return root / TICKETS


def is_on(root: Path) -> bool:
    return (root / RULES_DIR / "on").exists()


def managed_settings() -> dict:
    return {
        "env": {"CLAUDE_CODE_SHELL_PREFIX": SHELL_GATE, "EDITOR": NO_EDITOR, "VISUAL": NO_EDITOR},
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": TICKET_HOOK}]}]},
        "strictKnownMarketplaces": [],
        "disableCommandPluginSources": True,
        # the agent runs with --dangerously-skip-permissions; its one-time warning defaults to
        # "No, exit", so a guest's Enter would close the terminal
        "skipDangerousModePermissionPrompt": True,
    }


def sh_quote(s: str) -> str:
    """POSIX single quotes, the way claude quotes a command into its `eval '…'`."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _forms(cmd: str) -> list:
    """The command as claude puts it into its wrapper: `eval '<cmd>' ` — the whole command,
    quoted, so a longer command typed with `!` never matches a shorter pending one."""
    return ["eval " + q + " " for q in (sh_quote(cmd), "'" + cmd.replace("'", "'\\''") + "'")]


def hook_commands(paths) -> set:
    """Every hook and status line command in these settings files (missing or broken: none)."""
    out = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "command" and isinstance(node.get("command"), str):
                out.add(node["command"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for p in paths:
        try:
            data = json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            walk(data.get("hooks"))
            walk(data.get("statusLine"))
    return out


def settings_files(root: Path, cwd: str) -> list:
    home = Path(os.environ.get("HOME") or "/root")
    proj = Path(cwd)
    return [root / MANAGED, home / ".claude" / "settings.json",
            proj / ".claude" / "settings.json", proj / ".claude" / "settings.local.json"]


def write_ticket(folder: Path, hook_input, now=None) -> None:
    """PreToolUse: the model is about to run this command — let it through once."""
    try:
        cmd = hook_input["tool_input"]["command"]
    except (KeyError, TypeError):
        return
    if not isinstance(cmd, str) or not cmd.strip():
        return
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    t = time.time() if now is None else now
    name = f"{int(t)}-{secrets.token_hex(8)}"
    tmp = folder / (name + ".tmp")
    tmp.write_text(json.dumps({"command": cmd, "t": t}))
    os.replace(tmp, folder / name)


def decide(cmdline: str, hooks: set, folder: Path, now=None) -> bool:
    if cmdline in hooks:
        return True
    t = time.time() if now is None else now
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return False
    for name in names:
        if name.endswith(".tmp"):
            continue
        path = folder / name
        try:
            d = json.loads(path.read_text())
            cmd, made = d["command"], float(d["t"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if t - made > TICKET_TTL:
            _remove(path)
            continue
        if isinstance(cmd, str) and cmd.strip() and any(f in cmdline + " " for f in _forms(cmd)):
            if _remove(path):             # spent: two runs can't share one ticket
                return True
    return False


def _remove(path: Path) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def _write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _ours(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and (data.get("env") or {}).get(
        "CLAUDE_CODE_SHELL_PREFIX") == SHELL_GATE


def _tmux_sockets(root: Path) -> list:
    return sorted(glob.glob(str(root / "tmp" / "tmux-*" / "*")))


def install(root: Path, on: bool, source: str, run=subprocess.run) -> None:
    rules = root / RULES_DIR
    managed = root / MANAGED
    if not on and not rules.exists():      # never locked: nothing to lift, nothing written
        return
    if on and managed.exists() and not _ours(managed):
        raise SystemExit(f"{managed} is someone else's: not overwritten")
    # the gate stays in place when off: a claude started while on still calls it
    _write(rules / "gate.py", source, 0o755)
    _write(rules / "shell-gate", SHELL_GATE_SH, 0o755)
    _write(rules / "no-editor", NO_EDITOR_SH, 0o755)
    _write(rules / "tmux.conf", TMUX_LOCK, 0o644)
    for d in (root / "etc/agentkennel", rules):
        os.chmod(d, 0o755)
    kw = dict(capture_output=True, text=True, timeout=10)
    if on:
        _write(managed, json.dumps(managed_settings(), indent=2) + "\n", 0o644)
        _write(rules / "on", "on\n", 0o644)
        for sock in _tmux_sockets(root):
            run(["tmux", "-S", sock, "source-file", str(rules / "tmux.conf")], **kw)
            run(["tmux", "-S", sock, "kill-session", "-t", "=cmd-shell"], **kw)
    else:
        _remove(rules / "on")
        if _ours(managed):
            _remove(managed)
        for sock in _tmux_sockets(root):
            run(["tmux", "-S", sock, "set", "-g", "prefix", "C-b"], **kw)


# guestctl runs this file in the sandbox from stdin (any image has python3, none needs a copy):
#   docker exec -i sbx-<name> python3 -c LOADER install on   < guests/rules_gate.py
LOADER = ("import sys; s = sys.stdin.read(); g = {'__name__': 'rules_gate'}; exec(s, g); "
          "sys.exit(g['main'](sys.argv[1:], source=s))")


def main(argv=None, source=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = root_dir()
    if argv[:1] == ["prefix"] and len(argv) == 2:
        cmdline = argv[1]
        if is_on(root) and not decide(cmdline, hook_commands(settings_files(root, os.getcwd())),
                                      tickets_dir(root)):
            digest = hashlib.sha256(cmdline.encode()).hexdigest()[:12]
            print(REFUSAL, file=sys.stderr)
            try:
                tickets_dir(root).mkdir(mode=0o700, parents=True, exist_ok=True)
                with open(root / TICKETS / "refused.log", "a") as f:
                    f.write(f"{int(time.time())} {digest} {cmdline[-300:]!r}\n")
            except OSError:
                pass
            return REFUSED
        os.execvp("bash", ["bash", "-c", cmdline])
    if argv == ["ticket"]:
        try:
            write_ticket(tickets_dir(root), json.loads(sys.stdin.read() or "{}"))
        except Exception:       # a hook that fails must never stop the agent's own work
            pass
        return 0
    if argv[:1] == ["install"] and argv[1:] in (["on"], ["off"]):
        install(root, argv[1] == "on",
                source=source if source is not None else Path(__file__).read_text())
        print(f"rules lock: {argv[1]}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
