"""The guests module's tests never read the real guests/.env (a live server keeps its bot
token there) and never inherit the module's settings from the shell: every run starts
from a throwaway settings file that names only a test domain (texts in English, the
default; tests/test_texts.py covers other languages with a synthetic one)."""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

_KEYS = ("GUESTS_DOMAIN", "GUEST_BOT_TOKEN", "OWNER_TG_ID", "SERVER_IP", "CF_TOKEN_FILE",
         "CF_ZONE", "GUESTS_NOTIFY_CMD", "GUEST_IMAGE", "GUESTS_LANG", "GUESTS_LOCALE_DIR",
         "SITEGATE_SERVER_IP", "GUESTS_WEB", "GUESTS_FILES_PORT", "GUESTS_CADDY_ADMIN",
         "GUESTS_CERT_WAIT")
for _k in _KEYS:
    os.environ.pop(_k, None)
# the live terminal's own settings, under either name — but not the throwaway pane
# launcher tests/conftest.py set a moment ago (a run of both folders loads it first)
for _k in [k for k in os.environ if k.startswith(("AGENTKENNEL_", "AGENTDECK_"))
           and k != "AGENTKENNEL_LAUNCHER"]:
    del os.environ[_k]

_ENV = os.path.join(tempfile.mkdtemp(prefix="guests-env-"), ".env")
with open(_ENV, "w") as _f:
    _f.write("GUESTS_DOMAIN=example.test\n")
os.environ["GUESTS_ENV_FILE"] = _ENV
# never the owner's real bot ("Your bot": <AgentKennel>/.sessions/telegram.json): alerts in
# tests must have nowhere real to go
os.environ["AGENTKENNEL_TG_CONF"] = os.path.join(os.path.dirname(_ENV), "no-telegram.json")

# tmux finds its server by UID (/tmp/tmux-<uid>/), not by HOME: the tests' own tmux servers
# (test_rules_gate.py) and a bare `tmux` anywhere get a throwaway directory, never the live
# terminals' one. A run of both folders keeps the one tests/conftest.py made in this
# process (an inherited TMUX_TMPDIR — an xdist controller's — is not this process's own).
if "tests.conftest" not in sys.modules:
    _TMUX_DIR = tempfile.mkdtemp(prefix="pytest-tmux-")
    os.environ["TMUX_TMPDIR"] = _TMUX_DIR
    atexit.register(shutil.rmtree, _TMUX_DIR, ignore_errors=True)
os.environ.pop("TMUX", None)          # not "inside" the live server either


@pytest.fixture(autouse=True)
def _fresh_broken_block_throttle(monkeypatch):
    """claudekeys tells the owner about a guest's broken claude block once an hour, in a
    module-level dict: one test's notice must not use up a later test's (in any order or
    worker — random order found test_broken_block_guest_cannot_switch_own_or_back silent
    after test_broken_block_back_from_a_stored_key_changes_nothing)."""
    ck = sys.modules.get("claudekeys")
    if ck is not None and hasattr(ck, "_broken_told"):
        monkeypatch.setattr(ck, "_broken_told", {})
