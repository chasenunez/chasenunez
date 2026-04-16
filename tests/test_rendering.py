from datetime import date, timedelta

from update_readme import (
    render_language_bar,
    render_repo_table,
    render_weekday_histogram,
)


def _synthetic_days(n: int = 365, pattern=lambda i: i % 7):
    """Return ``n`` (date, count) tuples ending today."""
    today = date.today()
    return [(today - timedelta(days=n - 1 - i), pattern(i)) for i in range(n)]


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


def test_language_bar_percentages_sum_to_full_width():
    stats = {"Python": (80, 3), "JavaScript": (15, 2), "Go": (5, 1)}
    out = render_language_bar(stats, width=50)
    lines = out.split("\n")
    bar = lines[0]
    legend = "\n".join(lines[1:])
    assert len(bar) == 50
    for lang in stats:
        assert lang in legend


def test_language_bar_legend_never_exceeds_width():
    # Many languages force multi-line legend; every line must fit within width.
    stats = {
        "Python": (40, 8),
        "JavaScript": (25, 5),
        "Go": (15, 3),
        "Rust": (10, 2),
        "TypeScript": (10, 4),
    }
    width = 60
    out = render_language_bar(stats, width=width, min_fraction=0.0)
    lines = out.split("\n")
    for line in lines:
        assert len(line) <= width, f"line exceeded width {width}: {line!r}"


def test_language_bar_shows_repo_counts():
    stats = {"Python": (80, 6), "Rust": (20, 1)}
    out = render_language_bar(stats, width=80)
    # Legend may be on subsequent lines; join them all.
    legend = "\n".join(out.splitlines()[1:])
    assert "in 6 repos" in legend
    assert "in 1 repo" in legend  # singular


def test_language_bar_groups_small_into_other():
    stats = {"Python": (99, 1), "A": (1, 1), "B": (1, 1), "C": (1, 1)}
    out = render_language_bar(stats, width=50, min_fraction=0.05)
    legend = "\n".join(out.splitlines()[1:])
    assert "Other" in legend
    # Small languages must not appear as named entries before "Other".
    before_other = legend.split("Other")[0]
    assert " A " not in before_other
    assert " B " not in before_other
    assert " C " not in before_other


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
