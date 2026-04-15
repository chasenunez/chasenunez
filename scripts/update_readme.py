#!/usr/bin/env python3
"""
update_readme.py

Render a GitHub activity README using ASCII / braille graphics.

Data sources
------------
* GraphQL ``contributionsCollection`` — authoritative daily contribution calendar
  for the authenticated user (includes private contributions when authed).
  Used for: daily heat map, weekday histogram, weekly totals sparkline.
* REST ``/user/repos`` (or ``/users/:u/repos``) — top-N most recently updated
  repositories, used for the metadata table.
* REST ``/repos/:o/:r/{commits,branches,languages}`` — per-repo metadata.
* REST ``/repos/:o/:r/stats/commit_activity`` — per-repo weekly totals feeding
  the secondary per-repo heat grid. Results are cached to ``CACHE_FILE`` so
  sparse responses from GitHub never zero out a row silently.

Auth
----
Reads ``GH_PAT`` / ``GITHUB_TOKEN`` / ``GH_TOKEN`` from the environment.
Unauthenticated runs fall back to public data for ``USERNAME``.

Outputs
-------
* ``README.md`` — the rendered dashboard, wrapped in ``<pre>`` so braille /
  column alignment survives GitHub's markdown pipeline.
* ``.activity_cache.json`` — persistent cache committed alongside the README.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
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
WEEKS_PER_REPO = 47          # columns in the secondary per-repo heat grid
CALENDAR_WEEKS = 53          # columns in the primary daily calendar
LINE_LENGTH = 112
CACHE_FILE = ".activity_cache.json"
README_OUT = "README.md"
RESTRICTED_ROW = "restricted"

# 9-level braille shade ramp (index 0 == empty).
SHADES: List[str] = [" ", "⡀", "⡁", "⡑", "⡕", "⡝", "⣝", "⣽", "⣿"]
# Segment glyphs used by the language bar.
LANG_SEGMENTS: List[str] = ["█", "▓", "▒", "░", "▚", "▞", "▙", "▜", "▛"]

# Network tuning.
GITHUB_REST = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
HTTP_TIMEOUT = 30
STATS_ATTEMPTS = 6
STATS_BACKOFF_INITIAL = 1.0
STATS_BACKOFF_CAP = 8.0
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


def fetch_weekly_commits(session: requests.Session, owner: str, repo: str,
                         weeks: int = WEEKS_PER_REPO) -> Optional[List[int]]:
    """Return the last ``weeks`` weekly totals, or ``None`` if unavailable.

    Handles the common GitHub quirks:
    * ``202 Accepted`` while stats are being computed -> retry with backoff
    * ``200`` with empty body or empty list -> also treated as "not ready yet"
    """
    url = f"{GITHUB_REST}/repos/{owner}/{repo}/stats/commit_activity"
    backoff = STATS_BACKOFF_INITIAL
    for _ in range(STATS_ATTEMPTS):
        try:
            r = session.get(url, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            return None
        if r.status_code == 202 or (r.status_code == 200 and not r.content):
            time.sleep(min(backoff, STATS_BACKOFF_CAP))
            backoff *= 2
            continue
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        if not data:
            time.sleep(min(backoff, STATS_BACKOFF_CAP))
            backoff *= 2
            continue
        totals = [int(w.get("total", 0)) for w in data]
        if len(totals) >= weeks:
            return totals[-weeks:]
        return [0] * (weeks - len(totals)) + totals
    return None


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
# Cache
# ---------------------------------------------------------------------------
def load_cache(path: str = CACHE_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_cache(cache: dict, path: str = CACHE_FILE) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, sort_keys=True, indent=2)
            fh.write("\n")
    except Exception:
        pass


def merge_weekly_with_cache(repo: str, fresh: Optional[List[int]], cache: dict) -> List[int]:
    """Prefer ``fresh`` if it has signal; otherwise fall back to cached series."""
    zeros = [0] * WEEKS_PER_REPO
    cached = cache.get(repo)
    cached_ok = isinstance(cached, list) and len(cached) == WEEKS_PER_REPO and any(cached)
    if fresh and any(fresh):
        return fresh
    if cached_ok:
        return cached  # type: ignore[return-value]
    return fresh if fresh is not None else zeros


# ---------------------------------------------------------------------------
# Rendering — shade / helper
# ---------------------------------------------------------------------------
def shade_for(value: int, max_value: int) -> str:
    """Map a value in ``[0, max_value]`` to a braille shade glyph."""
    if max_value <= 0 or value <= 0:
        return SHADES[0]
    ratio = min(1.0, value / max_value)
    idx = int(round(ratio * (len(SHADES) - 1)))
    return SHADES[max(1, min(len(SHADES) - 1, idx))]


# ---------------------------------------------------------------------------
# Rendering — daily contribution calendar
# ---------------------------------------------------------------------------
def render_daily_calendar(days: Sequence[Tuple[date, int]],
                          weeks: int = CALENDAR_WEEKS) -> str:
    """Render a GitHub-style 7-row × ``weeks``-column braille calendar.

    Columns are Sunday-anchored weeks; rows are Sun..Sat (top to bottom).
    """
    if not days:
        return "(no contribution data)"
    counts = {d: c for d, c in days}
    last_day = days[-1][0]
    # End on the Saturday on-or-after ``last_day``. Python weekday: Mon=0..Sun=6.
    end = last_day + timedelta(days=(5 - last_day.weekday()) % 7)
    start = end - timedelta(days=weeks * 7 - 1)
    # Snap start back to Sunday.
    start -= timedelta(days=(start.weekday() - 6) % 7)

    max_c = max((c for _, c in days), default=0)

    grid: List[List[str]] = [[" "] * weeks for _ in range(7)]
    for i in range(weeks * 7):
        d = start + timedelta(days=i)
        if d > end:
            break
        col = i // 7
        # weekday(): Mon=0..Sun=6 -> Sun-first: Sun=0,...,Sat=6
        row = (d.weekday() + 1) % 7
        grid[row][col] = shade_for(counts.get(d, 0), max_c)

    # Month label row: one-letter initial at the first week of each month. We
    # keep it to a single char per column because each grid cell is one week.
    # Adjacent months would otherwise collide with 3-letter labels.
    month_row = [" "] * weeks
    seen_month: Optional[int] = None
    for col in range(weeks):
        week_start = start + timedelta(days=col * 7)
        if week_start.month != seen_month:
            month_row[col] = week_start.strftime("%b")[0]
            seen_month = week_start.month

    label_w = 4
    row_labels = ["   ", "Mon", "   ", "Wed", "   ", "Fri", "   "]
    out = [" " * (label_w + 1) + "".join(month_row)]
    for row in range(7):
        out.append(pad_to_width(row_labels[row], label_w, "right") + " " + "".join(grid[row]))
    legend = " " * (label_w + 1) + "low " + " ".join(SHADES[1:]) + " high"
    out.append(legend)
    total = sum(c for _, c in days)
    out.append(pad_to_width(f"{total} contributions in the last year", LINE_LENGTH, "center"))
    return "\n".join(out)


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
def render_language_bar(lang_totals: Dict[str, int], width: int = 80,
                        min_fraction: float = 0.02) -> str:
    """Stacked horizontal bar showing language byte share across repos."""
    total = sum(lang_totals.values())
    if total <= 0:
        return "(no language data)"
    items = sorted(lang_totals.items(), key=lambda kv: kv[1], reverse=True)
    kept: List[Tuple[str, int]] = []
    other = 0
    for lang, b in items:
        if b / total < min_fraction:
            other += b
        else:
            kept.append((lang, b))
    if other > 0:
        kept.append(("Other", other))

    bar_parts: List[str] = []
    legend_parts: List[str] = []
    used = 0
    for i, (lang, b) in enumerate(kept):
        # Last segment soaks up rounding drift so the bar is exactly ``width``.
        if i == len(kept) - 1:
            seg = max(0, width - used)
        else:
            seg = int(round(b / total * width))
        used += seg
        glyph = LANG_SEGMENTS[i % len(LANG_SEGMENTS)]
        bar_parts.append(glyph * seg)
        pct = b / total * 100
        legend_parts.append(f"{glyph} {lang} {pct:.0f}%")
    return "".join(bar_parts) + "\n" + "  ".join(legend_parts)


# ---------------------------------------------------------------------------
# Rendering — per-repo weekly heat grid
# ---------------------------------------------------------------------------
def render_per_repo_grid(repo_weekly: Dict[str, List[int]],
                         repo_order: Sequence[str],
                         label_w: int = 10) -> str:
    """Braille-shaded grid, one row per repo, ``WEEKS_PER_REPO`` columns."""
    if not repo_order:
        return ""
    lines: List[str] = []
    for repo in repo_order:
        weeks = repo_weekly.get(repo) or [0] * WEEKS_PER_REPO
        if len(weeks) < WEEKS_PER_REPO:
            weeks = [0] * (WEEKS_PER_REPO - len(weeks)) + weeks
        max_val = max(weeks) or 1
        cells = " ".join(shade_for(w, max_val) for w in weeks)
        label = pad_to_width(repo, label_w, "right")
        lines.append(f"{label}┤ {cells}")
    # Month x-axis.
    axis_cells: List[str] = []
    last_month: Optional[int] = None
    now = datetime.now(timezone.utc)
    for i in range(WEEKS_PER_REPO):
        dt = now - timedelta(days=(WEEKS_PER_REPO - 1 - i) * 7)
        if dt.month != last_month:
            axis_cells.append(dt.strftime("%b")[0])
            last_month = dt.month
        else:
            axis_cells.append(" ")
    axis = " " * label_w + " " + " ".join(axis_cells)
    lines.append(axis)
    return "\n".join(lines)


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
        centered("DAILY CONTRIBUTIONS"),
        sections["calendar"],
        "",
        divider,
        "",
        centered("DAY-OF-WEEK DISTRIBUTION"),
        sections["weekday"],
        "",
        divider,
        "",
        centered("PER-REPO WEEKLY ACTIVITY"),
        sections["per_repo"],
        "",
        divider,
        "",
        centered("LANGUAGE MIX"),
        sections["languages"],
        "",
        divider,
        "",
        centered("REPOSITORY SUMMARY"),
        sections["table"],
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
def aggregate_language_totals(session: requests.Session, rows: List[dict]) -> Dict[str, int]:
    """Sum byte counts per language across all non-private rows."""
    totals: Dict[str, int] = {}
    def one(r):
        if r.get("private"):
            return {}
        return fetch_languages(session, r["owner"], r["name_text"])
    with ThreadPoolExecutor(max_workers=min(METADATA_WORKERS, max(1, len(rows)))) as ex:
        for fut in as_completed([ex.submit(one, r) for r in rows]):
            try:
                data = fut.result()
            except Exception:
                continue
            for lang, b in data.items():
                totals[lang] = totals.get(lang, 0) + int(b)
    return totals


def gather_per_repo_weekly(session: requests.Session, rows: List[dict],
                           cache: dict) -> Tuple[Dict[str, List[int]], List[str], dict]:
    """Return (repo_weekly, repo_order, updated_cache).

    Public repos get their own row (keyed by repo name). Private repos are
    aggregated into a single ``RESTRICTED_ROW`` series.
    """
    updated_cache = dict(cache)
    repo_weekly: Dict[str, List[int]] = {}
    public_order: List[str] = []
    private_rows = [r for r in rows if r.get("private")]
    public_rows = [r for r in rows if not r.get("private")]

    def fetch(r):
        return r["name_text"], fetch_weekly_commits(session, r["owner"], r["name_text"])

    with ThreadPoolExecutor(max_workers=min(METADATA_WORKERS, max(1, len(public_rows)))) as ex:
        for fut in as_completed([ex.submit(fetch, r) for r in public_rows]):
            try:
                name, fresh = fut.result()
            except Exception:
                continue
            merged = merge_weekly_with_cache(name, fresh, cache)
            repo_weekly[name] = merged
            if fresh and any(fresh):
                updated_cache[name] = fresh

    # Preserve repo_order based on original row order (most recently updated).
    for r in public_rows:
        if r["name_text"] in repo_weekly:
            public_order.append(r["name_text"])

    if private_rows:
        agg = [0] * WEEKS_PER_REPO
        any_signal = False
        for r in private_rows:
            fresh = fetch_weekly_commits(session, r["owner"], r["name_text"])
            if fresh:
                for i, v in enumerate(fresh):
                    agg[i] += v
                if any(fresh):
                    any_signal = True
        merged = merge_weekly_with_cache(RESTRICTED_ROW, agg if any_signal else None, cache)
        repo_weekly[RESTRICTED_ROW] = merged
        if any_signal:
            updated_cache[RESTRICTED_ROW] = agg
        public_order.append(RESTRICTED_ROW)

    return repo_weekly, public_order, updated_cache


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a GitHub activity README.")
    p.add_argument("--output", default=README_OUT, help="Path to write the README to.")
    p.add_argument("--cache", default=CACHE_FILE, help="Path for the persistent cache file.")
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

    # 1. Overall contributions (primary graphic).
    try:
        calendar_days = fetch_contribution_calendar(session, USERNAME)
    except Exception as exc:
        print(f"GraphQL contribution calendar fetch failed: {exc}", file=sys.stderr)
        calendar_days = []

    # 2. Repo list (most recent first), then metadata table.
    try:
        all_repos = fetch_repos(session, token)
    except Exception as exc:
        print(f"Failed to fetch repositories: {exc}", file=sys.stderr)
        return 1
    top_repos = all_repos[:TOP_N]
    rows = build_repo_rows(session, top_repos)

    # 3. Per-repo weekly activity with cache merge.
    cache = load_cache(args.cache)
    repo_weekly, repo_order, updated_cache = gather_per_repo_weekly(session, rows, cache)
    save_cache(updated_cache, args.cache)

    # 4. Language mix.
    lang_totals = aggregate_language_totals(session, rows)

    # 5. Render sections.
    sections = {
        "calendar": render_daily_calendar(calendar_days),
        "weekday": render_weekday_histogram(calendar_days),
        "per_repo": render_per_repo_grid(repo_weekly, repo_order),
        "languages": render_language_bar(lang_totals),
        "table": render_repo_table([r for r in rows if not r.get("private")]),
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
