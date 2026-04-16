#!/usr/bin/env python3
"""
update_readme.py

Render a GitHub activity README using ASCII / braille graphics.

Data sources
------------
* GraphQL ``contributionsCollection`` — authoritative daily contribution
  stream, feeds the weekday histogram. Includes private contributions when
  authenticated.
* REST ``/user/repos`` (or ``/users/:u/repos``) — full repo list. The top-N
  most recently updated go into the metadata table; the full list drives the
  language mix.
* REST ``/repos/:o/:r/{commits,branches,languages}`` — per-repo metadata.

Auth
----
Reads ``GH_PAT`` / ``GITHUB_TOKEN`` / ``GH_TOKEN`` from the environment.
Unauthenticated runs fall back to public data for ``USERNAME``.

Output
------
``README.md`` — the rendered dashboard wrapped in ``<pre>`` so braille /
column alignment survives GitHub's markdown pipeline.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import requests

try:
    import wcwidth as _wcwidth
except ImportError:  # pragma: no cover - fallback tested via wcswidth()
    _wcwidth = None


# ---------------------------------------------------------------------------
# Config — safe to tune
# ---------------------------------------------------------------------------
USERNAME = "chasenunez"
TOP_N = 10
LINE_LENGTH = 112
README_OUT = "README.md"

# Languages to drop from the language-mix bar. HTML is almost always template
# boilerplate / GH Pages output and swamps the bar for no real signal. Add
# others here if they keep showing up as noise (e.g. "CSS", "SCSS").
EXCLUDED_LANGUAGES: set = {"HTML"}

# Segment glyphs used by the language bar.
LANG_SEGMENTS: List[str] = ["█", "▓", "▒", "░", "▚", "▞", "▙", "▜", "▛"]

# Network tuning.
GITHUB_REST = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
HTTP_TIMEOUT = 30
METADATA_WORKERS = 8


# ---------------------------------------------------------------------------
# Width helpers
# ---------------------------------------------------------------------------
def wcswidth(s: str) -> int:
    """Return the display width of ``s`` using wcwidth when available."""
    if _wcwidth is not None:
        try:
            w = _wcwidth.wcswidth(s)
            if w is not None and w >= 0:
                return w
        except Exception:
            pass
    total = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def pad_to_width(s: str, target: int, align: str = "left") -> str:
    """Pad or truncate ``s`` to ``target`` display columns."""
    cur = wcswidth(s)
    if cur == target:
        return s
    if cur < target:
        pad = target - cur
        if align == "right":
            return " " * pad + s
        if align == "center":
            left = pad // 2
            return " " * left + s + " " * (pad - left)
        return s + " " * pad
    # Truncate with an ellipsis.
    if target <= 0:
        return ""
    if target == 1:
        return s[:1]
    acc, out = 0, ""
    for ch in s:
        w = max(1, wcswidth(ch))
        if acc + w > target - 1:
            break
        out += ch
        acc += w
    out += "…"
    acc += 1
    if acc < target:
        out += " " * (target - acc)
    return out


# ---------------------------------------------------------------------------
# HTTP / auth
# ---------------------------------------------------------------------------
def auth_token() -> Optional[str]:
    """Return the first populated token env var, or ``None``."""
    for name in ("GH_PAT", "GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(name)
        if v:
            return v
    return None


def make_session(token: Optional[str]) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"update-readme ({USERNAME})",
    })
    if token:
        s.headers["Authorization"] = f"token {token}"
    return s


def gh_get(session: requests.Session, url: str, params: Optional[dict] = None) -> requests.Response:
    r = session.get(url, params=params or {}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def gh_paginated(session: requests.Session, url: str, params: Optional[dict] = None) -> List[dict]:
    """Follow ``Link: rel=next`` pagination and return a flat list."""
    items: List[dict] = []
    params = dict(params or {})
    params.setdefault("per_page", 100)
    next_url: Optional[str] = url
    cur_params: Optional[dict] = params
    while next_url:
        r = gh_get(session, next_url, params=cur_params)
        data = r.json()
        if not isinstance(data, list):
            break
        items.extend(data)
        m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        next_url = m.group(1) if m else None
        cur_params = None  # URL already encodes the next page's params
    return items


def gh_graphql(session: requests.Session, query: str, variables: Optional[dict] = None) -> dict:
    r = session.post(
        GITHUB_GRAPHQL,
        json={"query": query, "variables": variables or {}},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
CONTRIB_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            weekday
            contributionCount
          }
        }
      }
    }
  }
}
"""


def fetch_contribution_calendar(session: requests.Session, login: str) -> List[Tuple[date, int]]:
    """Return ``[(date, count), ...]`` ascending for the last ~year."""
    data = gh_graphql(session, CONTRIB_QUERY, {"login": login})
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days: List[Tuple[date, int]] = []
    for w in weeks:
        for d in w["contributionDays"]:
            days.append((date.fromisoformat(d["date"]), int(d["contributionCount"])))
    days.sort(key=lambda x: x[0])
    return days


def fetch_repos(session: requests.Session, token: Optional[str]) -> List[dict]:
    url = f"{GITHUB_REST}/user/repos" if token else f"{GITHUB_REST}/users/{USERNAME}/repos"
    repos = gh_paginated(session, url, params={"sort": "updated", "direction": "desc"})
    repos.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return repos


def count_from_link(session: requests.Session, url: str) -> int:
    """Use the ``rel=last`` page number trick to approximate a total count."""
    try:
        r = gh_get(session, url, params={"per_page": 1})
    except Exception:
        return 0
    m = re.search(r'[&?]page=(\d+)>; rel="last"', r.headers.get("Link", ""))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    try:
        data = r.json()
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def fetch_languages(session: requests.Session, owner: str, repo: str) -> Dict[str, int]:
    try:
        r = gh_get(session, f"{GITHUB_REST}/repos/{owner}/{repo}/languages")
        data = r.json()
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_repo_rows(session: requests.Session, repos: List[dict]) -> List[dict]:
    """Return table row dicts concurrently (commits, branches, language fixup)."""
    def one(r: dict) -> Optional[dict]:
        owner = (r.get("owner") or {}).get("login")
        name = r.get("name") or ""
        if not owner or not name:
            return None
        language = r.get("language") or ""
        commits = count_from_link(session, f"{GITHUB_REST}/repos/{owner}/{name}/commits")
        branches = count_from_link(session, f"{GITHUB_REST}/repos/{owner}/{name}/branches")
        # Prefer a non-HTML language when a repo is mostly templated pages.
        if language.strip().lower() == "html":
            langs = fetch_languages(session, owner, name)
            best = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)
            for lang_name, _ in best:
                if lang_name.strip().lower() != "html":
                    language = lang_name
                    break
        pushed = r.get("pushed_at") or r.get("updated_at") or ""
        last_commit = ""
        if pushed:
            try:
                iso = pushed.replace("Z", "+00:00")
                last_commit = datetime.fromisoformat(iso).strftime("%Y-%m-%d")
            except ValueError:
                last_commit = pushed
        return {
            "owner": owner,
            "name_text": name,
            "name_url": r.get("html_url", ""),
            "language": language,
            "size": int(r.get("size", 0)),
            "commits": commits,
            "branches": branches,
            "last_commit": last_commit,
            "private": bool(r.get("private")),
        }

    if not repos:
        return []
    results: List[Optional[dict]] = [None] * len(repos)
    with ThreadPoolExecutor(max_workers=min(METADATA_WORKERS, len(repos))) as ex:
        futures = {ex.submit(one, r): i for i, r in enumerate(repos)}
        for fut in as_completed(futures):
            try:
                results[futures[fut]] = fut.result()
            except Exception:
                pass
    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Rendering — weekday histogram
# ---------------------------------------------------------------------------
def render_weekday_histogram(days: Sequence[Tuple[date, int]], width: int = 60) -> str:
    """Horizontal histogram of contributions aggregated per weekday (Mon..Sun)."""
    if not days:
        return "(no contribution data)"
    totals = [0] * 7  # Mon=0..Sun=6
    for d, c in days:
        totals[d.weekday()] += c
    max_t = max(totals) or 1
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = []
    for name, t in zip(names, totals):
        bar_len = int(round(t / max_t * width))
        lines.append(f"{name} ┤ {'⣿' * bar_len} {t}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rendering — language breakdown bar
# ---------------------------------------------------------------------------
def render_language_bar(lang_stats: Dict[str, Tuple[int, int]], width: int = LINE_LENGTH,
                        min_fraction: float = 0.02) -> str:
    """Stacked horizontal bar of language byte share across repos.

    ``lang_stats`` is ``{lang: (bytes, repo_count)}``. The bar is always
    exactly ``width`` columns wide; the legend is greedy-wrapped to the same
    width so nothing ever spills past the container edge. The legend annotates
    each segment with the number of repos the language appears in so that a
    language with few bytes but wide adoption is still visible as "diverse".
    Languages in ``EXCLUDED_LANGUAGES`` are assumed already filtered out.
    """
    total = sum(b for b, _ in lang_stats.values())
    if total <= 0:
        return "(no language data)"
    items = sorted(lang_stats.items(), key=lambda kv: kv[1][0], reverse=True)
    kept: List[Tuple[str, int, int]] = []
    other_bytes = 0
    other_repos = 0
    for lang, (b, n) in items:
        if b / total < min_fraction:
            other_bytes += b
            other_repos = max(other_repos, n)
        else:
            kept.append((lang, b, n))
    if other_bytes > 0:
        kept.append(("Other", other_bytes, other_repos))

    # Build the bar and the per-language legend entry in one pass.
    bar_parts: List[str] = []
    entries: List[str] = []
    used = 0
    for i, (lang, b, n) in enumerate(kept):
        # Last segment soaks up rounding drift so the bar is exactly ``width``.
        if i == len(kept) - 1:
            seg = max(0, width - used)
        else:
            seg = int(round(b / total * width))
        used += seg
        glyph = LANG_SEGMENTS[i % len(LANG_SEGMENTS)]
        bar_parts.append(glyph * seg)
        pct = b / total * 100
        repo_note = "" if lang == "Other" else f" (in {n} repo{'s' if n != 1 else ''})"
        entries.append(f"{glyph} {lang} {pct:.0f}%{repo_note}")

    # Greedy-wrap the legend so no line exceeds ``width``.
    sep = "  "
    legend_lines: List[str] = [""]
    for entry in entries:
        current = legend_lines[-1]
        candidate = entry if not current else current + sep + entry
        if wcswidth(candidate) > width and current:
            legend_lines.append(entry)
        else:
            legend_lines[-1] = candidate
    return "".join(bar_parts) + "\n" + "\n".join(legend_lines)


# ---------------------------------------------------------------------------
# Rendering — repo metadata table
# ---------------------------------------------------------------------------
TABLE_COLS = ["Repository", "Main Language", "Total Bytes", "Total Commits",
              "Last Commit Date", "Branches"]
TABLE_KEYS = ["name_text", "language", "size", "commits", "last_commit", "branches"]


def render_repo_table(rows: List[dict], target_width: int = LINE_LENGTH) -> str:
    """Box-drawing table with anchor tags on the repository column."""
    if not rows:
        return ""
    cells: List[List[str]] = [[str(r.get(k, "")) for k in TABLE_KEYS] for r in rows]
    inner = [len(c) for c in TABLE_COLS]
    for row in cells:
        for i, v in enumerate(row):
            inner[i] = max(inner[i], len(v))
    PAD = 2
    widths = [w + PAD for w in inner]
    total = sum(widths) + len(widths) + 1
    if total < target_width:
        extra = target_width - total
        i = 0
        while extra > 0:
            widths[i % len(widths)] += 1
            extra -= 1
            i += 1
    elif total > target_width:
        # Shrink the repo-name column first; if still too wide, shrink others
        # equally subject to each column's header-length floor.
        excess = total - target_width
        floors = [len(c) + PAD for c in TABLE_COLS]
        take = min(excess, widths[0] - floors[0])
        widths[0] -= take
        excess -= take
        i = 1
        while excess > 0 and any(widths[j] > floors[j] for j in range(1, len(widths))):
            j = i % len(widths)
            if j != 0 and widths[j] > floors[j]:
                widths[j] -= 1
                excess -= 1
            i += 1
    inner = [w - PAD for w in widths]

    h, v = "═", "║"
    tl, tr, bl, br = "╔", "╗", "╚", "╝"
    tsep, msep, bsep, lsep, rsep = "╦", "╬", "╩", "╠", "╣"

    def border(left: str, mid: str, right: str) -> str:
        parts = [left]
        for i_, w in enumerate(widths):
            parts.append(h * w)
            parts.append(mid if i_ < len(widths) - 1 else right)
        return "".join(parts)

    top, middle, bottom = border(tl, tsep, tr), border(lsep, msep, rsep), border(bl, bsep, br)

    def clip(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:1]
        return text[:width - 1] + "…"

    lines = [top]
    header = v + v.join(" " + c.center(inner[i]) + " " for i, c in enumerate(TABLE_COLS)) + v
    lines.append(header)
    lines.append(middle)
    for row, raw in zip(cells, rows):
        repo_clipped = clip(row[0], inner[0])
        url = raw.get("name_url", "")
        if url:
            anchor = f'<a href="{url}">{repo_clipped}</a>'
            repo_cell = " " + anchor + " " * (inner[0] - len(repo_clipped)) + " "
        else:
            repo_cell = " " + repo_clipped.ljust(inner[0]) + " "
        other = [" " + clip(row[i], inner[i]).center(inner[i]) + " "
                 for i in range(1, len(TABLE_COLS))]
        lines.append(v + repo_cell + v + v.join(other) + v)
        lines.append(middle)
    lines[-1] = bottom
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# README assembly
# ---------------------------------------------------------------------------
def build_readme(sections: Dict[str, str], *, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    header_top = "⣀⣤⣶⣾⣿ Contribution Timeseries With Relative Allocation Among Recently Active Projects ⣿⣷⣶⣤⣀"
    header_bot = f"⠉⠛⠿⢿⣿ Updated {now.strftime('%A %Y-%m-%d %H:%M UTC')} ⣿⡿⠿⠛⠉"
    rule = "▔" * LINE_LENGTH
    divider = "─" * LINE_LENGTH

    def centered(text: str) -> str:
        return pad_to_width(text, LINE_LENGTH, "center")

    parts = [
        "<pre>",
        centered(header_top),
        rule,
        "",
        centered("REPOSITORY SUMMARY"),
        sections["table"],
        "",
        divider,
        "",
        centered("LANGUAGE MIX"),
        sections["languages"],
        "",
        divider,
        "",
        centered("DAY-OF-WEEK DISTRIBUTION"),
        sections["weekday"],
        "",
        rule,
        centered(header_bot),
        "</pre>",
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------
def aggregate_language_stats(session: requests.Session,
                             rows: List[dict]) -> Dict[str, Tuple[int, int]]:
    """Return ``{lang: (total_bytes, repo_count)}`` across non-private rows.

    ``repo_count`` is how many repos the language appears in at all (not just
    as the primary one) — this is the "diversity" metric surfaced in the
    language-bar legend. ``EXCLUDED_LANGUAGES`` are dropped here so every
    downstream consumer sees a pre-filtered dict.
    """
    def one(r):
        if r.get("private"):
            return {}
        return fetch_languages(session, r["owner"], r["name_text"])

    per_repo: List[Dict[str, int]] = []
    with ThreadPoolExecutor(max_workers=min(METADATA_WORKERS, max(1, len(rows)))) as ex:
        for fut in as_completed([ex.submit(one, r) for r in rows]):
            try:
                per_repo.append(fut.result())
            except Exception:
                continue

    totals: Dict[str, int] = {}
    repo_counts: Dict[str, int] = {}
    for langs in per_repo:
        for lang, b in langs.items():
            if lang in EXCLUDED_LANGUAGES:
                continue
            totals[lang] = totals.get(lang, 0) + int(b)
            repo_counts[lang] = repo_counts.get(lang, 0) + 1
    return {lang: (totals[lang], repo_counts[lang]) for lang in totals}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a GitHub activity README.")
    p.add_argument("--output", default=README_OUT, help="Path to write the README to.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="Print the rendered README to stdout instead of writing.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    token = auth_token()
    if token:
        print(f"Using auth token (length {len(token)}).", file=sys.stderr)
    else:
        print("No token — falling back to unauthenticated public data.", file=sys.stderr)
    session = make_session(token)

    # 1. Overall contributions (drives the weekday histogram).
    try:
        calendar_days = fetch_contribution_calendar(session, USERNAME)
    except Exception as exc:
        print(f"GraphQL contribution calendar fetch failed: {exc}", file=sys.stderr)
        calendar_days = []

    # 2. Repo list (most recent first) -> metadata table for the top-N.
    try:
        all_repos = fetch_repos(session, token)
    except Exception as exc:
        print(f"Failed to fetch repositories: {exc}", file=sys.stderr)
        return 1
    rows = build_repo_rows(session, all_repos[:TOP_N])

    # 3. Language mix — deliberately uses the *full* repo list so the bar
    # reflects every language we've shipped, not just the recently-active
    # top-N.
    all_lang_inputs = [
        {
            "owner": (r.get("owner") or {}).get("login"),
            "name_text": r.get("name"),
            "private": bool(r.get("private")),
        }
        for r in all_repos
        if (r.get("owner") or {}).get("login") and r.get("name")
    ]
    print(f"Aggregating languages across {len(all_lang_inputs)} repos...", file=sys.stderr)
    lang_stats = aggregate_language_stats(session, all_lang_inputs)

    # 4. Render sections.
    sections = {
        "table": render_repo_table([r for r in rows if not r.get("private")]),
        "languages": render_language_bar(lang_stats),
        "weekday": render_weekday_histogram(calendar_days),
    }
    readme = build_readme(sections)

    if args.print_only:
        sys.stdout.write(readme)
        return 0

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(readme)
    print(f"Wrote {args.output}.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
