#!/usr/bin/env python3
"""
scripts/update_readme.py

Gather GitHub repo activity and update README with:
 1. A table of top repos (and aggregated private repos)
 2. A heatmap of weekly commit activity per repo
 3. A line chart of total commits over time

We use only ASCII/text art so it fits in a GitHub README.
"""

from __future__ import annotations
import os, sys, time, re
from datetime import datetime, timezone, timedelta
from math import ceil, floor, isnan
from typing import Dict, List, Optional
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
USERNAME = "chasenunez"     # GitHub username/owner
TOP_N = 10                  # Number of top repos to display (including "restricted" if any)
WEEKS = 42                 # How many weeks of history to include (columns in heatmap)
MAX_COLUMNS = 110           # Maximum allowed width of output in characters
SHADES = [" ", "░", "▒", "▓", "█"]  # Heatmap intensity glyphs low->high
PLOT_HEIGHT = 10            # Vertical resolution of line chart
# ----------------------

GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})"
})

def auth_token() -> Optional[str]:
    """Get GitHub token from environment if available."""
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

def gh_get(url: str, params: dict|None=None, token: Optional[str]=None, timeout: int=30) -> requests.Response:
    """Send a GET request with optional GitHub token header."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp

def _get_paginated(url: str, params: dict|None=None, token: Optional[str]=None) -> List[dict]:
    """Retrieve all pages from a GitHub API endpoint that uses pagination."""
    out: List[dict] = []
    params = dict(params or {})
    params.setdefault("per_page", 100)
    next_url = url
    while next_url:
        r = gh_get(next_url, params=params if next_url == url else None, token=token)
        page = r.json() or []
        if isinstance(page, list):
            out.extend(page)
        else:
            break
        # Follow 'next' link header
        link = r.headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            next_url = m.group(1)
            params = None  # parameters are in the URL now
        else:
            break
    return out

def fetch_repos_for_user(token: Optional[str] = None) -> List[dict]:
    """Fetch all repos for the user (owner), sorted by recent update."""
    if token:
        # Fetch including private repos if token is provided
        url = f"{GITHUB_API}/user/repos"
        params = {"sort": "updated", "direction": "desc", "affiliation": "owner"}
    else:
        url = f"{GITHUB_API}/users/{USERNAME}/repos"
        params = {"sort": "updated", "direction": "desc"}
    return _get_paginated(url, params=params, token=token)

def _retry_stats_get(url: str, token: Optional[str]=None) -> Optional[requests.Response]:
    """
    GitHub Stats endpoints may return 202 if data is being generated.
    Retry with exponential backoff up to a few times.
    """
    attempt = 0
    while attempt < 3:
        try:
            r = gh_get(url, token=token)
        except requests.HTTPError:
            return None
        if r.status_code == 202:
            # Data not ready yet; wait and retry
            time.sleep(1 * (2 ** attempt))
            attempt += 1
            continue
        return r
    return None

def repo_commit_activity(owner: str, repo: str, token: Optional[str]=None) -> List[int]:
    """
    Get weekly commit counts for the given repo (last 52 weeks).
    Returns a list of length WEEKS (oldest -> newest), padding with zeros if needed.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    r = _retry_stats_get(url, token=token)
    if r:
        try:
            data = r.json()
            if isinstance(data, list) and data:
                weeks = [int(w.get("total", 0)) for w in data]
                if len(weeks) >= WEEKS:
                    return weeks[-WEEKS:]
                # Pad at the front if less than WEEKS available
                return [0] * (WEEKS - len(weeks)) + weeks
        except Exception:
            pass
    # In case of error or no data, return zeros
    return [0] * WEEKS

def get_commit_count(owner: str, repo: str, token: Optional[str]=None) -> int:
    """
    Get total commit count via GitHub API.
    Uses paginated commits endpoint to find the 'last' page, or fallback on length.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    params = {"per_page": 1}
    try:
        r = gh_get(url, params=params, token=token)
    except requests.HTTPError as e:
        # Repo might be empty (409) or inaccessible; return 0
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
    # If no Link or parsing fails, count items in response
    try:
        commits = r.json()
        if isinstance(commits, list):
            return len(commits)
    except Exception:
        pass
    return 0

def get_branch_count(owner: str, repo: str, token: Optional[str]=None) -> int:
    """Count branches by paginating the branches endpoint."""
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

def fetch_languages(owner: str, repo: str, token: Optional[str]=None) -> Dict[str,int]:
    """Fetch language byte counts for a repo."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/languages"
    try:
        r = gh_get(url, token=token)
        return r.json() or {}
    except requests.HTTPError:
        return {}

def make_ascii_table_with_links(rows: List[dict], max_width: Optional[int] = None) -> tuple[str,int,int]:
    """
    Build an ASCII table (Markdown-friendly) with clickable repo links.
    Returns (table_string, table_width, table_height).
    If max_width is set, attempt to trim the first column to fit.
    """
    cols = ["Repository", "Main Language", "Total Bytes", "Total Commits", 
            "Date of Last Commit", "Branches"]
    # Prepare row data
    data_rows = []
    for r in rows:
        data_rows.append([
            r.get("name_text", ""),
            r.get("language", ""),
            str(r.get("size", "")),
            str(r.get("commits", "")),
            r.get("last_commit", ""),
            str(r.get("branches", ""))
        ])
    # Compute initial column widths
    col_widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))
    # Add padding (2 spaces)
    col_widths = [w + 2 for w in col_widths]
    ncols = len(col_widths)
    table_width = sum(col_widths) + (ncols + 1)
    # If too wide, shrink the first column (Repository) as needed
    if max_width and table_width > max_width:
        excess = table_width - max_width
        min_first = 10 + 2  # allow at least 10 chars + 2 spaces
        reduce_amt = min(excess, col_widths[0] - min_first)
        if reduce_amt > 0:
            col_widths[0] -= reduce_amt
            table_width = sum(col_widths) + (ncols + 1)
    # Build top border
    top_line = "+" + "+".join("-" * w for w in col_widths) + "+"
    # Build header row
    header_cells = []
    for i, c in enumerate(cols):
        inner = col_widths[i] - 2
        header_cells.append(" " + c.center(inner) + " ")
    header_line = "|" + "|".join(header_cells) + "|"
    lines = [top_line, header_line, top_line]
    # Build each data row
    for r in rows:
        # Repository cell with HTML link
        name = r.get("name_text", "")
        url = r.get("name_url", "")
        inner_w0 = col_widths[0] - 2
        # Truncate repo name if too long
        if len(name) > inner_w0:
            vis_name = name[:inner_w0 - 1] + "…"
        else:
            vis_name = name
        left_pad = (inner_w0 - len(vis_name)) // 2
        right_pad = inner_w0 - len(vis_name) - left_pad
        repo_cell = "|" + " " * (left_pad + 1) + f'<a href="{url}">{vis_name}</a>' + " " * (right_pad + 1)
        # Other cells centered
        other_vals = [
            r.get("language", ""),
            str(r.get("size", "")),
            str(r.get("commits", "")),
            r.get("last_commit", ""),
            str(r.get("branches", ""))
        ]
        other_cells = []
        for i, val in enumerate(other_vals, start=1):
            w = col_widths[i] - 2
            pad_left = (w - len(val)) // 2
            pad_right = w - len(val) - pad_left
            other_cells.append(" " + " " * pad_left + val + " " * pad_right + " ")
        line = repo_cell + "|".join(other_cells) + "|"
        lines.append(line)
        lines.append(top_line)
    table_str = "\n".join(lines)
    table_height = len(lines)
    return table_str, table_width, table_height

def build_contrib_grid(repo_weekly: Dict[str, List[int]], repo_order: List[str]) -> str:
    """
    Build a heatmap of weekly commits per repo (ASCII art).
    Rows = repos (in repo_order), Columns = last WEEKS weeks (oldest->newest).
    Each cell is shaded relative to that repo's max week.
    Adds a legend and month initials on bottom axis.
    """
    label_w = max(10, max((len(r) for r in repo_order), default=10))
    label_w = min(label_w, 28)  # cap label width
    cols = WEEKS
    lines = []
    now = datetime.now(timezone.utc)
    for repo in repo_order:
        weeks = repo_weekly.get(repo, [0] * cols)
        if len(weeks) < cols:
            weeks = [0] * (cols - len(weeks)) + weeks
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
    # Legend line
    legend = " " * label_w + " " + " ".join(SHADES[1:]) + "  (low→high)"
    lines.append(legend)
    # X-axis with month initials (every 4 weeks)
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

def expand_weeks_to_days(weekly: List[float]) -> List[float]:
    """Expand weekly data to daily (uniformly) for finer plots (not used in final code)."""
    days: List[float] = []
    for w in weekly:
        per_day = (w / 7.0) if w is not None else 0.0
        days.extend([per_day] * 7)
    return days

def aggregate_daily_bytes(repos_weeks_bytes: Dict[str, List[float]]) -> List[float]:
    """Aggregate multiple repos' weekly data into one daily series (not used in final code)."""
    days_len = WEEKS * 7
    agg = [0.0] * days_len
    for weeks in repos_weeks_bytes.values():
        if len(weeks) < WEEKS:
            weeks = [0.0] * (WEEKS - len(weeks)) + weeks
        days = expand_weeks_to_days(weeks)
        for i, v in enumerate(days[-days_len:]):
            agg[i] += v
    return agg

def _isnum(n):
    """Helper to check if a value can be treated as a number."""
    try:
        return not isnan(float(n))
    except Exception:
        return False

def plot_with_mean(series, cfg=None) -> str:
    """
    ASCII line plot (adapted) with a dotted long-term mean line.
    Takes a series (list of numbers) oldest->newest.
    Config keys: 'height', 'format', 'offset'.
    """
    if not series:
        return ''
    # Wrap into list-of-list if needed
    if not isinstance(series[0], list):
        if all(isnan(n) for n in series):
            return ''
        else:
            series = [series]
    cfg = cfg or {}
    flattened = [j for sub in series for j in sub]
    numeric = [x for x in flattened if _isnum(x)]
    if not numeric:
        return ''
    minimum = cfg.get('min', min(numeric))
    maximum = cfg.get('max', max(numeric))
    symbols = cfg.get('symbols', ['┼','┤','╶','╴','─','╰','╭','╮','╯','│'])
    if minimum > maximum:
        raise ValueError("Min cannot exceed max for plot.")
    interval = maximum - minimum
    offset = cfg.get('offset', max(8, len(cfg.get('format', "{:8.1f} ").format(maximum))))
    height = cfg.get('height', PLOT_HEIGHT)
    ratio = height / interval if interval > 0 else 1
    min2 = int(floor(minimum * ratio))
    max2 = int(ceil(maximum * ratio))
    def clamp(y):
        return min(max(y, minimum), maximum)
    def scaled(y):
        return int(round(clamp(y) * ratio) - min2)
    rows = max2 - min2
    width = max(len(s) for s in series) + offset
    placeholder = cfg.get('format', "{:8.1f} ")
    # Initialize blank grid
    result = [[' '] * width for _ in range(rows + 1)]
    # Draw Y-axis labels and borders
    for y in range(min2, max2 + 1):
        label = placeholder.format(maximum - ((y - min2) * interval / (rows if rows else 1)))
        pos = max(offset - len(label), 0)
        for idx, ch in enumerate(label):
            if pos + idx < width:
                result[y - min2][pos + idx] = ch
        result[y - min2][offset - 1] = symbols[0] if y == 0 else symbols[1]
    # Draw data line(s)
    for series_i in series:
        for x in range(len(series_i) - 1):
            d0, d1 = series_i[x], series_i[x+1]
            if not _isnum(d0) and not _isnum(d1):
                continue
            # Handle NaN start or end
            if not _isnum(d0) and _isnum(d1):
                result[rows - scaled(d1)][x + offset] = symbols[2]; continue
            if _isnum(d0) and not _isnum(d1):
                result[rows - scaled(d0)][x + offset] = symbols[3]; continue
            # Both are numbers:
            y0, y1 = scaled(d0), scaled(d1)
            if y0 == y1:
                result[rows - y0][x + offset] = symbols[4]
            else:
                # Draw diagonal ends and vertical filling
                result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
                result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]
                for y in range(min(y0, y1)+1, max(y0, y1)):
                    result[rows - y][x + offset] = symbols[9]
    # Draw dotted mean line
    mean_val = sum(numeric) / len(numeric)
    try:
        mean_scaled = scaled(mean_val)
        mean_row = rows - mean_scaled
        mean_row = max(0, min(rows, mean_row))
        for c in range(offset, width):
            if result[mean_row][c] == ' ':
                result[mean_row][c] = '┄'
    except Exception:
        pass
    return "\n".join("".join(row).rstrip() for row in result)

def build_readme(ascii_table: str, contrib_grid: str, ascii_plot: str) -> str:
    """Combine all parts into the final README string (wrapped in a <pre> block)."""
    header = (
        "<pre>\n"
        "                           ┏━┓┏━╸┏━╸┏━╸┏┓╻╺┳╸   ┏━┓┏━╸┏━┓┏━┓   ┏━┓┏━╸╺┳╸╻╻ ╻╻╺┳╸╻ ╻                           \n"
        "                           ┣┳┛┣╸ ┃  ┣╸ ┃┗┫ ┃    ┣┳┛┣╸ ┣━┛┃ ┃   ┣━┫┃   ┃ ┃┃┏┛┃ ┃ ┗┳┛                           \n"
        "                           ╹┗╸┗━╸┗━╸┗━╸╹ ╹ ╹    ╹┗╸┗━╸╹  ┗━┛   ╹ ╹┗━╸ ╹ ╹┗┛ ╹ ╹  ╹                            \n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n\n"
    )
    # return (
    #     header
    #     f"{ascii_table}\n\n\n"
    #     f"{contrib_grid}\n\n\n"
    #     f"{ascii_plot}\n"
    #     "</pre>\n"
    # )

def main() -> None:
    token = auth_token()
    try:
        all_repos = fetch_repos_for_user(token=token)
    except Exception as e:
        print("Failed to fetch repositories:", e, file=sys.stderr)
        sys.exit(1)
    # Separate public and private
    public_repos = [r for r in all_repos if not r.get("private")]
    private_repos = [r for r in all_repos if r.get("private")]
    # Determine top N public repos (leaving room for "restricted" if there are private repos)
    if private_repos and token:
        count_public = max(0, TOP_N - 1)
    else:
        count_public = TOP_N
    top_public = public_repos[:count_public]
    # Build table rows for the top public repos
    rows = []
    for repo in top_public:
        owner = repo["owner"]["login"]
        name = repo["name"]
        html_url = repo.get("html_url", f"https://github.com/{owner}/{name}")
        langs = fetch_languages(owner, name, token)
        total_bytes = sum(langs.values()) if langs else 0
        if langs and total_bytes > 0:
            top_lang, top_bytes = sorted(langs.items(), key=lambda x: x[1], reverse=True)[0]
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
    # Generate ASCII table (with clickable repo links)
    ascii_table, table_width, table_height = make_ascii_table_with_links(rows, max_width=MAX_COLUMNS)
    print(f"Table built: width={table_width}, height={table_height}")
    # Prepare to fetch commit activity for each repo in parallel
    repo_names = [r["name_text"] for r in rows]
    repo_weekly: Dict[str, List[int]] = {}
    print(f"Fetching commit_activity for {len(repo_names)} repos...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(repo_commit_activity, USERNAME, name, token): name for name in repo_names}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                weeks = fut.result() or [0]*WEEKS
                if len(weeks) < WEEKS:
                    weeks = [0] * (WEEKS - len(weeks)) + weeks
                repo_weekly[name] = weeks
                print(f"  commit_activity fetched for {name}")
            except Exception as e:
                print(f"  commit_activity failed for {name}: {e}", file=sys.stderr)
                repo_weekly[name] = [0] * WEEKS
    # Aggregate private repos into "restricted" if any
    repo_order: List[str] = list(repo_names)
    if private_repos and token:
        print(f"Aggregating {len(private_repos)} private repos as 'restricted'...")
        agg_weekly = [0] * WEEKS
        for repo in private_repos:
            owner = repo["owner"]["login"]
            name = repo["name"]
            weeks = repo_commit_activity(owner, name, token=token) or [0]*WEEKS
            if len(weeks) < WEEKS:
                weeks = [0] * (WEEKS - len(weeks)) + weeks
            for i, v in enumerate(weeks):
                agg_weekly[i] += v
        repo_weekly["restricted"] = agg_weekly
        repo_order.append("restricted")
    # Build heatmap ASCII grid
    contrib_grid = build_contrib_grid(repo_weekly, repo_order)
    # Build weekly total commits series (sum over repos) for line chart
    weekly_totals: List[float] = [0.0] * WEEKS
    for name in repo_order:
        weeks = repo_weekly.get(name, [0]*WEEKS)
        if len(weeks) < WEEKS:
            weeks = [0]*(WEEKS - len(weeks)) + weeks
        for i, v in enumerate(weeks):
            weekly_totals[i] += float(v)
    # Prepare series for plotting: one column per week
    if not any(weekly_totals):
        ascii_plot = "(no activity data)"
    else:
        mean = sum(weekly_totals) / len(weekly_totals)
        centered = [v - mean for v in weekly_totals]
        # Scale for readability (use K/M if needed)
        max_abs = max(abs(x) for x in centered) or 1.0
        if max_abs >= 1_000_000:
            scale = 1_000_000.0; scale_suffix = "M"; dec_places = 1
        elif max_abs >= 1_000:
            scale = 1_000.0; scale_suffix = "K"; dec_places = 1
        else:
            scale = 1.0; scale_suffix = ""; dec_places = 1
        scaled_series = [x/scale for x in centered]
        # Ensure length matches WEEKS
        if len(scaled_series) != WEEKS:
            if len(scaled_series) > WEEKS:
                scaled_series = scaled_series[-WEEKS:]
            else:
                scaled_series = [0.0]*(WEEKS-len(scaled_series)) + scaled_series
        # Build label format to fit table_width
        fmt_w = 7
        fmt_template = f"{{:{fmt_w}.{dec_places}f}} "
        offset_len = len(fmt_template.format(0.0))
        required_width = offset_len + len(scaled_series) + 1
        # If too wide, reduce decimals or truncate
        while required_width > table_width and dec_places > 0:
            dec_places -= 1
            fmt_template = f"{{:{fmt_w}.{dec_places}f}} "
            offset_len = len(fmt_template.format(0.0))
            required_width = offset_len + len(scaled_series) + 1
        while required_width > table_width and fmt_w > 4:
            fmt_w -= 1
            fmt_template = f"{{:{fmt_w}.{dec_places}f}} "
            offset_len = len(fmt_template.format(0.0))
            required_width = offset_len + len(scaled_series) + 1
        # Truncate oldest weeks if still too wide
        if required_width > table_width:
            max_points = max(6, table_width - offset_len - 1)
            scaled_series = scaled_series[-max_points:]
            required_width = offset_len + len(scaled_series) + 1
        label_format = fmt_template
        # Plot with mean
        try:
            ascii_plot_body = plot_with_mean(scaled_series, {"height": PLOT_HEIGHT, "format": label_format, "offset": offset_len})
        except Exception:
            # Fallback: simpler format
            cfg_min = {"height": max(4, PLOT_HEIGHT//2), "format": "{:5.0f} ", "offset": 5}
            ascii_plot_body = plot_with_mean(scaled_series[-min(len(scaled_series), table_width - cfg_min["offset"] - 1):], cfg_min)
        # Prepare X-axis labels (month initials)
        now = datetime.now(timezone.utc)
        axis_cells = []
        for i in range(len(scaled_series)):
            # Mark every 4th week with month initial
            if i % 4 == 0:
                days_back = (WEEKS - 1 - i) * 7
                dt = now - timedelta(days=days_back)
                axis_cells.append(dt.strftime("%b")[0])
            else:
                axis_cells.append(" ")
        axis_line = " " * offset_len + " " + " ".join(axis_cells)
        # Assemble final ascii_plot string
        label_line = f"Activity (weekly commits{' /' + scale_suffix if scale_suffix else ''}; 1 col = 1 week; dotted = mean):"
        ascii_plot = label_line + "\n" + ascii_plot_body + "\n" + axis_line
    # Build full README content and write to file
    readme = build_readme(ascii_table, contrib_grid, ascii_plot)
    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)
    print("README.md updated.")

if __name__ == "__main__":
    main()
