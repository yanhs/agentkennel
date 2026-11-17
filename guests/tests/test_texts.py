"""The language of the guests module. English is built in (texts.EN) and is the default;
any other language is a local file <locale dir>/<code>.json picked by GUESTS_LANG — not
part of AgentKennel. These tests use a synthetic language, "xx" (pseudo_locale.py)."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import texts  # noqa: E402
import text_scenarios as sc  # noqa: E402
import pseudo_locale as px  # noqa: E402

GUESTS = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).resolve().parent / "golden_en.json"
USER_INPUT = ("Masha", "so what?")          # what the people in the scenarios typed
EXPORT = GUESTS / "scripts" / "export_locale.py"


def use(monkeypatch, tmp_path, lang=None, locale=True):
    texts._warned.clear()
    return px.use(monkeypatch, tmp_path, texts.EN, lang, locale)


def normalised(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# which language
# --------------------------------------------------------------------------- #
def test_english_is_the_default(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path)
    assert texts.lang() == "en"
    assert texts.t("btn_allow") == "✅ Let in"


def test_a_language_with_a_file_is_picked(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "xx")
    assert texts.lang() == "xx"
    assert texts.t("btn_allow") == px.pseudo("✅ Let in")
    assert texts.t("ago_h_min", h=1, m=2) == px.pseudo("{h} h {m} min").format(h=1, m=2)


@pytest.mark.parametrize("value,expect", [("xx", "xx"), ("XX", "xx"), (" xx ", "xx"),
                                          (" en ", "en"), ("de", "en"), ("", "en"),
                                          ("../xx", "en"), ("x/x", "en"), ("xx.json", "en")])
def test_the_setting_picks_the_table(monkeypatch, tmp_path, value, expect, capsys):
    folder = use(monkeypatch, tmp_path)
    settings = {"GUESTS_LANG": value, "GUESTS_LOCALE_DIR": str(folder)}
    assert texts.lang(settings) == expect


def test_the_locale_folder_defaults_to_guests_locale_which_git_ignores():
    assert texts.locale_dir({}) == GUESTS / "locale"
    r = subprocess.run(["git", "check-ignore", "-q", str(GUESTS / "locale" / "ru.json")],
                       cwd=GUESTS)
    assert r.returncode == 0, "guests/locale/ must be git-ignored: a language pack is local"


def test_the_locale_folder_can_be_set_in_the_environment(monkeypatch, tmp_path):
    folder = use(monkeypatch, tmp_path, "xx")
    env = tmp_path / ".env"
    env.write_text("GUESTS_LANG=xx\n")                      # no folder in the file ...
    monkeypatch.setenv("GUESTS_LOCALE_DIR", str(folder))   # ... but in the environment
    assert texts.lang() == "xx"


# --------------------------------------------------------------------------- #
# a language without its file, or a broken one: English, said once — never a crash
# --------------------------------------------------------------------------- #
def test_a_language_without_its_file_falls_back_to_english_and_says_so_once(
        monkeypatch, tmp_path, capsys):
    folder = use(monkeypatch, tmp_path, "xx", locale=False)
    assert texts.lang() == "en"
    assert texts.t("btn_allow") == "✅ Let in"
    err = capsys.readouterr().err
    assert "GUESTS_LANG=xx" in err and str(folder / "xx.json") in err and "English" in err
    texts.t("btn_allow")
    assert capsys.readouterr().err == ""                    # not on every text
    assert "xx.json" in texts.problem() and "English" in texts.problem()


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"text"', ""])
def test_a_broken_file_falls_back_to_english(monkeypatch, tmp_path, capsys, content):
    folder = use(monkeypatch, tmp_path, "xx", locale=False)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "xx.json").write_text(content)
    assert texts.lang() == "en" and texts.t("btn_allow") == "✅ Let in"
    assert "xx.json" in capsys.readouterr().err
    assert texts.problem()


def test_a_missing_or_unusable_key_is_said_in_english(monkeypatch, tmp_path, capsys):
    folder = use(monkeypatch, tmp_path, "xx", locale=False)
    data = px.table(texts.EN)
    del data["btn_allow"]
    data["btn_deny"] = 7                                  # not text
    data["gate_bad_pass"] = "{nobody_passes_this} !"        # a field English doesn't have
    data["gate_disabled"] = "{unclosed"                     # not a template at all
    data["_about"] = "a note for whoever edits the file"    # keys with _ are notes
    data["no_such_key"] = "an old text the module no longer uses"
    folder.mkdir(parents=True)
    (folder / "xx.json").write_text(json.dumps(data, ensure_ascii=False))
    assert texts.lang() == "xx"
    assert texts.t("btn_allow") == texts.EN["btn_allow"]
    assert texts.t("btn_deny") == texts.EN["btn_deny"]
    assert texts.t("gate_bad_pass") == texts.EN["gate_bad_pass"]
    assert texts.t("gate_disabled") == texts.EN["gate_disabled"]
    assert texts.t("gate_path") == px.pseudo(texts.EN["gate_path"])
    err = capsys.readouterr().err
    for key in ("btn_allow", "btn_deny", "gate_bad_pass", "gate_disabled"):
        assert key in err
    assert "_about" not in err and "no_such_key" not in err


def test_a_changed_file_applies_without_a_restart(monkeypatch, tmp_path):
    folder = use(monkeypatch, tmp_path, "xx")
    assert texts.t("btn_allow") == px.pseudo("✅ Let in")
    px.write(folder, texts.EN, btn_allow="✅ !!")
    assert texts.t("btn_allow") == "✅ !!"


def test_english_needs_no_file_and_has_no_problem(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "en", locale=False)
    assert texts.lang() == "en" and texts.problem() == ""


# --------------------------------------------------------------------------- #
# the doctor names a missing language pack
# --------------------------------------------------------------------------- #
def _docker_ok(*a, **k):
    return type("R", (), {"returncode": 0, "stdout": "27\n", "stderr": ""})()


def _doctor():
    import guestctl as gc
    return gc.doctor(run=_docker_ok,
                     get_me=lambda t: "b", resolve=lambda h: [], detect_ip=lambda: "",
                     cf_zone=lambda d: None, is_active=lambda u: True)


def test_doctor_says_when_the_language_pack_is_missing(monkeypatch, tmp_path):
    folder = use(monkeypatch, tmp_path, "xx", locale=False)
    lang = {c["id"]: c for c in _doctor()}["language"]
    assert not lang["ok"] and str(folder / "xx.json") in lang["detail"] + lang["fix"]
    assert "English" in lang["detail"]


def test_doctor_with_the_language_pack_in_place(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "xx")
    lang = {c["id"]: c for c in _doctor()}["language"]
    assert lang["ok"] and "xx" in lang["detail"]


def test_doctor_leaves_english_alone(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path)
    assert "language" not in [c["id"] for c in _doctor()]


# --------------------------------------------------------------------------- #
# what people read, through the real code paths
# --------------------------------------------------------------------------- #
def test_every_english_template_is_a_template():
    for key, value in texts.EN.items():
        assert isinstance(value, str) and value, key
        texts.fields(value)                                 # parses


def test_english_reproduces_the_recorded_texts_byte_for_byte(monkeypatch, tmp_path):
    """golden_en.json: everything the scenarios show, in English. A wording change is made
    on purpose: regenerate with `python3 guests/tests/text_scenarios.py > golden_en.json`."""
    use(monkeypatch, tmp_path, "en")
    assert normalised(sc.scenarios()) == json.loads(GOLDEN.read_text(encoding="utf-8"))


WORD = re.compile(r"[A-Za-z]{4,}")
# what the scenarios put into the texts as data, not wording: names, addresses, service
# names, error strings the code passes through, Telegram's own field names
DATA = {"anna", "masha", "boot", "crash", "many",              # guests, services
        "docker", "space", "left", "certbot", "failed",          # errors passed through
        "sendMessage", "editMessageText", "answerCallbackQuery", "setMyCommands",
        "chat", "type", "text", "inline", "keyboard", "callback", "data",   # Telegram's API
        "announce", "decide", "tell"}                            # the scenarios' own labels


def _words(obj) -> set:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("description", "commands"):     # the sandbox's own menu: English
                out |= _words(v)                         # on purpose (tgrelay.web_command)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _words(v)
    elif isinstance(obj, str):
        text = obj
        for typed in USER_INPUT:
            text = text.replace(typed, "")
        text = re.sub(r"https?://\S+|\S+\.example\.test\S*|/\w+|`[^`]*`|\b\w+:\w+:?\w*\b"
                      r"|<!--.*?-->", " ", text)
        out |= {w for w in WORD.findall(text)}
    return out


def test_the_language_reaches_everywhere_a_person_reads(monkeypatch, tmp_path):
    """In the synthetic language no English word is left: everything a guest or the owner
    reads goes through the language table."""
    use(monkeypatch, tmp_path, "xx")
    leaked = {w for w in _words(sc.scenarios()) if w.lower() not in {d.lower() for d in DATA}}
    assert not leaked, sorted(leaked)


def test_english_bot_samples(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "en")
    out = sc.scenarios()
    assert out["buttons"]["inline_keyboard"][0][0]["text"] == "✅ Let in"
    assert out["web_welcome"].startswith("Hello! This is your sandbox with Claude.")
    assert "<site>-<your name>.example.test" in out["web_welcome"]
    assert out["services_full"][0].startswith("Sandbox services (2 of 7 running):")
    assert out["seed_blocks"][2].startswith("\n<!-- sbx-services -->\n## Services")


def test_turnstile_refusals_follow_the_language(monkeypatch, tmp_path):
    import turnstile as ts
    use(monkeypatch, tmp_path, "xx")
    assert texts.t("gate_limit_5h", spent=5, limit=3, at="14:35") == \
        px.pseudo(texts.EN["gate_limit_5h"]).format(spent=5, limit=3, at="14:35")
    # the week resets with the owner's subscription (02.11.2026): the day and hour are said
    wed = texts.t("weekdays").split()[2]
    assert wed == px.pseudo("Wednesday")
    assert texts.t("gate_limit_week", spent=5, limit=3, day=wed, at="11:00") == \
        px.pseudo(texts.EN["gate_limit_week"]).format(spent=5, limit=3, day=wed, at="11:00")
    gates = ("gate_bad_pass", "gate_disabled", "gate_path", "gate_upstream")
    assert [texts.t(k) for k in gates] == [px.pseudo(texts.EN[k]) for k in gates]
    use(monkeypatch, tmp_path, "en")
    five = texts.t("gate_limit_5h", spent=5, limit=3, at="14:35")
    assert "5-hour limit used up (5 of 3)" in five and "14:35" in five
    week = texts.t("gate_limit_week", spent=5, limit=3, day=texts.t("weekdays").split()[2], at="11:00")
    assert week == "Weekly limit used up (5 of 3). It resets on Wednesday at 11:00, Los Angeles time."
    assert "gate_limit" not in texts.EN          # no daily limit any more (30.10.2026)
    assert ts.texts is texts


def test_the_list_lines_show_both_limits_in_english(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "en")
    ts_line = texts.t("ts_list_line", name="maria", state="on", five_used=1, five_limit=2,
                      week_used=3, week_limit=14, week_pct=0.01, lifted="", tg=7)
    assert "5h 1 of 2 · week 3 of 14" in ts_line
    ctl_line = texts.t("ctl_list_line", name="maria", tg=7, port=18101, state="running", on="on",
                       five_used=1, five_limit=2, week_used=3, week_limit=14)
    assert "5h 1 of 2 · week 3 of 14" in ctl_line


def test_the_wall_check_labels_follow_the_language(monkeypatch, tmp_path):
    import guestctl as gc
    use(monkeypatch, tmp_path, "xx")
    xx = gc.check_targets()
    assert [label for label, _, _ in xx] == [px.pseudo(s) for s in (
        "Claude turnstile", "Telegram turnstile", "server's ssh", "server's nginx",
        "internet (example.com:443)")]
    use(monkeypatch, tmp_path, "en")
    assert gc.check_targets()[0][0] == "Claude turnstile"
    assert [t[1:] for t in gc.check_targets()] == [t[1:] for t in xx]


# --------------------------------------------------------------------------- #
# export: a language table as a file (a template to translate, or a pack to hand over)
# --------------------------------------------------------------------------- #
def _export(*args, cwd=None, env_file=None):
    import os
    env = dict(os.environ)
    if env_file:
        env["GUESTS_ENV_FILE"] = str(env_file)
    return subprocess.run([sys.executable, str(EXPORT), *args], capture_output=True, text=True,
                          cwd=cwd, env=env, timeout=60)


def test_export_english_is_the_template_to_translate(tmp_path):
    out = tmp_path / "en.json"
    r = _export("en", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == texts.EN


def test_export_a_language_from_its_file(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "xx")
    r = _export("xx", env_file=tmp_path / ".env")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == px.table(texts.EN)


def test_export_a_language_built_into_an_older_version(tmp_path):
    """Before 2.1 a second language lived inside texts.py (TEXTS = {"en": EN, <code>: ...});
    --from-rev takes it out of that version, so a server that used it gets its file."""
    repo = tmp_path / "repo"
    (repo / "guests").mkdir(parents=True)
    (repo / "guests" / "texts.py").write_text(
        "import guestenv\nEN = {'a': 'Hello {name}', 'b': 'Bye'}\n"
        "XX = {'a': 'Ｈｅｌｌｏ {name}', 'b': 'Ｂｙｅ'}\nTEXTS = {'en': EN, 'xx': XX}\n",
        encoding="utf-8")
    for argv in (["init", "-q"], ["add", "guests/texts.py"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "x"]):
        subprocess.run(["git", *argv], cwd=repo, check=True)
    r = _export("xx", "--from-rev", "HEAD", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"a": "Ｈｅｌｌｏ {name}", "b": "Ｂｙｅ"}
    bad = _export("zz", "--from-rev", "HEAD", cwd=repo)
    assert bad.returncode != 0 and "zz" in bad.stderr


def test_export_refuses_a_language_with_no_file(monkeypatch, tmp_path):
    use(monkeypatch, tmp_path, "xx", locale=False)
    r = _export("xx", env_file=tmp_path / ".env")
    assert r.returncode != 0 and "xx.json" in r.stderr
