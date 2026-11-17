"""Every text the guests module shows a person — the guest bot, the owner's invitations and
alerts, the turnstiles' refusals, the rules written into a sandbox's CLAUDE.md, the
owner's command-line output — in one table: English, built in (EN below).

Another language is a local file, not part of AgentKennel: GUESTS_LANG=<code> in guests/.env
reads ``<locale dir>/<code>.json``, a JSON object {key: template} with the keys of EN. The
locale dir is GUESTS_LOCALE_DIR, else ``guests/locale/`` (git-ignored). Start a translation
from the English template: ``python3 guests/scripts/export_locale.py en -o guests/locale/<code>.json``.

Read on every call (the file again only when it changed), so a changed .env or language
file applies without a restart. Templates use str.format: a literal brace is written
``{{`` / ``}}``. A key the file lacks, or a template that isn't one or names a {field} the
English one doesn't, is said in English. GUESTS_LANG naming a language without a usable file
falls back to English — said once on stderr (the service's log) and in ``guestctl.py doctor``;
never a crash.
"""

from __future__ import annotations

import json
import re
import string
import sys
from pathlib import Path
from typing import Mapping, Optional, Tuple

import guestenv

DEFAULT = "en"
HERE = Path(__file__).resolve().parent
CODE = re.compile(r"[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?")    # en, xx, pt-br, zh_hans

EN = {
    # -- the guest bot ---------------------------------------------------------------- #
    "stranger": "This is a private bot. The owner gives access.",
    "cmd_web": "browser address and password",
    "cmd_newpassword": "a new browser password",
    "cmd_services": "what runs without a terminal",
    "cmd_claude": "what Claude runs on, or add your own key",
    "cl_now_owner_key": "Claude runs on the owner's API key. This week: ${used:.2f} of "
                        "${limit:.2f}.",
    "cl_now_owner_key_plain": "Claude runs on the owner's API key.",
    "cl_now_owner_sub": "Claude runs on the owner's subscription, with weekly limits.",
    "cl_now_own": "Claude runs on your own key or login (ends in {last4}).",
    "cl_now_own_none": "Claude is set to run on your own key or login, and none is saved yet. "
                       "Add one with the buttons below.",
    "cl_send_key": "Send your Anthropic API key here as a message (it starts with sk-ant-api). "
                   "The bot deletes the message right away and keeps the key only on the "
                   "server, never in the sandbox.",
    "cl_send_token": "In a terminal run `claude setup-token`, sign in, and send the token it "
                     "prints (it starts with sk-ant-oat) here as a message. The bot deletes the "
                     "message right away and keeps the token only on the server, never in the "
                     "sandbox.",
    "cl_withheld": "That message wasn't passed to the agent: it contained something that "
                   "looks like an Anthropic key. To use your own key, send the key by itself "
                   "as a message, or open /claude.",
    "cl_delete_it": "Please delete your message with the key yourself: the bot couldn't. "
                    "It was not passed on to anyone.",
    "btn_cl_own_key": "Own API key",
    "btn_cl_own_sub": "Own subscription",
    "btn_cl_back_key": "Back to the owner's key",
    "btn_cl_back_sub": "Back to the owner's subscription",
    "ago_min": "{m} min",
    "ago_h_min": "{h} h {m} min",
    "ago_h": "{h} h",
    "ago_d": "{d} d",
    "svc_refresh": "🔄 Refresh",
    "svc_unavailable": "The list of services is not available right now: the sandbox is "
                       "restarting or still on an old version. Try again in a minute.",
    "svc_none": "No services yet.\n\nA service is a program that runs without an open "
                "terminal: Tailscale, a site's server, a bot. Ask the agent: "
                "\"make this a service\".",
    "svc_header": "Sandbox services ({running} of {total} running):",
    "svc_restarts": ", restarts: {n}",
    "svc_running": "🟢 {name} — running",
    "svc_crashed": "crashed (exit code {code}), restarting soon",
    "svc_starting": "starting",
    "svc_waiting": "🟠 {name} — {why}{tail}",
    "svc_skipped_limit": "⚪ {name} — not started: at most {limit} services allowed",
    "svc_skipped": "⚪ {name} — not started: too many services",
    "svc_stopped": "⚪ {name} — stopped",
    "svc_logs": "Logs: /work/.services/<name>.log",
    "svc_note_restart": "Restarting {svc}",
    "svc_note_stop": "Stopping {svc}",
    "svc_note_start": "Starting {svc}",
    "svc_note_failed": "Didn't work",
    "svc_note_unknown": "Unknown button",
    "web_not_ready": "Your browser address is not ready yet — ask the owner.",
    "web_intro": "Hello! This is your sandbox with Claude.\n\n"
                 "Write your tasks right here. Commands:\n"
                 "/new — a new terminal, /list — your terminals, /use — switch,\n"
                 "/restart — restart a stuck terminal, /esc — interrupt Claude,\n"
                 "/services — what runs without a terminal (Tailscale, a site, a bot),\n"
                 "/web — the address and password again, /newpassword — a new password.\n",
    "web_intro_noweb": "Hello! This is your sandbox with Claude.\n\n"
                       "Write your tasks right here. Commands:\n"
                       "/new — a new terminal, /list — your terminals, /use — switch,\n"
                       "/restart — restart a stuck terminal, /esc — interrupt Claude,\n"
                       "/services — what runs without a terminal (Tailscale, a site, a bot).\n",
    "web_url": "In the browser: {url}",
    "web_password": "Password: {password}",
    "web_change": "After the first login, change it to your own: {url}/change-password",
    "web_forgot": "Forgot it? /newpassword sets a new one and sends it here.",
    "web_own": "Password: you set your own, and it is the one that works. Forgot it? /newpassword sets a new one and sends it here.",
    "web_new": "(a new one: the previous password no longer works)",
    "web_reset_failed": "Could not set a new password. Try again in a minute; if it still fails, write to the owner.",
    "web_sites": "The agent sends you files as internet links; you can publish a site "
                 "as <site>-<your name>.{domain}.",
    "domain_placeholder": "<domain>",
    # -- invitations (the owner decides in the same bot) ------------------------------ #
    "invite_sent": "Your request has been sent to the owner. As soon as they approve it, "
                   "I'll send the address and password here.",
    "invite_waiting": "The owner has your request already — waiting for their answer.",
    "invite_refused": "The owner did not give access.",
    "invite_failed": "Couldn't create the sandbox — the owner already knows and will sort it out.",
    "who_noname": "no name",
    "invite_request": "🔔 Access request: {who}, Telegram ID {uid}.\n"
                      "Let them in? A sandbox \"{name}\" will be created with the default limits.",
    "invite_not_yours": "This is not your button.",
    "invite_decided": "Already decided or in progress.",
    "invite_rejected": "✖ Refused: {who}.",
    "invite_creating": "⏳ Creating a sandbox for {who}…",
    "invite_created": "✅ {who} — sandbox \"{name}\" created, address and password sent.",
    "invite_created_no_welcome": "✅ {who} — sandbox \"{name}\" created, but the address and "
                                 "password did not go ({err}). They get them by sending /start "
                                 "to the guest bot.",
    "invite_create_failed": "🚨 Couldn't create sandbox \"{name}\" for {who}: {err}\n"
                            "You can press again.",
    "btn_allow": "✅ Let in",
    "btn_deny": "✖ Refuse",
    # -- the Claude turnstile ----------------------------------------------------------- #
    "gate_bad_pass": "Invalid pass.",
    "gate_bad_body": "The request is not valid JSON.",
    "gate_flag_off": "Reporting is not on for this guest.",
    "gate_flag_many": "Too many reports this hour; the owner has the earlier ones.",
    "flag_owner": "⚠️ {name} asked for something strange or illegal: «{text}». The agent declined.",
    "gate_disabled": "Guest access is turned off.",
    "gate_path": "This address is closed.",
    "gate_limit_5h": "5-hour limit used up ({spent} of {limit}). It resets at {at}, Los Angeles time.",
    "gate_limit_week": "Weekly limit used up ({spent} of {limit}). It resets on {day} at {at}, "
                       "Los Angeles time.",
    "weekdays": "Monday Tuesday Wednesday Thursday Friday Saturday Sunday",
    "gate_upstream": "The Claude server is unreachable.",
    "spend_alert": "⚠️ Sandbox {guest}: ≈{hour_pct:.1f}% of the subscription week spent in the "
                   "last hour ({hour_m:.1f}M). Today in total ≈{today_pct:.1f}%.\n"
                   "If this wasn't expected, see what keeps running: the guest's services "
                   "(/services) or `turnstile.py disable {guest}`.",
    # -- whose Claude a guest runs on (claude.mode) -------------------------------------- #
    "gate_no_owner_key": "Claude isn't available right now: the owner has been told.",
    "gate_no_own_key": "This sandbox runs Claude on your own key or login, and none is saved "
                       "yet. Add one: /claude in the bot, or \"Claude\" in the panel.",
    "gate_limit_usd": "Weekly limit used up (${spent:.2f} of ${limit:.2f}). It resets on {day} "
                      "at {at}, Los Angeles time.",
    "owner_key_problem": "⚠️ Claude on your API key, guest {guest}: {error}",
    "ownerkey_missing": "no API key is saved. Guests on your key are refused until you add one "
                        "(Server → Guests → Settings) or switch them to another way of "
                        "running Claude.",
    "ownerkey_refused": "Anthropic refused the key (HTTP {status}: {message}). Guests on your "
                        "key get this error until it's fixed.",
    "ownerkey_prices": "the price table can't be read ({error}), so dollars can't be counted. "
                       "Guests on your key are refused until it's fixed.",
    "owner_guest_own": "🔑 Guest {guest} switched to their own Claude key or login. Their Claude no longer runs on yours.",
    "owner_guest_back": "↩️ Guest {guest} went back from their own key to the way you set for them.",
    "own_not_a_key": "That doesn't look like an Anthropic API key or token (it starts with sk-ant-).",
    "own_refused": "Anthropic didn't accept it: {error}",
    "own_saved": "Saved (ends in {last4}). Claude now runs on your own key or login.",
    "own_failed": "Couldn't save it. Nothing was changed.",
    "own_back": "Back to the owner's setup. Your own key was deleted.",
    "own_back_not_allowed": "You're not on your own key, so there is nothing to go back from.",
    "own_back_key_stays": "Back to the owner's setup, but your saved key couldn't be deleted. "
                          "Tell the owner.",
    "own_settings_broken": "The owner needs to fix this guest's Claude settings first. "
                           "The owner has been told.",
    "gate_claude_many": "Too many tries for now. Wait an hour and try again.",
    "owner_claude_broken": "⚠️ Guest {guest}: their Claude settings in guests.json are not "
                           "valid ({error}). Their requests are refused until they're fixed "
                           "(the Claude button on the guest's row saves valid ones).",
    "owner_login_problem": "⚠️ Guest {guest} runs on your subscription, but your login can't be "
                           "read ({error}). Their requests are refused until it's fixed.",
    "usd_notice": "💵 Guest {guest} has spent ${spent:.2f} of their ${limit:.2f} a week on your "
                  "API key.",
    "usd_spend_alert": "⚠️ Sandbox {guest}: ${hour:.2f} spent on your API key in the last hour, "
                       "more than a quarter of their ${limit:.2f} a week.\n"
                       "If this wasn't expected, see what keeps running: the guest's services "
                       "(/services) or `turnstile.py disable {guest}`.",
    "ts_on": "on",
    "ts_off": "OFF",
    "ts_unlimited_until": " NO LIMIT until {until}",
    "ts_list_line": "{name}\t{state}\t5h {five_used} of {five_limit} · week {week_used} of "
                    "{week_limit} (≈{week_pct:.2f}% of the subscription week){lifted}\ttg={tg}",
    "ts_unlimited_set": "{name}: no limit until {until} (Los Angeles)",
    # -- the site gate ---------------------------------------------------------------- #
    "site_bad_name": "site name: latin a-z, digits and hyphens, 1–20 characters",
    "site_bad_port": "port: a number from 1024 to 65535",
    "site_no_domain": "sites are off: the owner has not set a domain (GUESTS_DOMAIN)",
    "site_too_long": "the name is too long",
    "site_guest_off": "the guest is turned off",
    "site_rules_no_publish": "The server owner's rules don't allow publishing new sites for you.",
    "site_no_ip": "the sandbox has no fixed address — needs guestctl recreate",
    "site_taken": "this address is taken",
    "site_reserved": "site names sb, sbx, sb-… and sbx-… are kept for the dashboards — pick another",
    "site_too_often": "one change a minute — try again in {wait} s",
    "site_too_busy": "too many site changes on this server in the last hour — try again later",
    "site_too_many": "at most {max} sites per guest — take an old one down first",
    "site_failed": "couldn't publish: {err}",
    "site_published": "🌐 Guest {guest} published a site https://{host} "
                      "(port {port} in their sandbox). Take down: sitegate.py remove {host}",
    "site_removed": "🌐 Guest {guest} took down the site https://{host}",
    "site_not_yours": "you have no such site",
    "site_need_pass": "a pass is needed: Authorization: Bearer <pass>",
    "site_routes": "available: GET /sites, POST /publish, DELETE /publish/<site>",
    # -- rules written into the sandbox's CLAUDE.md (for the guest's agent) ------------ #
    "rule_files": "## Files for the user — as an internet link\n"
                  "To give the user a file, put it into `/work/public/` and send the link\n"
                  "`{url}/<file name>` as the last line of the message. Names: latin letters, no spaces.\n"
                  "Everything in `/work/public/` is open to anyone who has the link; never put secrets there.\n",
    "rule_sites": "## A site on the internet: <site>-<guest>.{domain}\n"
                  "Run a server in the sandbox on 0.0.0.0:<port> (1024–65535), then:\n"
                  "`curl -s -X POST http://172.31.250.1:8794/publish -H \"Authorization: Bearer $ANTHROPIC_AUTH_TOKEN\" "
                  "-d '{{\"site\":\"test\",\"port\":3000}}'` — the answer gives the address "
                  "https://test-<guest>.{domain}\n"
                  "(DNS and the certificate take ~1 minute). List: `GET /sites`, take down: `DELETE /publish/<site>`.\n"
                  "At most 3 sites. The server's owner is told about every site.\n",
    "rule_services": "## Services: what must run without a terminal\n"
                     "A terminal is unloaded after 2 hours idle, and everything started from it (`&`, nohup,\n"
                     "tmux) dies with it. A program that must run all the time (Tailscale,\n"
                     "a published site's server, a bot) — make it a service:\n"
                     "1. A file `/work/.services/<name>.sh` (name: lower-case latin, digits, `-`, `_`). Inside:\n"
                     "   a command that runs in the foreground, without `&` and without daemon mode,\n"
                     "   e.g. `exec python3 /work/site/server.py`.\n"
                     "2. The keeper starts it within seconds and restarts it after a crash; output:\n"
                     "   `/work/.services/<name>.log`; state: `curl -s localhost:9876/status`.\n"
                     "3. `curl -X POST localhost:9876/stop/<name>` — stop (the file becomes `<name>.sh.off`),\n"
                     "   `/start/<name>` — start, `/restart/<name>` — restart. Delete the file to remove the service.\n"
                     "At most {max} services. When you add, stop or remove a service, tell the person in one\n"
                     "line: which service and why. The person sees their services with /services in Telegram.\n",
    # -- the owner's command line (guestctl) ------------------------------------------ #
    "ctl_web_done": "https://{host}  (password: {pw_file})",
    "ctl_recreate_busy": "{name} has terminals open ({n}): recreating would cut them off. "
                         "Wait, or use --force.",
    "ctl_no_container": "no container",
    "ctl_list_line": "{name}\ttg={tg}\t:{port}\t{state}\t{on}\t5h {five_used} of {five_limit}"
                     " · week {week_used} of {week_limit}",
    "ctl_open": "OPEN",
    "ctl_closed": "closed",
    "ctl_wall_claude": "Claude turnstile",
    "ctl_wall_telegram": "Telegram turnstile",
    "ctl_wall_ssh": "server's ssh",
    "ctl_wall_nginx": "server's nginx",
    "ctl_wall_internet": "internet (example.com:443)",
    # -- the sandbox's own disk (guests/disk.py, the watchman) ------------------------- #
    "disk_warn_guest": "⚠️ Your sandbox's disk is {pct}% full: {used} of {total} GB. When it is "
                       "full, saving files will fail. Delete what you no longer need, or ask "
                       "the agent: \"what takes the most space in my sandbox?\"",
    "disk_full_guest": "🛑 Your sandbox's disk is full: {used} of {total} GB. New files can't be "
                       "saved and programs may fail. Delete what you no longer need (ask the "
                       "agent what takes the most space); the sandbox keeps running.",
    "disk_programs_guest": "⚠️ The programs installed into your sandbox take {size} GB (the "
                           "suggested maximum is {high} GB). Remove the ones you don't use.",
    "disk_warn_owner": "⚠️ {name}: the sandbox's disk is {pct}% full ({used} of {total} GB). "
                       "The guest was told.",
    "disk_full_owner": "🛑 {name}: the sandbox's disk is full ({used} of {total} GB). Writes "
                       "fail; the sandbox keeps running. The guest was told.",
    "disk_programs_owner": "⚠️ {name}: programs installed into the sandbox take {size} GB "
                           "(warning from {warn} GB, a lot from {high} GB). The guest was told.",
}


# --------------------------------------------------------------------------- #
# the language files
# --------------------------------------------------------------------------- #
_cache: dict = {}          # path -> ((mtime_ns, size), table, problem)
_warned: set = set()       # what was already said on stderr in this process


def _settings(settings: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return settings if settings is not None else guestenv.load()


def wanted(settings: Optional[Mapping[str, str]] = None) -> str:
    """GUESTS_LANG as written (lower-case), "en" when unset."""
    return (_settings(settings).get("GUESTS_LANG") or "").strip().lower() or DEFAULT


def locale_dir(settings: Optional[Mapping[str, str]] = None) -> Path:
    raw = (_settings(settings).get("GUESTS_LOCALE_DIR") or "").strip()
    return Path(raw).expanduser() if raw else HERE / "locale"


def locale_file(code: str, settings: Optional[Mapping[str, str]] = None) -> Path:
    return locale_dir(settings) / f"{code}.json"


def fields(template: str) -> set:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _warn(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        print(f"guests texts: {message}", file=sys.stderr, flush=True)


def _read(path: Path) -> Tuple[Optional[dict], str]:
    """(the table, "") — or (None, why not). Unusable keys are left out (English)."""
    try:
        st = path.stat()
    except OSError:
        return None, f"{path} does not exist"
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1], hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        table, problem = None, f"{path} can't be read: {exc}"
    else:
        if not isinstance(data, dict):
            table, problem = None, f"{path} is not a JSON object of texts"
        else:
            table, bad = {}, []
            for key, english in EN.items():
                value = data.get(key)
                try:
                    usable = isinstance(value, str) and fields(value) <= fields(english)
                except ValueError:
                    usable = False
                if usable:
                    table[key] = value
                else:
                    bad.append(key)
            problem = ""
            if bad:
                _warn(f"{path}: said in English (missing or unusable): {', '.join(bad)}")
    _cache[path] = (stamp, table, problem)
    return table, problem


def _pick(settings: Optional[Mapping[str, str]] = None) -> Tuple[str, dict, str]:
    """(language, its table, why GUESTS_LANG wasn't used — "" when it was)."""
    settings = _settings(settings)
    code = wanted(settings)
    if code == DEFAULT:
        return DEFAULT, EN, ""
    if not CODE.fullmatch(code):
        problem = f"GUESTS_LANG={code} is not a language code; the texts are in English"
    else:
        table, why = _read(locale_file(code, settings))
        if table is not None:
            return code, table, ""
        problem = f"GUESTS_LANG={code}, but {why}; the texts are in English"
    _warn(problem)
    return DEFAULT, EN, problem


def lang(settings: Optional[Mapping[str, str]] = None) -> str:
    """The language the texts are in: GUESTS_LANG when its file is usable, else English."""
    return _pick(settings)[0]


def problem(settings: Optional[Mapping[str, str]] = None) -> str:
    """Why GUESTS_LANG isn't the language of the texts; "" when it is (or is English)."""
    return _pick(settings)[2]


def t(key: str, **fields) -> str:
    _, table, _ = _pick()
    return table.get(key, EN[key]).format(**fields)
