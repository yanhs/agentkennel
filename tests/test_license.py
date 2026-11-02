"""AGPL-3.0-only from v2.2.5 (owner's decision, 02.11.2026, before the launch): one licence,
said the same way everywhere. Versions up to v2.2.4 were MIT and stay MIT."""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPDX = "AGPL-3.0-only"
# sha256 of https://www.gnu.org/licenses/agpl-3.0.txt, verbatim: GitHub recognises it
AGPL_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"


def test_license_file_is_the_agpl_verbatim():
    data = (ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(data).hexdigest() == AGPL_SHA256


def test_image_label_says_agpl():
    wf = (ROOT / ".github" / "workflows" / "image.yml").read_text()
    assert f"org.opencontainers.image.licenses={SPDX}" in wf
    assert "licenses=MIT" not in wf


def test_readme_says_agpl_and_mit_only_as_history():
    md = (ROOT / "README.md").read_text()
    assert "License-AGPL" in md and "](LICENSE)" in md
    mits = [m.start() for m in re.finditer(r"\bMIT\b", md)]
    assert len(mits) == 1, "MIT only in the note on old versions"
    assert "v2.2.4" in md[mits[0] - 200:mits[0] + 200]


def test_contributing_says_how_contributions_are_licensed():
    c = (ROOT / "CONTRIBUTING.md").read_text()
    assert SPDX in c
    assert "other terms" in c          # the maintainer may also license contributions otherwise


def test_changelog_says_when_it_changed():
    log = (ROOT / "CHANGELOG.md").read_text()
    top = log[log.index("\n## v"):][:4000]
    assert SPDX in top and "v2.2.4" in top
