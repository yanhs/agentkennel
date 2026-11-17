"""The guest image is the AgentKennel image of the same version: the core entrypoint runs the
services keeper, and nothing of the guests module (bot token, passes, usage, the guests'
public files) can end up inside a sandbox."""

from pathlib import Path

GUESTS = Path(__file__).resolve().parents[1]
REPO = GUESTS.parent


def code(path):
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_guest_image_builds_from_the_agentkennel_image():
    lines = code(GUESTS / "image" / "Dockerfile")
    assert lines[0].startswith("ARG BASE=agentkennel:")
    assert lines[1] == "FROM ${BASE}"


def test_guest_image_has_no_keeper_of_its_own():
    """keeper.py of the first version is superseded by the core services_keeper.py."""
    text = (GUESTS / "image" / "Dockerfile").read_text()
    assert "COPY" not in text and "ENTRYPOINT" not in text
    assert not (GUESTS / "image" / "keeper.py").exists()


def test_the_core_entrypoint_runs_the_services_keeper():
    assert "services_keeper.py" in (REPO / "docker" / "entrypoint.sh").read_text()
    assert (REPO / "services_keeper.py").exists()


def test_the_guests_module_stays_out_of_the_image():
    """COPY . /app would otherwise bake guests/.env (the bot token), the guest book and
    the guests' public files into every sandbox."""
    assert "guests" in code(REPO / ".dockerignore")


def test_the_guest_image_has_the_managed_settings_folder_from_the_start():
    """Measured 17.11.2026 (claude 2.1.282 and 2.1.326): a managed-settings.json created
    mid-session is read only if its folder existed when claude started; otherwise a guest's
    rules lock reaches no terminal that is already open."""
    assert "RUN mkdir -p /etc/claude-code" in code(GUESTS / "image" / "Dockerfile")
