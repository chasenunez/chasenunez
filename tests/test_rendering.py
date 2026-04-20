import re

from update_readme import (
    TABLE_COLS,
    _fmt_lifespan,
    filter_recently_active,
    render_repo_table,
)
from datetime import datetime, timezone


def test_fmt_lifespan_none():
    assert _fmt_lifespan(None) == "—"


def test_fmt_lifespan_zero_days():
    # Brand-new repo, first commit today.
    assert _fmt_lifespan(0) == "<1 d"


def test_fmt_lifespan_thousands_separator():
    assert _fmt_lifespan(1234) == "1,234 d"


def test_filter_recently_active_keeps_in_window_drops_out():
    now = datetime(2026, 4, 20, tzinfo=timezone.utc)
    repos = [
        {"name": "fresh", "pushed_at": "2026-04-10T00:00:00Z"},            # 10d ago
        {"name": "stale", "pushed_at": "2025-01-01T00:00:00Z"},            # ~475d ago
        {"name": "edge", "pushed_at": "2025-10-23T00:00:00Z"},             # ~179d ago (in)
        {"name": "nothing"},                                                # no timestamp
    ]
    out = [r["name"] for r in filter_recently_active(repos, now=now, window_days=180)]
    assert "fresh" in out
    assert "edge" in out
    assert "stale" not in out
    assert "nothing" not in out


def _sample_rows():
    return [
        {
            "name_text": "alpha",
            "name_url": "https://example/alpha",
            "language": "Python",
            "size": 12345,
            "commits": 128,
            "lifespan_days": 365,
            "team_size": 3,
        },
        {
            "name_text": "beta",
            "name_url": "",
            "language": "Go",
            "size": 200,
            "commits": 5,
            "lifespan_days": None,  # unknown first-commit date
            "team_size": 1,
        },
    ]


def test_repo_table_has_all_new_columns():
    out = render_repo_table(_sample_rows(), target_width=100)
    header = out.splitlines()[1]
    for col in TABLE_COLS:
        assert col in header
    # Legacy columns must be gone.
    assert "Last Commit Date" not in out
    assert "Branches" not in out


def test_repo_table_rows_render_new_metrics():
    out = render_repo_table(_sample_rows(), target_width=100)
    # Commits formatted with thousands separator.
    assert "128" in out
    # Lifespan formatted with "d" suffix.
    assert "365 d" in out
    # Unknown lifespan falls back to em-dash.
    assert "—" in out
    # Team size rendered.
    assert " 3 " in out and " 1 " in out


def test_repo_table_width_and_borders():
    out = render_repo_table(_sample_rows(), target_width=100)
    lines = out.splitlines()
    assert lines[0].startswith("╔") and lines[0].endswith("╗")
    assert lines[-1].startswith("╚") and lines[-1].endswith("╝")
    # Every line has the same visible width once HTML anchors are stripped.
    strip_html = lambda s: re.sub(r"<[^>]+>", "", s)
    widths = {len(strip_html(line)) for line in lines}
    assert len(widths) == 1, widths
    assert widths.pop() == 100


def test_repo_table_anchor_emitted_for_urls():
    out = render_repo_table(_sample_rows(), target_width=100)
    assert '<a href="https://example/alpha">' in out
    # Row without a url stays plain text.
    assert "beta" in out


def test_repo_table_empty_returns_empty_string():
    assert render_repo_table([]) == ""
