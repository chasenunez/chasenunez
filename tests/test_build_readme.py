from datetime import datetime, timezone

from update_readme import build_readme


def test_build_readme_contains_all_sections():
    sections = {
        "calendar": "CAL",
        "weekday": "WEEK",
        "per_repo": "GRID",
        "languages": "LANG",
        "table": "TABLE",
    }
    out = build_readme(sections, now=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc))
    assert out.startswith("<pre>")
    assert "</pre>" in out
    for marker in ("CAL", "WEEK", "GRID", "LANG", "TABLE"):
        assert marker in out
    assert "DAILY CONTRIBUTIONS" in out
    assert "DAY-OF-WEEK DISTRIBUTION" in out
    assert "Updated Wednesday 2026-04-15 12:00 UTC" in out
