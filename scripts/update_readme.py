#!/usr/bin/env python3
"""
scripts/update_readme.py

Improved version of the user's script with the following changes:
- robust pagination when fetching repositories (handles GitHub's 100-per-page limit)
- improved handling of GitHub "202 Accepted" for stats endpoints (exponential backoff)
- fallback to counting commits per-week via the commits endpoint when /stats/commit_activity
  doesn't return usable data (prevents spurious blanks)
- uses /stats/code_frequency to compute weekly lines-changed (additions+deletions) as a proxy
  for "bytes committed"; expands weekly values to daily values and aggregates across repos
- adds an ASCII line-plot (based on the provided inspiration) that shows daily activity
  centered on the long-term mean for the timeframe
- conservative behaviour for private repos (only included when a token is present)
- improvements to table formatting and defensive programming

Notes:
- This script can be API-heavy if lots of repos are involved. Use a valid GH_PAT in GH_PAT
  environment variable to increase rate limits and to include private repos.
- The code_frequency and commit_activity endpoints may take a few seconds on GitHub to compute
  (they return 202 while computing). The script retries with exponential backoff.

Requirements:
    pip install requests

"""
from __future__ import annotations
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from math import floor, isnan
from typing import Dict, List, Tuple, Optional

import requests

# ---------- Configuration ----------
USERNAME = "chasenunez"
TOP_N = 10
WEEKS = 42  # number of weeks to show
SHADES = [" ", "░", "▒", "▓", "█"]  # intensity glyphs low->high
STATS_MAX_RETRIES = 12
STATS_RETRY_SLEEP = 2  # base seconds, exponential backoff applied
PER_PAGE = 100  # GitHub max per_page
# -----------------------------------

GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})",
})


def auth_token() -> Optional[str]:
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def gh_get(url: str, params: dict | None = None, token: str | None = None, timeout: int = 30) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp


def _get_paginated(url: str, params: dict | None = None, token: str | None = None) -> List[dict]:
    """Generic paginator for endpoints that return lists with Link headers."""
    out: List[dict] = []
    params = dict(params or {})
    params.setdefault("per_page", PER_PAGE)
    next_url = url
    while next_url:
        r = gh_get(next_url, params=params if next_url == url else None, token=token)
        page = r.json() or []
        if isinstance(page, list):
            out.extend(page)
        else:
            break
        link = r.headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            next_url = m.group(1)
            params = None  # subsequent cursors already contained in next_url
        else:
            break
    return out


def fetch_repos_for_user(token: str | None = None) -> List[dict]:
    """
    Fetch all repositories for the user. If token is provided, use /user/repos with
    affiliation=owner to include private repos owned by the token user.
    """
    if token:
        url = f"{GITHUB_API}/user/repos"
        params = {"sort": "updated", "direction": "desc", "affiliation": "owner"}
    else:
        url = f"{GITHUB_API}/users/{USERNAME}/repos"
        params = {"sort": "updated", "direction": "desc"}
    return _get_paginated(url, params=params, token=token)


def _retry_stats_get(url: str, token: str | None = None) -> Optional[requests.Response]:
    """Call stats endpoints which may return 202 while GitHub computes them; retry with backoff."""
    attempt = 0
    while attempt < STATS_MAX_RETRIES:
        try:
            r = gh_get(url, token=token)
        except requests.HTTPError as e:
            # treat errors (e.g., 404, private unauthorized) as not available
            # caller should handle None
            return None
        if r.status_code == 202:
            # compute backoff and sleep
            sleep_time = STATS_RETRY_SLEEP * (2 ** attempt)
            time.sleep(sleep_time)
            attempt += 1
            continue
        return r
    return None


def repo_commit_activity(owner: str, repo: str, token: str | None = None) -> List[int]:
    """
    Prefer /stats/commit_activity (weekly totals, oldest->newest). If not available or
    obviously incomplete, fall back to counting commits per-week via the commits API.
    Returns list of length WEEKS (oldest->newest).
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    r = _retry_stats_get(url, token=token)
    if r is not None:
        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                weeks = [int(w.get("total", 0)) for w in data]
                # normalize length to WEEKS
                if len(weeks) >= WEEKS:
                    return weeks[-WEEKS:]
                pad = [0] * (WEEKS - len(weeks))
                return pad + weeks
        except Exception:
            pass

    # fallback: count commits per-week using commits endpoint with since/until (more reliable)
    return repo_weekly_from_commits(owner, repo, token=token)


def repo_weekly_from_commits(owner: str, repo: str, token: str | None = None) -> List[int]:
    """Count commits per each of the last WEEKS weeks by querying commits?since&until.
    Uses per_page=1 and Link header to estimate counts which is much lighter than
    fetching full lists.
    Returns list oldest->newest of length WEEKS.
    """
    now = datetime.now(timezone.utc)
    # compute week starts so last element is the most recent week
    weeks: List[int] = []
    # We'll define the latest week to start at midnight UTC of the date that is multiple of 7 days before now.
    # Simpler: generate WEEKS weeks ending today (each week is [start, start+7days))
    for i in range(WEEKS, 0, -1):
        start = now - timedelta(days=i * 7)
        end = start + timedelta(days=7)
        iso_since = start.isoformat(timespec='seconds').replace('+00:00', 'Z')
        iso_until = end.isoformat(timespec='seconds').replace('+00:00', 'Z')
        url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
        params = {"since": iso_since, "until": iso_until, "per_page": 1}
        try:
            r = gh_get(url, params=params, token=token)
        except requests.HTTPError:
            weeks.append(0)
            continue
        link = r.headers.get("Link", "")
        if link:
            m = re.search(r'[&?]page=(\d+)>;\s*rel="last"', link)
            if m:
                try:
                    weeks.append(int(m.group(1)))
                    continue
                except ValueError:
                    pass
        # otherwise fall back to length of returned list
        try:
            commits = r.json()
            if isinstance(commits, list):
                weeks.append(len(commits))
            else:
                weeks.append(0)
        except Exception:
            weeks.append(0)
    return weeks


def get_commit_count(owner: str, repo: str, token: str | None = None) -> int:
    """
    Estimate total commits using per_page=1 and Link header parsing.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    params = {"per_page": 1}
    try:
        r = gh_get(url, params=params, token=token)
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 409:
            return 0
        return 0
    link = r.headers.get("Link", "")
    if link:
        m = re.search(r'[&?]page=(\d+)>;\s*rel="last"', link)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    try:
        commits = r.json()
        if isinstance(commits, list):
            return len(commits)
    except Exception:
        pass
    return 0


def get_branch_count(owner: str, repo: str, token: str | None = None) -> int:
    """
    Estimate number of branches using per_page=1 on branches endpoint and parsing Link header.
    Falls back to len(returned_list) if no Link header.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/branches"
    params = {"per_page": 1}
    try:
        r = gh_get(url, params=params, token=token)
    except requests.HTTPError:
        return 0
    link = r.headers.get("Link", "")
    if link:
        m = re.search(r'[&?]page=(\d+)>;\s*rel="last"', link)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    try:
        data = r.json()
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return 0


def fetch_languages(owner: str, repo: str, token: str | None = None) -> Dict[str, int]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/languages"
    try:
        r = gh_get(url, token=token)
        return r.json() or {}
    except requests.HTTPError:
        return {}


# ---------- ASCII table builder with clickable links (using HTML anchors in <pre>) ----------
def make_ascii_table_with_links(rows: List[dict]) -> Tuple[str, int, int]:
    cols = ["Repository", "Main Language", "Total Bytes", "Total Commits", "Date of Last Commit", "Branches"]
    data_rows = []
    for r in rows:
        data_rows.append([
            r.get("name_text", ""),
            r.get("language", ""),
            str(r.get("size", "0")),
            str(r.get("commits", "0")),
            r.get("last_commit", ""),
            str(r.get("branches", "0"))
        ])

    widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    widths = [w + 2 for w in widths]

    top_line = "+" + "+".join(["-" * w for w in widths]) + "+"
    lines = [top_line]

    header_cells = []
    for i, c in enumerate(cols):
        content = " " + c.center(widths[i] - 2) + " "
        header_cells.append(content)
    lines.append("|" + "|".join(header_cells) + "|")
    lines.append(top_line)

    for r in rows:
        cells = []
        vis_name = r.get("name_text", "")
        url = r.get("name_url", "")
        inner_w0 = widths[0] - 2
        if len(vis_name) > inner_w0:
            vis_name_display = vis_name[:inner_w0 - 1] + "…"
        else:
            vis_name_display = vis_name
        left_pad = (inner_w0 - len(vis_name_display)) // 2
        right_pad = inner_w0 - len(vis_name_display) - left_pad
        repo_cell = " " + (" " * left_pad) + f'<a href="{url}">{vis_name_display}</a>' + (" " * right_pad) + " "
        cells.append(repo_cell)

        other = [r.get("language", ""), str(r.get("size", "0")), str(r.get("commits", "0")), r.get("last_commit", ""), str(r.get("branches", "0"))]
        for i, val in enumerate(other, start=1):
            w = widths[i] - 2
            cell = " " + val.center(w) + " "
            cells.append(cell)
        lines.append("|" + "|".join(cells) + "|")
        lines.append(top_line)

    table_str = "\n".join(lines)
    table_width = len(top_line)
    table_height = len(lines)
    return table_str, table_width, table_height


# ---------- contributions grid builder (WEEKS columns) ----------
def build_contrib_grid(repo_weekly: Dict[str, List[int]], repo_order: List[str]) -> str:
    label_w = max(10, max((len(r) for r in repo_order), default=10))
    label_w = min(label_w, 28)
    cols = WEEKS

    lines = []
    for repo in repo_order:
        weeks = repo_weekly.get(repo, [0] * cols)
        if len(weeks) < cols:
            weeks = ([0] * (cols - len(weeks))) + weeks
        max_val = max(weeks) or 1
        row_cells = []
        for w in weeks:
            ratio = w / max_val if max_val > 0 else 0.0
            idx = int(round(ratio * (len(SHADES) - 1)))
            idx = max(0, min(len(SHADES) - 1, idx))
            row_cells.append(SHADES[idx])
        vis_name = repo
        if len(vis_name) > label_w:
            vis_name = vis_name[: label_w - 1] + "…"
        else:
            vis_name = vis_name.ljust(label_w)
        lines.append(f"{vis_name} {' '.join(row_cells)}")

    legend = " " * label_w + " " + " ".join(SHADES[1:]) + "  (low->high)"
    lines.append(legend)

    now = datetime.now(timezone.utc)
    axis_cells = []
    for i in range(cols):
        if i % 4 == 0:
            days_back = (cols - 1 - i) * 7
            dt = now - timedelta(days=days_back)
            axis_cells.append(dt.strftime("%b")[0])
        else:
            axis_cells.append(" ")
    axis_line = " " * label_w + " " + " ".join(axis_cells)
    lines.append(axis_line)

    return "\n".join(lines)


def build_rows_for_table(top_public: List[dict], token: str | None) -> List[dict]:
    rows = []
    for repo in top_public:
        owner = repo["owner"]["login"]
        name = repo["name"]
        html_url = repo.get("html_url", f"https://github.com/{owner}/{name}")
        langs = fetch_languages(owner, name, token)
        total_bytes = sum(langs.values()) if langs else 0
        if langs and total_bytes > 0:
            sorted_langs = sorted(langs.items(), key=lambda x: x[1], reverse=True)
            top_lang, top_bytes = sorted_langs[0]
            pct = (top_bytes / total_bytes) * 100 if total_bytes else 0
            lang_label = f"{top_lang} ({pct:.0f}%)"
        else:
            lang_label = "Unknown (0%)"
        commits = get_commit_count(owner, name, token=token)
        branches = get_branch_count(owner, name, token=token)
        last_commit = repo.get("pushed_at", "")
        try:
            if last_commit:
                last_commit = last_commit.rstrip("Z")
                last_commit = datetime.fromisoformat(last_commit).strftime("%Y-%m-%d")
        except Exception:
            pass
        rows.append({
            "name_text": name,
            "name_url": html_url,
            "language": lang_label,
            "size": total_bytes,
            "commits": commits,
            "last_commit": last_commit,
            "branches": branches
        })
    return rows


# ---------- code_frequency wrapper to get weekly lines-changed (additions+deletions) ----------
def fetch_code_frequency(owner: str, repo: str, token: str | None = None) -> Optional[List[int]]:
    """Return list of weekly "changes" (additions + abs(deletions)) oldest->newest.
    If unavailable, return None to indicate fallback required.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/code_frequency"
    r = _retry_stats_get(url, token=token)
    if not r:
        return None
    try:
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        weeks = []
        for item in data:
            # item = [week_unix_ts, additions, deletions]
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            additions = int(item[1]) if item[1] is not None else 0
            deletions = int(item[2]) if item[2] is not None else 0
            weeks.append(abs(additions) + abs(deletions))
        if len(weeks) >= WEEKS:
            return weeks[-WEEKS:]
        pad = [0] * (WEEKS - len(weeks))
        return pad + weeks
    except Exception:
        return None


# ---------- simple ascii plot (adapted from the provided inspiration) ----------

def _isnum(n):
    try:
        return not isnan(float(n))
    except Exception:
        return False


def plot_ascii(series, cfg=None):
    if len(series) == 0:
        return ''
    if not isinstance(series[0], list):
        if all((not _isnum(n)) for n in series):
            return ''
        else:
            series = [series]
    cfg = cfg or {}
    minimum = cfg.get('min', min(filter(_isnum, [j for i in series for j in i])))
    maximum = cfg.get('max', max(filter(_isnum, [j for i in series for j in i])))
    symbols = cfg.get('symbols', ['┼', '┤', '╶', '╴', '─', '╰', '╭', '╮', '╯', '│'])
    if minimum > maximum:
        raise ValueError('The min value cannot exceed the max value.')
    interval = maximum - minimum
    offset = cfg.get('offset', 8)
    height = cfg.get('height', int(cfg.get('height', interval if interval > 0 else 4)))
    ratio = height / interval if interval > 0 else 1
    min2 = int(floor(minimum * ratio))
    max2 = int(floor(maximum * ratio))
    def clamp(n):
        return min(max(n, minimum), maximum)
    def scaled(y):
        return int(round(clamp(y) * ratio) - min2)
    rows = max2 - min2
    width = 0
    for i in range(0, len(series)):
        width = max(width, len(series[i]))
    width += offset
    placeholder = cfg.get('format', '{:8.2f} ')
    result = [[' '] * width for i in range(rows + 1)]
    for y in range(min2, max2 + 1):
        label = placeholder.format(maximum - ((y - min2) * interval / (rows if rows else 1)))
        x = max(offset - len(label), 0)
        for idx, ch in enumerate(label):
            if x + idx < width:
                result[y - min2][x + idx] = ch
        result[y - min2][offset - 1] = symbols[0] if y == 0 else symbols[1]
    d0 = series[0][0]
    if _isnum(d0):
        result[rows - scaled(d0)][offset - 1] = symbols[0]
    for i in range(0, len(series)):
        for x in range(0, len(series[i]) - 1):
            d0 = series[i][x + 0]
            d1 = series[i][x + 1]
            if (not _isnum(d0)) and (not _isnum(d1)):
                continue
            if (not _isnum(d0)) and _isnum(d1):
                result[rows - scaled(d1)][x + offset] = symbols[2]
                continue
            if _isnum(d0) and (not _isnum(d1)):
                result[rows - scaled(d0)][x + offset] = symbols[3]
                continue
            y0 = scaled(d0)
            y1 = scaled(d1)
            if y0 == y1:
                result[rows - y0][x + offset] = symbols[4]
                continue
            result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
            result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]
            start = min(y0, y1) + 1
            end = max(y0, y1)
            for y in range(start, end):
                result[rows - y][x + offset] = symbols[9]
    return '\n'.join([''.join(row).rstrip() for row in result])


def expand_weeks_to_days(weekly: List[int]) -> List[float]:
    # evenly distribute weekly total across 7 days; returns list of length len(weekly)*7 oldest->newest
    days: List[float] = []
    for w in weekly:
        per_day = (w / 7.0) if w is not None else 0.0
        days.extend([per_day] * 7)
    return days


def aggregate_daily_changes(repos_weeks: Dict[str, List[int]]) -> List[float]:
    # produce aggregated daily series oldest->newest
    days_len = WEEKS * 7
    agg = [0.0] * days_len
    for weeks in repos_weeks.values():
        if len(weeks) < WEEKS:
            weeks = ([0] * (WEEKS - len(weeks))) + weeks
        days = expand_weeks_to_days(weeks)
        for i, v in enumerate(days[-days_len:]):
            agg[i] += v
    return agg


def build_readme(ascii_table: str, contrib_grid: str, ascii_plot: str) -> str:
    return (
        "<pre>\n"
        "                           ┏━┓┏━╸┏━╸┏━╸┏┓╻╺┳╸   ┏━┓┏━╸┏━┓┏━┓   ┏━┓┏━╸╺┳╸╻╻ ╻╻╺┳╸╻ ╻                           \n"
        "                           ┣┳┛┣╸ ┃  ┣╸ ┃┗┫ ┃    ┣┳┛┣╸ ┣━┛┃ ┃   ┣━┫┃   ┃ ┃┃┏┛┃ ┃ ┗┳┛                           \n"
        "                           ╹┗╸┗━╸┗━╸┗━╸╹ ╹ ╹    ╹┗╸┗━╸╹  ┗━┛   ╹ ╹┗━╸ ╹ ╹┗┛ ╹ ╹  ╹                            \n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n\n"
        f"{ascii_table}\n\n\n"
        f"{contrib_grid}\n\n\n"
        f"Activity (daily, centered on long-term mean):\n"
        f"{ascii_plot}\n"
        "</pre>\n"
    )


def main() -> None:
    token = auth_token()
    try:
        all_repos = fetch_repos_for_user(token=token)
    except Exception as e:
        print("Failed to fetch repositories:", e, file=sys.stderr)
        sys.exit(1)

    public_repos = [r for r in all_repos if not r.get("private")]
    private_repos = [r for r in all_repos if r.get("private")]

    top_public = public_repos[:TOP_N]
    rows = build_rows_for_table(top_public, token)

    ascii_table, ascii_width, ascii_height = make_ascii_table_with_links(rows)

    # gather weekly commit activity (oldest->newest)
    repo_weekly: Dict[str, List[int]] = {}
    repo_order: List[str] = []

    for r in rows:
        name = r["name_text"]
        owner = r.get("name_url", "").split("/github.com/")[-1].split("/")[0] if r.get("name_url") else USERNAME
        # owner default to USERNAME; primary key is name
        owner = USERNAME
        weekly = repo_commit_activity(owner, name, token=token)
        if len(weekly) < WEEKS:
            weekly = ([0] * (WEEKS - len(weekly))) + weekly
        repo_weekly[name] = weekly
        repo_order.append(name)

    restricted_name = "restricted"
    if private_repos and token:
        agg = [0] * WEEKS
        for repo in private_repos:
            owner = repo["owner"]["login"]
            name = repo["name"]
            weekly = repo_commit_activity(owner, name, token=token)
            if len(weekly) < WEEKS:
                weekly = ([0] * (WEEKS - len(weekly))) + weekly
            for i in range(WEEKS):
                agg[i] += weekly[i]
        repo_weekly[restricted_name] = agg
        repo_order.append(restricted_name)
    else:
        if token:
            repo_weekly[restricted_name] = [0] * WEEKS
            repo_order.append(restricted_name)

    contrib_grid = build_contrib_grid(repo_weekly, repo_order)

    # Build code-frequency (lines changed) weekly series per repo and aggregate daily
    repo_codefreq_weeks: Dict[str, List[int]] = {}
    for repo in rows:
        name = repo["name_text"]
        owner = USERNAME
        weeks = fetch_code_frequency(owner, name, token=token)
        if weeks is None:
            # fallback: approximate by using commit counts as a proxy
            weeks = repo_weekly.get(name, [0] * WEEKS)
        if len(weeks) < WEEKS:
            weeks = ([0] * (WEEKS - len(weeks))) + weeks
        repo_codefreq_weeks[name] = weeks

    # include restricted (private) codefreq if token provided
    if private_repos and token:
        agg_weeks = [0] * WEEKS
        for repo in private_repos:
            owner = repo["owner"]["login"]
            name = repo["name"]
            weeks = fetch_code_frequency(owner, name, token=token)
            if weeks is None:
                weeks = repo_weekly.get(name, [0] * WEEKS)
            if len(weeks) < WEEKS:
                weeks = ([0] * (WEEKS - len(weeks))) + weeks
            for i in range(WEEKS):
                agg_weeks[i] += weeks[i]
        repo_codefreq_weeks[restricted_name] = agg_weeks

    daily_agg = aggregate_daily_changes(repo_codefreq_weeks)  # oldest->newest, per-day
    if len(daily_agg) == 0:
        ascii_plot = "(no activity data)"
    else:
        mean = sum(daily_agg) / len(daily_agg)
        centered = [v - mean for v in daily_agg]
        # choose reasonable height based on dynamic range
        rng = max(centered) - min(centered) if len(centered) > 0 else 0
        height = 8
        try:
            ascii_plot = plot_ascii(centered, {'height': height, 'format': '{:8.2f} '})
        except Exception:
            ascii_plot = plot_ascii(centered[:200], {'height': height, 'format': '{:8.2f} '})

    readme = build_readme(ascii_table, contrib_grid, ascii_plot)

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)

    print("README.md updated.")


if __name__ == "__main__":
    main()
