from datetime import date, timedelta

from update_readme import (
    SHADES,
    WEEKS_PER_REPO,
    render_language_bar,
    render_per_repo_grid,
    render_repo_table,
    render_weekday_histogram,
    shade_for,
)


def _synthetic_days(n: int = 365, pattern=lambda i: i % 7):
    """Return ``n`` (date, count) tuples ending today."""
    today = date.today()
    return [(today - timedelta(days=n - 1 - i), pattern(i)) for i in range(n)]


def test_shade_for_bounds():
    assert shade_for(0, 10) == SHADES[0]
    assert shade_for(10, 10) == SHADES[-1]
    assert shade_for(5, 10) in SHADES[1:]
    # Zero max_value should never divide by zero.
    assert shade_for(5, 0) == SHADES[0]


def test_shade_for_never_index_zero_for_positive_counts():
    # Any positive value should use at least the lowest non-empty shade.
    assert shade_for(1, 1_000_000) == SHADES[1]


def test_weekday_histogram_shape():
    days = _synthetic_days(14, lambda i: 1)  # every day has 1 contribution
    out = render_weekday_histogram(days)
    lines = out.splitlines()
    assert len(lines) == 7
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for line, name in zip(lines, names):
        assert line.startswith(name)


def test_weekday_histogram_aggregates():
    # Two mondays only.
    monday = date(2026, 4, 13)  # verified: weekday() == 0
    assert monday.weekday() == 0
    days = [(monday, 3), (monday + timedelta(days=7), 4)]
    out = render_weekday_histogram(days)
    mon_line = out.splitlines()[0]
    assert mon_line.endswith("7")


def test_per_repo_grid_has_one_row_per_repo_plus_axis():
    weekly = {
        "alpha": [1] * WEEKS_PER_REPO,
        "beta": [0] * (WEEKS_PER_REPO - 1) + [9],
    }
    out = render_per_repo_grid(weekly, ["alpha", "beta"])
    lines = out.splitlines()
    assert len(lines) == 3  # 2 repos + axis
    assert "alpha" in lines[0]
    assert "beta" in lines[1]


def test_language_bar_percentages_sum_to_full_width():
    stats = {"Python": (80, 3), "JavaScript": (15, 2), "Go": (5, 1)}
    out = render_language_bar(stats, width=50)
    bar, legend = out.split("\n")
    assert len(bar) == 50
    for lang in stats:
        assert lang in legend


def test_language_bar_shows_repo_counts():
    stats = {"Python": (80, 6), "Rust": (20, 1)}
    legend = render_language_bar(stats, width=40).splitlines()[1]
    assert "in 6 repos" in legend
    assert "in 1 repo" in legend  # singular


def test_language_bar_groups_small_into_other():
    stats = {"Python": (99, 1), "A": (1, 1), "B": (1, 1), "C": (1, 1)}
    out = render_language_bar(stats, width=50, min_fraction=0.05)
    legend = out.splitlines()[1]
    assert "Other" in legend
    assert "A" not in legend.split("Other")[0]


def test_language_bar_empty():
    assert render_language_bar({}) == "(no language data)"


def test_repo_table_basic():
    import re
    rows = [
        {"name_text": "alpha", "name_url": "https://example/alpha", "language": "Python",
         "size": 123, "commits": 10, "branches": 1, "last_commit": "2026-04-01"},
        {"name_text": "beta", "name_url": "", "language": "Go",
         "size": 456, "commits": 42, "branches": 2, "last_commit": "2026-03-30"},
    ]
    out = render_repo_table(rows, target_width=80)
    lines = out.splitlines()
    assert lines[0].startswith("╔") and lines[0].endswith("╗")
    assert lines[-1].startswith("╚") and lines[-1].endswith("╝")
    # Every line has the same *visible* width once HTML anchors are stripped.
    strip_html = lambda s: re.sub(r"<[^>]+>", "", s)
    widths = {len(strip_html(line)) for line in lines}
    assert len(widths) == 1, widths
    joined = "\n".join(lines)
    assert "Python" in joined and "alpha" in joined
    # Anchor emitted for rows that have a url.
    assert '<a href="https://example/alpha">' in joined
    # Row without a url gets plain text.
    assert "beta" in joined
