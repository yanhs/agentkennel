# Changelog

All notable changes to AgentKennel (formerly AgentDeck). Newest first.

## Unreleased

### Telegram
- **A second bot for notifications.** On the Telegram page (**⋯ → Telegram**), an optional **Notifications bot**: warnings about guests (spending, a strange or illegal request, a site published) go there instead of the chat with your agents. The bot writes to you once when you save it, and is saved only if that worked (a new bot can't write to you before you press Start in it). An empty field sends them to the main bot again. "Let them in?" stays in the main bot. Works when the bridge is a systemd service too; `GUESTS_NOTIFY_CMD` still wins.

## v2.4.0 — guests: web on/off and the agent's rules on the Server page

### Guests
- **Web on/off and the agent's rules, on the Server page.** Each guest has a **Web** switch: off takes their terminals in the browser down (the bot and the agent stay), on brings them back. With web off, **Rules** keeps the guest's agent to one job: the work in your words, the folders it may change, what it may do (edit, rebuild and restart, install packages, publish new sites, set up other services). The turnstile puts these rules into every Claude request of that guest, so nothing inside the sandbox can leave them out; the agent politely declines anything else. Publishing a new site is refused by the site gate itself when not allowed. "Tell me when they ask for something strange or illegal" sends you a Telegram message (at most 5 an hour). The folders rule is the agent's, not the file system's: a read-only /work would break a site's own publish script. Design: docs/superpowers/specs/2026-11-12-guest-rules-design.md. CLI: `guestctl rules <name> '<json>'`, `guestctl folders <name>`.
- **A person let in always gets the address and password.** The admin's bot runs for weeks; the guest texts it had loaded at its start could be older than the guest bot code it loads at the first "yes", and the welcome failed on a missing line (12.11.2026: the sandbox was made, nothing was sent, the admin was told nothing). The welcome now always uses the texts as they are on disk, and if it still can't be sent, the admin's message says so and that /start in the guest bot gives the address.
- **A guest without web access.** `guestctl web-off <name>` takes the guest's terminals in the browser (sb-<name>) down and keeps them down: the bot and the agent go on working, the sandbox and its sites keep running. The bot's menu loses /web and /newpassword (a menu already shown is updated within half a minute, no sandbox restart), /start greets without an address or password, and /web typed by hand gets no answer about an address. `guestctl start` doesn't bring web back; `guestctl web <name>` does.

## v2.3.0 — a new install opens with a terminal; "Open in tab" copies and pastes; guests always get a working password

### Changed
- **Guests: /web never sends a dead password; /newpassword sets a new one.** /web in the guest bot checks the password it hands out against the sandbox's own: while it is the one in force it is sent as before; when the guest set their own, /web says so instead of sending one that no longer works (only a hash is kept, so it can't be read back). /newpassword, new in the bot's menu, puts a new password in and sends it. /web itself never changes anything.
- **A new install opens with something on screen.** On the first visit (no terminal registry yet) the dashboard makes one terminal, "Getting started", and shows it; the Tasks and Server rows are already in the list, one click away. An existing install never gets it, even after deleting every terminal. API: `GET /api/library` carries `first_run`; `POST /api/library/welcome` makes that terminal once (`{"id": null}` when the registry already exists).
- **Tasks, Server and cmd rows have icons.** In the left list, the pinned Tasks, Server and Command line rows now show a small icon before the name (a checklist, the server gauge, a prompt), so they stand apart from ordinary terminals.

### Fixed
- **"Open in tab" opens a terminal that can copy and paste.** The button used to open the bare terminal page, which has none of the dashboard's copy, paste, 📎 or image-paste helpers. It now opens the dashboard itself with `?solo=<id>`: no left column, the terminal fills the tab, and every helper works. Reloading the tab reopens the same terminal, and the tab title is the topic's name. The task board still opens as itself.

## v2.2.9 — `install.sh --reset-password` works on a real install; one header per site

### Fixed
- **`~/agentkennel/install.sh --reset-password` stopped with "DIR: unbound variable"** (v2.2.8). The reset ran before the installer had worked out its folder; it now finds it itself (its own clone, the install record or `AGENTKENNEL_DIR`). The Docker reset was not affected.

### Agents' rules
- **A site has one header, one menu and one footer.** The "Websites: Next.js" block adds: one component on every page, the landing included; a test checks they're the same on all pages, and the agent checks the site by going from page to page. Upgrading replaces the old block if it is still word for word what AgentKennel wrote.

## v2.2.8 — forgot the password? one command; Docker hands over the link right away

### Sign-in
- **Forgot the password: one command, a new one-time link.** `~/agentkennel/install.sh --reset-password`, or in Docker `docker compose exec agentkennel python3 status_server.py --reset-password`. The old password and every signed-in browser stop working at once, no restart. The sign-in page says so under "Forgot your password?".

### Docker
- **The setup link right after the start.** `docker compose up -d --wait && docker compose logs agentkennel | grep setup`: `--wait` returns once the dashboard answers (the image has a health check now), and the log line is the whole link, the server's address in front (`AGENTKENNEL_PUBLIC_PORT` from docker-compose.yml; the address is asked from api.ipify.org once, on a fresh install only).

## v2.2.7 — guests' week resets with your subscription's

### Guests
- **The guests' week now resets when your subscription's week does**, not on Monday midnight. The turnstile reads the moment from Anthropic's replies (the `anthropic-ratelimit-unified-7d-reset` header, on every guest request) and keeps it; until it has seen one, the week still runs from Monday. A guest who used up the week is told the real day and hour it comes back.

### Server page
- **Your subscription's limits show even with no 5-hour window open.** Anthropic then sends only the week's numbers, and the dashboard showed nothing at all; now the week shows and the 5 hours read 0.

## v2.2.6 — links: nothing glued to them, and they go where they're needed

### Agents' rules
- **The links rule said the wrong thing.** Since v2.0.3 the agents' CLAUDE.md said "a link goes last: the last line of the message". What it should say, and now does: nothing touches a link (a space or a line break right after it, no brackets, no `[text](url)`), and the link goes where it is needed, in the sentence or the step it belongs to. Upgrading replaces the old block in the CLAUDE.md AgentKennel wrote, if it is still word for word what AgentKennel put there; a block you edited stays yours.

## v2.2.5 — new license (AGPL-3.0); the product's name shown in the dashboard

### License
- **AgentKennel is now `AGPL-3.0-only`** (was MIT). Using it, changing it and running it on your own servers, for yourself or your company, stays free. What the AGPL adds: if you run a changed copy as a service for other people, you offer them its source. Releases up to v2.2.4 were MIT and stay MIT. [CONTRIBUTING.md](CONTRIBUTING.md) says how contributions are licensed.

### Dashboard
- **The name is in the dashboard, not only in the tab.** The paw and "AgentKennel" head the sidebar; on a phone they sit at the left end of the bar with the ⋯ menu (the paw alone while a terminal is open, so its name keeps the room). The empty screen, before you pick a terminal, shows both. On a phone that screen now says the list is above, not on the left.

## v2.2.4 — a guest's dot says whether agents are loaded

### Server page
- **Guests: the dot shows the agents, not just the sandbox.** Green used to mean only "the sandbox's container runs", even with no agent in it. Now: amber while an agent in it works, green while agents are loaded and waiting, grey when the sandbox is on with none loaded (idle ones are unloaded to save memory), a hollow ring when it is off. The row says how many ("2 agents · 1 working"), the summary counts them all. The count comes from the server page's own process sampling: no extra calls into the sandboxes.

## v2.2.3 — behind your own proxy, "+ New" works

### Fixed
- **Behind a proxy of your own (nginx, Traefik, a load balancer) with HTTPS, a new terminal couldn't be created (403).** The proxy says the browser came over https; Caddy didn't believe it and the dashboard took the browser's address for a foreign one. Caddy now believes a proxy on the same machine or its private network (this includes Docker's port forwarding). `AGENTKENNEL_ORIGIN` is no longer needed in that case; it still works.

## v2.2.2 — HTTPS in Docker by default, websites on Next.js

### Docker
- **HTTPS by default.** `docker compose up -d` serves `https://<your-ip>:8765` with a certificate the container makes for itself: the connection is encrypted, the browser warns once. Before, it was plain http and the password travelled in clear. `./https.sh` still gets a trusted certificate (a free `<ip>.sslip.io` name or your domain), and `./https.sh --off` now goes back to this default. Plain http behind a proxy of your own: `AGENTKENNEL_SITE=:8765`.

### Agents' rules
- **Websites: Next.js.** The default CLAUDE.md gets one more block (once, and only in the file AgentKennel wrote): a new website or web app is built with Next.js on TypeScript with Tailwind CSS, pages people should find are rendered on the server, an existing site keeps its stack. Keep only its mark line, `<!-- agentkennel-web -->`, to opt out.

### Upgrading
- Docker behind your own proxy that talks plain http to the container: set `AGENTKENNEL_SITE=:8765`, or point the proxy at `https://` (certificate check off).

## v2.2.1 — sites on the Server page, a ready image, both installs tested

### Server page
- **Services kept by the services keeper are Sites & apps.** A site or app you run as `<work folder>/.services/<name>.sh` now shows under Sites & apps by its own name, with its whole process tree, instead of System › Other; a stopped one shows as stopped.

### Install
- **A ready Docker image with every release:** `ghcr.io/yanhs/agentkennel:<version>` and `:latest`, amd64 and arm64. `docker compose up -d` downloads it instead of building; `docker compose up -d --build` still builds your checkout; your volumes stay the same either way.
- **The Docker way is tested end to end, like `install.sh`:** `docker compose up -d`, the setup link from the logs, the first login, a new terminal running Claude, a restart that keeps the password. GitHub runs it on every push, and after a release on the image it has just published.
- The release page carries `install.sh` and `docker-compose.yml`.

### Sandboxes for other people (`--guests`)
- `guestctl move-domain --from OLD` moves every guest to a new `GUESTS_DOMAIN`: the old addresses redirect, no sandbox is restarted.
- `guestctl address <guest> <name>` gives a guest a readable address (`sb-<name>.<domain>`) instead of a row of digits; the old one redirects.
- `guestctl recreate` works for a guest with its own disk.

## v2.2.0 — Claude's usage next to the terminals

### Dashboard
- **How much of your Claude allowance is used, at a glance.** To the right of the cmd button, two small lines: `5h 7%` and `wk 9%` — the share of the 5-hour window and of the week already used, the same numbers Claude Code's `/usage` shows. Amber from 70 %, red from 90 %. Hover for when each one resets and which terminals spent the most today ("Today by terminal: …"; work outside the terminals — a Telegram bot, headless runs — counts as one row, "Other conversations"). The numbers come from the rate-limit headers of a one-token request to Anthropic, made at most once every 10 minutes with your `CLAUDE_CODE_OAUTH_TOKEN` (`~/.claude/oauth.env`); without a token the chip stays hidden. Not shown on phones.
- **＋ New** instead of "＋ New terminal" (the title still says what it does); on a phone the search field gets the room.

### A container with limits shows itself
- When the cgroup caps memory or CPU below the host's (`docker run --memory 4g --cpus 2`), the Server page and the CPU/RAM line above the terminals show the container: its own memory, cores and CPU use, "· sandbox" after the name, and no host load averages or uptime. `AGENTKENNEL_DISK_PATH` names the disk the Server page shows. Without limits nothing changes.

### Upgrading
- Your own nginx in front (not Caddy): add `location /api/quota` from `nginx/agents-subdomain.conf` — a prefix, it also serves `/api/quota/terminals`. Docker and `install.sh` (Caddy) need nothing.

## v2.1.1 — your own names on the Server page

### Changed
- **The Server page names your services from your own file.** The built-in names now cover AgentKennel's own units (`agentkennel-status`, `-tasks`, `-sessions`, `-caddy`, `-services`, `-reaper`, the Telegram bridge) and common ones (nginx, PostgreSQL, MariaDB, PHP-FPM, ttyd, Postfix, Docker, containerd, Tailscale). To name your own services and containers, write `~/.config/agentkennel/server-names.json` (or point `AGENTKENNEL_SERVER_NAMES` at a file): its entries are merged over the built-in ones; a broken file is ignored with one line in the log. The format is in [docs/configuration.md](docs/configuration.md).
- **Everything AgentKennel prints is in English**, including the slot-migration tool (`migrate_library.py`); a migrated slot without a label is named `terminal N`.
- Tests and examples use made-up data.

## v2.1.0 — internal names follow the new name

### What changed
The brand changed in v2.0.5; now the technical names follow it.
- **systemd units** `agentdeck-*` → `agentkennel-*` (`agentkennel-status`, `-tasks`, `-sessions`, `-caddy`, `-services`, the `agentkennel-reaper` timer); the guests' units `agentdeck-guest-*` → `agentkennel-guest-*`.
- **Variables** `AGENTDECK_*` → `AGENTKENNEL_*` (all of them: `AGENTKENNEL_WORKDIR`, `AGENTKENNEL_PASSFILE`, `AGENTKENNEL_ORIGIN`, `AGENTKENNEL_SESSION`, …).
- **A new install** goes to `~/agentkennel` and records itself in `~/.config/agentkennel/install.env`; a folder it cloned itself carries `.agentkennel-managed`.
- **Docker:** the compose service is `agentkennel`, the volumes `agentkennel-*`, and the project has a fixed name (`name: agentkennel`), so the volumes are `agentkennel_agentkennel-*` whatever the folder is called. Images: `agentkennel:<version>`, `agentkennel-guest:<version>`.
- **Marks** in the `~/.claude/CLAUDE.md` AgentKennel writes: `<!-- agentkennel-environment -->`, `-safety`, `-tdd`, `-links`, `-services`.
- Smaller ones: `~/.claude/agentkennel-public-ips` (the secrets guard's list of this server's IPs), `~/.cache/agentkennel/`, the tmux option `@agentkennel-conf`, the browser's `agentkennel-theme` and `agentkennel-copy-hint`, the board's `agentkennel:open-terminal` message to the dashboard, the self-signed certificate's `CN=agentkennel`, the placeholder `/path/to/agentkennel` in `hooks/settings.example.json`.
- Kept as they were: the login cookie (nobody is logged out), the Python file names, and the files inside `.sessions/`.

### Compatibility: the old names still work
- **Every setting is read under both names**: `AGENTKENNEL_<X>` first, then `AGENTDECK_<X>`; an empty value counts as unset. One helper does it in Python (`envcompat.py`), one in `start.sh` and the Docker entrypoint (`envcompat.sh`); `install.sh`, `https.sh`, `open-session.sh`, `bin/pane` and `docker-compose.yml` spell the same rule as `${AGENTKENNEL_X:-${AGENTDECK_X:-…}}`. Systemd drop-ins, `.env` files and `docker run -e` lines with the old names keep working.
- **Where AgentKennel hands a variable on, it sets both names**: each terminal gets `AGENTKENNEL_SESSION` and `AGENTDECK_SESSION`; the units' `Environment=` lines carry both (`WORKDIR`, `PASSFILE`, `AUTH_SECRET`, `SITE` — and an `AGENTDECK_*` line of yours in the install's `.env` still wins over the unit's value, as before); `start.sh` and the Docker entrypoint export both to everything they start; a guest container is created with both names for its password, site, origin and public IPs (an older guest image inside reads the old ones).
- **Old marks count as present.** A `CLAUDE.md` written by an earlier version has `<!-- agentdeck-… -->` marks: no block is added a second time, and a kept old mark line still opts out of its block. The guard hooks are recognised by their path, as before.
- Also accepted: the old install record (`~/.config/agentdeck/install.env`, read when there is no new one and replaced by it) and the old `.agentdeck-managed` marker (`--uninstall --purge` still deletes only a folder the installer cloned itself); `~/.claude/agentdeck-public-ips` (both files are read); a tmux server marked `@agentdeck-conf` (not sourced again); the browser's old `agentdeck-theme` / `agentdeck-copy-hint`; the `agentdeck:open-terminal` message from an open board tab; `guestctl recreate` reads the password of a sandbox made earlier (`AGENTDECK_PASSWORD` in `docker inspect`) and keeps the image the container has; `guestctl doctor` counts the guests' services running under their old names.
- `guard_kill.py` protects `agentkennel-*` and `agentdeck-*` units alike.

### Added
- **The paw in the tab.** The dashboard and the login page have their own icon (the paw from agentkennel.com) in the tab, in bookmarks and on a phone's home screen, instead of the browser's grey globe. The three icon files (`web/favicon.svg`, `favicon.ico`, `apple-touch-icon.png`) are served without a session; both Caddyfiles and `nginx/agents-subdomain.conf` say so. On your own nginx, copy that one `location` line.

### Fixed
- **An `--http` install stays on http after any number of re-runs.** The first re-run recorded the mode as "kept", and the one after it read that as "never asked for" and moved the install to HTTPS on the same port.

### Upgrading
- `install.sh`: run it again (the `curl | bash` line, or `./install.sh` in your folder). It keeps the folder you have (`~/agentdeck` stays `~/agentdeck`: the agents' tmux socket is inside it), stops, disables and removes the `agentdeck-*` units and starts `agentkennel-*` in their place. Terminals keep running through the move — the agents' tmux server is not stopped with a unit (`KillMode=process`); until the next reboot systemd still lists the old unit that started it, as not-found and inactive. Your password, terminals and board stay. `--uninstall` removes both the old and the new units.
- Guests: run `guests/install.sh` again; it moves `agentdeck-guest-*` system units to the new names. Running sandboxes are left alone and keep their image until you recreate them.
- Docker: the volumes have new names, so a `docker compose up` after the pull starts empty. To keep your data, stop the old container and copy its volumes once (replace `agentdeck` with the folder name you cloned into):

  ```bash
  docker compose -p agentdeck down
  for v in agentdeck-claude:agentkennel-claude agentdeck-sessions:agentkennel-sessions \
           agentdeck-work:agentkennel-work caddy-data:caddy-data; do
    docker volume create "agentkennel_${v#*:}" >/dev/null
    docker run --rm -v "agentdeck_${v%%:*}:/from" -v "agentkennel_${v#*:}:/to" alpine cp -a /from/. /to/
  done
  docker compose up -d --build
  ```
  With `./https.sh`, run it again after the pull: the file it writes names the service.

## v2.0.5 — AgentDeck is now AgentKennel

### The name
- **AgentDeck is now AgentKennel** — a kennel is where your agents are kept and fed: on your own server. The repository moves to [github.com/yanhs/agentkennel](https://github.com/yanhs/agentkennel) (GitHub redirects the old address, so existing clones keep pulling) and the site to agentkennel.com.
- **Nothing to migrate.** Internal names keep the earlier name, so existing installs upgrade in place: the `agentdeck-*` systemd units, the install folder `~/agentdeck`, the `AGENTDECK_*` variables, the compose service `agentdeck` and its volumes, and the `<!-- agentdeck-… -->` marks in `CLAUDE.md`. Docker: keep running `git pull` in the folder you already have — Compose prefixes volume names with the folder's name, so a fresh clone into a new `agentkennel` folder would start with empty volumes.

### Dashboard
- **📎 File: give an agent any file.** The top-bar button (was 📎 Image) opens the file picker — several files at once — and you can also drop files onto the terminal. Each file uploads to a private folder on the server (`.sessions/uploads/<date>/`, folder 0700, files 0600, 100 MB per file; `AGENTDECK_UPLOAD_DIR`, `AGENTDECK_UPLOAD_MAX_MB`), keeps its name (made safe to type: `Résumé final.pdf` → `Résumé_final.pdf`), and its path is typed into the open terminal and copied to the clipboard. A path, not a link: the agent reads the file where it is, and nothing is published on the web. Screenshots still paste with Ctrl+V / ⌘V.

### Upgrading
- Your own nginx in front (not Caddy): add `location = /api/upload` from `nginx/agents-subdomain.conf` (`client_max_body_size 101m; proxy_request_buffering off`), and `client_max_body_size 0;` inside `location = /__auth` — the login probe runs for every request with its body size, and nginx's default 1 MB there refused any larger upload or pasted screenshot with a 500. Docker and `install.sh` (Caddy) need nothing.

## v2.0.4 — Only the setup link sets the password

### Security
- **A fresh install can't be taken by whoever finds it first.** With no password yet, the login page used to offer "Set a password" to anyone, and the first visitor owned the dashboard — a shell with your sudo. A new `sslip.io` name shows up in public certificate logs within minutes, and bots read them. Now the password can be set only through a one-time link, `/login?setup=<token>`: `install.sh` prints it at the end (run it again to see it), the Docker container writes it to its log (`docker compose logs agentdeck | grep setup`), and `python3 status_server.py --setup-link` shows it on the server. Without it the page says where to find it, and a POST without the token is refused. The token is kept next to the password file (`.sessions/.dashpass.setup`, readable only by you) and deleted once the password is set. Installs that already have a password are not affected; `AGENTDECK_PASSWORD` still pre-seeds it.

### Upgrading
- `install.sh`: run it again (or `git pull` and `./install.sh`). Docker: `git pull && docker compose up -d --build`.

## v2.0.3 — Links go last

### Agents
- **A link is the last line of the reply.** AgentDeck's `~/.claude/CLAUDE.md` gets a short "Links go last" block (marked `<!-- agentdeck-links -->`; added once to a file AgentDeck wrote earlier, a CLAUDE.md of your own is never touched): a link you should open goes alone on the last line, with nothing after it. Telegram and terminals glue the next character to an address — `(https://example.com/x).` stops opening at the bracket and the period. To drop it, delete the block and keep its mark line.

### Upgrading
- `install.sh`: run it again (or `git pull` and `./install.sh`). Docker: `git pull && docker compose up -d --build`. The CLAUDE.md is read when Claude starts, so the rule applies to terminals started or restarted after the upgrade.

## v2.0.2 — Test first

### Agents
- **Agents write the test before the code.** AgentDeck's `~/.claude/CLAUDE.md` gets a short "Test first" block (marked `<!-- agentdeck-tdd -->`; added once to a file AgentDeck wrote earlier, a CLAUDE.md of your own is never touched): every code change starts with a test that fails for the right reason, then the code that makes it pass, then the whole test suite, and the result is shown before "done"; a bug fix starts with a test that reproduces the bug; if a change breaks an existing test, the change is fixed, not the test. Docs, config values and one-off commands are exempt. To opt out, delete the block but keep its mark line — it is not added back.

### Upgrading
- `install.sh`: run it again (or `git pull` and `./install.sh`). Docker: `git pull && docker compose up -d --build`. The CLAUDE.md is read when Claude starts, so the rule applies to terminals started (or restarted with ⟳) after the update.

## v2.0.1 — Safety rules, held in code

### Agents
- **What never happens without you.** AgentDeck's `~/.claude/CLAUDE.md` gets a short safety block (marked `<!-- agentdeck-safety -->`; added once to a file AgentDeck wrote earlier, a CLAUDE.md of your own is never touched): secrets and personal data — keys, tokens, passwords, IP addresses, phone numbers, user ids — never go into a commit, a public file or a link (files added by name, the diff read before a push, made-up values in tests); signals only to processes the agent started; ask first before deleting what it did not create, force-pushing or rewriting pushed history, dropping data, or sending anything to people, and a yes covers that one action only; no printing of environment variables or config files with secrets; a job that runs later reports its result, success and failure alike; risky changes on a copy, and "done" means the result was checked.

### Guard hooks
Words get forgotten; three new Claude Code hooks hold the riskiest rules in code. They run before every shell command an agent runs, block with a message saying what they found and what to do instead, take a visible one-time override written into the command, and let the command through if they fail themselves.
- **`guard_kill.py` — signals only to the agent's own processes.** `pkill`/`killall` patterns, `kill $(pgrep …)`, `… | xargs kill`, `fuser -k`, `tmux kill-server`/`kill-session` on the terminals' own tmux, and `systemctl stop|restart|kill` of a unit that holds the terminals are checked by running their read-only half (`pgrep` with the same pattern, the `ps | grep` before the `xargs`) and blocked if they would hit anything outside the agent's own process tree, with a list of what would be hit. A `|` in a `pkill -f` pattern is regex "or": such a pattern once matched the tmux server that hosts every terminal and closed them all. Override: `KILL_OK=1`. Also checks `Monitor` commands.
- **`guard_email.py` — mail to people only with your yes.** `sendmail`, `mail`/`mailx`, `msmtp`, `mutt`, Python's `smtplib`, `git send-email`, and calls to mail services' APIs (Resend, SendGrid, Mailgun, Postmark, Brevo, SparkPost, Amazon SES), also inside `docker exec`, are blocked unless the command carries `SEND_APPROVED=1` — written only after you saw the text, the sender and the recipient and said send; a yes covers that one message. Drafts and reading mail are free.
- **`guard_secrets.py` — no secrets in a commit or a push.** On `git commit` it reads what is staged (and what a `git add` in the same command or `commit -a` would add); on `git push`, the commits the remote doesn't have yet. It blocks on a private key, a token of a known kind (Telegram bot, Anthropic, GitHub, AWS, Slack) or this server's public IP, shows the file and line with the value masked, and says how to take it out. The IP comes from the network interfaces (never a network request), `AGENTDECK_PUBLIC_IPS` or `~/.claude/agentdeck-public-ips`. Override: `SECRETS_OK=1`.
- **No double guards.** `install.sh` and the Docker entrypoint install the three along with the board hooks (`AGENTDECK_GUARDS=0` still skips them all; `--uninstall` / `install_guards.py --remove` take them out). If the agents' work folder already runs a guard of the same file name in its own `.claude/settings.json` or `settings.local.json`, AgentDeck's copy is not added (`--workdir`, by default the parent of the services folder) — two copies would block one command twice.
- What they can't see: a script run from a file, a signal or mail sent from inside a program. They are a seat belt, not a sandbox.

### Docker
- **git is in the image.** Agents in the container can commit their work, and the secrets guard has something to check; before, `git` was missing.

### Upgrading
- `install.sh`: run it again (or `git pull` and `./install.sh`). Docker: `git pull && docker compose up -d --build`. Hooks and the CLAUDE.md are read when Claude starts, so the new guards apply to terminals started (or restarted with ⟳) after the update; no service needs a restart.

## v2.0.0 — Services

### Services — programs that outlive a terminal
- **A tunnel, a site's server or a bot no longer dies with its terminal.** An idle terminal is unloaded, and everything started from it (`&`, `nohup`, tmux) went with it. Now such a program becomes a *service*: its command goes into `<work folder>/.services/<name>.sh`, and the services keeper starts it within seconds, restarts it if it crashes (with a growing pause), keeps its output in `<name>.log`, and stops it together with its child processes when the file is removed. At most 10 services. The keeper starts with AgentDeck — its own unit (`agentdeck-services`) on a server install, the entrypoint in Docker — and is brought back if it dies itself. State and actions: `curl localhost:9876/status`, `POST /stop|start|restart/<name>` (this machine only).
- **/services in Telegram.** The bot lists your services — running for how long, crashed with which exit code, stopped — with buttons to restart, stop or start each one; the list redraws itself after a tap.
- **Services on the Server page.** The dashboard's Server page has a Services block: every service with its state (running for how long, crashed with which exit code, stopped), its log (the last 200 lines, kept fresh while open) and the same Restart / Stop / Start buttons. Behind it, `GET /api/services`, `GET /api/services/log/<name>` and `POST /api/services/<action>/<name>` in the status server — same-origin only, like the terminal library.
- **Agents know about it.** The default `~/.claude/CLAUDE.md` now carries a short services rule, and an existing one gets it appended once (a block marked `<!-- agentdeck-services -->`; keep the mark line to opt out): long-running programs go into services, and the agent tells you in one line when it adds, stops or removes one.

### Agents
- **Agents know where they run.** Asked "if I change the password and open the web, does my other terminal stop?", an agent used to answer "I can't tell how your platform works" — and that it would forget the conversation. AgentDeck's own `~/.claude/CLAUDE.md` now says it plainly (a block marked `<!-- agentdeck-environment -->`, added once to a file AgentDeck wrote earlier; a CLAUDE.md of your own is never touched): the dashboard and Telegram are two windows into the same terminals, nothing stops when you close them or change the password, a restart or reopening resumes the same conversation, and a new terminal is a new conversation — shared facts go into files or memory.

### Telegram
- **The program's own notices stay out of the chat.** Claude Code prints lines meant for the person at the keyboard — a hook's message to the agent ("Ran 1 stop hook", "Stop hook error: Do not stop yet…"), "✔ Update installed · Restart to apply", "Updated to latest…" — and the bot forwarded them with the reply; Telegram's translation even turned the update line into "restart your device". The live view, the final reply and /read now drop them; the agent's answer and its work show as before.

### Docker
- **Dead processes no longer pile up in the container.** The container's first process (Caddy) never cleans up finished processes, so every program that ended after its parent (an SSH connection helper, a crashed terminal's Claude) stayed behind as a "zombie" and took one of the container's process slots — enough of them and nothing new could start, Claude included. `docker-compose.yml` now runs a tiny init first (`init: true`) that cleans them up. Re-create the container to get it: `docker compose up -d`.
- **A terminal whose Claude died reopens.** A dead-but-not-cleaned-up Claude still looked alive, and opening its conversation was refused as "already open in another Claude". It is now treated as gone.

### Upgrading
- `install.sh`: run the install command again (or `git pull` and `./install.sh`) — it adds the services keeper (`agentdeck-services`) and restarts the dashboard; terminals keep running. If you use the Telegram bot, stop and start it once on the Telegram page so it runs the new version. Docker: `git pull && docker compose up -d --build`.

## v1.9.0 — The terminal list stays put; /restart in Telegram

### Dashboard and Telegram
- **The terminal list no longer reorders itself.** It used to put loaded terminals above unloaded ones and, until you dragged something, the most recently used one on top — so opening a terminal, or the idle reaper unloading one, made the list jump. Now the order only changes when you change it: a new terminal goes on top, dragging moves it, and nothing else does. Loaded terminals still show their status dot; unloaded ones are still greyed. The same order is used by `/list` in the Telegram bot. Terminals from before the update that never got a place keep a fixed order too (newest first) until you drag them.

### Telegram
- **A reply is no longer lost to a slow moment.** The bot waited only 5 seconds for Telegram and, if the edit that puts Claude's final answer into the message timed out once, gave up — the answer never showed. It now waits 15 seconds and retries the final answer after a timeout or a dropped connection (an edit that turns out to have landed counts as delivered).
- **/restart** restarts the current terminal, like ⟳ on the page: Claude starts again on the same conversation. If Claude is busy, the bot asks first with a "Restart anyway" button; when a restart isn't possible it says why (not loaded, archived, in the trash, open elsewhere).
- **TG_BRIDGE_API_BASE** points the bridge at a Bot API relay instead of api.telegram.org — for setups where the real bot token must not be on the machine running the bridge (for example, one sandbox per person behind a shared bot).

### Upgrading
- Restart the dashboard's status server and the Telegram bridge after updating (with Docker, rebuilding and restarting the container does it).

## v1.8.0 — Trash and ⟳ restart; select several lines on a phone

### Dashboard
- **🗑 Trash.** Archive is for terminals you need, just not now; the trash is for ones you don't need, kept just in case. 🗑 on every terminal — in the list and in the archive — asks once and moves it to the trash (in red with "!" if the terminal is busy, saying what it would interrupt). **Show trash**, next to Show archived, lists them with **Restore**: a terminal goes back where it was. Nothing in the trash is erased by itself: **Empty trash** asks in red how many conversations will be erased for good, and only then deletes them from disk. The old Delete button on archived terminals is replaced by 🗑, and trashed terminals don't show up in the Telegram bot.
- **⟳ restarts a loaded terminal: Claude starts again in the same conversation.** Use it when Claude hangs, or so new launch settings (like the scroll speed below) take effect. One question: a plain "Restart?" for an idle terminal, a red one with "!" when it is busy (printing, a timer pending, a background task, working). An open tab reattaches by itself.

### Phone
- **Select several lines on a phone: press and hold, then drag.** The lines are copied when you lift your finger. Before, a terminal running Claude's fullscreen view took every touch as a mouse click: a tap selected a word, a double tap a line, and a drag down only scrolled. A tap is now one click, and a swipe still scrolls.
- **Tab key on the phone's key bar,** next to the cursor arrows.
- **The key bar's icons are all one size.** ←, → and ↵ used to look smaller than ↑ and ↓ on a phone. The icons are now drawn as SVG.

### Terminal
- **The mouse wheel scrolls Claude 3 lines per notch, not 1.** Inside tmux, Claude's fullscreen view scrolled one line at a time. AgentDeck now starts Claude with `CLAUDE_CODE_SCROLL_SPEED=3`; your own `/scroll-speed` setting still wins. It takes effect when a terminal next starts — press ⟳.

### Upgrading
- Restart the dashboard's status server and the Telegram bridge after updating (with Docker, rebuilding and restarting the container does it). `POST /api/library/delete` is gone; use `/api/library/trash` and `/api/library/empty-trash`.

## v1.7.3 — Ctrl+V pastes like in any app, dictation apps (Wispr Flow) included

### Terminal
- **Ctrl+V pastes like in any app.** Text and images; dictation apps such as Wispr Flow, which paste with Ctrl+V for you, now get their text into the terminal. Ctrl+Shift+V and ⌘V on a Mac still work.

## v1.7.2 — Ctrl+V pastes text and images (dictation apps like Wispr Flow work), Ctrl+C copies a selection

### Terminals
- **Ctrl+V pastes text and images, so dictation apps like Wispr Flow work; Ctrl+C copies
  a selection.** Ctrl+V in the terminal used to read only an image off the clipboard and
  dropped text, so voice dictation that puts text on the clipboard and presses Ctrl+V
  (Wispr Flow) typed nothing. Now Ctrl+V is the browser's own paste: text goes into the
  terminal exactly once, an image still uploads and its path is typed in, Ctrl+Shift+V and
  ⌘V on a Mac work as before, and text fields on the dashboard keep their normal paste.
  Ctrl+C with a selection (xterm's, or the mouse selection just copied from tmux) copies it
  and sends nothing, like Windows Terminal; with no selection it sends the interrupt (^C)
  as always. A tmux mouse selection counts only for 3 seconds after the drag and only in
  the terminal it was made in (tmux clears the highlight at once, so a later Ctrl+C is
  always the interrupt). Both keys also work on a Russian or other non-Latin keyboard
  layout. On a Mac, Ctrl+C stays the interrupt and Ctrl+V the image hotkey (⌘C/⌘V copy
  and paste).

## v1.7.1 — one number per terminal; a stray pkill can no longer take every terminal down

### Terminals
- **One number per terminal: a terminal and its conversation always share one number.**
  Claude can change the conversation under a running terminal: some of its own restarts
  drop the session id AgentDeck gave it (on 2.1.282 the "Try the new fullscreen renderer?"
  offer answered yes, `/tui fullscreen`, an update or sign-in restart — the bypass-permissions
  "yes" alone does not); `/clear` starts a new conversation; `/resume` switches to
  another. The dashboard used to keep the old number while the task board (and Claude
  itself) used the new one, and a restart opened an empty conversation under the old
  number. Now the terminal takes the new conversation's number: the new `convo_sync.py`
  reads the file Claude keeps per running process (`~/.claude/sessions/<pid>.json`), finds
  each terminal's Claude through the process tree (not by the file's tmux name, which goes
  stale after a rename), moves the registry entry and renames tmux `cs-<old>` to
  `cs-<new>` — an open tab stays attached, the process is not touched, a pending-timer hold
  moves along, and a restart resumes the live conversation. It runs on every list refresh,
  before close / archive / delete / rename, in `library_cli.py ensure` and `active`, in the
  idle reaper, and before the Telegram bridge reads a chat's terminal. The old number: when
  it never held a conversation (a restart right after opening), it disappears and old links
  (`/?open=`, the task board, `/sess/?arg=`, `/use`) still land on the terminal; when it
  has messages (`/clear` after work), it stays in the list as its own unloaded terminal,
  "… (earlier)", and opens on its own. A conversation open in two terminals at once
  (`/resume` of one that another terminal has open) is left as it is and logged — nothing
  is merged or killed. The open page and a Telegram chat follow their terminal to its new
  number, but not back to an earlier conversation picked on purpose. The pending-timer
  hook and `library_cli.py hold -` use the conversation live now, not the number the pane
  was started with. A Claude on a terminal of its own inside a pane (`script -c claude`)
  is not mistaken for the terminal's, and the npm package's `claude.exe` counts as Claude.
  Only the pane AgentDeck started is the terminal: a Claude started in a pane opened later
  in it (split-window / new-window, by hand or by Claude's own Bash tool) does not take the
  terminal over. A conversation whose first prompt is longer than the 4 MB the check reads
  counts as a conversation (it used to vanish from the list on `/clear`). The page and a
  Telegram chat follow several switches in a row (two `/clear`s while the tab or the chat
  was idle) to the terminal, not to the middle conversation, and a Telegram message or pick
  goes to the number `ensure` opened when that number changed on the way.
  Claude's own prompts are left as they are (nothing is pre-accepted for the user).
- **A careless `pkill -f grep` elsewhere on the server can no longer take down every
  terminal:** a tmux server keeps, as its own command line, the command line of the tmux
  call that started it, and that used to be `tmux … new-session … -c <folder> bash -lic
  'for v in $(env | … grep -i CLAUDE) … exec claude --resume …'` — so `pkill -f` with
  `grep`, `claude`, `bash` or `env` in its pattern (even inside a `… | grep` meant as a
  pipe) matched the server and killed all terminals at once. The server also keeps the
  working directory of that call, so a terminal's folder there would make `fuser -k
  <folder>` or `lsof +D <folder> | xargs kill` ("free this folder") do the same. Now,
  when no server runs, AgentDeck starts one first with a line that says nothing — `tmux
  -f /dev/null new-session -d -s hold-<hex> tmux wait-for hold-<hex>`, called from `/`:
  no grep/claude/bash/env, no `agentdeck`, no path of the checkout or of a folder —
  loads `tmux.conf` into it, makes the terminal on it and lets the placeholder go. The
  terminal's own `new-session` (`-c <folder> <repo>/bin/pane <code>`, or `… bin/pane
  shell` for `cmd`) runs on a server that is already up, so its line is not the server's,
  and tmux's new windows in the terminal still open in its folder. Every tmux call that
  makes a session is made from `/`. The new launcher `bin/pane` (a name with no such
  word either) checks the code, enters the folder (one it can't enter: home, tmux's own
  rule), loads the login shell's environment, unsets `CLAUDE*`, sources
  `~/.claude/oauth.env`, exports `AGENTDECK_SESSION` and execs claude with the arguments
  `library_cli.py ensure` prepared for this start (`--resume`/`--session-id`, the
  ultracode/max restore). The start is left in `<checkout>/.sessions/launch/<code>`, read
  once, and found from the launcher's own path — never from the environment, which in a
  pane is the tmux server's (whoever started it first), not the dashboard's: a moved
  registry (`AGENTDECK_LIBRARY`) cannot send it elsewhere. It is used only if nobody else
  could have written it (not a link, not writable by others) and runs only a program
  named `claude` (`claude.exe`: the npm build), so `CLAUDE_BIN` must name a file called
  that, as `install.sh` sets it. A start tmux did not take — refused, or tmux hung for
  15 s (now a message and exit 1, not a traceback) — is removed at once. A
  Telegram-started old numbered slot is made the same way under a neutral name and
  renamed to `claude-terminal-N`. A server that is already running keeps its line and
  working directory until it next starts. (`pkill -f claude` still ends every Claude
  itself — that is what it asks for; the conversations stay resumable.)
- **The old numbered terminals' buttons reach only their own terminal.** tmux reads a
  bare `-t claude-terminal` as a prefix when no session has exactly that name — and slot
  #1's `claude-terminal` is the prefix of every slot. With #1 not running and one other
  slot up, the × (unload) of #1 killed that other terminal, and `/compact`, a model or
  effort change for #1 were typed into it; the status list showed #1 running with the
  other's screen. Every tmux target there is exact now (`=name`, `=name:`).

## v1.7.0 — HTTPS by default, copy & paste like a desktop terminal, ultracode survives restarts

### Install on your own server (`install.sh`)
- **One command; agents get the whole server.**
  `curl -fsSL https://raw.githubusercontent.com/yanhs/agentdeck/master/install.sh | bash` (or
  `./install.sh` from a clone) on Ubuntu 22.04 / 24.04 or Debian 12 (x86_64, aarch64):
  apt basics, ttyd + Caddy binaries, Node.js 22 when needed, Claude Code (the Dockerfile's
  pinned version), the repo in `~/agentdeck`, guard hooks merged into `~/.claude`, and
  systemd services running as you (status server, task board, the sessions ttyd, Caddy for
  the login and HTTPS, an idle-reaper timer) that start at boot. Options: `--https [domain]`,
  `--http`, `--uninstall [--purge]`, `--check`, `--yes`, `--telegram`.
  Refuses root (unless `AGENTDECK_ALLOW_ROOT=1`) and unsupported systems without changing
  anything; re-running upgrades and repairs, and restarts never kill running agents. The
  agents get a tmux server of their own (`TMUX_TMPDIR=~/agentdeck/.sessions/tmux`), so
  `--uninstall` stops only that one and never your own tmux sessions; `--purge` deletes only
  a folder the installer cloned itself (recorded in `~/.config/agentdeck/install.env`, with a
  marker inside) and refuses anything else.
- **HTTPS by default, never a password in clear text.** The one-liner serves
  `https://<your-ip-with-dashes>.sslip.io` with a free Let's Encrypt certificate when ports
  80 and 443 are free (80 redirects; `:8765` isn't served); with 443 taken, the same
  certificate on `:8443` (or the next free port up to 8453); with 80 taken, no public IP, or
  no certificate within ~120 s (a cloud firewall…) — HTTPS with a self-signed certificate on
  `:8765` (the browser warns once). It waits for the certificate itself and says in plain
  words which case you got, why, and how to get a trusted one (`install.sh --https`, with a
  Caddy log excerpt when Let's Encrypt failed). `--https` insists on a trusted certificate
  (it stops if port 80 is busy); `--https your-domain.com` (or
  `AGENTDECK_SITE=your-domain.com`) uses your own domain instead of sslip.io. Plain http only
  with an explicit `--http`. The mode is recorded in `~/.config/agentdeck/install.env` and
  kept on re-runs; an install from the earlier http-by-default installer moves to HTTPS on
  its next run. Test-only switches: `AGENTDECK_TLS_INTERNAL=1`, `AGENTDECK_ACME_CA`,
  `AGENTDECK_CERT_WAIT`.
- **New terminals open in `~/projects`,** not the whole home (Claude asks to trust that
  folder only). The installer creates it; `AGENTDECK_WORKDIR=/path` at install time picks
  another one, recorded and kept on re-runs. The Docker sandbox keeps `/work`.
- **Installer tests.** `tests/test_install_sh.py` (preflight, OS/arch, units, every HTTPS
  case and message, uninstall and purge rules — fast; it runs a copy of install.sh in a tmp
  sandbox where every outside command is a shim and any write outside the sandbox fails the
  test) and `tests/install/run.sh <distro>` (systemd containers: install, login, new
  terminal, reboot, re-run, uninstall); `SCENARIO=` picks the HTTPS case it meets
  (`cert-timeout`, `port80-busy`, `internal`, `internal-alt`, `http`), and `smoke.py` logs in
  and attaches to a terminal over https too (`--insecure`, `--connect`).
  `.github/workflows/install.yml` runs them on every push, plus `./install.sh --yes` on real
  Ubuntu 22.04 / 24.04 VMs.

### Terminals
- **Terminals keep ultracode (and max effort) across a restart:** AgentDeck reads from the
  terminal's transcript the effort it ended at and relaunches with it; other levels already
  persist through Claude Code's own default. The newest evidence wins: an `/effort` or
  `/model` output, the ultracode on/off notice Claude Code records whatever switched it
  (`/config`, Alt+P, Remote Control), and the effort each reply ran at.
  (`--settings '{"ultracode":true}'` or `--effort max` on the `--resume` launch only; the
  transcript is read backwards and the scan stops once the newest evidence settles it; any
  read problem means a plain `--resume`.)
- **Terminals in folders with emoji or very long paths resume their conversation** instead
  of starting a new one: the transcript folder name now follows Claude Code's own rule
  (UTF-16 code units; names over 200 characters are cut and get a hash suffix).
- **Mouse selection copies like a desktop terminal on a fresh install.** AgentDeck now ships
  its own `tmux.conf` (mouse mode, copy into tmux's buffer on mouse release — what the
  dashboard's copy reads — a 50,000-line scrollback, right click left to the browser); it
  used to live only in the author's `~/.tmux.conf`. Every tmux server AgentDeck starts reads
  it (`tmux -f <repo>/tmux.conf`, the Telegram bridge too); one that was already running
  gets it once. Your own `~/.tmux.conf` is sourced at the end, so it still applies on top.
- **"Copied · Ctrl+Shift+V to paste".** After a mouse selection in a terminal actually
  reaches the clipboard, a small chip in the terminal's top-right corner confirms it for
  ~1.5 s (⌘V on a Mac). After 5 copies in a browser it just says "Copied". Terminals only,
  not the Tasks or Server tabs.
- **A terminal waiting on its own timer stays loaded.** `install_guards.py` now also wires
  `hooks/hold_on_timer.py` (PostToolUse on `ScheduleWakeup`/`CronCreate`/`Monitor`), so the
  idle reaper no longer unloads an agent before its timer fires.

### Docker
- **`./https.sh` — HTTPS in one command, no domain needed.** Uses a free `<ip>.sslip.io` name
  (or `./https.sh your-domain.com`), writes `AGENTDECK_SITE` + a ports override into `.env`,
  checks 80/443 are free, waits for the Let's Encrypt certificate; `--off` reverts. The
  placeholder `you@example.com` ACME email is now dropped (Let's Encrypt refuses it).
- **Guard hooks on by default in Docker.** The container start merges
  `hooks/guard_task_board.py` + `hooks/guard_dont_stop.py` into the agents'
  `~/.claude/settings.json` (idempotent; other settings and your own hooks kept) and writes a
  short default `~/.claude/CLAUDE.md` with the board commands if none exists — every task an
  agent takes on goes on the board, and it keeps working while the task is open. Opt out:
  `AGENTDECK_GUARDS=0`. `start.sh` leaves your own `~/.claude` alone unless
  `AGENTDECK_GUARDS=1`. New `hooks/install_guards.py` (install / `--remove` / `--check`).

### README
- **Full control or a sandbox.** "Try it in a minute" is the one-line install (full
  control of the server, best on a dedicated VPS); "Try it in a sandbox (server control
  isn't available in this mode)" is Docker, with its limits spelled out. Quick start has
  both, in that order.
- **Leads with a demo animation** (`docs/demo.gif`, `docs/demo.mp4`), then **Why AgentDeck**
  — including "Sessions survive restarts" (browser close, AgentDeck restart, server reboot;
  ultracode included) and "Copy & paste like a desktop terminal" — a one-minute try, and an
  honest comparison table. Social preview image `docs/social-preview.png`.

### Tests
- The whole test run gets a private tmux server (`TMUX_TMPDIR` set in `tests/conftest.py`),
  so no test can list, kill or type into running terminals, and the run stops at once if a
  test deletes the session registry (`.sessions/library.json`).

## v1.6.0 — archive that frees memory, Telegram menu with the archive

### Dashboard
- **Archive unloads the terminal** right away, unless it is working (printing, a timer, a
  background task); a busy one unloads by itself once it goes quiet. The dashboard says which.
- **Clicking an archived terminal restores and opens it.** The Restore button is gone; Delete
  stays. The task board's terminal links restore an archived terminal the same way.

### Telegram bridge
- **`/archive`**: archived terminals as buttons (newest first); a tap restores and opens it,
  like a click on the dashboard. `/archive <text>` searches the archive.
- **`/use`** buttons list the terminals outside the archive, loaded first; `/list` shows how
  many are archived.
- **After a pick the bot re-reads the session by itself**: a second message "📺 «name» · id"
  with the terminal's screen once Claude is up, or its last reply if it can't load.
- A topic still running in an old numbered terminal is served there (no "already open
  elsewhere" refusal); `/read` on an unloaded terminal shows its last reply from the transcript.
- The menu says "terminal" everywhere, like the dashboard.

### Repo
- The nginx example has no numbered `/terminalN` routes any more (one ttyd on `/sess/`).
- README gallery: fewer task-board shots; new archive and Telegram screenshots.

## v1.5.1 — terminal ⇄ tasks links, fixes from a fresh-user Docker install test

Tested before release: the full suite, and a fresh install from GitHub with Docker following
the README step by step (password, new terminal, cmd, Tasks, Server, theme, Password and
Telegram pages, logout/login, data surviving `docker compose up -d --force-recreate`).

### Links
- **A terminal's code opens its tasks** from the terminal list too (it already did from the
  top bar); the rest of the row still opens the terminal.
- **A task's terminal code opens that terminal** from the task board — inside the dashboard it
  switches to the terminal, on its own it opens `/?open=<code>`.
- **Tasks, Server and Command line** rows take one line, without captions.

### Docker
- **Server tab works in Docker.** Both Caddyfiles now route `/api/server` to the backend
  (it was only in the nginx config, so the tab showed 404). A test derives every backend path
  from `nginx/agents-subdomain.conf` and checks both Caddyfiles route it, so they can't drift.
- **HTTPS certificates persist.** The image sets `XDG_DATA_HOME=/data` and
  `XDG_CONFIG_HOME=/data/config`, so Caddy keeps its Let's Encrypt certificates in the
  `caddy-data` volume (it used to stay empty and certificates were lost on re-create).
- **Agents' work persists.** `/work` is a named volume (`agentdeck-work`); replace it with
  `./my-project:/work` to work on your own files.
- **Pasted images and Telegram files** no longer point at the author's server: defaults are
  `.sessions/paste` and `.sessions/tgfiles` inside the repo (the persisted volume in Docker),
  no public URL (the path is handed back instead), whisper runs with the bridge's own Python,
  and a bot-started agent runs in `$AGENTDECK_WORKDIR` (or `~`). Override with
  `AGENTDECK_PASTE_DIR` / `AGENTDECK_PASTE_URL` and `TG_FILES_DIR` / `TG_FILES_URL` /
  `TG_WHISPER_PY` / `TG_AGENT_CWD`.
- **ttyd for ARM too.** The Dockerfile downloads the ttyd build for the machine's CPU
  (x86_64 or aarch64).
- **No fixed container name** in `docker-compose.yml`, so two installs (`-p`) don't clash.
- **Claude Code version pinned** with a build argument (`CLAUDE_CODE_VERSION`, default
  `2.1.282`); `--build-arg CLAUDE_CODE_VERSION=latest` takes the newest.

### Dashboard
- **Tab title** is "AgentDeck" (it showed the author's domain).
- **After logout** the browser shows the login page instead of a cached, empty dashboard:
  HTML is sent with `Cache-Control: no-cache` (Caddy and nginx).
- **No typed command line when a terminal opens.** The Claude pane starts with its command
  directly, instead of showing the long `for v in … exec claude …` line (twice) first.
- **Login page** says 🛰 AgentDeck and asks only for the password when the dashboard has its
  own password (Docker / `start.sh`). The Username field stays where it's checked (htpasswd).
- **The ⋯ menu** (Telegram, Password, Theme, Logout) is available on the empty dashboard,
  not only while a terminal is open.

### Docs
- README: the Docker quick start needs one free TCP port (8765) open in the firewall — and
  Docker-published ports bypass `ufw`; `AGENTDECK_PORT=9000 docker compose up -d`; the working
  self-signed HTTPS command (`AGENTDECK_SITE=https://:8765`); for a real domain edit `ports:`
  to `80:80` + `443:443` (both free); the first terminal asks for a colour theme, then how to
  log in; voice messages don't work in the Docker image (no faster-whisper / ffmpeg).
- `.env.example`: the dashboard login lines are commented out ("Docker: leave unset") —
  compose loads `.env` automatically, and the old example set a fixed password and domain.

## v1.5.0 — server page, light theme, guard hooks

- **Server page.** A live view of the machine (`web/server.html`, `GET /api/server`, collected
  by `server_status.py` every 5 s for under 1% of one core): CPU, memory, disk and load gauges,
  a 15-minute CPU chart by group, a "who uses the CPU right now" bar, and rows for every agent
  (with the tests and builds it started), site, container, service and background job, each
  with CPU and memory. Command lines are never shown in full; anything that looks like a
  secret is masked. Opens as a closable **Server** tab next to Tasks.
- **Light theme.** A Dark / Light switch in the dashboard's ⋯ menu (remembered in `localStorage` `agentdeck-theme`, applied before first paint); the embedded Tasks and Server pages follow it live. The terminal stays dark.
- **Guard hooks.** `hooks/guard_task_board.py` blocks the first file edit of a session until
  the task is on the board (or the agent runs `NO_BOARD=1 true` for a trivial change).
  `hooks/guard_dont_stop.py` blocks ending a turn while this session's board task is still
  active and no background command, timer, monitor or sub-agent will resume it. Both give way
  if they hit an error of their own. Settings are environment variables. Copy the wiring from
  `hooks/settings.example.json`. Tests: `tests/test_guard_hooks.py`.
- **Terminal id → its tasks.** Clicking the 8-character code in the top bar opens the Tasks tab
  filtered to that terminal (`/tasks/?session=<code>`, shown as a removable chip).
- **Task board inside the dashboard** uses the dashboard's colours and a slightly smaller type.
- **RAM readout** in GB with one decimal (`13.8/23.5G`).
- **Leaner repo.** The numbered-slot launch scripts (`launch-claude*.sh`, `_order_gate.py`) are
  no longer shipped; `migrate_library.py` still upgrades an old install that has them.

## v1.4.0 — named terminals instead of numbered slots

The fixed row of numbered terminals (1–8, later 1–12) is gone. Terminals are now a
**session library**: as many named terminals as you like, each one a Claude conversation,
with only a limited number loaded in memory at a time.

### Session library

- **Named terminals.** Each terminal has a name and an 8-character code. The code is the
  start of the Claude session id, so the dashboard row, the tmux session (`cs-<code>`), the
  logs and the transcript file (`<uuid>.jsonl`) all share it. No slot numbers anywhere.
- **＋ New terminal** starts a new Claude conversation (name optional — a dated default is
  used). **✎** renames it.
- **Archive / Restore / Delete.** Archive hides a terminal from the list; **Show archived**
  brings the archive back so you can restore it. Only an archived terminal can be deleted;
  its transcript is moved to `.sessions/trash/`, never erased.
- **Your own order.** Drag rows to arrange them (on touch screens, use ▲ ▼). Loaded terminals stay at the
  top; the rest follow, greyed.
- **Search** by name or code, archived terminals included. Enter opens the first match.
- **Memory limit.** At most `AGENTDECK_MAX_ACTIVE` terminals (default 12) run at once.
  Opening one more unloads the least recently used terminal that is idle and has no tab
  open. If all of them are busy, it says so and you try again later. Unloading loses nothing: the
  conversation stays on disk and `claude --resume` picks it up on the next click.
- **One web terminal for all.** A single `ttyd` serves every terminal at
  `/sess/?arg=<code>` (`open-session.sh` → `library_cli.py`), instead of one `ttyd` and one
  launch script per slot. It refuses to start a second Claude on a conversation that is
  already running somewhere else.
- **Timers keep a terminal loaded.** `hooks/hold_on_timer.py` (a Claude Code hook) marks a
  terminal as busy while a `ScheduleWakeup`, `CronCreate` or `Monitor` it set is pending, so
  unloading does not kill the timer.
- **Library API** in `status_server.py`: `GET /api/library` and
  `POST /api/library/{new,rename,archive,close,delete,reorder}`.

### Idle reaper

- New `idle_reaper.py` (run from cron once a minute) unloads terminals nobody is using:
  no screen output for **2 hours** (archived terminals: **2 minutes**).
- It never unloads a terminal that has a browser tab open, holds a timer, or runs a
  background task (a background task stops protecting it after 24 h of silence).
- "Idle" is measured by the last screen output, not by a CPU sample.

### Dashboard

- **cmd** — one plain `bash` command line in the browser, shared by every tab; not counted
  toward the memory limit. `exit` closes it; press **cmd** again for a new one.
- **Tasks tab.** The **T** button opens the task board inside the dashboard as a tab,
  pinned above the terminal list; ✕ closes it.
- **Account menu (⋯)** holds Telegram, Password and Logout. **Esc** is the first button above
  the terminal.
- **English-only** dashboard, Telegram bridge replies, and terminal messages.

### Task board

- Redesigned: created/updated dates and times, search across tasks, steps and notes,
  status filters (All / Active / Blocked / Pending / Done), an agent filter, and sorting
  (recently updated, recently created, title). Follows the system light/dark theme.
- Each task shows the agent's session code, recorded by `tracker.py` from the Claude
  session it runs in — the same code the terminal list shows.

### Telegram bridge

- Works with named terminals: `/use <part of a name or code>` (several matches → buttons),
  `/list` (loaded terminals first), `/new <name>`. An unloaded terminal is loaded before
  your message is typed in, and text is typed only when Claude is actually running there.
- `/use N` still reaches a terminal migrated from slot N.
- The **Telegram** setup page sees a bridge that runs as the systemd service
  (`claude-tg-bridge`, or `TG_SYSTEMD_UNIT`) and never starts a second one on the same token.

### Login

- With `AGENTDECK_PASSFILE` set, the dashboard keeps its own password file. If that file is
  still empty but an nginx htpasswd exists, the first correct login copies the password
  into it — nobody has to set it again. **Password** in the account menu changes it.

### Moving from numbered slots

- `migrate_library.py --dry-run` shows what will happen; `--apply` turns each old slot
  with a real conversation into a named terminal (name from `agents.json`), and replaces
  each old `launch-claude-N.sh` with a small script that opens that terminal (the original
  is kept as `.pre-library`). Terminals that are running are **not** touched or restarted.
  Nothing is deleted. `--swap-dashboard` / `--rollback-dashboard` switch the page.

### Docker and `./start.sh`

- Both now run the session library: one `ttyd` on `/sess/`, the library API, the task board
  on `/tasks/`, and the idle reaper once a minute. The old numbered `/terminalN` routes are
  gone from both Caddyfiles.
- `AGENTDECK_ORIGIN` is optional: when unset, a library change is accepted only from the
  dashboard's own address (the request's scheme + Host, as the proxy forwards them).

### nginx

- New routes in `nginx/agents-subdomain.conf`: `/sess/` (the terminal, WebSocket),
  `/api/library`, `/telegram` and `/change-password` (the last two used to fall through to
  the dashboard page).

682 tests.

## v1.3.0 — set your password in the browser

- The first visit sets the dashboard password (entered twice); a **Password** button
  changes it later.
- Dashboard, APIs and terminals all stay behind the login. Works with `docker compose up`
  and `./start.sh`; an existing nginx + htpasswd setup is untouched.

## v1.2.0 — secure by default

- Every install path requires a login: `docker compose up` and `./start.sh` refuse to start
  without `AGENTDECK_PASSWORD`.
- Set `AGENTDECK_SITE` to your domain and Caddy fetches a TLS certificate for it.
- Older releases (v1.0.0–v1.1.1) shipped an unauthenticated dashboard and were removed.
