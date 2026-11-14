"""A guest's `claude` settings: whose credential its Claude runs on."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import turnstile as ts  # noqa: E402

RULES = {"on": True, "work": "The website: its pages and texts.", "folders": ["/work/site"],
         "actions": {"edit": True, "restart": True, "packages": True,
                     "publish": False, "services": False},
         "flag": True}
OLD_DEFAULT = {"mode": "owner_sub", "owner_mode": "owner_sub", "usd_week": 20, "notify_at": 0.8}


@pytest.fixture
def book(tmp_path):
    return ts.GuestBook(tmp_path / "guests.json")


def _raw(book):
    return json.loads(book.path.read_text("utf-8"))["guests"]


def test_modes_constant():
    assert ts.MODES == ("owner_key", "own", "owner_sub")


def test_old_record_reads_as_owner_sub_and_stays_untouched(book):
    book.path.write_text(json.dumps({"guests": {"vlad": {
        "pass_sha256": "x", "week_limit": 70, "five_hour_limit": 10, "enabled": True}}}))
    before = book.path.read_bytes()
    g = book.get("vlad")
    assert g.claude == OLD_DEFAULT
    assert book.path.read_bytes() == before


def test_claude_dict_not_shared(book):
    book.add("anna", 70)
    a, b = book.get("anna"), book.get("anna")
    a.claude["mode"] = "own"
    assert b.claude["mode"] == "owner_sub"


def test_invalid_stored_block_reads_as_default(book):
    book.add("anna", 70)
    assert book.get("anna").claude_error is None
    book._update("anna", claude={"mode": "bogus", "usd_week": -3})
    g = book.get("anna")
    assert g.claude == OLD_DEFAULT
    assert g.claude_error and "mode" in g.claude_error
    book.set_claude("anna", mode="owner_key")      # a valid merge clears the error
    assert book.get("anna").claude_error is None


def test_record_without_claude_has_no_error(book):
    book.path.write_text(json.dumps({"guests": {"vlad": {"pass_sha256": "x", "week_limit": 70}}}))
    g = book.get("vlad")
    assert g.claude_error is None and g.claude["mode"] == "owner_sub"


def test_unknown_key_raises(book):
    with pytest.raises(ValueError):
        ts.validate_claude({"usd_wek": 5})
    book.add("anna", 70)
    with pytest.raises(ValueError):
        book.set_claude("anna", usd_wek=5)
    with pytest.raises(ValueError):
        book.add("bob", 70, claude={"modee": "own"})


def test_add_stores_claude(book):
    book.add("anna", 70, claude={"mode": "owner_key", "usd_week": 5})
    assert book.get("anna").claude == {"mode": "owner_key", "owner_mode": "owner_key",
                                       "usd_week": 5, "notify_at": 0.8}
    assert _raw(book)["anna"]["claude"]["mode"] == "owner_key"


def test_add_rejects_bad_claude(book):
    with pytest.raises(ValueError):
        book.add("anna", 70, claude={"mode": "nope"})
    assert book.names() == []


def test_set_claude_keeps_everything_else(book):
    book.add("anna", 70)
    book.set_web("anna", False)
    book.set_rules("anna", RULES)
    book.set_limit("anna", 700)
    book.set_unlimited_until("anna", 123.0)
    before = _raw(book)["anna"]
    book.set_claude("anna", mode="owner_key", usd_week=5)
    after = _raw(book)["anna"]
    for k, v in before.items():
        assert after[k] == v
    for k in ("rules", "web", "week_limit", "five_hour_limit", "unlimited_until"):
        assert k in after and json.dumps(after[k]) == json.dumps(before[k])
    assert set(after) - set(before) == {"claude"}
    assert book.get("anna").claude == {"mode": "owner_key", "owner_mode": "owner_key",
                                       "usd_week": 5, "notify_at": 0.8}


def test_set_claude_own_keeps_owner_mode(book):
    book.add("anna", 70)
    book.set_claude("anna", mode="owner_key")
    book.set_claude("anna", mode="own")
    c = book.get("anna").claude
    assert c["mode"] == "own" and c["owner_mode"] == "owner_key"
    book.set_claude("anna", mode="owner_sub")
    c = book.get("anna").claude
    assert c["mode"] == "owner_sub" and c["owner_mode"] == "owner_sub"


def test_own_from_scratch_owner_mode_owner_key():
    assert ts.validate_claude({"mode": "own"})["owner_mode"] == "owner_key"
    assert ts.validate_claude({"mode": "owner_sub"})["owner_mode"] == "owner_sub"
    assert ts.validate_claude({}) == OLD_DEFAULT


def test_usd_week_float_ok():
    assert ts.validate_claude({"usd_week": 7.5})["usd_week"] == 7.5


@pytest.mark.parametrize("bad", [
    {"mode": "free"}, {"owner_mode": "own"}, {"usd_week": 0}, {"usd_week": -1},
    {"usd_week": 100001}, {"usd_week": True}, {"usd_week": "5"},
    {"notify_at": 0}, {"notify_at": 1.1}, {"notify_at": "x"}, {"notify_at": True},
])
def test_bad_values_raise(bad):
    with pytest.raises(ValueError):
        ts.validate_claude(bad)


def test_set_claude_bad_value_changes_nothing(book):
    book.add("anna", 70)
    before = book.path.read_bytes()
    with pytest.raises(ValueError):
        book.set_claude("anna", usd_week=0)
    assert book.path.read_bytes() == before


def test_set_claude_unknown_guest(book):
    with pytest.raises(KeyError):
        book.set_claude("nobody", mode="own")


OLD_USAGE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS usage (ts REAL, day TEXT, guest TEXT,"
    " model TEXT, input INT, output INT, cache_create INT, cache_read INT,"
    " weighted INT)"
)


def test_ledger_migrates_old_schema(tmp_path):
    import sqlite3
    p = tmp_path / "usage.db"
    db = sqlite3.connect(str(p))
    db.execute(OLD_USAGE_SCHEMA)
    db.execute("INSERT INTO usage VALUES (100.0,'1970-01-01','g','m',1,2,3,4,10)")
    db.commit()
    db.close()
    for _ in range(2):  # opening twice is fine
        led = ts.Ledger(p)
    cols = [r[1] for r in led._db.execute("PRAGMA table_info(usage)")]
    assert "mode" in cols and "usd" in cols
    assert led._db.execute("SELECT mode, usd FROM usage").fetchone() == ("owner_sub", 0)
    assert led.usd_between("g", 0, 1000) == 0.0
    assert led.spent_between("g", 0, 1000) == 10


def test_usd_sums_only_owner_key_within_bounds(tmp_path):
    led = ts.Ledger(tmp_path / "usage.db")
    u = {"input_tokens": 10}
    assert led.record("g", "m", u, ts_=100, mode="owner_key", usd=1.5) == ts.weighted(u)
    led.record("g", "m", u, ts_=200, mode="owner_key", usd=2.0)
    led.record("g", "m", u, ts_=300, mode="owner_key", usd=4.0)   # end bound excluded
    led.record("g", "m", u, ts_=150, mode="own", usd=9.0)
    led.record("g", "m", u, ts_=150, usd=9.0)                     # default owner_sub
    led.record("h", "m", u, ts_=150, mode="owner_key", usd=7.0)
    assert led.usd_between("g", 100, 300) == pytest.approx(3.5)
    assert led.usd_since("g", 200) == pytest.approx(6.0)
    assert led.usd_between("nobody", 0, 1000) == 0.0


def test_once_is_true_once(tmp_path):
    import threading
    led = ts.Ledger(tmp_path / "usage.db")
    assert led.once("x") is True
    assert led.once("x") is False
    assert led.once("y") is True
    res = []
    th = [threading.Thread(target=lambda: res.append(led.once("race"))) for _ in range(20)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert res.count(True) == 1


# --------------------------------------------------------------------------- #
# Task 5: the gate per mode — whose credential goes upstream, dollars, the owner told
# --------------------------------------------------------------------------- #
import http.client  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import claudekeys  # noqa: E402  (guests/ is on sys.path through turnstile)

OWNER = "sk-ant-oat01-OWNER-SUBSCRIPTION-LOGIN-xxxxxxxx"
OWNER_KEY = "sk-ant-api03-OWNER-API-KEY-aaaaaaaaaaaaaaaaaaaaWXYZ"
GUEST_KEY = "sk-ant-api03-GUEST-OWN-KEY-bbbbbbbbbbbbbbbbbbbb1234"
GUEST_TOKEN = "sk-ant-oat01-GUEST-SETUP-TOKEN-cccccccccccccccc5678"
RESP = json.dumps({"id": "m", "model": "claude-sonnet-4-5", "content": [],
                   "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}).encode()


class Upstream:
    """A stand-in for api.anthropic.com whose answer the test sets (status, body, headers)."""

    def __init__(self):
        self.requests, self.status, self.body, self.headers = [], 200, RESP, {}
        up = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                self.rfile.read(n)
                up.requests.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(up.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(up.body)))
                for k, v in up.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(up.body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def up():
    u = Upstream()
    yield u
    u.server.shutdown()


@pytest.fixture
def keys(tmp_path):
    return claudekeys.KeyStore(tmp_path)


@pytest.fixture
def ledger(tmp_path):
    return ts.Ledger(tmp_path / "usage.db")


@pytest.fixture
def told():
    return []


def _serve(**kw):
    server = ts.make_server("127.0.0.1", 0, **kw)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def gate(up, book, ledger, keys, told):
    server = _serve(book=book, ledger=ledger, owner_token=lambda: OWNER, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    notify=told.append, log=lambda line: None)
    yield server
    server.shutdown()


def call(gate, secret, header="authorization", betas=None):
    conn = http.client.HTTPConnection("127.0.0.1", gate.server_address[1], timeout=10)
    h = {"authorization": f"Bearer {secret}"} if header == "authorization" \
        else {"x-api-key": secret}
    if betas:
        h["anthropic-beta"] = betas
    conn.request("POST", "/v1/messages", headers=h,
                 body=json.dumps({"model": "m", "messages": []}))
    r = conn.getresponse()
    return r.status, r.read()


def message(body):
    return json.loads(body)["error"]["message"]


def wait_for(told, n, timeout=3.0):
    """Owner messages leave on their own thread: wait until n have come, then a moment more
    so a wrong extra one would show."""
    end = time.time() + timeout
    while len(told) < n and time.time() < end:
        time.sleep(0.02)
    time.sleep(0.15)
    return told


def guest(book, name="anna", **claude):
    secret = book.add(name, week_limit=10 ** 12)
    if claude:
        book.set_claude(name, **claude)
    return secret


# -- rewrite_headers / upstream_auth ------------------------------------------------- #
def test_rewrite_headers_oauth_is_todays():
    out = ts.rewrite_headers({"Authorization": "Bearer sbx-pass", "x-api-key": "sbx-pass",
                              "anthropic-beta": "a-1, b-2", "content-type": "x"},
                             ("oauth", "TOK"))
    assert out["authorization"] == "Bearer TOK"
    assert "x-api-key" not in {k.lower() for k in out}
    assert out["anthropic-beta"].split(",") == ["a-1", "b-2", ts.OAUTH_BETA]
    assert "sbx-pass" not in json.dumps(out)


def test_rewrite_headers_api_key():
    out = ts.rewrite_headers({"Authorization": "Bearer sbx-pass",
                              "anthropic-beta": f"a-1,{ts.OAUTH_BETA}", "content-type": "x"},
                             ("api", "KEY"))
    low = {k.lower(): v for k, v in out.items()}
    assert low["x-api-key"] == "KEY"
    assert "authorization" not in low
    assert low["anthropic-beta"] == "a-1"
    assert "sbx-pass" not in json.dumps(out)
    only_oauth = ts.rewrite_headers({"anthropic-beta": ts.OAUTH_BETA}, ("api", "KEY"))
    assert "anthropic-beta" not in {k.lower() for k in only_oauth}
    none = ts.rewrite_headers({"x-api-key": "sbx-pass"}, ("api", "KEY"))
    assert {k.lower() for k in none} == {"x-api-key", "accept-encoding"}
    assert none["x-api-key"] == "KEY"


def test_upstream_auth_per_mode(book, keys):
    guest(book, "aa", mode="owner_sub")
    guest(book, "bb", mode="owner_key")
    guest(book, "cc", mode="own")
    tok = lambda: OWNER  # noqa: E731
    assert ts.upstream_auth(book.get("aa"), keys, tok) == ("oauth", OWNER)
    assert ts.upstream_auth(book.get("bb"), keys, tok) is None
    assert ts.upstream_auth(book.get("cc"), keys, tok) is None
    keys.set_owner(OWNER_KEY)
    keys.put("cc", "oauth", GUEST_TOKEN)
    assert ts.upstream_auth(book.get("bb"), keys, tok) == ("api", OWNER_KEY)
    assert ts.upstream_auth(book.get("cc"), keys, tok) == ("oauth", GUEST_TOKEN)
    assert ts.upstream_auth(book.get("bb"), None, tok) is None


def test_upstream_auth_owner_sub_without_a_login_is_none(book, keys):
    guest(book, "aa")

    def broken():
        raise RuntimeError("no CLAUDE_CODE_OAUTH_TOKEN in /x/oauth.env")
    assert ts.upstream_auth(book.get("aa"), keys, broken) is None
    keys.set_owner(OWNER_KEY)                       # never the key instead of the login
    assert ts.upstream_auth(book.get("aa"), keys, broken) is None


# -- what goes upstream, per mode ---------------------------------------------------- #
@pytest.mark.parametrize("header", ["authorization", "x-api-key"])
def test_each_mode_sends_its_own_credential_and_never_the_pass(gate, up, book, keys, header):
    keys.set_owner(OWNER_KEY)
    keys.put("own-api", "api", GUEST_KEY)
    keys.put("own-tok", "oauth", GUEST_TOKEN)
    passes = {n: guest(book, n, mode=m) for n, m in [
        ("sub", "owner_sub"), ("key", "owner_key"), ("own-api", "own"), ("own-tok", "own")]}
    for name, p in passes.items():
        assert call(gate, p, header, betas=f"x-1,{ts.OAUTH_BETA}")[0] == 200, name
    sent = dict(zip(passes, up.requests))
    for name, p in passes.items():
        assert p not in json.dumps(sent[name]), name
    assert sent["sub"]["authorization"] == f"Bearer {OWNER}" and "x-api-key" not in sent["sub"]
    assert ts.OAUTH_BETA in sent["sub"]["anthropic-beta"]
    assert sent["key"]["x-api-key"] == OWNER_KEY and "authorization" not in sent["key"]
    assert sent["key"]["anthropic-beta"] == "x-1"
    assert sent["own-api"]["x-api-key"] == GUEST_KEY and "authorization" not in sent["own-api"]
    assert sent["own-tok"]["authorization"] == f"Bearer {GUEST_TOKEN}"
    assert ts.OAUTH_BETA in sent["own-tok"]["anthropic-beta"]
    for name in ("key", "own-api", "own-tok"):            # nobody else runs on the login
        assert OWNER not in json.dumps(sent[name]), name


# -- something missing: never the subscription instead ------------------------------ #
def test_owner_key_missing_503_and_the_owner_told_once(gate, up, book, told):
    a = guest(book, "anna", mode="owner_key")
    b = guest(book, "boris", mode="owner_key")
    for p in (a, b, a):
        status, body = call(gate, p)
        assert status == 503 and message(body) == ts._t("gate_no_owner_key")
    assert up.requests == []
    assert len(wait_for(told, 1)) == 1                     # shared across guests, an hour
    assert "anna" in told[0]


def test_owner_key_problem_is_told_again_after_an_hour(up, book, ledger, keys, told):
    now = [1_000_000.0]
    server = _serve(book=book, ledger=ledger, owner_token=lambda: OWNER, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    notify=told.append, log=lambda line: None, clock=lambda: now[0])
    try:
        p = guest(book, mode="owner_key")
        call(server, p)
        now[0] += 3599
        call(server, p)
        assert len(wait_for(told, 1)) == 1
        now[0] += 2
        call(server, p)
        assert len(wait_for(told, 2)) == 2
    finally:
        server.shutdown()


def test_many_concurrent_requests_tell_the_owner_once(gate, book, told):
    p = guest(book, mode="owner_key")
    th = [threading.Thread(target=call, args=(gate, p)) for _ in range(12)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert len(wait_for(told, 1)) == 1


def test_own_without_a_key_is_403_with_how_to_add_one(gate, up, book, keys, told):
    keys.set_owner(OWNER_KEY)                       # the owner's key is NOT used for them
    p = guest(book, mode="own")
    status, body = call(gate, p)
    assert status == 403 and message(body) == ts._t("gate_no_own_key")
    assert "/claude" in message(body)
    assert up.requests == [] and wait_for(told, 0) == []


def test_owner_sub_without_a_login_is_refused_and_the_owner_told(up, book, ledger, keys, told):
    def no_login():
        raise RuntimeError("no CLAUDE_CODE_OAUTH_TOKEN in /x/oauth.env")
    keys.set_owner(OWNER_KEY)
    server = _serve(book=book, ledger=ledger, owner_token=no_login, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    notify=told.append, log=lambda line: None)
    try:
        p = guest(book)
        for _ in range(2):
            status, body = call(server, p)
            assert status == 503 and message(body) == ts._t("gate_no_owner_key")
        assert up.requests == []
        assert len(wait_for(told, 1)) == 1 and "anna" in told[0]
        assert OWNER_KEY not in told[0]
    finally:
        server.shutdown()


def test_a_broken_claude_block_is_refused_not_run_on_the_subscription(gate, up, book, told):
    p = guest(book)
    book._update("anna", claude={"mode": "bogus"})
    for _ in range(2):
        status, body = call(gate, p)
        assert status == 503 and message(body) == ts._t("gate_no_owner_key")
    assert up.requests == []
    assert len(wait_for(told, 1)) == 1
    assert "anna" in told[0] and "bogus" in told[0]


# -- the owner's key refused upstream ------------------------------------------------ #
@pytest.mark.parametrize("status,error", [
    (401, {"type": "authentication_error", "message": f"invalid x-api-key {OWNER_KEY}"}),
    (403, {"type": "permission_error", "message": "This key is disabled."}),
    (400, {"type": "invalid_request_error",
           "message": "Your credit balance is too low to access the Anthropic API."}),
])
def test_owner_key_refused_upstream_passes_through_and_the_owner_is_told_once(
        gate, up, book, keys, told, status, error):
    keys.set_owner(OWNER_KEY)
    p = guest(book, mode="owner_key")
    up.status = status
    up.body = json.dumps({"type": "error", "error": error}).encode()
    for _ in range(2):
        got, body = call(gate, p)
        assert (got, body) == (status, up.body)
    assert len(wait_for(told, 1)) == 1
    assert "anna" in told[0] and str(status) in told[0]
    assert OWNER_KEY not in told[0] and "sk-ant" not in told[0]


def test_other_upstream_errors_are_not_the_owners_problem(gate, up, book, keys, told):
    keys.set_owner(OWNER_KEY)
    keys.put("own", "api", GUEST_KEY)
    p = guest(book, mode="owner_key")
    o = guest(book, "own", mode="own")
    up.status = 400
    up.body = b'{"type":"error","error":{"type":"invalid_request_error","message":"bad"}}'
    assert call(gate, p)[0] == 400
    up.status = 401                                     # the guest's own key: theirs to fix
    assert call(gate, o)[0] == 401
    assert wait_for(told, 0) == []


# -- dollars ------------------------------------------------------------------------- #
def test_rows_carry_the_mode_and_owner_key_dollars(gate, up, book, keys, ledger):
    keys.set_owner(OWNER_KEY)
    keys.put("own", "api", GUEST_KEY)
    for name, mode in (("key", "owner_key"), ("own", "own"), ("sub", "owner_sub")):
        assert call(gate, guest(book, name, mode=mode))[0] == 200
    rows = dict((g, (m, u)) for g, m, u in ledger._db.execute("SELECT guest, mode, usd FROM usage"))
    assert rows["key"] == ("owner_key", pytest.approx(3.0))   # sonnet-4-5: $3 a million in
    assert rows["own"] == ("own", 0) and rows["sub"] == ("owner_sub", 0)


def test_owner_key_dollar_limit_429(gate, up, book, keys, ledger):
    from datetime import datetime
    keys.set_owner(OWNER_KEY)
    p = guest(book, mode="owner_key", usd_week=5)
    ledger.record("anna", "m", {"input_tokens": 1}, mode="owner_key", usd=5.0)
    status, body = call(gate, p)
    assert status == 429 and up.requests == []
    w = ts.usd_window(book.get("anna"), ledger)
    at = datetime.fromtimestamp(w["resets_at"], ts.DAY_TZ)
    assert message(body) == ts._t("gate_limit_usd", spent=5.0, limit=5,
                                  day=ts._t("weekdays").split()[at.weekday()],
                                  at=at.strftime("%H:%M"))
    assert "5.00" in message(body)
    book.set_unlimited_until("anna", time.time() + 3600)       # "lift for now" works
    assert call(gate, p)[0] == 200


def test_usd_window_counts_this_week_on_the_owners_key(book, ledger):
    guest(book, mode="owner_key", usd_week=20)
    now = 2_000_000_000.0
    start, end = ts.week_bounds(now, ledger.week_reset())
    ledger.record("anna", "m", {}, ts_=start - 1, mode="owner_key", usd=100.0)   # last week
    ledger.record("anna", "m", {}, ts_=start + 5, mode="owner_key", usd=1.25)
    ledger.record("anna", "m", {}, ts_=start + 6, mode="own", usd=50.0)
    ledger.record("anna", "m", {}, ts_=start + 7, usd=50.0)
    w = ts.usd_window(book.get("anna"), ledger, now)
    assert w == {"used": pytest.approx(1.25), "limit": 20, "resets_at": int(end)}


def test_owner_key_ignores_the_token_limits_and_own_has_none(gate, up, book, keys, ledger):
    keys.set_owner(OWNER_KEY)
    keys.put("own", "api", GUEST_KEY)
    k = book.add("key", week_limit=7)
    book.set_claude("key", mode="owner_key")
    o = book.add("own", week_limit=7)
    book.set_claude("own", mode="own", usd_week=1)
    for name in ("key", "own"):
        ledger.record(name, "m", {"input_tokens": 10 ** 6})                    # tokens over
    ledger.record("own", "m", {}, mode="owner_key", usd=99.0)                 # dollars over
    assert ts.over_limit(book.get("key"), ledger) is None
    assert ts.over_limit(book.get("own"), ledger) is None
    assert call(gate, k)[0] == 200 and call(gate, o)[0] == 200


def test_eighty_percent_notice_once_across_three_requests(up, book, ledger, keys, told):
    keys.set_owner(OWNER_KEY)
    server = _serve(book=book, ledger=ledger, owner_token=lambda: OWNER, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    notify=told.append, log=lambda line: None, prices=lambda m, u: 4.0)
    try:
        p = guest(book, mode="owner_key", usd_week=10)
        assert call(server, p)[0] == 200                   # $4: under 80%
        assert wait_for(told, 0) == []
        assert call(server, p)[0] == 200                   # $8: the notice
        assert call(server, p)[0] == 200                   # $12: not again
        assert len(wait_for(told, 1)) == 1
        assert told[0] == ts._t("usd_notice", guest="anna", spent=8.0, limit=10)
    finally:
        server.shutdown()


def test_unreadable_price_table_refuses_owner_key(up, book, ledger, keys, told):
    keys.set_owner(OWNER_KEY)

    def broken(model, usage):
        raise ValueError("prices.json: the price table has no models")
    server = _serve(book=book, ledger=ledger, owner_token=lambda: OWNER, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    notify=told.append, log=lambda line: None, prices=broken)
    try:
        status, body = call(server, guest(book, mode="owner_key"))
        assert status == 503 and up.requests == []
        assert len(wait_for(told, 1)) == 1 and "price" in told[0]
        keys.put("own", "api", GUEST_KEY)                  # others don't need the table
        assert call(server, guest(book, "own", mode="own"))[0] == 200
        assert call(server, guest(book, "sub"))[0] == 200
    finally:
        server.shutdown()


# -- the week reset comes only from the owner's subscription ------------------------- #
def test_only_owner_sub_traffic_sets_the_week_reset(gate, up, book, keys, ledger):
    keys.set_owner(OWNER_KEY)
    keys.put("own", "oauth", GUEST_TOKEN)
    o = guest(book, "own", mode="own")
    k = guest(book, "key", mode="owner_key")
    up.headers = {ts.WEEK_RESET_HEADER: "1793818800"}
    assert call(gate, o)[0] == 200 and call(gate, k)[0] == 200
    assert ledger.week_reset() is None
    assert call(gate, guest(book, "sub"))[0] == 200
    assert ledger.week_reset() == 1793818800


# -- the subscription's token windows count only subscription traffic ---------------- #
def test_token_windows_count_only_owner_sub_rows(gate, book, ledger):
    p = book.add("anna", week_limit=700)                # 5 hours: 100
    now = time.time()
    ledger.record("anna", "m", {"input_tokens": 10 ** 6}, ts_=now - 60, mode="own")
    ledger.record("anna", "m", {"input_tokens": 10 ** 6}, ts_=now - 60, mode="owner_key", usd=3)
    assert ledger.spent_today("anna") == 0
    assert ledger.spent_since("anna", 0) == 0
    assert ledger.spent_between("anna", 0, now + 1) == 0
    assert ledger.five_hour_window("anna", now) is None
    w = ts.usage_windows(book.get("anna"), ledger, now)
    assert w["five_hour"]["used"] == 0 and w["seven_day"]["used"] == 0
    assert ts.over_limit(book.get("anna"), ledger, now) is None
    assert call(gate, p)[0] == 200
    ledger.record("anna", "m", {"input_tokens": 50}, ts_=now - 30)          # owner_sub
    assert ledger.spent_since("anna", 0) == 50 + ts.weighted(json.loads(RESP)["usage"])


# -- the hourly alert follows the mode ------------------------------------------------ #
def test_spend_watch_usd_alerts_over_a_quarter_of_the_week_in_an_hour(ledger):
    sent, now = [], [100_000.0]
    watch = ts.SpendWatch(ledger, notify=sent.append, clock=lambda: now[0], background=False,
                          log=lambda line: None)
    ledger.record("anna", "m", {}, ts_=now[0] - 4000, mode="owner_key", usd=100.0)  # older
    ledger.record("anna", "m", {}, ts_=now[0] - 60, mode="owner_key", usd=5.0)
    ledger.record("anna", "m", {"input_tokens": 10 ** 9}, ts_=now[0] - 60)     # owner_sub rows
    watch.check_usd("anna", 20)                       # $5 is not MORE than a quarter of $20
    assert sent == []
    ledger.record("anna", "m", {}, ts_=now[0], mode="owner_key", usd=0.01)
    watch.check_usd("anna", 20)
    watch.check_usd("anna", 20)
    assert sent == [ts._t("usd_spend_alert", guest="anna", hour=5.01, limit=20)]
    now[0] += 3601
    ledger.record("anna", "m", {}, ts_=now[0], mode="owner_key", usd=6.0)
    watch.check_usd("anna", 20)
    assert len(sent) == 2


def test_the_gate_alerts_by_mode(up, book, ledger, keys):
    keys.set_owner(OWNER_KEY)
    keys.put("own", "api", GUEST_KEY)
    seen = []

    class Spy:
        def check(self, name):
            seen.append(("tokens", name))

        def check_usd(self, name, usd_week):
            seen.append(("usd", name, usd_week))
    server = _serve(book=book, ledger=ledger, owner_token=lambda: OWNER, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    watch=Spy(), log=lambda line: None)
    try:
        for name, mode in (("sub", "owner_sub"), ("key", "owner_key"), ("own", "own")):
            assert call(server, guest(book, name, mode=mode, usd_week=7))[0] == 200
    finally:
        server.shutdown()
    assert seen == [("tokens", "sub"), ("usd", "key", 7)]


def test_a_slow_telegram_never_delays_the_guest(up, book, ledger, keys):
    def slow(text):
        time.sleep(3)
    server = _serve(book=book, ledger=ledger, owner_token=lambda: OWNER, keys=keys,
                    upstream=("http", "127.0.0.1", up.server.server_address[1]),
                    notify=slow, log=lambda line: None)
    try:
        p = guest(book, mode="owner_key")
        t0 = time.time()
        assert call(server, p)[0] == 503
        assert time.time() - t0 < 1.5
    finally:
        server.shutdown()


# -- serving: a server with only an API key starts ------------------------------------ #
def test_serve_starts_without_the_owners_login(tmp_path, monkeypatch):
    got = {}

    class Fake:
        def serve_forever(self):
            got["served"] = True

    def fake_make_server(*a, **kw):
        got["args"], got["kw"] = a, kw
        return Fake()
    monkeypatch.setenv("TURNSTILE_DATA", str(tmp_path))
    monkeypatch.setenv("TURNSTILE_OAUTH_ENV", str(tmp_path / "missing-oauth.env"))
    monkeypatch.setattr(ts, "make_server", fake_make_server)
    assert ts.main(["serve"]) == 0
    assert got["served"]
    assert isinstance(got["kw"]["keys"], claudekeys.KeyStore)
    assert got["kw"]["keys"].dir == tmp_path / "keys"


# -- a migration raced by another process ----------------------------------------------- #
def test_add_column_tolerates_a_column_another_process_added(tmp_path):
    import sqlite3
    db = sqlite3.connect(str(tmp_path / "x.db"))
    db.execute("CREATE TABLE usage (ts REAL)")
    ts._add_column(db, "usage", "mode TEXT DEFAULT 'owner_sub'")
    ts._add_column(db, "usage", "mode TEXT DEFAULT 'owner_sub'")       # no error
    assert [r[1] for r in db.execute("PRAGMA table_info(usage)")] == ["ts", "mode"]
    with pytest.raises(sqlite3.OperationalError):
        ts._add_column(db, "nope", "x INT")
