"""What kind of install this is, and the git steps of an update.

An install from install.sh (or a clone of your own) is a git clone, usually on `master`:
an update moves that branch forward to the release tag (`git merge --ff-only`), so a
later `install.sh` run (`git pull --ff-only`) keeps working. A clone on a detached HEAD
moves detached. Before anything changes, preflight() refuses a clone with changed
tracked files or with commits that are not in the release (local work, a development
branch): those are yours, and an update would lose or tangle them."""
from __future__ import annotations

import os
import subprocess

OFF_WORDS = ("0", "off", "no", "false")


class GitError(Exception):
    pass


def _env_get(env, name):
    return env.get("AGENTKENNEL_" + name) or env.get("AGENTDECK_" + name) or ""


def kind(install_dir, env=None, docker=None):
    """"git" (one click), "docker" (the compose command is shown), "manual" (not a clone:
    the steps are shown) or "off" (AGENTKENNEL_UPDATES=0)."""
    env = os.environ if env is None else env
    if _env_get(env, "UPDATES").strip().lower() in OFF_WORDS:
        return "off"
    if docker is None:
        docker = os.path.exists("/.dockerenv")
    if docker:
        return "docker"
    return "git" if os.path.exists(os.path.join(install_dir, ".git")) else "manual"


def git(install_dir, *args, timeout=120):
    r = subprocess.run(["git", "-C", install_dir, *args], capture_output=True, text=True,
                       timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if r.returncode != 0:
        raise GitError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.rstrip()


def head(install_dir):
    return git(install_dir, "rev-parse", "HEAD")


def branch(install_dir):
    """The branch the clone is on, None when HEAD is detached."""
    try:
        return git(install_dir, "symbolic-ref", "-q", "--short", "HEAD") or None
    except GitError:
        return None


def fetch_tag(install_dir, url, tag):
    """Fetch one release tag from `url`; its commit."""
    git(install_dir, "fetch", "-q", "--no-tags", url, f"+refs/tags/{tag}:refs/tags/{tag}")
    return git(install_dir, "rev-parse", f"{tag}^{{commit}}")


def preflight(install_dir, target):
    """What stops moving this clone to commit `target` ([] = nothing)."""
    problems = []
    changed = git(install_dir, "status", "--porcelain", "--untracked-files=no")
    if changed:
        files = ", ".join(line[3:] for line in changed.splitlines()[:5])
        problems.append(f"files of AgentKennel were changed here ({files}): commit or undo "
                        "those changes, then update")
    r = subprocess.run(["git", "-C", install_dir, "merge-base", "--is-ancestor", "HEAD", target],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 1:
        problems.append("this install has commits that are not in the release (local work or "
                        "a development branch): update it with git yourself")
    elif r.returncode != 0:
        problems.append(f"git could not compare this install with the release: {r.stderr.strip()[:200]}")
    return problems


def move_to(install_dir, target):
    """The clone at `target`: its branch fast-forwarded, or a detached HEAD moved."""
    if branch(install_dir):
        git(install_dir, "merge", "-q", "--ff-only", target)
    else:
        git(install_dir, "checkout", "-q", "--detach", target)


def move_back(install_dir, old, old_branch):
    """Undo move_to: the branch (or detached HEAD) at `old` again."""
    if old_branch:
        if branch(install_dir) != old_branch:
            git(install_dir, "checkout", "-q", old_branch)
        git(install_dir, "reset", "-q", "--keep", old)
    else:
        git(install_dir, "checkout", "-q", "--detach", old)


def changed(install_dir, a, b, path):
    """`path` differs between commits a and b."""
    r = subprocess.run(["git", "-C", install_dir, "diff", "--quiet", a, b, "--", path],
                       capture_output=True, timeout=60)
    return r.returncode == 1
