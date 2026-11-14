# Guests: whose Claude they run on — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each guest runs on the owner's API key (release default, dollar limit), their own key or subscription token, or the owner's subscription (today's behaviour, with a warning) — chosen in the owner's panel, and by the guest via the bot's `/claude` and their web panel.

**Architecture:** The turnstile stays the only way out for every guest; it picks the upstream credential per guest (`claude.mode` in `guests.json`) and counts dollars for `owner_key` guests. Secrets live in one new module (`guests/claudekeys.py`) on the host, files 0600, never in argv, logs, API answers or sandboxes. guestctl is the one writer the panel and the relay call; the relay and the turnstile share `claudekeys` for checking and switching.

**Tech Stack:** Python 3.10 stdlib (http.server, sqlite3, urllib), vanilla JS in `web/server.html` / `web/index.html`, pytest, Playwright for page tests.

**Spec:** `docs/superpowers/specs/2026-11-13-guest-claude-keys-design.md` (read it first; the mockup it links is the UI reference).

## Global Constraints

- Work in a worktree: `git worktree add /home/ubuntu/pr/terminal-keys -b feat/guest-claude-keys master`. All paths below are relative to it. The live services run from `/home/ubuntu/pr/terminal`; nothing there changes until Task 13.
- Modes: `owner_key`, `own`, `owner_sub`. A record without `claude` = `owner_sub` (existing guests on every install). Release default for new guests when no defaults were saved: `owner_key`.
- Defaults: `usd_week` 20, `notify_at` 0.8; `owner_sub` keeps today's token limits (`DEFAULT_LIMIT` 140_000_000 a week, 5 h = a seventh).
- No silent fallback to the subscription, ever.
- A secret never appears in argv (pass it on stdin), a log line, a JSON answer (only `last4`), `guests.json`, or a sandbox. Files holding secrets are 0600.
- Every person-facing text goes through `guests/texts.py`, in both `EN` and `RU` (same keys and fields — `guests/tests/test_texts.py`). Panel labels are English. Pronouns in texts: "they/their".
- Never write "on your subscription" as the description of guests (README, About, docs).
- TDD: every step that adds behaviour starts with a failing test. Long runs (any pytest file with browser tests, the full suites) in the background.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never `git add -A`.

## Review Focus

1. **A key pasted with spaces, a newline, or inside a sentence** ("here: sk-ant-api03-…") — expected: found and taken, the message never reaches the sandbox, and it is deleted from the chat. Test in Task 8.
2. **The owner's key revoked or out of credit mid-week** — expected: the guest gets the plain "owner has been told" text, the owner gets one message per hour at most, nothing goes to the subscription. Test in Task 5.
3. **A model name the price table doesn't know** (a new model) — expected: priced at the highest price in the table, never $0. Test in Task 1.
4. **Saving a guest's Claude settings while rules/web/limits are set** — expected: nothing else in the record changes. Test in Task 3.
5. **Secrets leaking into listings** — expected: `guestctl list --json`, `/api/guests`, `/guest/usage` carry `has_key`/`last4` only. Tests in Tasks 7 and 9.

---

### Task 1: Price table and dollars per request

**Files:**
- Create: `guests/prices.json`, `guests/pricing.py`
- Test: `guests/tests/test_pricing.py`

**Interfaces:**
- Produces: `pricing.usd(model: str | None, usage: dict) -> float`; `pricing.load(path: Path | None = None) -> dict`.

`prices.json`: `{"updated": "YYYY-MM-DD", "source": "<pricing page URL>", "models": {"<family or id>": {"input": $/MTok, "output": …, "cache_write": …, "cache_read": …}}}`. Fill the values from Anthropic's pricing page at build time (use the `claude-api` skill; never from memory); keys are family names matched as substrings of the model id (`"opus-5"`, `"sonnet-5"`, `"haiku-4-5"`, …), longest match wins.

- [ ] **Step 1: Write the failing tests**

```python
def test_usd_counts_every_kind_of_token(prices_file):     # a temp prices.json with one model "m-1": 1/2/3/4 $ per MTok
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
         "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert pricing.usd("claude-m-1-2026", u, path=prices_file) == pytest.approx(1 + 2 + 3 + 4)

def test_an_unknown_model_costs_the_most_expensive_price(prices_file2):   # two models, 1x and 10x
    assert pricing.usd("claude-new-thing", {"output_tokens": 1_000_000}, path=prices_file2) == pytest.approx(10 * OUT)

def test_the_shipped_table_prices_the_models_claude_code_uses():
    for m in ("claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"):   # plus the current ones from the table
        assert pricing.usd(m, {"input_tokens": 1000}) > 0
```

(`usd` takes an optional `path=` for tests.)

- [ ] **Step 2: Run** `cd guests && python3 -m pytest tests/test_pricing.py -q` — expected: FAIL (no module).
- [ ] **Step 3: Implement** `pricing.py` (load cached by mtime; longest-substring match; unknown → per-field max over the table).
- [ ] **Step 4: Run** the same — expected: PASS.
- [ ] **Step 5: Commit** `guests/prices.json guests/pricing.py guests/tests/test_pricing.py`.

### Task 2: Secrets store and key checks

**Files:**
- Create: `guests/claudekeys.py`
- Test: `guests/tests/test_claudekeys.py`

**Interfaces:**
- Produces:
  - `classify(text: str) -> tuple[str, str] | None` — finds `sk-ant-api…` (→ `("api", key)`) or `sk-ant-oat…` (→ `("oauth", token)`) anywhere in the text, stripped; `None` otherwise. Regex: `sk-ant-(api|oat)\d{2}-[A-Za-z0-9_\-]{20,}`.
  - `last4(secret: str) -> str`.
  - `class KeyStore(root: Path)`: `get(name) -> tuple[str, str] | None`, `put(name, kind, secret)`, `drop(name)`, `owner() -> str | None`, `set_owner(key: str)`, `drop_owner()`. Files: `<root>/keys/<name>.json` `{"kind","secret","saved"}`, `<root>/keys/_owner.json`; dir 0700, files 0600, atomic replace.
  - `check(kind: str, secret: str, upstream=("https","api.anthropic.com",443), timeout=20) -> tuple[bool, str]` — `api`: `GET /v1/models?limit=1` with `x-api-key` and `anthropic-version: 2023-06-01`; `oauth`: `POST /v1/messages` with `Authorization: Bearer`, `anthropic-beta: oauth-2025-04-20`, model `claude-haiku-4-5`, `max_tokens: 1`, one "hi" message (costs the token's owner a fraction of a cent). 2xx → `(True, "")`; else `(False, <Anthropic's error.message or "HTTP <code>">)`; network error → `(False, "can't reach Anthropic: …")`.

- [ ] **Step 1: Write the failing tests** — `classify` on: bare key, key with spaces/newline, key inside a sentence, an oat token, a random `sk-` string (None); `KeyStore` round-trip, file mode `0o600`, dir `0o700`, `drop` removes; `check` against a local fake upstream (reuse the `FakeUpstream` pattern from `turnstile/tests/test_turnstile.py`): 200 → ok, 401 with `{"error":{"message":"invalid x-api-key"}}` → `(False, "invalid x-api-key")`, and the request carried `x-api-key` (api) / `Bearer` + oauth beta (oauth).
- [ ] **Step 2: Run** `python3 -m pytest tests/test_claudekeys.py -q` — FAIL.
- [ ] **Step 3: Implement** `claudekeys.py`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit.**

### Task 3: The guest's `claude` settings in the guest book

**Files:**
- Modify: `guests/turnstile/turnstile.py` (`Guest`, `GuestBook._guest`, `GuestBook.add`, new `set_claude`)
- Test: `guests/turnstile/tests/test_claude_modes.py` (new)

**Interfaces:**
- Produces:
  - `MODES = ("owner_key", "own", "owner_sub")`; `Guest.claude: dict` always filled: `{"mode", "owner_mode", "usd_week", "notify_at"}`; a record without `claude` → `{"mode": "owner_sub", "owner_mode": "owner_sub", "usd_week": 20, "notify_at": 0.8}`.
  - `validate_claude(d: dict) -> dict` (ValueError on unknown mode, `owner_mode` not in `owner_key|owner_sub`, `usd_week` not a number 0 < x ≤ 100000, `notify_at` not in (0,1]).
  - `GuestBook.add(..., claude: dict | None = None)` — stores `claude` when given.
  - `GuestBook.set_claude(name: str, **fields) -> None` — merges into the stored `claude`, validates, keeps every other field of the record. Setting `mode` to `owner_key`/`owner_sub` also sets `owner_mode` to it.

- [ ] **Step 1: Write the failing tests:** old record → `owner_sub`; `set_claude(name, mode="owner_key", usd_week=5)` keeps `rules`, `web`, `week_limit`, `unlimited_until` byte-equal (Review Focus 4); `mode="own"` keeps `owner_mode`; bad values raise.
- [ ] **Step 2: Run** `python3 -m pytest turnstile/tests/test_claude_modes.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it and `turnstile/tests` — PASS.
- [ ] **Step 5: Commit.**

### Task 4: Dollars in the ledger

**Files:**
- Modify: `guests/turnstile/turnstile.py` (`Ledger`)
- Test: `guests/turnstile/tests/test_claude_modes.py`

**Interfaces:**
- Produces: `Ledger.record(guest, model, usage, ts_=None, mode="owner_sub", usd=0.0) -> int` (weighted, as today); `Ledger.usd_between(guest, start, end) -> float` (only rows with `mode='owner_key'`); `Ledger.usd_since(guest, since) -> float`; `Ledger.once(key: str) -> bool` (True the first time a key is seen; stored in `meta`). Columns `mode TEXT DEFAULT 'owner_sub'`, `usd REAL DEFAULT 0` added with `ALTER TABLE` when missing.

- [ ] **Step 1: Failing tests:** an existing `usage.db` created with today's schema opens and gains the columns, old rows count as `owner_sub` / $0; `usd_between` sums only `owner_key` rows within bounds; `once("x")` True then False.
- [ ] **Step 2–4:** run (FAIL) → implement → run (PASS).
- [ ] **Step 5: Commit.**

### Task 5: The gate per mode

**Files:**
- Modify: `guests/turnstile/turnstile.py` (`rewrite_headers`, `make_server`, `over_limit`, `SpendWatch`, `main`), `guests/texts.py`
- Test: `guests/turnstile/tests/test_claude_modes.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `rewrite_headers(headers, auth: tuple[str, str]) -> dict` — `("oauth", token)`: today's behaviour; `("api", key)`: `x-api-key: key`, no `authorization`, `oauth-2025-04-20` removed from `anthropic-beta`.
  - `upstream_auth(guest, keys: KeyStore, owner_token: Callable[[], str]) -> tuple[str, str] | None` — `owner_sub` → `("oauth", owner_token())`; `owner_key` → `("api", keys.owner())`; `own` → `keys.get(guest.name)`; None when missing.
  - `usd_window(guest, ledger, now=None) -> dict` `{"used", "limit", "resets_at"}` in dollars over `week_bounds(now, ledger.week_reset())`.
  - `over_limit(guest, ledger, now=None)` — `owner_key`: the dollar week only (`gate_limit_usd`); `own`: None; `owner_sub`: today's.
  - `make_server(..., keys: KeyStore, prices=pricing.usd)`.
  - New texts: `gate_no_owner_key` ("Claude isn't available right now: the owner has been told."), `gate_no_own_key` (how to add one: /claude in the bot, "Claude" in the panel), `gate_limit_usd` (`{spent}`, `{limit}`, `{day}`, `{at}`), `owner_key_problem` (to the owner: `{guest}`, `{error}`), `usd_notice` (80%: `{guest}`, `{spent}`, `{limit}`), `usd_spend_alert` (`{guest}`, `{hour}`, `{limit}`).

Behaviour:
- Missing credential → `owner_key`: 503 `gate_no_owner_key` + owner told (`owner_key_problem`, at most once an hour, shared across guests); `own`: 403 `gate_no_own_key`.
- `owner_key` upstream answers 401/403, or 400 whose body says "credit balance" → passed to the guest as is, owner told (same cooldown).
- `WEEK_RESET_HEADER` is read only when `mode == "owner_sub"`.
- After a request: `ledger.record(..., mode=guest.claude["mode"], usd=prices(model, usage) if mode == "owner_key" else 0)`. For `owner_key`: when the week's dollars cross `notify_at × usd_week`, send `usd_notice` once per guest per week (`ledger.once(f"usd80:{name}:{week_start}")`); `SpendWatch` for `owner_key` guests alerts when `usd_since(now-3600) > usd_week / 4` (`usd_spend_alert`), today's weighted alert stays for `owner_sub`, none for `own`.
- `main serve`: `keys = KeyStore(data)`; the owner's OAuth env is no longer required at start (warn in the log instead) — a server with only an API key must start.

- [ ] **Step 1: Failing tests** (fake upstream from `test_turnstile.py`): per mode the upstream sees the right auth headers and never the pass; `owner_key` without a key → 503 and one owner message for two requests within the hour (Review Focus 2); revoked key (upstream 401) → owner told once; "credit balance" 400 → owner told; `own` without key → 403; dollar limit reached → 429 with `gate_limit_usd`; 80% notice sent once across three requests; an `own` guest's `anthropic-ratelimit-unified-7d-reset` header doesn't change `ledger.week_reset()`; an `owner_sub` guest behaves exactly as the existing tests say (they still pass unchanged).
- [ ] **Step 2–4:** FAIL → implement → `python3 -m pytest turnstile/tests -q` PASS (background).
- [ ] **Step 5: Commit.**

### Task 6: The guest's own endpoints on the turnstile

**Files:**
- Modify: `guests/turnstile/turnstile.py` (`guest_usage`, handler), `guests/claudekeys.py`
- Test: `guests/turnstile/tests/test_claude_modes.py`

**Interfaces:**
- Produces:
  - `claude_view(guest, ledger, keys) -> dict` — `{"mode", "owner_mode", "has_key": bool, "last4": str|None, "usd": {"used","limit","resets_at"} | None}` (`usd` only for `owner_key`). Shared by `/guest/usage`, the relay and guestctl.
  - `GET /guest/usage` gains `"claude": claude_view(...)`; existing fields unchanged (older images). For `owner_key`, `seven_day.utilization` = dollars used / limit, so an old chip shows a sensible "wk N%".
  - `claudekeys.switch_own(book, keys, name, text, check=check) -> tuple[bool, str]` — classify → check → `keys.put` → `book.set_claude(name, mode="own")` → `guestenv.notify_owner(t("owner_guest_own", guest=name))`; on failure nothing changes and the message says why (`own_not_a_key`, or Anthropic's error).
  - `claudekeys.switch_back(book, keys, name) -> tuple[bool, str]` — only when `owner_mode` is set; `keys.drop(name)`, `mode = owner_mode`, owner told (`owner_guest_back`).
  - `POST /guest/claude` with the guest's pass, JSON `{"secret": "..."}` or `{"back": true}` → `{"ok": bool, "message": str, "claude": claude_view}`; body capped at 8 KB; refused for a disabled guest.

- [ ] **Step 1: Failing tests:** `/guest/usage` with no `claude` record shows `owner_sub`; posting a good key (fake upstream 200) switches to `own`, stores 0600, owner told, answer has `last4` and no secret anywhere in the body; bad key → `ok: false`, mode unchanged; `back` returns to `owner_mode` and deletes the file; an unknown pass → 401.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit.**

### Task 7: guestctl — per-guest Claude, defaults for new guests, the owner's key

**Files:**
- Modify: `guests/guestctl.py` (argparse, `cmd_add`, `guests_report`, new `cmd_claude`, `cmd_defaults`, `cmd_owner_key`), `guests/invites.py` (web default)
- Create: `guests/defaults.py` — `load(data: Path) -> dict`, `save(data: Path, d: dict) -> dict` (validated) for `<data>/new_guest_defaults.json`: `{"claude_mode": "owner_key", "usd_week": 20, "notify_at": 0.8, "week_limit": 140000000, "web": true, "rules": {}}`; a missing file = exactly this.
- Test: `guests/tests/test_guestctl.py`, `guests/tests/test_invites_desk.py`

**Interfaces:**
- Produces (CLI, JSON on stdout):
  - `guestctl claude <name> '<json>'` — `{"mode"?, "usd_week"?, "notify_at"?}`; with `"secret": true` reads the guest's key from **stdin** and goes through `switch_own`. Choosing `owner_sub` is allowed only with `"understood": true` in the JSON.
  - `guestctl defaults [--set '<json>']` — prints / saves defaults; `claude_mode: owner_sub` needs `"understood": true`; `owner_key` needs a saved owner key.
  - `guestctl owner-key` — reads the key from stdin, `check("api", …)`, saves; `--remove`; prints `{"ok", "last4"|"error"}`.
  - `cmd_add`: when `--limit` is not given, takes `week_limit` from defaults; passes `claude={"mode": d["claude_mode"], "owner_mode": d["claude_mode"] if != "own" else "owner_key", ...}`; applies `rules` when set and `web: false` via `set_web`.
  - `invites.Admit` skips `["web", name]` when defaults say `web: false`.
  - `guests_report()` rows gain `"claude": claude_view(...)`; `list --json` top level gains `"owner_key": {"set": bool, "last4": …}` and `"owner_key_week_usd": float` (sum over guests).

- [ ] **Step 1: Failing tests:** add with no defaults file → `owner_key`, $20; with defaults `owner_sub` → `owner_sub`; old guests untouched; `claude` refuses `owner_sub` without `understood`; secret comes from stdin and **is not in `sys.argv`** of the subprocess the panel will spawn (assert on `GuestsPanel.run` args in Task 9); `list --json` contains no `sk-ant` substring (Review Focus 5); `Admit` with `web: false` runs only `add`.
- [ ] **Step 2–4:** FAIL → implement → `python3 -m pytest tests/test_guestctl.py tests/test_invites_desk.py -q` PASS.
- [ ] **Step 5: Commit.**

### Task 8: `/claude` in the guest bot

**Files:**
- Modify: `guests/tgrelay/tgrelay.py` (`route`, `_menu_for`/`_with_extras`, new `claude_command`, `_show_claude`, `_press_claude`), `guests/texts.py`
- Test: `guests/tgrelay/tests/test_tgrelay.py`

**Interfaces:**
- Consumes: `claude_view`, `switch_own`, `switch_back`, `classify` (Tasks 2, 6).
- Produces: menu item `{"command": "claude", "description": EN["cmd_claude"]}` for every guest (web on or off); callback data `cl:own_key`, `cl:own_sub`, `cl:back`.

Behaviour:
- `/claude` → text from `claude_view` (`cl_now_owner_key` with dollars, `cl_now_owner_sub`, `cl_now_own` with last4) + buttons: "Own API key", "Own subscription", and "Back to the owner's key" only when mode is `own` and `owner_mode` is set.
- "Own API key" → `cl_send_key`; "Own subscription" → `cl_send_token` (explains `claude setup-token`).
- **Any** message whose text `classify()` finds is handled here and never queued for the sandbox: `deleteMessage` it, run `switch_own` in the background, answer `cl_saved` (last4) or `cl_failed` (reason). No waiting state is needed: the pattern decides.

- [ ] **Step 1: Failing tests:** the menu has `/claude` with web on and off; `/claude` answers with the current mode; a message "here it is: sk-ant-api03-<40 chars> " (Review Focus 1) is not in the sandbox's `getUpdates`, `deleteMessage` was called with its id, `switch_own` was called with the text; a bad key answers `cl_failed`; "Back" switches back; a guest can't press another guest's button (the guest is found by sender, like `/services`).
- [ ] **Step 2–4:** FAIL → implement → `python3 -m pytest tgrelay/tests -q` PASS.
- [ ] **Step 5: Commit.**

### Task 9: The owner panel's API

**Files:**
- Modify: `status_server.py` (`GuestsPanel.run` gains `input: str | None = None`; new `claude`, `settings`, `owner_key` methods; routes `POST /api/guests/claude`, `POST /api/guests/settings`, `POST /api/guests/owner-key`; `state()` adds `"settings"`, `"owner_key"`)
- Test: `tests/test_guests_api.py`

**Interfaces:**
- Produces: `POST /api/guests/claude {name, mode?, usd_week?, secret?, understood?}`; `POST /api/guests/settings {claude_mode?, usd_week?, notify_at?, week_limit?, web?, rules?, understood?}`; `POST /api/guests/owner-key {key}` / `{remove: true}`. Errors from guestctl are returned as 400 with its message. Secrets go to `run(..., input=secret)`.

- [ ] **Step 1: Failing tests:** each route calls guestctl with the right args and the secret only on stdin (assert `"sk-ant" not in " ".join(args)`); `/api/guests` GET contains `owner_key.last4` and no `sk-ant` substring; `owner_sub` without `understood` → 400.
- [ ] **Step 2–4:** FAIL → implement → `python3 -m pytest tests/test_guests_api.py -q` PASS.
- [ ] **Step 5: Commit.**

### Task 10: The owner panel's page

**Files:**
- Modify: `web/server.html` (Guests block)
- Test: `tests/test_server_guests.py` (Playwright, background)

UI exactly as the mockup parts 1–3 (`https://files.agentkennel.com/agentkennel-guest-claude-1113/`):
- Settings → "For new guests" section above the bot setup: Claude for new guests (3 radios with the sub-lines), Your API key (input, "Check and save", "✓ checked, works · …xY4k" / Anthropic's error), the limit field that follows the mode (`$ a week` / `% of your week` = `week_limit / 8_700_000` / hidden for own), "Tell me in Telegram at 80%", Web default, What the agent may do default; Save.
- Choosing "Your subscription" anywhere opens the warning (text from the spec) with "I understand"; Save stays disabled until ticked; sends `understood: true`.
- Row label after the name: `your key · $6.10 of $20` + bar / `own key` / `your subscription ⚠ · wk 1%` (amber); a **Claude** button opening the per-guest form (mode, limit, paste a key for them).
- Block summary gains `your key this week: $9.40` when any guest is on `owner_key`.
- When the defaults say `owner_key` and no owner key is saved, the Settings section and the invite flow show one line: "Add your API key in Settings — new guests run on it" (creating a guest is still allowed; the gate refuses with `gate_no_owner_key` until a key is added).

- [ ] **Step 1: Failing tests** (stub `/api/guests` like the existing tests): the three labels render; the warning blocks Save until ticked and the POST carries `understood: true`; the limit field switches unit with the mode; the key input is never echoed back into the page after save (only last4).
- [ ] **Step 2–4:** FAIL → implement → PASS (background).
- [ ] **Step 5: Commit.** Then update `tests/make_screenshots.py` payloads (`claude` per guest) so `services-guests.png` regenerates without errors; regenerate it.

### Task 11: The guest's web panel

**Files:**
- Modify: `status_server.py` (guest side: `POST /api/quota/claude` → turnstile `POST /guest/claude` with `ANTHROPIC_AUTH_TOKEN`; `/api/quota` passes `claude` through), `web/index.html` (a "Claude" label next to the quota chip, the "Your Claude" dialog from mockup part 5), route in the Caddyfiles/nginx template like `/api/quota` (find them with `grep -rn "api/quota" --include=Caddyfile* --include=*.conf* .`)
- Test: `tests/test_quota_api.py`, `tests/test_index_quota.py`

Behaviour: label shows `Claude · owner's key $6.10/$20` / `own key …x4Fk` / today's chip for `owner_sub`; the dialog: now-line, radios API key / Your Claude subscription (with the `claude setup-token` hint), input, "Check and save", "Back to the owner's key" (only when `owner_mode` is set and mode is `own`). The page never keeps the secret after the POST (input cleared). On the owner's own install (`source != "guest"`) nothing new shows.

- [ ] **Step 1: Failing tests:** `/api/quota/claude` forwards the body to a fake turnstile with the pass and returns its answer without the pass; the page shows the label per mode and the dialog posts and clears the input.
- [ ] **Step 2–4:** FAIL → implement → PASS (background).
- [ ] **Step 5: Commit.**

### Task 12: Docs

**Files:** `README.md` (Guests section + top bullet), `guests/README.md`, `CHANGELOG.md` (Unreleased), `docs/telegram.md` if it lists guest commands.

Content: guests run on the owner's API key by default with a dollar limit; own key or subscription token via `/claude` or the panel; "your subscription" exists, with the note that Anthropic's terms don't allow one subscription for several people. Existing guests keep running as before after the update.

- [ ] **Step 1:** run the README tests `python3 -m pytest tests -k readme -q` (background) after editing — PASS; `grep -n "on your subscription" README.md guests/README.md` returns nothing about guests.
- [ ] **Step 2: Commit.**

### Task 13: Merge, deploy on this server, check live

- [ ] **Step 1:** full suites in the worktree (background): `python3 -m pytest -q` and `cd guests && python3 -m pytest -q` — all pass; fresh-install check `tests/install/run.sh ubuntu-24.04` on a clone — 0 FAIL.
- [ ] **Step 2:** fast-forward `master`, push.
- [ ] **Step 3:** on this server, **before** restarting anything: `python3 guests/guestctl.py defaults --set '{"claude_mode":"owner_sub","understood":true}'` (owner, 13.11: keep this server as it is now).
- [ ] **Step 4:** restart `agentkennel-guest-turnstile`, `agentkennel-guest-tgrelay` (system units, sudo) and the user unit `agents-status-server`. Not `claude-tg-bridge`.
- [ ] **Step 5:** check live: `guestctl list --json` → every existing guest `mode: owner_sub`; turnstile log shows `PASS guest=…` for the next guest request with today's numbers; `/claude` in the guest bot answers for `yan` (the owner's own test guest); the Server page shows the labels; no `sk-ant` in `journalctl -u agentkennel-guest-turnstile`.
- [ ] **Step 6:** CHANGELOG version + release like v2.5.0 (tag, `gh release`, image workflow green, `:latest` updated). Sandboxes are **not** recreated; their web label comes with the next idle recreate.

### Task 14: agentkennel.com (owner, 14.11: "Сюда тоже надо внести изменения")

The site (`/home/ubuntu/pr/agentkennel-web`, Next.js, branch `main`; live = `npm run build` then `rsync -a --delete out/ /home/ubuntu/pr/agentkennel-web-live/`) says nothing about guests in text today; only the demo (`public/assets/demo.mp4`, `public/assets/img/demo.gif`, made by `tests/make_demo.py` in this repo with `tests/make_screenshots.py:guests_payload`) shows the Guests block. It goes live together with the release in Task 13, not before.

**Files:**
- Modify: `src/app/page.tsx` (features: a card or a paragraph about guests), `src/app/(site)/install/page.tsx` (the **Claude.** paragraph), `public/assets/demo.mp4`, `public/assets/img/demo.gif`
- Test: `tests/site.test.mjs` (`npm test`)

Content, same facts as Task 12: other people can each get their own sandbox with a Telegram bot and a web terminal; guests run on **your Anthropic API key** by default, with a dollar limit a week; or on their own key or Claude login; "your subscription" is possible, but Anthropic's terms don't allow one subscription for several people. Never "on your subscription" as the description of guests. The landing's own line "Official Claude Code CLI, your own subscription" is about the owner's own agents and stays.

- [ ] **Step 1: Failing test** in `tests/site.test.mjs`: the built landing contains "API key" in the guests text and the warning about one subscription for several people; no page's guest text matches `/guests?[^.]*on your subscription/i`.
- [ ] **Step 2–4:** FAIL → edit the pages → `npm run build && npm test` PASS (background).
- [ ] **Step 5:** regenerate the demo after Task 10 (`guests_payload` carries `claude` per guest: one `owner_key` with `$` spent, one `own`), copy both files into `public/assets/`, look at the guests beat frame by frame — the rows show `your key · $… of $20`, nothing says subscription.
- [ ] **Step 6:** `npm run check:clicks` and `npm run check:header` (background) — 0 failures; commit, push `yanhs/*`.
- [ ] **Step 7 (with Task 13 Step 6):** build, rsync to `agentkennel-web-live`, then `npm run check:header https://agentkennel.com/` and check the new text is on the live page (curl, 200).
