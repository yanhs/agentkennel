#!/usr/bin/env python3
"""Write a language table of the guests module as a language file (guests/texts.py).

    python3 guests/scripts/export_locale.py en [-o FILE]
        the built-in English table: the template to translate into a new language
    python3 guests/scripts/export_locale.py CODE [-o FILE]
        CODE as this server uses it (its file under GUESTS_LOCALE_DIR / guests/locale/,
        with English for any key the file lacks)
    python3 guests/scripts/export_locale.py CODE --from-rev REV [-o FILE]
        a language that was built into guests/texts.py at git revision REV (before 2.1 a
        second table lived in the module itself): run inside the checkout that has REV

The file is a JSON object {key: template}; put it at guests/locale/CODE.json and set
GUESTS_LANG=CODE in guests/.env. Without -o it goes to stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import types
from pathlib import Path

GUESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GUESTS))
import texts  # noqa: E402


def from_rev(code: str, rev: str) -> dict:
    """TEXTS[code] of guests/texts.py at REV (git in the current folder)."""
    src = subprocess.run(["git", "show", f"{rev}:guests/texts.py"], capture_output=True,
                         text=True)
    if src.returncode != 0:
        raise SystemExit(f"git show {rev}:guests/texts.py failed: {src.stderr.strip()}")
    old = types.ModuleType("texts_at_rev")
    old.__file__ = str(GUESTS / "texts.py")
    exec(compile(src.stdout, f"{rev}:guests/texts.py", "exec"), old.__dict__)  # noqa: S102
    table = getattr(old, "TEXTS", {}).get(code)
    if not isinstance(table, dict):
        known = ", ".join(sorted(getattr(old, "TEXTS", {}))) or "none"
        raise SystemExit(f"no language {code!r} in guests/texts.py at {rev} (it has: {known})")
    return dict(table)


def current(code: str) -> dict:
    code = code.strip().lower()
    if code == texts.DEFAULT:
        return dict(texts.EN)
    settings = dict(texts.guestenv.load(), GUESTS_LANG=code)
    if texts.lang(settings) != code:
        raise SystemExit(texts.problem(settings) or f"no language file for {code}")
    _, table, _ = texts._pick(settings)
    return {key: table.get(key, english) for key, english in texts.EN.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="export_locale.py", description=__doc__.split("\n")[0])
    ap.add_argument("code", help="language code: en, or one with a file / at --from-rev")
    ap.add_argument("--from-rev", metavar="REV", help="take it from guests/texts.py at REV")
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    a = ap.parse_args(argv)
    table = from_rev(a.code, a.from_rev) if a.from_rev else current(a.code)
    out = json.dumps(table, ensure_ascii=False, indent=1) + "\n"
    if a.output:
        Path(a.output).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
