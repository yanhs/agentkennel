# A guest's rules in the live terminal too (spike + prototype, 17.11.2026)

Owner: "the filter works in the live terminal too; on send, the restriction instruction is
always added, or the line is refused". Branch `feat/guests-live-filter`, not merged.

## The idea

The turnstile already adds the owner's rules to **every** Claude request of a guest, and the
sandbox reaches Claude only through it — nothing inside can take the rules out. That was
switched off with web access on, because in the browser a guest could act **without** Claude.
So the job is not a second filter, it is closing the ways around the agent:

* **Turnstile:** `rules_active = enabled and rules.on` (web no longer matters). The site gate
  uses the same function, so "publish new sites: not allowed" now holds with web on too.
* **Sandbox:** while the rules are on, the live terminal is Claude-only. `guests/rules_gate.py`
  is piped in by `guestctl` and run as root (`docker exec -i -u 0 … python3 -c LOADER install
  on|off`); it works on any image, nothing has to be rebuilt.

## What was verified by experiment

Throwaway container from `agentkennel-guest:v2.3.0` (claude 2.1.282), `--network none`, a stub
Messages API that logs requests and can answer with a Bash `tool_use`, claude started in tmux
as the sandbox starts it (`--dangerously-skip-permissions`). Removed afterwards.

| Way around Claude | Before | Result |
|---|---|---|
| `!command` in Claude's input (bash mode) | runs at once as root | **refused** |
| the same through the Telegram bridge (`send-keys` of `!…`) | runs | **refused** (same path) |
| tmux Ctrl+B c (new window = shell), Ctrl+B : `run-shell` | shell / any command | **closed** (no prefix key) |
| Ctrl+G (and `/memory`) open `$EDITOR` | ran the editor | **closed** (EDITOR → refusal) |
| `/plugin marketplace add …` (plugins bring hooks, MCP servers) | allowed | **blocked by policy** |
| dashboard "cmd" command line (`/sess/?arg=shell`) | bash | **refused server-side**, button hidden |
| Ctrl+Z | — | no escape: claude is the pane's own process, there is no shell to return to |
| `/hooks` | — | read-only in this version: a guest can't add a hook by hand |

Facts the design rests on (all seen, not read):

1. `!command` fires **no hook** — not UserPromptSubmit, not PreToolUse; only `Stop` /
   `MessageDisplay` after Claude's reply to the output, i.e. after the command ran. No setting
   switches bash mode off (docs agree: `defaultShell`, `respondToBashCommands` only).
2. But `!command` — like every Bash tool call, every hook, status line and stdio MCP server —
   is started through **`CLAUDE_CODE_SHELL_PREFIX`**, as `<prefix> '<whole wrapper>'` with the
   command inside as `eval '<cmd>'`.
3. A model's Bash call is preceded by PreToolUse; a `!` command is not. So: the PreToolUse hook
   leaves a one-time **ticket** with the command, and the prefix (the gate) runs only a hook
   command from the settings files or a command whose ticket matches `eval '<cmd>' ` exactly
   (quotes and background calls checked: `echo 'it'"'"'s'`, `run_in_background`). Anything
   else gets "The server's owner has limited this sandbox to its work: commands are run only
   by Claude" and is logged (hash + tail) in `/run/agentkennel-rules/refused.log`.
4. Managed settings (`/etc/claude-code/managed-settings.json`) and their `env` apply to a
   claude **already running** (lock turned on mid-session: the next `!` was refused), and off
   gives `!` back. tmux's prefix is set on the running servers too.
5. The sandbox's own guard hooks (user settings) keep running through the gate.

## What the prototype does

* `guests/rules_gate.py install on`: writes the gate (`/etc/agentkennel/rules/`), the flag
  `on`, managed settings (shell prefix → gate, PreToolUse ticket hook, EDITOR/VISUAL → refusal,
  `strictKnownMarketplaces: []`, `disableCommandPluginSources`; the bypass-permissions consent
  is left as the sandbox has it — the spike's "No, exit" dialog came from a test that had
  overwritten that consent, not from the lock), sets tmux
  `prefix None` on running servers and closes `cmd-shell`. `off` lifts it; the gate stays and
  passes everything (a claude started earlier still calls it). Someone else's
  managed-settings.json is never overwritten.
* Core: `open-session.sh shell` and `library_cli.py shell-ensure` refuse while the flag exists;
  `GET /api/library` says `shell.locked`, the page hides **cmd**; `tmux.conf` reads the lock
  last (tmux servers started later).
* `guestctl`: `rules` locks/unlocks a running sandbox (stopped: at `start`), `add` and
  `recreate` lock when the rules are on. Server page: Rules work with web on; texts say the
  terminals are then Claude-only.
* No UserPromptSubmit hook: the turnstile already adds the rules to every request where the
  guest can't touch them; a hook inside the sandbox would only repeat them and could be
  removed by the agent. "Refuse the line" is what the gate does to `!`.

## Review round (17.11.2026, after 8f788b1)

Measured, benign (throwaway container from `agentkennel-guest:livefilter-8f788b1`, stub API):

* **Lock turned on while the guest's claude runs** (claude 2.1.282 and 2.1.326): the agent's
  own Bash keeps working (`ls`, a command with quotes) *if `/etc/claude-code` existed when that
  claude started*. If the folder did not exist, that claude never reads the managed settings:
  no prefix, no tickets, `!` still runs, until it is restarted. Fixes: the guest image creates
  the folder (`guests/image/Dockerfile`), `install off` keeps it, and `install on` counts the
  claudes started before the folder existed; `guestctl` tells the owner to restart them
  (restarting a guest's terminal mid-work is the owner's call, not ours).
* **Self-update:** sbx-yan runs claude **2.1.326**; its image ships 2.1.282 — the npm package
  was replaced inside the container about 20 minutes after it started. The lock now sets
  `DISABLE_AUTOUPDATER=1`. Real wrappers of both versions are the unit tests' fixtures
  (`guests/tests/fixtures/claude-prefix-*.json`); the agent's commands pass on both.

Fixed: off removes `tmux.conf` and the flag (and `tmux.conf` reads the lock only while `on`
exists), restores tmux's default prefix and then the guest's own `~/.tmux.conf`; `no-editor`
hands over to a real editor when the rules are off; a missing gate refuses with a clear
message (exit 126, never 2 — a hook exiting 2 would block every tool); a crashing gate says
"the rules gate failed … tell the owner"; a deleted working folder is handled; project hooks
count from `$CLAUDE_PROJECT_DIR` and every folder above the cwd; `refused.log` is capped at
64 KB; failed locks on `add`, `recreate` and `start` reach the owner (and `recreate` saves the
new container first); turning rules off never fails on the lock; the lock's exec (20 s) fits in
the server's wait for `rules` (45 s).

**Telling the owner** (all English):

* At once, through the turnstile's `/flag` and its limit of 5 an hour per guest: a `!` command
  the gate refused (`kind=bypass`, the command taken out of claude's wrapper, secrets scrubbed,
  200 characters), the command line opened by URL (`report shell`), an editor opened from
  Claude (`report editor`), and — by the agent, told so in the rules — attempts to get the rules
  lifted or ignored (`kind=lift`).
* Daily: ordinary off-topic refusals are recorded quietly by the agent (`kind=offtopic`, their
  own cap of 30 an hour, stored in the turnstile's ledger). A thread in the turnstile sends one
  digest a day from 09:00 the owner's time — per guest a count and the three commonest topics —
  and nothing at all when there were none.
* All of it only while the guest's "tell me" box is on (the existing flag setting).
* Not reportable: tmux keys. With no prefix key, tmux receives nothing to act on, so there is
  no event to report.

## What remains (honestly)

1. **The agent itself.** Everything in the sandbox runs as root, the agent too. It *can*
   remove the lock — but only if asked, and every such request reaches it with the owner's
   rules ("politely decline anything outside this work"). The protection against the agent is
   the model following the rules, exactly as with web off today. A determined jailbreak of the
   model is not closed by this.
2. **Hooks the agent writes.** The gate passes hook commands from user/project settings (the
   sandbox's guard hooks live there); exec-form hooks skip the prefix (docs). Both need a file
   written — by the agent, see 1. Plugins, the other source, are blocked.
3. **Old images.** The `tmux.conf` lock line and the `/etc/claude-code` folder are in this
   branch's image only (the folder: claudes open before the first lock need a restart). On an older image
   the lock is applied to the tmux servers running at lock time; a server started later (all
   terminals closed, then a new one) has Ctrl+B again until the sandbox is recreated on the new
   image. The `cmd` button and `/sess/?arg=shell` also need the new image (older: the gate
   still blocks `!`, but the command line opens).
4. **Not tried:** the Monitor tool (a model command not wrapped in `eval '…'` would be refused —
   a lost feature, not a hole), MCP servers the agent adds itself, every slash command one by
   one. A ticket left by a PreToolUse whose call another hook then blocked waits up to 10
   minutes; it only matches that exact command, which the model meant to run anyway.
5. **The dashboard beyond terminals** was read, not attacked: uploads go to a private folder,
   services can only be started/stopped as they are, no route runs a typed command.

Fallback if the prefix trick stops working in a future claude (e.g. `!` no longer through
`CLAUDE_CODE_SHELL_PREFIX`): show the guest our own input box that forwards only messages
(no `!`, no keys) to the tmux pane, and serve the terminal read-only (`ttyd` without `-W`).
The test `test_prefix_refuses_with_a_plain_reason` and the spike steps above are the check to
repeat after a claude upgrade.
