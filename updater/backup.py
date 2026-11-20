"""A copy of the install before an update: <update dir>/backups/<time>-v<version>/

    code.tar.gz   every file git tracks (as it is on disk), without docs/ (pictures)
    config/       the settings an update may change: the install's .env and agents.json,
                  ~/.claude/settings.json and ~/.claude/CLAUDE.md (the guard hooks write them)
    meta.json     the version, commit and branch it was taken at; which settings were absent

Never the user's data: .sessions/ (terminals, board, password), the work folder.
A rollback moves git back to the commit and puts config/ back (restore_config); the code
archive is there for a restore by hand: tar -xzf code.tar.gz -C <install>."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

KEEP = 3
INSTALL_FILES = (".env", "agents.json")
HOME_FILES = (".claude/settings.json", ".claude/CLAUDE.md")


def _config_files(install_dir, home):
    for rel in INSTALL_FILES:
        yield f"install/{rel}", Path(install_dir) / rel
    for rel in HOME_FILES:
        yield f"home/{rel}", Path(home) / rel


def _tracked(install_dir):
    out = subprocess.run(["git", "-C", install_dir, "ls-files", "-z"], capture_output=True,
                         check=True, timeout=60).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0")
            if p and not p.startswith("docs/")]


def make(install_dir, home, root, version, head, branch, now=None):
    now = time.time() if now is None else now
    root = Path(root)
    dest = root / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-v{version}"
    n = 1
    while dest.exists():
        n += 1
        dest = root / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{n}-v{version}"
    (dest / "config").mkdir(parents=True)
    os.chmod(root, 0o700)
    with tarfile.open(dest / "code.tar.gz", "w:gz") as t:
        for rel in _tracked(install_dir):
            p = Path(install_dir) / rel
            if p.is_file() or p.is_symlink():
                t.add(p, arcname=rel, recursive=False)
    absent = []
    for name, src in _config_files(install_dir, home):
        if src.is_file():
            (dest / "config" / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / "config" / name)
        else:
            absent.append(name)
    (dest / "meta.json").write_text(json.dumps(
        {"version": version, "head": head, "branch": branch, "created": now,
         "install": str(install_dir), "absent": absent}, indent=1))
    prune(root)
    return dest


def restore_config(backup_dir, install_dir, home):
    """The settings as they were: copied back, and the ones that did not exist removed."""
    backup_dir = Path(backup_dir)
    meta = json.loads((backup_dir / "meta.json").read_text())
    for name, dst in _config_files(install_dir, home):
        src = backup_dir / "config" / name
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif name in meta.get("absent", []) and dst.is_file():
            dst.unlink()


def prune(root, keep=KEEP):
    dirs = sorted((p for p in Path(root).iterdir() if p.is_dir() and (p / "meta.json").exists()),
                  key=lambda p: json.loads((p / "meta.json").read_text()).get("created", 0))
    for old in dirs[:-keep] if keep else dirs:
        shutil.rmtree(old, ignore_errors=True)
