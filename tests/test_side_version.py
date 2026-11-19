"""Owner 19.11.2026: the panel shows no version anywhere. A small line at the bottom of the sidebar:
'AgentKennel v<version> · Site · GitHub'. The version comes from web/version.json, which must match
the newest CHANGELOG heading, so a release cannot forget it."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def newest_changelog_version():
    m = re.search(r"^## v(\d+\.\d+\.\d+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert m, "no '## vX.Y.Z' heading in CHANGELOG.md"
    return m.group(1)


def test_version_json_matches_the_changelog():
    v = json.loads((WEB / "version.json").read_text())["version"]
    assert v == newest_changelog_version()


def test_sidebar_has_the_version_line_with_site_and_github_links():
    for page in ("index.html", "index-lib.html"):
        html = (WEB / page).read_text()
        foot = re.search(r'<div class="side-foot"[^>]*>([\s\S]*?)</div>', html)
        assert foot, f"{page}: no side-foot"
        body = foot.group(1)
        assert 'id="akVer"' in body
        assert 'href="https://agentkennel.com"' in body
        assert 'href="https://github.com/yanhs/agentkennel"' in body
        assert "fetch('/version.json'" in html or 'fetch("/version.json"' in html
