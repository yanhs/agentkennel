"""A made-up AgentKennel upstream for the update tests: a bare repo with release tags, and an
install cloned from it at an older release. Nothing here touches the real checkout."""
import json
import os
import subprocess
from pathlib import Path

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "PATH": "/usr/local/bin:/usr/bin:/bin"}


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       env={**GIT_ENV, "HOME": str(cwd)})
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def changelog(versions):
    return "# Changelog\n\n" + "".join(f"## v{v} — release {v}\n\n- Change in {v}.\n\n"
                                       for v in reversed(versions))


def commit_release(src: Path, v, released, extra=None):
    (src / "web").mkdir(exist_ok=True)
    (src / "web" / "version.json").write_text(json.dumps({"version": v}))
    (src / "CHANGELOG.md").write_text(changelog(released))
    for name, text in (extra or {}).items():
        p = src / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", f"release {v}")
    git(src, "tag", f"v{v}")


def upstream(tmp: Path, releases=("2.7.2", "2.7.3", "2.8.0"), base=None):
    """(bare, src): a bare repo with one commit and tag per release. `base`: a folder whose
    files go into the first commit (the real code, for the end-to-end test)."""
    src = tmp / "upstream-src"
    src.mkdir()
    git(src, "init", "-q", "-b", "master")
    if base:
        subprocess.run(["cp", "-a", f"{base}/.", str(src)], check=True)
    done = []
    for v in releases:
        done.append(v)
        commit_release(src, v, done)
    bare = tmp / "upstream.git"
    git(tmp, "clone", "-q", "--bare", str(src), str(bare))
    return bare, src


def install_at(tmp: Path, bare: Path, tag, name="install"):
    """A clone of `bare` on master, moved back to `tag` (an install of that release)."""
    d = tmp / name
    git(tmp, "clone", "-q", str(bare), str(d))
    git(d, "reset", "-q", "--hard", tag)
    return d
