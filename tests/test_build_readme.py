from datetime import datetime, timezone

from update_readme import build_readme


def test_build_readme_contains_all_sections():
    sections = {
        "weekday": "WEEK",
        "languages": "LANG",
        "table": "TABLE",
    }
    out = build_readme(sections, now=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc))
    assert out.startswith("<pre>")
    assert "</pre>" in out
    for marker in ("WEEK", "LANG", "TABLE"):
        assert marker in out
    assert "DAILY CONTRIBUTIONS" not in out
    assert "PER-REPO" not in out
    assert "DAY-OF-WEEK DISTRIBUTION" in out
    assert "REPOSITORY SUMMARY" in out
    assert "LANGUAGE MIX" in out
    # Section order: table → languages → weekday.
    assert out.index("TABLE") < out.index("LANG") < out.index("WEEK")
    assert "Updated Wednesday 2026-04-15 12:00 UTC" in out
