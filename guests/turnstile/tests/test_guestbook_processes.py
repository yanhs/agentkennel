"""guests.json has three writer processes (the turnstile, the Telegram relay, guestctl):
every write is a locked read-modify-write of the file on disk, nothing is lost and no
writer trips over another's temporary file."""

import json
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import turnstile as ts  # noqa: E402

N = 300


def _limits(path, name, n, q):
    book, errors = ts.GuestBook(path), []
    for i in range(n):
        try:
            book.set_limit(name, 1000 + i)
        except Exception as exc:          # noqa: BLE001 — counted, the test shows them
            errors.append(repr(exc))
    q.put((name, errors))


def _usd(path, name, n, q):
    book, errors = ts.GuestBook(path), []
    for i in range(n):
        try:
            book.set_claude(name, usd_week=1 + i)
        except Exception as exc:          # noqa: BLE001
            errors.append(repr(exc))
    q.put((name + ":usd", errors))


def _run(jobs):
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=fn, args=(*args, q)) for fn, args in jobs]
    for p in procs:
        p.start()
    results = [q.get(timeout=120) for _ in procs]
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    return dict(results)


def test_two_processes_writing_different_guests_lose_nothing(tmp_path):
    path = tmp_path / "guests.json"
    book = ts.GuestBook(path)
    book.add("aa", 10)
    book.add("bb", 10)
    assert book.get("aa").week_limit == 10            # this process has the file cached
    errors = _run([(_limits, (path, "aa", N)), (_limits, (path, "bb", N))])
    assert errors == {"aa": [], "bb": []}
    data = json.loads(path.read_text())["guests"]
    assert data["aa"]["week_limit"] == data["bb"]["week_limit"] == 1000 + N - 1
    # a reader in another process sees the other writers' last change
    assert book.get("aa").week_limit == book.get("bb").week_limit == 1000 + N - 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["guests.json", "guests.json.lock"]


def test_set_claude_racing_set_limit_on_one_guest_keeps_both(tmp_path):
    path = tmp_path / "guests.json"
    ts.GuestBook(path).add("aa", 10, claude={"mode": "owner_key"})
    errors = _run([(_limits, (path, "aa", N)), (_usd, (path, "aa", N))])
    assert errors == {"aa": [], "aa:usd": []}
    rec = json.loads(path.read_text())["guests"]["aa"]
    assert rec["week_limit"] == 1000 + N - 1
    assert rec["claude"]["usd_week"] == N and rec["claude"]["mode"] == "owner_key"


def test_the_book_and_its_lock_are_private(tmp_path):
    path = tmp_path / "guests.json"
    ts.GuestBook(path).add("aa", 10)
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "guests.json.lock").stat().st_mode & 0o777 == 0o600


def test_a_failed_change_writes_nothing_and_the_next_read_is_from_disk(tmp_path):
    path = tmp_path / "guests.json"
    book = ts.GuestBook(path)
    book.add("aa", 10, claude={"mode": "owner_key"})
    before = path.read_bytes()
    try:
        book.set_claude("aa", usd_week=0)
    except ValueError:
        pass
    assert path.read_bytes() == before
    assert book.get("aa").claude["usd_week"] == 20
