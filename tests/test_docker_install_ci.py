"""The Docker way of installing is tested like install.sh (owner, 31.10.2026: "is the Docker
install tested too?" — until then GitHub only built the image, nobody ran it).

tests/install/docker_e2e.sh follows the README: `docker compose up -d`, the one-time setup
link from the logs, the first login, a new terminal, a restart that keeps the password.
GitHub runs it on every push against the checkout (`--build`), and after a release against
the image it has just pushed to ghcr.io.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests" / "install" / "docker_e2e.sh"
INSTALL = REPO / ".github" / "workflows" / "install.yml"
IMAGE = REPO / ".github" / "workflows" / "image.yml"


def test_the_script_follows_the_readme():
    text = SCRIPT.read_text()
    assert "docker compose" in text and "up -d" in text
    assert "/login?setup=" in text                        # the link from `logs | grep setup`
    assert "smoke.py" in text and "--first-run" in text and "--new-terminal" in text
    assert "restart" in text                              # the password survives a restart
    assert "down -v" in text                              # leaves nothing behind
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_it_tests_the_checkout_or_a_published_version():
    text = SCRIPT.read_text()
    assert "--build" in text                              # the checkout
    assert "AGENTKENNEL_VERSION" in text and "pull" in text   # a released image


def test_every_push_runs_it_on_the_checkout():
    text = INSTALL.read_text()
    assert "tests/install/docker_e2e.sh build" in text


def test_a_release_runs_it_on_the_image_it_pushed():
    text = IMAGE.read_text()
    assert "tests/install/docker_e2e.sh ${{ steps.v.outputs.version }}" in text
