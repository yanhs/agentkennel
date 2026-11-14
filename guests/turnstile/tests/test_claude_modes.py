"""A guest's `claude` settings: whose credential its Claude runs on."""

import json

import pytest

import turnstile as ts

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
    book._update("anna", claude={"mode": "bogus", "usd_week": -3})
    assert book.get("anna").claude == OLD_DEFAULT


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
    book.set_unlimited_until("anna", 123.0)
    before = _raw(book)["anna"]
    book.set_claude("anna", mode="owner_key", usd_week=5)
    after = _raw(book)["anna"]
    for k, v in before.items():
        assert after[k] == v
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
