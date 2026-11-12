# Guests: web on/off and the agent's rules — design

Owner, 12.11.2026. Mockup he approved ("делай"):
https://files.agentkennel.com/agentkennel-guest-rules-1112/

## Why

The owner gives some guests a sandbox for one job. Vlad: only the website angelsrepair.com.
The worry is not that he breaks the site, it is that he uses the owner's subscription for
something else. With terminals in the browser nothing can hold him (he types into a shell
himself). Without web access every action is the agent's, on a Telegram message, so the
agent's work can be limited.

## What the owner gets (Server page, Guests block)

- **Web** switch per guest: on = `guestctl web` (address, certificate), off = `guestctl
  web-off` (address down, bot and agent stay). Both take a minute: a job, like Stop/Start.
- **Rules** button, usable only while web is off:
  - Anything in its sandbox / **Only this work**;
  - the work in the owner's words;
  - folders it may change;
  - actions: edit the site's files, rebuild and restart, install packages the site needs,
    publish new sites, set up other services (bots, tunnels, scheduled jobs);
  - anything else is declined politely by the agent;
  - "tell me when he asks for something strange or illegal" (on by default).

## How it holds

1. **The turnstile puts the rules into every Claude request of that guest** (a system
   block appended to `/v1/messages*` bodies). The sandbox can't skip it: all of its Claude
   traffic goes through the turnstile, on the host. The agent declines anything outside the
   work in the guest's language and does no part of it. Active only while `enabled`,
   `web` is off and the rules are on; with web on the rules are kept but not applied.
2. **Publishing new sites is refused by the site gate** when that action is off: a hard
   wall on the host.
3. **Strange or illegal**: the rules tell the agent to report it once with
   `POST http://172.31.250.1:8790/flag` (its own pass). The turnstile sends the owner
   "⚠️ <guest> asked for something strange or illegal: …" through the usual owner channel
   (`guestenv.notify_owner`), at most 5 an hour per guest. Ordinary off-topic requests are
   not reported (owner's words).

Decided against (the builder's call, said to the owner): read-only mounts for "other
folders". Vlad's `scripts/publish.sh` renames folders in `/work` itself
(`angels-help-live.new` → `angels-help-live`), so a read-only `/work` would break the live
site's publishing. The folders rule is the agent's, like the others.

## Data

`guests.json`, per guest: `"web": bool` (exists) and

```json
"rules": {"on": true, "work": "…", "folders": ["/work/angels-help"],
          "actions": {"edit": true, "restart": true, "packages": true,
                      "publish": false, "services": false},
          "flag": true}
```

Written by `guestctl rules <name> '<json>'` (validated: work ≤ 1000 chars, ≤ 10 folders
under /work, known action keys), read by the turnstile and the site gate (the book re-reads
on change), shown by `guestctl list --json`. The panel posts to `/api/guests/rules`.

## Not now

Hard per-folder walls; rules for guests with web on; reporting ordinary off-topic asks.
