"""Release numbers (vX.Y.Z) and what CHANGELOG.md says changed between two of them."""
from __future__ import annotations

import re
from typing import Optional

_TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
# "## v2.8.0 — a title" (an em dash, or a plain hyphen)
_HEAD = re.compile(r"^## v(\d+\.\d+\.\d+)(?:\s+[—-]\s*(.*))?\s*$", re.M)


def parse(v) -> Optional[tuple]:
    """(major, minor, patch) for "v2.7.2" / "2.7.2"; None for anything else (pre-releases too)."""
    m = _TAG.fullmatch((v or "").strip()) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def plain(v) -> Optional[str]:
    """"2.7.2" for "v2.7.2"; None when it is not a release number."""
    p = parse(v)
    return "%d.%d.%d" % p if p else None


def newer(a, b) -> bool:
    """a is a later release than b (False when either is not a release number)."""
    pa, pb = parse(a), parse(b)
    return bool(pa and pb and pa > pb)


def is_patch(current, new) -> bool:
    """new is a later patch release of the same major.minor (2.7.2 -> 2.7.3)."""
    pc, pn = parse(current), parse(new)
    return bool(pc and pn and pc[:2] == pn[:2] and pn[2] > pc[2])


def sections(text) -> list:
    """[{version, title, body}] for every "## vX.Y.Z — title" heading, in file order."""
    text = text or ""
    heads = list(_HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append({"version": m.group(1), "title": (m.group(2) or "").strip(),
                    "body": text[m.end():end].strip()})
    return out


def excerpt(text, current, latest, max_chars: int = 6000) -> list:
    """The sections after `current` up to and including `latest`, newest first, at most
    max_chars of body in all (the last one shown is cut with "…" and marked cut)."""
    picked = [s for s in sections(text)
              if newer(s["version"], current) and not newer(s["version"], latest)]
    picked.sort(key=lambda s: parse(s["version"]), reverse=True)
    out, left = [], max_chars
    for s in picked:
        if left <= 0:
            break
        if len(s["body"]) > left:
            s = {**s, "body": s["body"][:left].rstrip() + "…", "cut": True}
        left -= len(s["body"])
        out.append(s)
    return out
