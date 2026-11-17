"""The script install's Caddy carries the guests' addresses too (guests/webchoice.py):
the Caddyfile imports every <host>.caddy block from <AgentKennel>/.sessions/caddy-sites at
its top level, through AGENTKENNEL_CADDY_SITES (set by agentkennel-caddy.service), with a
default that imports nothing, so start.sh / Docker / an old unit load the same file as
before. The real caddy (PATH or CADDY_BIN) adapts it both ways when it is around."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_install_sh import Box, render, ROOT  # noqa: E402  (the sandboxed install.sh)

IMPORT = 'import "{$AGENTKENNEL_CADDY_SITES:/dev/null}"'


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def test_caddyfile_imports_the_guests_sites_at_the_top_level():
    lines = (ROOT / "Caddyfile").read_text().splitlines()
    assert IMPORT in lines                     # a line of its own, not indented: top level
    # after the global options block, outside every site block
    depth, at = 0, None
    for i, line in enumerate(lines):
        code = line.split("#", 1)[0]
        if line == IMPORT:
            at = depth
        depth += code.count("{") - code.count("}")       # {$VAR} placeholders balance
    assert at == 0


def test_caddy_unit_points_at_the_sites_folder(box):
    u = render(box, "agentkennel-caddy.service", AGENTKENNEL_SITE="203-0-113-7.sslip.io")
    assert ("Environment=AGENTKENNEL_CADDY_SITES=/home/tester/agentkennel/.sessions/"
            "caddy-sites/*.caddy") in u


def test_install_makes_the_sites_folder():
    text = (ROOT / "install.sh").read_text()
    body = text[text.index("install_services() {"):]
    body = body[:body.index("\n}\n")]
    assert any(line.strip().startswith("mkdir -p ") and '"$DIR/.sessions/caddy-sites"' in line
               for line in body.splitlines())


def caddy_bin():
    return os.environ.get("CADDY_BIN") or shutil.which("caddy")


@pytest.mark.skipif(not caddy_bin(), reason="no caddy binary (PATH or CADDY_BIN)")
@pytest.mark.parametrize("sites", ["unset", "empty", "one"])
def test_real_caddy_adapts_the_caddyfile(tmp_path, sites):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "AGENTKENNEL_SITE": "203-0-113-7.sslip.io"}
    folder = tmp_path / "caddy-sites"
    if sites != "unset":
        env["AGENTKENNEL_CADDY_SITES"] = f"{folder}/*.caddy"
    if sites == "one":
        folder.mkdir()
        (folder / "sb-anna.example.test.caddy").write_text(
            "sb-anna.example.test {\n\treverse_proxy 127.0.0.1:18101\n}\n")
    r = subprocess.run([caddy_bin(), "adapt", "--config", str(ROOT / "Caddyfile"),
                        "--adapter", "caddyfile"], capture_output=True, text=True,
                       env=env, cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    assert '"203-0-113-7.sslip.io"' in r.stdout
    assert ('"sb-anna.example.test"' in r.stdout) == (sites == "one")
