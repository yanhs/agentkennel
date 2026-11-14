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
