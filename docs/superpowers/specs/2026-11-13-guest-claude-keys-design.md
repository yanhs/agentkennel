# Guests: whose Claude they run on — design

Owner, 13.11.2026. Mockup he approved ("хорошо", then two additions below):
https://files.agentkennel.com/agentkennel-guest-claude-1113/

## Why

Today every guest's Claude traffic goes through the turnstile on the owner's subscription
login. Anthropic's terms don't allow one subscription for several people. The owner's
position: people may set it up as they like, but the product must say what the rules are,
and the main path must be one the rules allow. His choice for the main path: **the owner's
Anthropic API key**, billed by the token.

## Ways a guest can run (per guest, `claude.mode`)

| Mode | Upstream auth | Who pays | Limit |
|---|---|---|---|
| `owner_key` (**default for new guests**) | owner's API key, `x-api-key` | owner, per token | dollars per week |
| `own` | the guest's own API key (`x-api-key`) or own subscription token from `claude setup-token` (`Authorization: Bearer` + oauth beta) | guest | none by default |
| `owner_sub` ⚠ | owner's subscription login (today's behaviour) | owner's plan | today's weighted-token limits (5 h + week), unchanged |

All three still go through the turnstile, so the agent's rules, `/flag`, usage counting and
"enabled" keep working exactly as now. A sandbox still only knows its pass; no key or token
of anyone is ever put into a sandbox's environment or files.

Tested 13.11: a `claude setup-token` token forwarded through a pass-through proxy gives 200
(`Bearer`, oauth beta). API key through a gateway is the standard path (not tested here: no
key on the server yet).

## Owner side (Server page → Guests)

1. **Settings → "For new guests"** (above today's bot setup):
   - Claude for new guests: Your API key / Their own key or login / Your subscription ⚠;
   - Your API key field (stored on the host only, shown as last 4 characters, "✓ checked,
     works" after a check);
   - limit for a guest on your key: **$20 a week** default; "Tell me in Telegram at 80%"
     (on by default);
   - web access default (on), what the agent may do default (anything / only this work…).
   A new guest gets these values when created; each can be changed per guest afterwards.
2. **Choosing "Your subscription"** (default or per guest) opens a warning: "Anthropic's
   terms don't allow sharing one Claude subscription between several people…". It saves
   only with "I understand" ticked.
3. **Guest row**: label `your key · $6.10 of $20` with a bar, `own key`, or
   `your subscription ⚠ · wk 1%` (amber). A **Claude** button opens the per-guest choice
   (mode, the dollar limit, paste the guest's key for them).
4. Block header shows the total spent on the owner's key this week.
5. When a guest switches to their own key or back, the owner gets a message through the
   owner channel (`guestenv.notify_owner`, so the notifications bot if set):
   "cheap3d switched to their own key".

## Guest side

- **Bot menu item `/claude`** (next to /web, /newpassword, /services; owner: "можно пункт
  меню такой добавить"). Shows what the guest runs on now ("The owner's key · $6.10 of $20
  this week") and buttons: Own API key / Own subscription (explains `claude setup-token`) /
  Back to the owner's key. The relay on the host handles it; **the message with the key is
  read by the relay, never forwarded to the sandbox, and deleted from the chat** (builder's
  call, said to the owner).
- **Web panel** (sb-<name>): a "Claude" label next to cmd (where today's usage line is)
  opens "Your Claude": what it runs on now, paste an API key or a setup-token, "Check and
  save", "Back to the owner's key". The panel sends it to the turnstile with the guest's
  own pass (like `GET /guest/usage` today). Owner's words: guests can set it "и через веб
  интерфейс тоже".
- **Takes effect immediately** after a successful check (owner: "сразу"), no approval.
- A guest can't pick `owner_sub` or `owner_key` themselves; "Back to the owner's key"
  returns to whatever the owner set for that guest (`owner_key` or `owner_sub`).

## Checking a key

Before saving (owner's or guest's), the turnstile makes one cheap call upstream with it and
saves only on success; the error text from Anthropic is shown otherwise. It must not cost
the owner anything (the check uses the key being checked).

## Dollar limits

- The turnstile already reads `usage` from every response (`UsageParser`). For
  `owner_key` guests it turns it into dollars with a **price table** (per model: input,
  output, cache write, cache read), kept in one file so it can be updated when Anthropic
  changes prices.
- Week = the guest's existing week window (`week_bounds`); the UI shows the real reset
  time.
- At 80%: one message to the owner. At 100%: the guest is refused like today's limit, with
  the same kind of text, until the week resets or the owner raises the limit / "lift for
  now" (`unlimited_until`, exists).
- `own` guests: no limit unless the owner sets one; their usage is still recorded.

## Data

`guests.json`, per guest:

```json
"claude": {"mode": "owner_key", "usd_week": 20, "notify_at": 0.8}
```

Keys and tokens are **not** in `guests.json`: one file per secret under the turnstile's
state directory, mode 0600, never logged, never returned by any API (only the last 4
characters). The owner's key: next to `~/.claude/oauth.env`, read the same way.
Defaults for new guests: a small settings file the panel writes (`/api/guests/settings`).

## Docs

README and guests/README: guests run on the owner's API key by default; own key/login is
supported; "your subscription" exists with the note that Anthropic's terms don't allow one
subscription for several people. Never "on your subscription" as the description of guests
(owner, 13.11).

## Existing guests and this server (owner, 13.11)

Owner: "оставить и по умолчанию конкретно у меня на сервере оставить все так как сейчас, а
в релизе уже по другому".

- **Guests that exist before the update stay on `owner_sub`**: a record without a `claude`
  field means `owner_sub`, on every install. Nothing stops working on upgrade; the owner
  moves a guest by hand.
- **The release default for new guests is `owner_key`**: whenever the "For new guests"
  settings were never saved.
- **On the owner's server** the "For new guests" default is set to `owner_sub` once after
  the deploy (his word above stands for the "I understand" tick). New guests there keep
  getting today's behaviour until he changes it.
- An upgraded install with existing guests and no saved default: new guests get
  `owner_key`, and since no key is saved yet, the panel says so when creating one (create
  is still allowed; the guest is refused with the "owner has been told" text until a key is
  added or the owner picks another mode).

## When something is missing (builder's calls, after the builder check)

- **No silent fallback to the subscription, ever.** A guest on `owner_key` whose key is
  missing, revoked or out of credit is refused with a plain text ("Claude isn't available
  right now, the owner has been told"); the owner gets one message. The panel can't save
  "Your API key" as the default until a key has passed the check.
- **`own` with no key yet**: requests are refused with how to add one (`/claude` in the bot,
  "Claude" in the panel). "Back to the owner's key" is offered only when the owner's choice
  for that guest is `owner_key` or `owner_sub` (kept per guest as `claude.owner_mode`;
  `mode` is what runs now).
- **Limits on `owner_key`**: dollars per week only; today's 5-hour token window is not
  used there. The hourly spend alert is rewritten in dollars (one message when a guest
  spends more than a quarter of their week's dollars within an hour). `owner_sub` keeps
  today's token limits and alert unchanged.
- **The week reset time** the turnstile learns from Anthropic's responses is taken only from
  `owner_sub` traffic (a guest's own subscription would bring back their own reset time).
- **Delivery**: the turnstile, relay and owner panel change on the host at once; the bot's
  `/claude` works immediately. The guest's web panel lives in the sandbox image, so its
  "Claude" label arrives when each sandbox is next recreated while idle (no terminal is
  closed for this). `GET /guest/usage` keeps its current fields for older images.

## Limits follow the mode (owner, 13.11: "если подписка, то считаются не доллары")

- `owner_key`: dollars a week (above).
- `owner_sub`: today's measure, unchanged — share of the owner's week (weighted tokens,
  1% ≈ 8.7M) plus the 5-hour window; the row shows `your subscription ⚠ · wk 1%`, the
  guest sees today's "Today X of Y tokens" line. Today's default (140M a week ≈ 16% of the
  owner's week) stays.
- `own`: nothing is limited by default; the row shows just `own key` (the guest pays, so
  the owner has nothing to count).
- So the "For new guests" limit field changes with the chosen way: "$20 a week" for Your
  API key, "16% of your week" for Your subscription, hidden for Their own key or login.
  The per-guest Claude window does the same. Switching a guest between modes keeps both
  numbers, so switching back restores the old limit.

## Not now

Billing guests; per-model limits; limits for `own` guests by default; showing anyone
else's key in full.

## Left to the builder

Endpoint names, file layout, which upstream call checks a key, the price-table format,
exact texts (EN in the panel, guest texts through `texts.py` like the rest).
