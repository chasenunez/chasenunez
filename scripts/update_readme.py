#!/usr/bin/env python3
"""
scripts/update_readme.py

Shows bytes-per-day activity in the README:
 - Uses /stats/code_frequency (weekly additions+deletions -> lines changed)
 - Converts lines -> bytes via AVG_BYTES_PER_LINE (approximation)
 - Expands weekly -> daily (even distribution)
 - Produces ASCII line plot (adapted from the provided `plot` inspiration)
   and overlays a dotted horizontal mean line
Other features:
 - pagination, token support, private repo aggregation, thread pool for speed
"""
from __future__ import annotations
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from math import ceil, floor, isnan
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

RESTRICTED_NAME = "restricted"

# APPROX: bytes per line changed. Adjust if you want a different conversion.
AVG_BYTES_PER_LINE = 40.0

# Plot settings
PLOT_HEIGHT = 10  # rows for plot; increase for more vertical resolution
PLOT_FORMAT = "{:8.1f} "  # label formatting (right-justified)
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
    if token:
        url = f"{GITHUB_API}/user/repos"
        params = {"sort": "updated", "direction": "desc", "affiliation": "owner"}
    else:
        url = f"{GITHUB_API}/users/{USERNAME}/repos"
        params = {"sort": "updated", "direction": "desc"}
    return _get_paginated(url, params=params, token=token)


def _retry_stats_get(url: str, token: str | None = None) -> Optional[requests.Response]:
    attempt = 0
    while attempt < STATS_MAX_RETRIES:
        try:
            r = gh_get(url, token=token)
        except requests.HTTPError:
            return None
        if r.status_code == 202:
            sleep_time = STATS_RETRY_SLEEP * (2 ** attempt)
            time.sleep(sleep_time)
            attempt += 1
            continue
        return r
    return None


def repo_commit_activity(owner: str, repo: str, token: str | None = None) -> List[int]:
    """
    Keep as fallback for commit counts. Prefer commit_activity; otherwise zeros.
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
    return [0] * WEEKS


def get_commit_count(owner: str, repo: str, token: str | None = None) -> int:
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


def fetch_code_frequency(owner: str, repo: str, token: str | None = None) -> Optional[List[int]]:
    """Return list of weekly lines-changed (additions + abs(deletions)) oldest->newest."""
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


# ---------- plotting: adapted from your inspiration with mean overlay ----------
def _isnum(n):
    try:
        return not isnan(float(n))
    except Exception:
        return False


def plot_with_mean(series, cfg=None):
    """
    Adapted `plot()` from the inspiration code, with an added dotted mean overlay.
    series: list of numbers (oldest->newest)
    cfg: optional dict with keys: 'height', 'format' (label), 'min', 'max'
    """
    from math import ceil

    if len(series) == 0:
        return ''

    if not isinstance(series[0], list):
        if all(isnan(n) for n in series):
            return ''
        else:
            series = [series]

    cfg = cfg or {}

    # Flatten and compute min/max using numeric values (ignore NaN)
    flattened = [j for i in series for j in i]
    numeric = [x for x in flattened if _isnum(x)]
    if not numeric:
        return ''

    minimum = cfg.get('min', min(numeric))
    maximum = cfg.get('max', max(numeric))

    default_symbols = ['┼', '┤', '╶', '╴', '─', '╰', '╭', '╮', '╯', '│']
    symbols = cfg.get('symbols', default_symbols)

    if minimum > maximum:
        raise ValueError('The min value cannot exceed the max value.')

    interval = maximum - minimum
    # offset controls space reserved for labels; choose a safe default
    offset = cfg.get('offset', max(8, len(cfg.get('format', PLOT_FORMAT).format(maximum))))
    height = cfg.get('height', PLOT_HEIGHT)
    ratio = height / interval if interval > 0 else 1

    min2 = int(floor(minimum * ratio))
    max2 = int(ceil(maximum * ratio))

    def clamp(n):
        return min(max(n, minimum), maximum)

    def scaled(y):
        return int(round(clamp(y) * ratio) - min2)

    rows = max2 - min2

    width = 0
    for i in range(0, len(series)):
        width = max(width, len(series[i]))
    width += offset

    placeholder = cfg.get('format', PLOT_FORMAT)

    # Build the grid as list of lists for mutability
    result = [[' '] * width for i in range(rows + 1)]

    # axis and labels
    for y in range(min2, max2 + 1):
        # compute label (top->bottom)
        label = placeholder.format(maximum - ((y - min2) * interval / (rows if rows else 1)))
        pos = max(offset - len(label), 0)
        # write label characters into result[y-min2][pos:pos+len(label)]
        for idx, ch in enumerate(label):
            if pos + idx < width:
                result[y - min2][pos + idx] = ch
        result[y - min2][offset - 1] = symbols[0] if y == 0 else symbols[1]

    # first value tick
    try:
        d0 = series[0][0]
        if _isnum(d0):
            result[rows - scaled(d0)][offset - 1] = symbols[0]
    except Exception:
        pass

    # Plot the line(s)
    for i in range(0, len(series)):
        color = None  # we don't use color codes here
        for x in range(0, len(series[i]) - 1):
            d0 = series[i][x + 0]
            d1 = series[i][x + 1]

            if isnan(d0) and isnan(d1):
                continue

            if isnan(d0) and _isnum(d1):
                result[rows - scaled(d1)][x + offset] = symbols[2]
                continue

            if _isnum(d0) and isnan(d1):
                result[rows - scaled(d0)][x + offset] = symbols[3]
                continue

            y0 = scaled(d0)
            y1 = scaled(d1)
            if y0 == y1:
                result[rows - y0][x + offset] = symbols[4]
                continue

            # diagonal pieces
            result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
            result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]

            start = min(y0, y1) + 1
            end = max(y0, y1)
            for y in range(start, end):
                result[rows - y][x + offset] = symbols[9]

    # ----- overlay dotted mean line -----
    # compute mean using numeric values from flattened series
    mean_val = sum(numeric) / len(numeric)
    # scale mean value to row coordinate using same mapping
    try:
        mean_scaled = scaled(mean_val)
        mean_row = rows - mean_scaled
        mean_row = max(0, min(rows, mean_row))
        # draw dotted mean (use '┄') across plotting area (offset .. width-1)
        for c in range(offset, width):
            # don't overwrite existing plot glyphs (only fill spaces)
            if result[mean_row][c] == ' ':
                result[mean_row][c] = '┄'
    except Exception:
        # if mapping fails, skip mean overlay gracefully
        pass

    return '\n'.join([''.join(row).rstrip() for row in result])


def expand_weeks_to_days(weekly: List[float]) -> List[float]:
    days: List[float] = []
    for w in weekly:
        per_day = (w / 7.0) if w is not None else 0.0
        days.extend([per_day] * 7)
    return days


def aggregate_daily_bytes(repos_weeks_bytes: Dict[str, List[float]]) -> List[float]:
    days_len = WEEKS * 7
    agg = [0.0] * days_len
    for weeks in repos_weeks_bytes.values():
        if len(weeks) < WEEKS:
            weeks = ([0.0] * (WEEKS - len(weeks))) + weeks
        days = expand_weeks_to_days(weeks)
        # take last days_len days (oldest->newest)
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
        f"Activity (daily bytes; dotted line = long-term mean):\n"
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

    # sensible defaults if not defined elsewhere
    AVG_BYTES_PER_LINE = globals().get("AVG_BYTES_PER_LINE", 50.0)
    RESTRICTED_NAME = globals().get("RESTRICTED_NAME", "restricted")
    PLOT_HEIGHT = globals().get("PLOT_HEIGHT", 8)

    public_repos = [r for r in all_repos if not r.get("private")]
    private_repos = [r for r in all_repos if r.get("private")]

    top_public = public_repos[:TOP_N]
    rows = build_rows_for_table(top_public, token)

    ascii_table, ascii_width, ascii_height = make_ascii_table_with_links(rows)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    repos_to_query = [r["name_text"] for r in rows]

    # fetch commit_activity in parallel (for contrib grid)
    repo_weekly: Dict[str, List[int]] = {}
    print(f"Fetching commit_activity for {len(repos_to_query)} repos...")
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
                print(f"  commit_activity fetched for {repo_name}")
            except Exception as e:
                print(f"  commit_activity failed for {repo_name}: {e}", file=sys.stderr)
                repo_weekly[repo_name] = [0] * WEEKS

    # fetch code_frequency (lines) in parallel, convert to BYTES (per-week)
    repo_codefreq_weeks_bytes: Dict[str, List[float]] = {}
    print(f"Fetching code_frequency for {len(repos_to_query)} repos...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_code_frequency, USERNAME, repo_name, token): repo_name for repo_name in repos_to_query}
        for fut in as_completed(futures):
            repo_name = futures[fut]
            try:
                weeks_lines = fut.result()
                if weeks_lines is None:
                    weeks_lines = repo_weekly.get(repo_name, [0] * WEEKS)
                if len(weeks_lines) < WEEKS:
                    weeks_lines = ([0] * (WEEKS - len(weeks_lines))) + weeks_lines
                # convert lines -> bytes
                weeks_bytes = [float(x) * AVG_BYTES_PER_LINE for x in weeks_lines]
                repo_codefreq_weeks_bytes[repo_name] = weeks_bytes
                print(f"  code_frequency (lines->bytes) for {repo_name}")
            except Exception as e:
                print(f"  code_frequency failed for {repo_name}: {e}", file=sys.stderr)
                repo_codefreq_weeks_bytes[repo_name] = [0.0] * WEEKS

    repo_order: List[str] = list(repos_to_query)

    # aggregate private repos into RESTRICTED_NAME if token present
    if private_repos and token:
        print(f"Aggregating {len(private_repos)} private repos into '{RESTRICTED_NAME}'...")
        agg_weekly = [0] * WEEKS
        agg_bytes = [0.0] * WEEKS
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
                agg_bytes[i] += float(cf[i]) * AVG_BYTES_PER_LINE

        repo_weekly[RESTRICTED_NAME] = agg_weekly
        repo_codefreq_weeks_bytes[RESTRICTED_NAME] = agg_bytes
        repo_order.append(RESTRICTED_NAME)
    else:
        if token and not private_repos:
            repo_weekly[RESTRICTED_NAME] = [0] * WEEKS
            repo_codefreq_weeks_bytes[RESTRICTED_NAME] = [0.0] * WEEKS
            repo_order.append(RESTRICTED_NAME)

    contrib_grid = build_contrib_grid(repo_weekly, repo_order)

    # ------------- BUILD WIDTH-CONSTRAINED ASCII PLOT ----------------
    # convert aggregated weekly bytes -> daily series (oldest -> newest)
    daily_bytes = aggregate_daily_bytes(repo_codefreq_weeks_bytes)  # oldest->newest per-day

    if not daily_bytes or all(v == 0 for v in daily_bytes):
        ascii_plot = "(no activity data)"
    else:
        # center series on long-term mean
        mean = sum(daily_bytes) / len(daily_bytes)
        centered = [v - mean for v in daily_bytes]

        # automatic scaling to keep axis labels short ('' / 'K' / 'M')
        max_abs = max(abs(x) for x in centered) if centered else 0.0
        if max_abs >= 1_000_000:
            scale = 1_000_000.0
            scale_suffix = "M"
            dec_places = 2
        elif max_abs >= 1_000:
            scale = 1_000.0
            scale_suffix = "K"
            dec_places = 1
        else:
            scale = 1.0
            scale_suffix = ""
            dec_places = 2

        scaled = [x / scale for x in centered]

        # compute label width from actual min/max values (so labels cannot overflow)
        vmin = min(scaled)
        vmax = max(scaled)
        # prepare candidate formatted strings and compute width needed
        fmt_spec = f".{dec_places}f"
        min_label = format(vmin, fmt_spec)
        max_label = format(vmax, fmt_spec)
        # allow sign and one trailing space
        label_width = max(len(min_label), len(max_label), len("0" + format(0, fmt_spec))) + 1
        offset_len = label_width
        safety = 1

        # ensure there's room for at least some plot columns; reduce decimals if needed
        max_plot_points = ascii_width - offset_len - safety
        if max_plot_points < 8:
            # try fewer decimals
            dec_places = max(0, dec_places - 1)
            fmt_spec = f".{dec_places}f"
            min_label = format(vmin, fmt_spec)
            max_label = format(vmax, fmt_spec)
            label_width = max(len(min_label), len(max_label), len("0" + format(0, fmt_spec))) + 1
            offset_len = label_width
            max_plot_points = ascii_width - offset_len - safety

        # as a final fallback, force offset to leave room for at least 6 plot cols
        if max_plot_points < 6:
            offset_len = max(4, ascii_width - 6 - safety)
            max_plot_points = max(6, ascii_width - offset_len - safety)

        # downsample by averaging to fit the width
        def downsample_avg(series: List[float], max_pts: int) -> List[float]:
            n = len(series)
            if n <= max_pts:
                return series[:]
            out: List[float] = []
            step = n / float(max_pts)
            pos = 0.0
            for _ in range(max_pts):
                start = int(round(pos))
                pos += step
                end = int(round(pos))
                if end <= start:
                    end = min(n, start + 1)
                chunk = series[start:end]
                out.append(sum(chunk) / len(chunk))
            return out

        series_fit = downsample_avg(scaled, max_plot_points)

        # build format string with computed width to align with offset_len
        # subtract 1 for final trailing space included in format
        fmt_width = max(1, offset_len - 1)
        label_format = "{:" + str(fmt_width) + fmt_spec + "} "

        cfg = {
            "height": PLOT_HEIGHT,
            "format": label_format,
            "offset": offset_len,
        }

        # generate ASCII plot; caller should be aware labels are in scaled units (K/M) relative to mean
        try:
            ascii_plot_body = plot_ascii(series_fit, cfg)
        except Exception:
            # if plot fails for any unexpected reason, fallback to a short series
            ascii_plot_body = plot_ascii(series_fit[-(max(1, max_plot_points)):], cfg)

        # add a short header line indicating units
        if scale_suffix:
            ascii_plot = f"Activity (daily bytes / {scale_suffix}; dotted line = long-term mean):\n{ascii_plot_body}"
        else:
            ascii_plot = "Activity (daily bytes; dotted line = long-term mean):\n" + ascii_plot_body

    # build README and write
    readme = build_readme(ascii_table, contrib_grid, ascii_plot)

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)

    print("README.md updated.")
