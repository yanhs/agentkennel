"""The dashboard's source for tests that read it as text.

Since 19.11.2026 web/index.html holds the markup only; its styles are web/css/panel.css and
its code web/js/*.js (in the page's order). A test that looks for a rule, a function or a
string "in the page" reads all of it, in the order the browser gets it."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
_ASSET = re.compile(r'<(?:link|script)\b[^>]*?\b(?:href|src)="/((?:css|js)/[^"?]+)(?:\?[^"]*)?"')


def panel_assets(page="index.html"):
    """[Path] of the CSS/JS files the page links, in order."""
    return [WEB / rel for rel in _ASSET.findall((WEB / page).read_text(encoding="utf-8"))]


def panel_source(page="index.html"):
    """The page's HTML followed by every CSS/JS file it links, in order."""
    parts = [(WEB / page).read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in panel_assets(page)]
    return "\n".join(parts)
