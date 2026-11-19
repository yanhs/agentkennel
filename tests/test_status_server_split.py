"""status_server is split by concern into backend/ (owner 19.11.2026: "isn't it sensible to
split such big files?"), and stays the one entry point and namespace:

- `python3 status_server.py` (the systemd unit, start.sh, the Docker entrypoint) and its flags;
- `import status_server` gives every name the one file had (tests, tg_bridge.py, guests/);
- setting a name on it (monkeypatch) reaches the code in backend/, as it did in one file;
- importing it afresh reads the settings from the environment again;
- the paths it derives are the checkout's, not backend/'s."""
import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
# the last commit with status_server.py in one file
ONE_FILE = "33e8054"


def fresh():
    sys.modules.pop("status_server", None)
    return importlib.import_module("status_server")


def top_level_names(src):
    out = set()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.update((a.asname or a.name).split(".")[0] for a in node.names)
    return out


def test_files_are_small():
    assert len((ROOT / "status_server.py").read_text().splitlines()) < 200
    for p in BACKEND.glob("*.py"):
        assert len(p.read_text().splitlines()) < 700, p.name


def test_every_name_of_the_one_file_is_still_there():
    r = subprocess.run(["git", "show", f"{ONE_FILE}:status_server.py"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"no {ONE_FILE} in this clone (shallow?)")
    ss = fresh()
    missing = sorted(n for n in top_level_names(r.stdout) if not hasattr(ss, n))
    assert not missing, missing


def test_every_backend_name_is_re_exported():
    ss = fresh()
    for m in ss._PARTS:
        for name in vars(m):
            if not name.startswith("__"):
                assert getattr(ss, name) is getattr(m, name), (m.__name__, name)


def test_a_name_set_on_status_server_reaches_the_backend_code(monkeypatch, tmp_path):
    ss = fresh()
    from backend import telegram, http_api
    conf = str(tmp_path / "telegram.json")
    monkeypatch.setattr(ss, "TG_CONF", conf)
    assert telegram.TG_CONF == conf
    (tmp_path / "telegram.json").write_text('{"token": "1:x"}')
    assert ss.tg_load()["token"] == "1:x"                     # the function sees it
    # a function replaced on the facade is the one other modules call
    monkeypatch.setattr(ss, "tg_render", lambda msg="": "FAKE")
    assert http_api.tg_render() == "FAKE" and telegram.tg_render() == "FAKE"
    monkeypatch.undo()
    assert telegram.TG_CONF == str(ROOT / ".sessions" / "telegram.json")
    assert http_api.tg_render is telegram.tg_render is ss.tg_render


def test_a_fresh_import_reads_the_environment_again(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKENNEL_UPLOAD_DIR", str(tmp_path / "a"))
    first = fresh()
    assert first.UPLOAD_DIR == str(tmp_path / "a")
    monkeypatch.setenv("AGENTKENNEL_UPLOAD_DIR", str(tmp_path / "b"))
    second = fresh()
    assert second.UPLOAD_DIR == str(tmp_path / "b")
    assert first.UPLOAD_DIR == str(tmp_path / "a")             # the old one keeps its own


def test_paths_are_the_checkouts():
    ss = fresh()
    assert ss.WEB_DIR == str(ROOT / "web")
    assert ss.TG_SCRIPT == str(ROOT / "tg_bridge.py")
    assert ss.LIBRARY_CLI == str(ROOT / "library_cli.py")
    assert ss.AGENTS_FILE == str(ROOT / "agents.json")
    assert ss.GUESTS_DEFAULT_DIR == ROOT.resolve() / "guests"
    for p in BACKEND.glob("*.py"):
        if p.name != "__init__.py":
            assert "__file__" not in p.read_text(), p.name


def test_the_entry_point_still_runs_from_anywhere(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTKENNEL_", "AGENTDECK_"))}
    env["AGENTKENNEL_PASSFILE"] = str(tmp_path / "pass")
    env["AGENTKENNEL_AUTH_SECRET"] = str(tmp_path / "secret")
    env["AGENTKENNEL_HTPASSWD"] = str(tmp_path / "none")       # no legacy nginx login here
    r = subprocess.run([sys.executable, str(ROOT / "status_server.py"), "--setup-link"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("/login?setup="), (r.stdout, r.stderr)
