# Configuration

[← back to the README](../README.md)

AgentKennel needs no config file to start. Everything below is optional.

With `install.sh`, put settings in `~/agentkennel/.env` (the systemd units read it, and your
values win), then run the installer again or `sudo systemctl restart 'agentkennel-*'`. With
Docker, put them in a `.env` next to `docker-compose.yml`.

| What | Where | Notes |
|---|---|---|
| Terminals (names, codes, order, archive) | `.sessions/library.json` | written by the dashboard; `AGENTKENNEL_LIBRARY` moves it |
| A terminal's start, for `bin/pane` | `.sessions/launch/<code>` of the checkout | read once and removed; always next to `bin/pane` (`AGENTKENNEL_LIBRARY` does not move it: a pane's environment is the tmux server's) |
| How many terminals stay loaded | `AGENTKENNEL_MAX_ACTIVE` env | default `12` |
| Working directory | `AGENTKENNEL_WORKDIR` env | where new terminals start; `install.sh`: `~/projects` (set it when installing to change it; re-runs keep it), Docker: `/work`, otherwise the directory above the repo |
| Dashboard port (Docker) | `AGENTKENNEL_PORT` env | default `8765` |
| Your own domain | `--https your-domain.com` or `AGENTKENNEL_SITE` env | its A record must point at the server |
| Dashboard address (for POST checks) | `AGENTKENNEL_ORIGIN` env | default: the request's own scheme + Host (as the proxy forwards them); set e.g. `https://agents.example.com` behind a proxy without `X-Forwarded-Proto`; library changes from any other origin are refused |
| Dashboard password | `.sessions/.dashpass` (`AGENTKENNEL_PASSFILE`) | set once through the one-time setup link; change it with **⋯ → Password**; `AGENTKENNEL_PASSWORD` pre-seeds it |
| Idle reaper timings | `REAPER_IDLE_SECONDS`, `REAPER_ARCHIVED_IDLE_SECONDS`, `REAPER_BG_MAX_SECONDS` | defaults 7200 / 120 / 86400 |
| Guard hooks (task board before edits, no silent mid-task stop, safety guards) | `AGENTKENNEL_GUARDS` env | Docker: on by default, `0` turns them off; `install.sh`: on by default, `0` skips them; `start.sh`: `1` installs them into `~/.claude/settings.json` |
| This server's public IPs, for `guard_secrets.py` | `AGENTKENNEL_PUBLIC_IPS` env or `~/.claude/agentkennel-public-ips` | needed in Docker, where the network interfaces are private |
| Claude Code version | `CLAUDE_CODE_VERSION` | pinned by the installer and the Dockerfile |
| Telegram bridge | **⋯ → Telegram** in the dashboard, or `.env` | see [Telegram](telegram.md) |
| Pasted images (Ctrl+V / ⌘V) | `AGENTKENNEL_PASTE_DIR`, `AGENTKENNEL_PASTE_URL` env | default `.sessions/paste`, no public URL; the terminal gets the file's path either way |
| Uploaded files (📎 File / drop) | `AGENTKENNEL_UPLOAD_DIR`, `AGENTKENNEL_UPLOAD_MAX_MB` env | default `.sessions/uploads/<date>/`, private (0700/0600), 100 MB per file; never served on the web |
| Pretty project names | `PROJECT_MAP` in `status_server.py` | optional, cosmetic |
| Friendly names on the Server page | `~/.config/agentkennel/server-names.json` (`AGENTKENNEL_SERVER_NAMES`) | optional, cosmetic; see below |
| Updates: the release checked, the night-time patch update | `.sessions/update/` (`AGENTKENNEL_UPDATE_DIR`) | the panel checks GitHub's latest release once a day (`AGENTKENNEL_UPDATE_REPO`, default this repo; any other git remote is read with `git ls-remote`); `AGENTKENNEL_UPDATES=0` turns updates off. See **Updates** below |
| Which services an update restarts | `AGENTKENNEL_UPDATE_RESTART` (`user:a.service system:b.service`) or `AGENTKENNEL_UPDATE_RESTART_CMD` | default: the unit the backend runs in (with `install.sh`: `agentkennel-status` and `agentkennel-tasks`); the health check asks `AGENTKENNEL_UPDATE_HEALTH_URL` (default `http://127.0.0.1:3011/api/update`) for up to `AGENTKENNEL_UPDATE_HEALTH_TIMEOUT` s (default 90) |
| Reverse proxy / TLS (manual setup) | `nginx/agents-subdomain.conf` | a template: swap in your domain and paths, see [Manual setup](manual-setup.md) |

**Server page names.** The Server page names AgentKennel's own units and a few common
ones (nginx, PostgreSQL, Docker, …); everything else shows its systemd description or
Docker image. To name your own, write `server-names.json`:
`{"units": {"shop-api.service": ["Shop API", null, "apps"]}, "containers": [["shop-(\\w+)", "Shop web ({1})"]]}`.
A unit is `["Name", "what it does" or null for the unit's own description, "apps" | "system" | "jobs"]`;
a container is `["<regex for the whole name>", "description"]`, where `{1}`, `{2}` are the
regex groups. Your units win over the built-in ones and your container patterns are tried
first. It is read when the backend starts; a broken file is ignored with one line in its log.

**Updates.** When a new release is out, the line above the version at the bottom of the
sidebar says **Update available: vX.Y.Z** (on a phone: in the **⋯** menu); it opens what
changed (the release's CHANGELOG.md since your version) and **Update now**.

- **When it is refused.** While an agent works (it printed in the last 15 s, waits on its own
  timer, runs a background task or uses the CPU) the panel names it and offers **Update when
  all are idle**. A clone with changed tracked files or commits that are not in the release is
  not touched (update it with git yourself), nor a panel whose restart could stop the terminals.
- **What it does** (`python3 -m updater.apply`, in a process of its own): a backup in
  `.sessions/update/backups/<time>-v<version>/` (the code git tracks, the install's `.env` and
  `agents.json`, `~/.claude/settings.json` and `~/.claude/CLAUDE.md`; never `.sessions`), then
  `git fetch` of the release tag and a fast-forward of your branch to it, the new release's own
  setup steps (`python3 -m updater.setup_steps`: the guard hooks, as `install.sh` installs them),
  a restart of the panel's services only (never one that holds the terminals, such as a
  Telegram bridge that started the tmux server), and a check that the panel answers with the
  new version. Anything failing after the backup rolls back: git to the old commit, the
  settings from the backup, the services restarted, the old version checked. The result
  stays in the panel until you read it, and goes to Telegram when the install has a bot.
- **Night-time patch updates** (off by default): "Install patch releases by themselves at
  night" in the dialog installs a patch release (2.7.2 → 2.7.3, never 2.8.0) between 02:00
  and 05:00 server time, only when no agent works; a release that failed once is left to you.
- **Docker:** the dialog shows `docker compose pull && docker compose up -d` instead. A
  guest's sandbox shows nothing: its updates are the owner's.
- **When `install.sh` itself changed** in a release, the result says to run it once more
  (it may need sudo, which the panel never uses).

