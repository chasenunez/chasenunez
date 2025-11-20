#!/usr/bin/env python3
"""
scripts/update_readme.py

Corrected and annotated version — fixes plotting area (auto-scaling) and draws a dotted mean line.
Other improvements (pagination, retries, fallbacks, private-aggregate) are retained.

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
STATS_MAX_RETRIES = 3
STATS_RETRY_SLEEP = 1  # base seconds, exponential backoff applied
PER_PAGE = 100  # GitHub max per_page

# Name used to aggregate private/restricted repos
RESTRICTED_NAME = "restricted"
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
        except requests.HTTPError:
            # treat errors (e.g., 404, private unauthorized) as not available
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
    Prefer /stats/commit_activity; if unavailable, fall back to zeros or code_frequency
    (do NOT perform per-week /commits queries — that's very slow).
    Returns list of length WEEKS (oldest->newest).
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    r = _retry_stats_get(url, token=token)
    if r is not None:
        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                weeks = [int(w.get("total", 0)) for w in data]
                if len(weeks) >= WEEKS:
                    return weeks[-WEEKS:]
                pad = [0] * (WEEKS - len(weeks))
                return pad + weeks
        except Exception:
            pass

    # FALLBACK: try code_frequency as a lightweight proxy (additions+deletions)
    cf = fetch_code_frequency(owner, repo, token=token)
    if cf is not None:
        # convert lines-changed to a proxy for commit count (heuristic)
        return [int(round(x / 10.0)) for x in cf]
    # final fallback: return zeros instead of doing heavy commits-by-week queries
    return [0] * WEEKS


def repo_weekly_from_commits(owner: str, repo: str, token: str | None = None) -> List[int]:
    """Count commits per each of the last WEEKS weeks by querying commits?since&until.

    NOTE: This function is kept for completeness but is intentionally not used by default
    because it causes many API calls (WEEKS requests per repo).
    """
    now = datetime.now(timezone.utc)
    weeks: List[int] = []
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


# ---------- improved safe plotter (auto-scaling) ----------
def _isnum(n):
    try:
        return not isnan(float(n))
    except Exception:
        return False


def plot_ascii(series, cfg=None):
    """
    Robust ASCII plotter.
    - series: list of numeric values (oldest->newest)
    - cfg: optional dict supporting:
        - 'height': int rows (default 8)
        - 'format': format string for y-axis labels (default '{:8.1f} ')
        - 'draw_mean': bool whether to draw a dotted mean line (default True)
    Returns multiline string (rows top->bottom).
    """
    if not series or all((not _isnum(x)) for x in series):
        return ""

    cfg = cfg or {}
    height = int(cfg.get("height", 8))
    fmt = cfg.get("format", "{:8.1f} ")
    draw_mean = cfg.get("draw_mean", True)

    # filter numeric and convert to floats (keep None/NaN for missing)
    vals = []
    for x in series:
        try:
            vals.append(float(x))
        except Exception:
            vals.append(float('nan'))

    # compute numeric min/max ignoring NaNs
    numeric = [v for v in vals if _isnum(v)]
    if not numeric:
        return ""

    min_val = min(numeric)
    max_val = max(numeric)

    # if min == max, give a tiny range so all points map to center
    if min_val == max_val:
        min_val -= 0.5
        max_val += 0.5

    # label width based on formatted min/max
    sample_max = fmt.format(max_val).rstrip()
    sample_min = fmt.format(min_val).rstrip()
    label_w = max(len(sample_max), len(sample_min), 6)
    offset = label_w + 2  # label + space + axis char

    width = offset + len(series)
    # create grid rows x cols filled with spaces
    grid = [[" "] * width for _ in range(height)]

    # draw y-axis labels and axis char at column offset-1
    for row in range(height):
        # compute label value for this row (top row -> max_val)
        if height == 1:
            yv = max_val
        else:
            yv = max_val - (row * (max_val - min_val) / (height - 1))
        label = fmt.format(yv)
        # ensure label length fits label_w
        label_s = label.rjust(label_w)
        for i, ch in enumerate(label_s):
            grid[row][i] = ch
        grid[row][offset - 1] = "┤"

    # map series values to row indices (0..height-1)
    denom = (max_val - min_val)
    rows_map: List[Optional[int]] = []
    for v in vals:
        if not _isnum(v):
            rows_map.append(None)
        else:
            frac = (v - min_val) / denom if denom != 0 else 0.5
            scaled = int(round(frac * (height - 1)))
            # invert because row 0 is top (max)
            row_idx = (height - 1) - scaled
            # clamp just in case
            row_idx = max(0, min(height - 1, row_idx))
            rows_map.append(row_idx)

    # place point markers (●) at (row_idx, col)
    for x, row_idx in enumerate(rows_map):
        col = offset + x
        if row_idx is None:
            continue
        # do not overwrite label area
        if 0 <= row_idx < height and 0 <= col < width:
            grid[row_idx][col] = "●"

    # optionally draw dotted mean line (use '┄' for dotted)
    if draw_mean:
        mean_val = sum(numeric) / len(numeric)
        frac = (mean_val - min_val) / denom if denom != 0 else 0.5
        mean_row = (height - 1) - int(round(frac * (height - 1)))
        mean_row = max(0, min(height - 1, mean_row))
        for col in range(offset, width):
            # only draw dotted mean where there's no point
            if grid[mean_row][col] == " ":
                grid[mean_row][col] = "┄"

    # convert grid rows into strings and rstrip trailing spaces
    out_lines = ["".join(r).rstrip() for r in grid]
    return "\n".join(out_lines)


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
        f"Activity (daily; dotted line = long-term mean):\n"
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

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Build list of repo names to query (in the same order as rows)
    repos_to_query = [r["name_text"] for r in rows]

    # 1) fetch commit_activity in parallel
    repo_weekly: Dict[str, List[int]] = {}
    print(f"Fetching commit_activity for {len(repos_to_query)} repos in parallel...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(repo_commit_activity, USERNAME, repo_name, token): repo_name for repo_name in repos_to_query}
        for fut in as_completed(futures):
            repo_name = futures[fut]
            try:
                weeks = fut.result()
                if weeks is None:
                    weeks = [0] * WEEKS
                if len(weeks) < WEEKS:
                    weeks = ([0] * (WEEKS - len(weeks))) + weeks
                repo_weekly[repo_name] = weeks
                # progress log
                print(f"  commit_activity fetched for {repo_name}")
            except Exception as e:
                print(f"  commit_activity failed for {repo_name}: {e}", file=sys.stderr)
                repo_weekly[repo_name] = [0] * WEEKS

    # 2) fetch code_frequency in parallel (used for daily plot)
    repo_codefreq_weeks: Dict[str, List[int]] = {}
    print(f"Fetching code_frequency for {len(repos_to_query)} repos in parallel...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_code_frequency, USERNAME, repo_name, token): repo_name for repo_name in repos_to_query}
        for fut in as_completed(futures):
            repo_name = futures[fut]
            try:
                weeks = fut.result()
                if weeks is None:
                    weeks = repo_weekly.get(repo_name, [0] * WEEKS)
                if len(weeks) < WEEKS:
                    weeks = ([0] * (WEEKS - len(weeks))) + weeks
                repo_codefreq_weeks[repo_name] = weeks
                print(f"  code_frequency fetched for {repo_name}")
            except Exception as e:
                print(f"  code_frequency failed for {repo_name}: {e}", file=sys.stderr)
                repo_codefreq_weeks[repo_name] = [0] * WEEKS

    # Build repo_order for display (rows order)
    repo_order: List[str] = list(repos_to_query)

    # include restricted (private) repos aggregated only if token provided
    if private_repos and token:
        print(f"Aggregating {len(private_repos)} private repos into '{RESTRICTED_NAME}'...")
        # aggregate commit_activity (weekly) across private repos
        agg_weekly = [0] * WEEKS
        agg_codefreq = [0] * WEEKS
        for repo in private_repos:
            owner = repo["owner"]["login"]
            name = repo["name"]
            weeks = repo_commit_activity(owner, name, token=token)
            if weeks is None:
                weeks = [0] * WEEKS
            if len(weeks) < WEEKS:
                weeks = ([0] * (WEEKS - len(weeks))) + weeks
            for i in range(WEEKS):
                agg_weekly[i] += weeks[i]

            cf = fetch_code_frequency(owner, name, token=token)
            if cf is None:
                cf = [0] * WEEKS
            if len(cf) < WEEKS:
                cf = ([0] * (WEEKS - len(cf))) + cf
            for i in range(WEEKS):
                agg_codefreq[i] += cf[i]

        # add aggregated rows to both maps and order list
        repo_weekly[RESTRICTED_NAME] = agg_weekly
        repo_codefreq_weeks[RESTRICTED_NAME] = agg_codefreq
        repo_order.append(RESTRICTED_NAME)
    else:
        # If token present but there are no private repos, optionally include an empty restricted row
        if token and not private_repos:
            repo_weekly[RESTRICTED_NAME] = [0] * WEEKS
            repo_codefreq_weeks[RESTRICTED_NAME] = [0] * WEEKS
            repo_order.append(RESTRICTED_NAME)

    # Build contributions grid now that repo_weekly and repo_order are ready
    contrib_grid = build_contrib_grid(repo_weekly, repo_order)

    # aggregate codefreq into daily series and plot
    daily_agg = aggregate_daily_changes(repo_codefreq_weeks)  # oldest->newest, per-day
    if len(daily_agg) == 0:
        ascii_plot = "(no activity data)"
    else:
        # Draw a clear plot automatically scaled to data; draw dotted mean line
        height = 8
        ascii_plot = plot_ascii(daily_agg, {'height': height, 'format': '{:8.1f} ', 'draw_mean': True})

    readme = build_readme(ascii_table, contrib_grid, ascii_plot)

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)

    print("README.md updated.")


if __name__ == "__main__":
    main()
