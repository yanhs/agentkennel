"""A synthetic language for the tests: "xx", the English table with every letter outside the
{fields} turned into its full-width twin ("Let in" -> "Ｌｅｔ ｉｎ"). It reads like no real
language, so a test can tell at a glance which words came through the language table and
which were left in English by the code. Not a test module itself (no test_ prefix)."""

import json
import string
from pathlib import Path

CODE = "xx"


def _wide(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalpha() else c for c in text)


def pseudo(template: str) -> str:
    """The template in the synthetic language: same {fields}, same format specs."""
    out = []
    for literal, name, spec, conv in string.Formatter().parse(template):
        out.append(_wide(literal).replace("{", "{{").replace("}", "}}"))
        if name is not None:
            out.append("{" + name + (f"!{conv}" if conv else "") + (f":{spec}" if spec else "") + "}")
    return "".join(out)


def table(english: dict) -> dict:
    return {key: pseudo(value) for key, value in english.items()}


def write(folder: Path, english: dict, code: str = CODE, **overrides) -> Path:
    """<folder>/<code>.json with the synthetic table (+ overrides); returns the file."""
    folder.mkdir(parents=True, exist_ok=True)
    data = table(english)
    data.update(overrides)
    path = folder / f"{code}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def use(monkeypatch, tmp_path: Path, english: dict, lang=None, locale=True) -> Path:
    """Point the guests module at a throwaway .env (GUESTS_LANG=lang) and a locale folder
    holding the synthetic language (unless locale=False). Returns the locale folder."""
    folder = tmp_path / "locale"
    if locale:
        write(folder, english)
    env = tmp_path / ".env"
    env.write_text("GUESTS_DOMAIN=example.test\n" + (f"GUESTS_LANG={lang}\n" if lang else "")
                   + f"GUESTS_LOCALE_DIR={folder}\n")
    monkeypatch.setenv("GUESTS_ENV_FILE", str(env))
    return folder
