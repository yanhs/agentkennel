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


def test_building_the_checkout_leaves_the_release_tags_alone():
    # `up -d --build` tags what it builds with the compose file's image name; under
    # :latest that hid the published image from the next `docker compose up -d` (31.10).
    text = SCRIPT.read_text()
    assert 'AGENTKENNEL_VERSION="e2e-build-' in text
    assert "docker rmi" in text


# HTTPS by default (owner, 31.10.2026: "должно быть https"): `docker compose up -d` serves
# https://<ip>:8765 with a certificate the container makes for itself — encrypted, the
# browser warns once — the same fallback install.sh uses. ./https.sh still gets a trusted
# certificate; AGENTKENNEL_SITE=:8765 is plain http, behind a proxy of your own.

def test_compose_serves_https_by_default():
    text = (REPO / "docker-compose.yml").read_text()
    assert 'AGENTKENNEL_SITE: "${AGENTKENNEL_SITE:-${AGENTDECK_SITE:-https://:8765}}"' in text


def test_the_readme_opens_the_docker_install_over_https():
    text = (REPO / "README.md").read_text()
    assert "http://<your-vps-ip>:8765" not in text
    assert "https://<your-vps-ip>:8765" in text


def test_the_end_to_end_test_goes_over_https():
    text = SCRIPT.read_text()
    assert 'URL="https://127.0.0.1:$AGENTKENNEL_PORT"' in text
    assert text.count("--insecure") >= 2
