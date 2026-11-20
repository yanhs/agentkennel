"""Before an update changes anything: a copy of the code and of the settings the update may
change (the guard hooks' entries in ~/.claude), with a time stamp. Not the user's data
(.sessions: terminals, board, password). A rollback puts the settings back."""
import json
import tarfile

from tests.update_fixtures import install_at, upstream
from updater import backup as B
from updater import state as S


def test_state_files_are_private_and_written_whole(tmp_path):
    p = tmp_path / "u" / "job.json"
    S.write(p, {"a": 1})
    assert S.read(p, {}) == {"a": 1}
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert S.read(tmp_path / "missing.json", {"d": 1}) == {"d": 1}
    (tmp_path / "bad.json").write_text("{nope")
    assert S.read(tmp_path / "bad.json", None) is None
    assert not list((tmp_path / "u").glob("*.tmp*"))


def test_state_lives_in_the_installs_sessions_folder(tmp_path):
    assert S.update_dir(str(tmp_path), env={}) == tmp_path / ".sessions" / "update"
    assert S.update_dir(str(tmp_path), env={"AGENTKENNEL_UPDATE_DIR": "/x/y"}).as_posix() == "/x/y"


def make_install(tmp_path):
    bare, _ = upstream(tmp_path)
    d = install_at(tmp_path, bare, "v2.7.2")
    (d / ".sessions").mkdir()
    (d / ".sessions" / "library.json").write_text("{}")          # user data: not in a backup
    (d / ".env").write_text("AGENTKENNEL_ORIGIN=https://x\n")
    (d / "untracked.txt").write_text("not ours\n")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"hooks": "old"}')
    return d, home


def test_a_backup_has_the_code_and_the_settings_not_the_data(tmp_path):
    d, home = make_install(tmp_path)
    root = tmp_path / "backups"
    b = B.make(str(d), str(home), root, version="2.7.2", head="abc", branch="master", now=1700000000)
    assert b.parent == root and b.name.endswith("-v2.7.2")
    with tarfile.open(b / "code.tar.gz") as t:
        names = set(t.getnames())
    assert "web/version.json" in names and "CHANGELOG.md" in names
    assert not any(n.startswith(".sessions") or n == "untracked.txt" or n.startswith(".git")
                   for n in names)
    assert (b / "config" / "install" / ".env").read_text() == "AGENTKENNEL_ORIGIN=https://x\n"
    assert (b / "config" / "home" / ".claude" / "settings.json").read_text() == '{"hooks": "old"}'
    meta = json.loads((b / "meta.json").read_text())
    assert meta["version"] == "2.7.2" and meta["head"] == "abc" and meta["branch"] == "master"
    assert "home/.claude/CLAUDE.md" in meta["absent"]


def test_restore_puts_the_settings_back_and_removes_what_the_update_added(tmp_path):
    d, home = make_install(tmp_path)
    b = B.make(str(d), str(home), tmp_path / "backups", version="2.7.2", head="abc",
               branch="master", now=1700000000)
    (home / ".claude" / "settings.json").write_text('{"hooks": "new"}')
    (home / ".claude" / "CLAUDE.md").write_text("added by the update\n")
    (d / ".env").write_text("changed\n")
    B.restore_config(b, str(d), str(home))
    assert (home / ".claude" / "settings.json").read_text() == '{"hooks": "old"}'
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert (d / ".env").read_text() == "AGENTKENNEL_ORIGIN=https://x\n"


def test_only_the_newest_backups_are_kept(tmp_path):
    d, home = make_install(tmp_path)
    root = tmp_path / "backups"
    made = [B.make(str(d), str(home), root, version="2.7.2", head="abc", branch="master",
                   now=1700000000 + i * 60) for i in range(5)]
    left = sorted(p.name for p in root.iterdir())
    assert left == sorted(p.name for p in made[-B.KEEP:])
