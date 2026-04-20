from datetime import datetime, timezone

from update_readme import build_readme


def test_build_readme_contains_table_and_chrome():
    sections = {"table": "TABLE_PLACEHOLDER"}
    out = build_readme(
        sections,
        now=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
        active_window_days=180,
    )
    assert out.startswith("<pre>")
    assert "</pre>" in out
    assert "TABLE_PLACEHOLDER" in out
    assert "Updated Wednesday 2026-04-15 12:00 UTC" in out
    # Header reflects the active window in months.
    assert "Repositories Active in the Last 6 Months" in out


def test_build_readme_no_legacy_sections():
    out = build_readme(
        {"table": "TABLE_PLACEHOLDER"},
        now=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
    )
    for legacy in (
        "DAILY CONTRIBUTIONS",
        "DAY-OF-WEEK DISTRIBUTION",
        "LANGUAGE MIX",
        "REPOSITORY SUMMARY",
        "PER-REPO",
    ):
        assert legacy not in out, legacy


def test_build_readme_handles_small_window():
    # 30-day window should render "1 Months" (we don't pluralize — simplest).
    out = build_readme(
        {"table": "X"},
        now=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
        active_window_days=30,
    )
    assert "1 Months" in out
