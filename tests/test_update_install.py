"""The git side of an update: what kind of install this is, the checks before anything
changes, moving to the release tag and back. All on a made-up upstream (update_fixtures)."""
import pytest

from tests.update_fixtures import git, install_at, upstream
from updater import install as I


@pytest.fixture
def setup(tmp_path):
    bare, src = upstream(tmp_path)
    d = install_at(tmp_path, bare, "v2.7.2")
    return bare, src, d


def test_kind(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    assert I.kind(str(tmp_path), env={}, docker=False) == "git"
    assert I.kind(str(tmp_path / "nogit"), env={}, docker=False) == "manual"
    assert I.kind(str(tmp_path), env={}, docker=True) == "docker"
    assert I.kind(str(tmp_path), env={"AGENTKENNEL_UPDATES": "0"}, docker=False) == "off"
    assert I.kind(str(tmp_path), env={"AGENTDECK_UPDATES": "off"}, docker=False) == "off"
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: /elsewhere\n")          # a worktree
    assert I.kind(str(tmp_path / "wt"), env={}, docker=False) == "git"


def test_fetch_gives_the_tags_commit(setup):
    bare, src, d = setup
    sha = I.fetch_tag(str(d), str(bare), "v2.8.0")
    assert sha == git(src, "rev-parse", "v2.8.0^{commit}")


def test_a_clean_install_behind_the_release_passes_the_checks(setup):
    bare, _, d = setup
    sha = I.fetch_tag(str(d), str(bare), "v2.8.0")
    assert I.preflight(str(d), sha) == []


def test_changed_tracked_files_stop_the_update(setup):
    bare, _, d = setup
    sha = I.fetch_tag(str(d), str(bare), "v2.8.0")
    (d / "CHANGELOG.md").write_text("mine\n")
    (d / "untracked.txt").write_text("fine\n")          # untracked files are left alone
    problems = I.preflight(str(d), sha)
    assert len(problems) == 1 and "CHANGELOG.md" in problems[0]


def test_local_commits_stop_the_update(setup):
    bare, _, d = setup
    (d / "mine.txt").write_text("x\n")
    git(d, "add", "mine.txt")
    git(d, "commit", "-q", "-m", "mine")
    sha = I.fetch_tag(str(d), str(bare), "v2.8.0")
    problems = I.preflight(str(d), sha)
    assert problems and "not in the release" in problems[0]


def test_move_to_a_tag_keeps_the_branch_and_back_restores_it(setup):
    bare, src, d = setup
    old = I.head(str(d))
    sha = I.fetch_tag(str(d), str(bare), "v2.8.0")
    assert I.branch(str(d)) == "master"
    I.move_to(str(d), sha)
    assert I.head(str(d)) == sha and I.branch(str(d)) == "master"
    assert '"2.8.0"' in (d / "web" / "version.json").read_text()
    I.move_back(str(d), old, "master")
    assert I.head(str(d)) == old and I.branch(str(d)) == "master"
    assert '"2.7.2"' in (d / "web" / "version.json").read_text()


def test_a_detached_install_moves_detached(setup):
    bare, _, d = setup
    git(d, "checkout", "-q", "--detach", "v2.7.2")
    old = I.head(str(d))
    sha = I.fetch_tag(str(d), str(bare), "v2.7.3")
    assert I.branch(str(d)) is None
    I.move_to(str(d), sha)
    assert I.head(str(d)) == sha and I.branch(str(d)) is None
    I.move_back(str(d), old, None)
    assert I.head(str(d)) == old


def test_changed_between_versions(setup):
    bare, src, d = setup
    a = git(src, "rev-parse", "v2.7.2^{commit}")
    I.fetch_tag(str(d), str(bare), "v2.8.0")
    b = git(src, "rev-parse", "v2.8.0^{commit}")
    assert I.changed(str(d), a, b, "CHANGELOG.md")
    assert not I.changed(str(d), a, b, "install.sh")
