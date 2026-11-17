"""What AgentKennel publishes is English only (owner, 17.11.2026: "what people download from
the internet — no Russian anywhere"). Every file in git is checked for Cyrillic letters, in
its text and in its name. A language of your own for the guests module is a local file
(guests/locale/<code>.json, git-ignored; guests/texts.py) and never comes here.

A test that needs non-Latin input (a keyboard layout, Unicode handling) uses another script
— Greek — and code that DETECTS Cyrillic spells the range with escapes ("\\u0400-\\u04ff").
Binary files (images, video) are not read: a screenshot is checked when it is made
(tests/make_screenshots.py refuses Cyrillic on the page)."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile("[\u0400-\u052f\u1c80-\u1c8f\u2de0-\u2dff\ua640-\ua69f]")
# path -> why Cyrillic is allowed there; keep it empty unless there is no other way
ALLOWED: dict = {}


def tracked() -> list:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("not a git checkout")
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def cyrillic_lines(data: bytes) -> list:
    """[(line number, line)] with a Cyrillic letter; [] for a binary file."""
    if b"\0" in data[:8192]:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return [(n, line.strip()[:120]) for n, line in enumerate(text.splitlines(), 1)
            if CYRILLIC.search(line)]


HELLO = "\u041f\u0440\u0438\u0432\u0435\u0442"      # "hello" in Russian, spelled with escapes


def test_the_check_finds_cyrillic_and_lets_other_scripts_through():
    assert cyrillic_lines(f"x = '{HELLO}'\n".encode()) == [(1, f"x = '{HELLO}'")]
    assert cyrillic_lines("Ukrainian \u0457, \u0454\n".encode())
    assert cyrillic_lines("Γεια σου, ω, 'é', ✅ — «»\n".encode()) == []
    assert cyrillic_lines("[\\u0400-\\u04ff]\n".encode()) == []       # spelled with escapes
    assert cyrillic_lines(b"\x89PNG\r\n\x1a\n\0\0" + HELLO.encode()) == []   # binary


def test_no_cyrillic_in_any_tracked_file():
    found = {}
    for rel in tracked():
        path = ROOT / rel
        if rel in ALLOWED or not path.is_file():
            continue
        if CYRILLIC.search(rel):
            found[rel] = [(0, "the file name")]
            continue
        lines = cyrillic_lines(path.read_bytes())
        if lines:
            found[rel] = lines[:3]
    assert not found, "Cyrillic in published files (translate it; a language pack goes to " \
                      f"guests/locale/, not git): {found}"


def test_the_allowlist_names_only_files_that_exist():
    files = set(tracked())
    assert [p for p in ALLOWED if p not in files] == []
