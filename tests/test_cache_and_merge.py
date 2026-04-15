import json
import os

from update_readme import (
    WEEKS_PER_REPO,
    load_cache,
    merge_weekly_with_cache,
    save_cache,
)


def test_cache_roundtrip(tmp_path):
    path = str(tmp_path / "cache.json")
    data = {"alpha": [1, 2, 3], "beta": [0, 0, 0]}
    save_cache(data, path)
    assert json.load(open(path)) == data
    assert load_cache(path) == data


def test_cache_missing_file_returns_empty(tmp_path):
    assert load_cache(str(tmp_path / "nope.json")) == {}


def test_cache_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{ not json")
    assert load_cache(str(path)) == {}


def test_merge_prefers_fresh_when_it_has_signal():
    fresh = [0] * (WEEKS_PER_REPO - 1) + [5]
    cached = [1] * WEEKS_PER_REPO
    cache = {"alpha": cached}
    assert merge_weekly_with_cache("alpha", fresh, cache) == fresh


def test_merge_falls_back_to_cache_when_fresh_empty():
    cached = [1] * WEEKS_PER_REPO
    cache = {"alpha": cached}
    assert merge_weekly_with_cache("alpha", None, cache) == cached
    assert merge_weekly_with_cache("alpha", [0] * WEEKS_PER_REPO, cache) == cached


def test_merge_returns_zeros_when_nothing_known():
    out = merge_weekly_with_cache("ghost", None, {})
    assert len(out) == WEEKS_PER_REPO
    assert all(v == 0 for v in out)


def test_merge_ignores_cache_with_wrong_length():
    cache = {"alpha": [1, 2, 3]}  # wrong length
    fresh = None
    out = merge_weekly_with_cache("alpha", fresh, cache)
    assert len(out) == WEEKS_PER_REPO
