"""A guest's rules (owner, 12.11.2026: Vlad — only the website). With web access off the
agent is the guest's only hand; the turnstile puts the owner's rules into every Claude
request of that guest, so nothing inside the sandbox can skip them. Strange or illegal
asks are reported to the owner through POST /flag."""

import http.client
import json
import threading

import pytest

from test_turnstile import FakeUpstream, OWNER  # noqa: F401  (same fake Anthropic)
import turnstile as ts

SITE = {"on": True, "work": "The website angelsrepair.com: its pages, texts, photos, prices.",
        "folders": ["/work/angels-help"],
        "actions": {"edit": True, "restart": True, "packages": True,
                    "publish": False, "services": False},
        "flag": True}


@pytest.fixture
def upstream():
    up = FakeUpstream()
    yield up
    up.close()


@pytest.fixture
def book(tmp_path):
    return ts.GuestBook(tmp_path / "guests.json")


@pytest.fixture
def told():
    return []


@pytest.fixture
def gate(upstream, book, tmp_path, told):
    server = ts.make_server("127.0.0.1", 0, book=book, ledger=ts.Ledger(tmp_path / "u.db"),
                            owner_token=lambda: OWNER,
                            upstream=("http", "127.0.0.1", upstream.port),
                            notify=told.append)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def post(gate, path, body, secret):
    conn = http.client.HTTPConnection("127.0.0.1", gate.server_address[1], timeout=10)
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    conn.request("POST", path, body=data, headers={"authorization": f"Bearer {secret}"})
    r = conn.getresponse()
    return r.status, r.read()


def vlad(book, web=False, rules=SITE, enabled=True):
    secret = book.add("vlad", week_limit=10**9)
    book.set_web("vlad", web)
    if rules is not None:
        book.set_rules("vlad", rules)
    book.set_enabled("vlad", enabled)
    return secret


def sent_system(upstream):
    return json.loads(upstream.requests[-1]["body"]).get("system")


# ── the book ──────────────────────────────────────────────────────────────────
def test_rules_are_kept_in_the_book(book):
    vlad(book)
    assert book.get("vlad").rules == SITE


def test_a_guest_without_rules_has_none(book):
    book.add("anna", week_limit=10)
    assert book.get("anna").rules == {}


@pytest.mark.parametrize("bad", [
    {"on": True, "work": ""},                                      # nothing to keep to
    {"on": True, "work": "x" * 1001},
    {"on": True, "work": "site", "folders": ["/etc"]},             # only under /work
    {"on": True, "work": "site", "folders": ["/work/../etc"]},
    {"on": True, "work": "site", "actions": {"rm_rf": True}},
    {"on": "yes", "work": "site"},
])
def test_bad_rules_are_refused(book, bad):
    book.add("vlad", week_limit=10)
    with pytest.raises(ValueError):
        book.set_rules("vlad", bad)


def test_rules_apply_only_with_web_off_and_rules_on(book):
    vlad(book)
    assert ts.rules_active(book.get("vlad"))
    book.set_web("vlad", True)
    assert not ts.rules_active(book.get("vlad"))           # kept, not applied
    book.set_web("vlad", False)
    book.set_rules("vlad", dict(SITE, on=False))
    assert not ts.rules_active(book.get("vlad"))


def test_the_rules_text_says_the_work_the_folders_and_what_is_off():
    text = ts.rules_prompt("vlad", SITE)
    assert "angelsrepair.com" in text and "/work/angels-help" in text
    assert "decline" in text.lower()
    assert "publish" in text.lower()                        # named as not allowed
    assert "/flag" in text and "$ANTHROPIC_AUTH_TOKEN" in text


def test_without_flag_the_rules_dont_mention_reporting():
    assert "/flag" not in ts.rules_prompt("vlad", dict(SITE, flag=False))


# ── every request carries them ────────────────────────────────────────────────
@pytest.mark.parametrize("system", [
    None,
    "You are Claude Code.",
    [{"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}}],
])
def test_the_rules_go_into_every_request_whatever_its_system(gate, upstream, book, system):
    secret = vlad(book)
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    if system is not None:
        body["system"] = system
    status, _ = post(gate, "/v1/messages?beta=true", body, secret)
    assert status == 200
    got = sent_system(upstream)
    assert isinstance(got, list)
    assert "angelsrepair.com" in got[-1]["text"] and "cache_control" not in got[-1]
    if isinstance(system, list):                            # the cached part untouched
        assert got[0] == system[0]
    elif system:
        assert got[0]["text"] == system
    assert json.loads(upstream.requests[-1]["body"])["messages"] == body["messages"]


def test_no_rules_no_change(gate, upstream, book):
    secret = book.add("anna", week_limit=10**9)
    body = json.dumps({"model": "m", "system": "S", "messages": []}).encode()
    post(gate, "/v1/messages", body, secret)
    assert upstream.requests[-1]["body"] == body             # byte for byte


def test_web_on_no_change(gate, upstream, book):
    secret = vlad(book, web=True)
    post(gate, "/v1/messages", {"model": "m", "system": "S", "messages": []}, secret)
    assert sent_system(upstream) == "S"


def test_a_body_that_isnt_json_is_refused_not_passed_without_rules(gate, upstream, book):
    secret = vlad(book)
    status, _ = post(gate, "/v1/messages", b"not json", secret)
    assert status == 400 and upstream.requests == []


# ── strange or illegal: the owner hears ───────────────────────────────────────
def test_flag_tells_the_owner(gate, book, told):
    secret = vlad(book)
    status, _ = post(gate, "/flag", {"text": "find a database of competitors' phones"}, secret)
    assert status == 200
    [msg] = told
    assert "vlad" in msg and "competitors' phones" in msg


def test_flag_is_only_for_guests_with_reporting_on(gate, book, told):
    secret = vlad(book, rules=dict(SITE, flag=False))
    status, _ = post(gate, "/flag", {"text": "x"}, secret)
    assert status == 403 and told == []


def test_flag_needs_a_pass(gate, told):
    status, _ = post(gate, "/flag", {"text": "x"}, "sbx-nope")
    assert status == 401 and told == []


def test_flag_is_capped_per_hour(gate, book, told):
    secret = vlad(book)
    codes = [post(gate, "/flag", {"text": f"n{i}"}, secret)[0] for i in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    assert len(told) == 5


def test_a_long_flag_is_cut(gate, book, told):
    secret = vlad(book)
    post(gate, "/flag", {"text": "y" * 5000}, secret)
    assert len(told[0]) < 800
