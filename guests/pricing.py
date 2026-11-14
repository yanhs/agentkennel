"""Dollars per request. The turnstile calls usd(model, usage) after each response, with
the `usage` object the API returned, and adds the result to the guest's spend.

The table is prices.json (USD per million tokens, copied from Anthropic's pricing page —
see its "source" and "updated"). Keys are family substrings of the model id; the longest
match wins ("opus-4-5" beats "opus-4"). A model with no match — or no model at all — is
charged the highest price for each kind of token in the table, tier prices included:
never undercount.

A row may carry "above": {"prompt_tokens": N, <the four prices>}. A request whose prompt
(input + cache-write + cache-read tokens) is over N pays the "above" prices for all of it."""
import json
import re
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "prices.json"
_FIELDS = (("input_tokens", "input"), ("output_tokens", "output"),
           ("cache_creation_input_tokens", "cache_write"),
           ("cache_read_input_tokens", "cache_read"))
_cache = {}  # path -> (mtime_ns, document)


def load(path=None):
    """The whole prices document, re-read only when the file's mtime changes."""
    p = Path(path) if path else DEFAULT_PATH
    mtime = p.stat().st_mtime_ns
    hit = _cache.get(p)
    if hit and hit[0] == mtime:
        return hit[1]
    doc = json.loads(p.read_text())
    if not doc.get("models"):
        raise ValueError(f"{p}: the price table has no models")
    _cache[p] = (mtime, doc)
    return doc


def _prompt_tokens(usage):
    return sum((usage.get(k) or 0) for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _matches(key, model):
    """The key must be a whole family name inside the id: it starts the id or follows a "-"
    or "/", and the id then ends, or carries a "[1m]"-style or "@date" suffix, or goes on
    with "-" and something that is not one more version number. So "sonnet-5" matches
    "claude-sonnet-5" and "claude-sonnet-5-20260101" but not "claude-sonnet-5-6" (a new
    model: unknown, charged the maximum)."""
    return re.search(r"(?:^|[-/])" + re.escape(key) + r"(?=$|[\[@]|-(?!\d{1,2}(?:$|[-\[@])))",
                     model) is not None


def _row(models, model):
    best = None
    if model:
        for key in models:
            if _matches(key, model) and (best is None or len(key) > len(best)):
                best = key
    if best is not None:
        return models[best]
    rows = [t for r in models.values() for t in (r, r.get("above") or r)]
    return {f: max((r[f] for r in rows), default=0) for _, f in _FIELDS}


def usd(model, usage, path=None):
    row = _row(load(path)["models"], model)
    usage = usage or {}
    above = row.get("above")
    if above and _prompt_tokens(usage) > above["prompt_tokens"]:
        row = above
    return sum((usage.get(u) or 0) * row[f] for u, f in _FIELDS) / 1_000_000
