"""The README's pictures (owner, 18.11.2026: "the task board is shown twice and the guests'
Rules have no screenshot"). Each picture exists, is light enough for GitHub, has a real alt
text, and appears once; the guests' Rules are shown on the Server page, in the guest's own
web terminal and in your Telegram."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MAX_BYTES = 300 * 1024


def images():
    md = README.read_text()
    found = [(alt, src) for alt, src in re.findall(r"!\[([^\]]*)\]\(([^)\s]+)\)", md)]
    found += [(alt, src) for src, alt in re.findall(r'<img\s+src="([^"]+)"[^>]*?alt="([^"]*)"', md)]
    return [(alt, src) for alt, src in found if not src.startswith("http")]


def test_every_picture_exists_and_is_light():
    for _, src in images():
        path = ROOT / src
        assert path.is_file(), src
        if path.suffix == ".png":
            assert path.stat().st_size < MAX_BYTES, f"{src}: {path.stat().st_size // 1024} KB"


def test_every_picture_says_what_it_shows():
    for alt, src in images():
        assert len(alt.split()) >= 5, f"{src}: alt text {alt!r} is too short"


def test_no_picture_twice_and_one_task_board():
    srcs = [src for _, src in images()]
    assert len(srcs) == len(set(srcs)), "a picture is shown twice"
    boards = [s for s in srcs if Path(s).name in ("tasks-tab.png", "tasks-filtered.png",
                                                  "task-board.png", "task-board-dark.png",
                                                  "light-tasks.png")]
    assert len(boards) == 1, boards


def test_the_guests_rules_are_shown_where_they_hold():
    srcs = {Path(s).name for _, s in images()}
    assert {"guests-rules.png", "guest-terminal-locked.png", "telegram-rules.png"} <= srcs


def test_no_stale_alt_text():
    for alt, src in images():
        if src.endswith("guests-rules.png"):
            assert "Telegram only" not in alt     # alex has web on with rules on since v2.7
