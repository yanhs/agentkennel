"""Dollars per request: the price table (prices.json) and pricing.usd()."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pricing  # noqa: E402

OUT = 1.0
ALL = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
       "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}


def _write(tmp_path, models, name="prices.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"updated": "2026-01-01", "source": "test", "models": models}))
    return p


@pytest.fixture
def prices_file(tmp_path):
    return _write(tmp_path, {"m-1": {"input": 1, "output": 2, "cache_write": 3, "cache_read": 4}})


@pytest.fixture
def prices_file2(tmp_path):
    return _write(tmp_path, {
        "cheap": {"input": 1 * OUT, "output": 1 * OUT, "cache_write": 1 * OUT, "cache_read": 1 * OUT},
        "dear": {"input": 10 * OUT, "output": 10 * OUT, "cache_write": 10 * OUT, "cache_read": 10 * OUT},
    })


def test_usd_counts_every_kind_of_token(prices_file):
    assert pricing.usd("claude-m-1-2026", ALL, path=prices_file) == pytest.approx(1 + 2 + 3 + 4)


def test_an_unknown_model_costs_the_most_expensive_price(prices_file2):
    assert pricing.usd("claude-new-thing", {"output_tokens": 1_000_000},
                       path=prices_file2) == pytest.approx(10 * OUT)


def test_a_missing_model_is_unknown_too(prices_file2):
    assert pricing.usd(None, {"input_tokens": 1_000_000}, path=prices_file2) == pytest.approx(10)


def test_unknown_takes_the_max_of_each_field_separately(tmp_path):
    p = _write(tmp_path, {"a": {"input": 9, "output": 1, "cache_write": 1, "cache_read": 1},
                          "b": {"input": 1, "output": 8, "cache_write": 1, "cache_read": 1}})
    assert pricing.usd("zzz", {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
                       path=p) == pytest.approx(9 + 8)


def test_missing_usage_fields_count_as_zero(prices_file):
    assert pricing.usd("m-1", {}, path=prices_file) == 0
    assert pricing.usd("m-1", {"output_tokens": 500_000, "input_tokens": None},
                       path=prices_file) == pytest.approx(1.0)


def test_the_longest_matching_key_wins(tmp_path):
    p = _write(tmp_path, {"opus-4": {"input": 15, "output": 0, "cache_write": 0, "cache_read": 0},
                          "opus-4-5": {"input": 5, "output": 0, "cache_write": 0, "cache_read": 0}})
    u = {"input_tokens": 1_000_000}
    assert pricing.usd("claude-opus-4-5-20251101", u, path=p) == pytest.approx(5)
    assert pricing.usd("claude-opus-4-20250514", u, path=p) == pytest.approx(15)


def test_the_table_is_reloaded_when_the_file_changes(tmp_path):
    p = _write(tmp_path, {"m": {"input": 1, "output": 0, "cache_write": 0, "cache_read": 0}})
    u = {"input_tokens": 1_000_000}
    assert pricing.usd("m", u, path=p) == pytest.approx(1)
    _write(tmp_path, {"m": {"input": 2, "output": 0, "cache_write": 0, "cache_read": 0}})
    import os
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert pricing.usd("m", u, path=p) == pytest.approx(2)


def test_load_returns_the_whole_document(prices_file):
    doc = pricing.load(prices_file)
    assert doc["models"]["m-1"]["output"] == 2


def test_the_shipped_table_prices_the_models_claude_code_uses():
    for m in ("claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
              "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
              "claude-fable-5-2", "claude-opus-4-5-20251101"):
        assert pricing.usd(m, {"input_tokens": 1000}) > 0


def test_the_shipped_table_names_its_source_and_date():
    doc = pricing.load()
    assert doc["source"].startswith("https://")
    assert doc["updated"] == "2026-11-14"
    for fam, row in doc["models"].items():
        assert set(row) == {"input", "output", "cache_write", "cache_read"}, fam
        assert all(isinstance(v, (int, float)) and v >= 0 for v in row.values()), fam
