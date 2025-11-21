#!/usr/bin/env python3
"""
Enhanced update_readme.py with fixes for private repos, heatmap data, and chart width.
"""
import os, sys, time, re
from datetime import datetime, timezone, timedelta
from math import ceil, floor, isnan
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ---------- Configuration ----------
USERNAME = "chasenunez"
TOP_N = 10            # Number of top repositories to include (including private)
WEEKS = 42            # Number of weeks to show in charts
MAX_WIDTH = 110       # Max characters wide for all figures (table, heatmap, plot)
RESTRICTED_NAME = "restricted"
AVG_BYTES_PER_LINE = 40.0
PLOT_HEIGHT = 10
PLOT_FORMAT = "{:8.1f} "
SHADES = [" ", "░", "▒", "▓", "█"]  # For heat map (low→high intensity)
GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})",
})
# -----------------------------------

def auth_token() -> Optional[str]:
    """Get GitHub token from environment (PAT)."""
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

def gh_get(url: str, params: dict=None, token: str=None, timeout: int=30) -> requests.Response:
    """Helper to make GET requests with optional token auth."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp

def _get_paginated(url: str, params: dict=None, token: str=None) -> List[dict]:
    """Handle GitHub pagination (per_page up to 100)."""
    items = []
    params = dict(params or {})
    params.setdefault("per_page", 100)
    next_url = url
    while next_url:
        r = gh_get(next_url, params=params if next_url == url else None, token=token)
        data = r.json() or []
        if isinstance(data, list):
            items.extend(data)
        else:
            break
        link = r.headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            next_url = m.group(1)
            params = None
        else:
            break
    return items

def fetch_repos_for_user(token: str=None) -> List[dict]:
    """
    Fetch all repositories visible to the user.
    Using /user/repos (authenticated) returns both public and private repos:contentReference[oaicite:2]{index=2}.
    """
    if token:
        url = f"{GITHUB_API}/user/repos"
        params = {"sort": "updated", "direction": "desc"}
    else:
        # Without auth, only public repos
        url = f"{GITHUB_API}/users/{USERNAME}/repos"
        params = {"sort": "updated", "direction": "desc"}
    return _get_paginated(url, params=params, token=token)

def _retry_stats_get(url: str, token: str=None) -> Optional[requests.Response]:
    """Retry wrapper for /stats endpoints (which may return 202 if data is not cached)."""
    attempt = 0
    while attempt < 3:
        try:
            r = gh_get(url, token=token)
        except requests.HTTPError:
            return None
        if r.status_code == 202:
            time.sleep(1 * (2 ** attempt))
            attempt += 1
            continue
        return r
    return None

def repo_commit_activity(owner: str, repo: str, token: str=None) -> List[int]:
    """
    Get weekly commit counts for the repo (last up to 52 weeks):contentReference[oaicite:3]{index=3}.
    Returns a list of length WEEKS (oldest->newest).
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    r = _retry_stats_get(url, token=token)
    if r is not None:
        data = r.json()
        if isinstance(data, list):
            weeks = [int(item.get("total", 0)) for item in data]
            if len(weeks) >= WEEKS:
                return weeks[-WEEKS:]
            # Pad front with zeros if fewer weeks returned
            return [0]*(WEEKS - len(weeks)) + weeks
    return [0] * WEEKS

def get_commit_count(owner: str, repo: str, token: str=None) -> int:
    """Get total commits on default branch by using per_page=1 and reading Link header."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    try:
        r = gh_get(url, params={"per_page":1}, token=token)
    except requests.HTTPError as e:
        # e.g. empty repo (409) or not found
        return 0
    link = r.headers.get("Link", "")
    if link:
        m = re.search(r'[&?]page=(\d+)>; rel="last"', link)
        if m:
            return int(m.group(1))
    try:
        commits = r.json()
        if isinstance(commits, list):
            return len(commits)
    except Exception:
        pass
    return 0

def get_branch_count(owner: str, repo: str, token: str=None) -> int:
    """Count branches similarly by reading Link header."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/branches"
    try:
        r = gh_get(url, params={"per_page":1}, token=token)
    except requests.HTTPError:
        return 0
    link = r.headers.get("Link", "")
    if link:
        m = re.search(r'[&?]page=(\d+)>; rel="last"', link)
        if m:
            return int(m.group(1))
    try:
        data = r.json()
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return 0

def fetch_languages(owner: str, repo: str, token: str=None) -> Dict[str,int]:
    """Get languages used in repo (bytes per language)."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/languages"
    try:
        r = gh_get(url, token=token)
        return r.json() or {}
    except requests.HTTPError:
        return {}

def make_ascii_table_with_links(rows: List[dict], max_repo_name_width: int=None) -> Tuple[str,int,int]:
    """
    Build an ASCII table (with Markdown links in repo names).
    Optionally cap the repository name column to max_repo_name_width characters.
    Returns (table_str, table_width, table_height).
    """
    cols = ["Repository", "Main Language", "Total Bytes", "Total Commits", "Last Commit Date", "Branches"]
    data_rows = []
    for r in rows:
        data_rows.append([
            r.get("name_text",""),
            r.get("language",""),
            str(r.get("size","0")),
            str(r.get("commits","0")),
            r.get("last_commit",""),
            str(r.get("branches","0"))
        ])
    # Compute column widths
    widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    widths = [w+2 for w in widths]
    # Cap first column if needed
    if max_repo_name_width and widths:
        inner0 = widths[0] - 2
        if inner0 > max_repo_name_width:
            widths[0] = max_repo_name_width + 2
    # Build table lines
    top_line = "+" + "+".join("-"*w for w in widths) + "+"
    lines = [top_line]
    header = []
    for i,c in enumerate(cols):
        header.append(" " + c.center(widths[i]-2) + " ")
    lines.append("|" + "|".join(header) + "|")
    lines.append(top_line)
    # Rows
    for r in rows:
        cells = []
        vis_name = r.get("name_text","")
        url = r.get("name_url","")
        inner0 = widths[0] - 2
        # Truncate visible repo name if needed
        if len(vis_name) > inner0:
            vis_name = vis_name[:inner0-1] + "…"
        # Pad name into column
        name_padded = vis_name.ljust(inner0)
        repo_cell = " " + name_padded + " "
        # Replace visible text with Markdown link
        if url:
            repo_cell = repo_cell.replace(vis_name, f'<a href="{url}">{vis_name}</a>')
        cells.append(repo_cell)
        # Other columns
        for i,val in enumerate([r.get("language",""),
                                str(r.get("size","0")),
                                str(r.get("commits","0")),
                                r.get("last_commit",""),
                                str(r.get("branches","0"))], start=1):
            w = widths[i] - 2
            cells.append(" " + val.center(w) + " ")
        lines.append("|" + "|".join(cells) + "|")
        lines.append(top_line)
    table_str = "\n".join(lines)
    return table_str, len(top_line), len(lines)



def build_rows_for_table(repos: List[dict], token: str=None) -> List[dict]:
    """Construct the rows for the summary table (public repos only)."""
    rows = []
    for repo in repos:
        owner = repo["owner"]["login"]; name = repo["name"]
        html_url = repo.get("html_url","")
        langs = fetch_languages(owner, name, token)
        total_bytes = sum(langs.values()) if langs else 0
        lang_label = "Unknown (0%)"
        if langs and total_bytes>0:
            top_lang, top_bytes = max(langs.items(), key=lambda x: x[1])
            pct = (top_bytes/total_bytes)*100
            lang_label = f"{top_lang} ({pct:.0f}%)"
        commits = get_commit_count(owner, name, token)
        branches = get_branch_count(owner, name, token)
        last = repo.get("pushed_at","")
        try:
            if last:
                last = last.rstrip("Z")
                last = datetime.fromisoformat(last).strftime("%Y-%m-%d")
        except Exception:
            pass
        rows.append({
            "name_text": name,
            "name_url": html_url,
            "language": lang_label,
            "size": total_bytes,
            "commits": commits,
            "last_commit": last,
            "branches": branches
        })
    return rows

def build_contrib_grid(repo_weekly: Dict[str, List[int]], repo_order: List[str], label_w_override: Optional[int] = None) -> str:
    """
    Build a heatmap of weekly commits per repo (ASCII art).
    Accepts label_w_override so we can align left padding with the plot.
    """
    if label_w_override is not None:
        label_w = max(6, min(28, label_w_override))
    else:
        label_w = max(10, max((len(r) for r in repo_order), default=10))
        label_w = min(label_w, 28)

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
        # Keep one space between name and first cell, and one space between cells
        lines.append(f"{vis_name} {' '.join(row_cells)}")

    legend = " " * label_w + " " + " ".join(SHADES[1:]) + "  (low→high)"
    lines.append(legend)

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

def plot_with_mean(series, cfg=None) -> str:
    """
    ASCII line plot (adapted) with a dotted long-term mean line.
    series: list of numbers (oldest->newest). cfg: {'height','format','offset'}
    Important change: ensure Y-axis minimum is never below 0 (commits cannot be negative).
    """
    if not series:
        return ''
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

    # Force minimum >= 0
    minimum = cfg.get('min', min(numeric))
    minimum = max(0.0, float(minimum))
    maximum = cfg.get('max', max(numeric))

    default_symbols = ['┼', '┤', '╶', '╴', '─', '╰', '╭', '╮', '╯', '│']
    symbols = cfg.get('symbols', default_symbols)

    if minimum > maximum:
        raise ValueError('The min value cannot exceed the max value.')

    interval = maximum - minimum
    offset = cfg.get('offset', max(8, len(cfg.get('format', "{:8.1f} ").format(maximum))))
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

    placeholder = cfg.get('format', "{:8.1f} ")

    # Build grid
    result = [[' '] * width for i in range(rows + 1)]

    # axis and labels
    for y in range(min2, max2 + 1):
        label = placeholder.format(maximum - ((y - min2) * interval / (rows if rows else 1)))
        pos = max(offset - len(label), 0)
        for idx, ch in enumerate(label):
            if pos + idx < width:
                result[y - min2][pos + idx] = ch
        result[y - min2][offset - 1] = symbols[0] if y == 0 else symbols[1]

    # Plot the line(s)
    for i in range(0, len(series)):
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

            result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
            result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]

            start = min(y0, y1) + 1
            end = max(y0, y1)
            for y in range(start, end):
                result[rows - y][x + offset] = symbols[9]

    # dotted mean line (mean of actual numeric values)
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

    return '\n'.join([''.join(row).rstrip() for row in result])


def build_readme(ascii_table: str, contrib_grid: str, ascii_plot: str) -> str:
    """Combine ASCII components into the final README markdown (inside a <pre> block)."""
    return (
        "<pre>\n"
        f"{ascii_table}\n\n"
        f"{contrib_grid}\n\n"
        f"{ascii_plot}\n"
        "</pre>\n"
    )

def main():
    token = auth_token()
    try:
        all_repos = fetch_repos_for_user(token=token)
    except Exception as e:
        print("Failed to fetch repositories:", e, file=sys.stderr)
        sys.exit(1)

    # Sort repos by updated time descending
    all_repos.sort(key=lambda x: x.get("updated_at",""), reverse=True)
    top_repos = all_repos[:TOP_N]
    public_repos = [r for r in top_repos if not r.get("private")]
    private_repos = [r for r in top_repos if r.get("private")]

    # Build table from public repos
    rows = build_rows_for_table(public_repos, token)
    ascii_table, ascii_width, ascii_height = make_ascii_table_with_links(rows)

    # If table too wide, truncate repo name column
    if ascii_width > MAX_WIDTH:
        top_line = ascii_table.splitlines()[0]
        segments = top_line.strip('+').split('+')
        col_widths = [len(seg) for seg in segments]
        if col_widths:
            orig_inner0 = col_widths[0] - 2
            shrink = ascii_width - MAX_WIDTH
            new_inner0 = max(orig_inner0 - shrink, 10)
            ascii_table, ascii_width, ascii_height = make_ascii_table_with_links(rows, max_repo_name_width=new_inner0)

    # Fetch weekly commit activity for each public repo in parallel
    repos_to_query = [r["name_text"] for r in rows]
    repo_weekly: Dict[str, List[int]] = {}
    print(f"Fetching commit_activity for {len(repos_to_query)} repos...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(repo_commit_activity, USERNAME, repo, token): repo for repo in repos_to_query}
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                weeks = fut.result() or [0]*WEEKS
            except Exception:
                weeks = [0]*WEEKS
            if len(weeks) < WEEKS:
                weeks = [0]*(WEEKS-len(weeks)) + weeks
            repo_weekly[repo] = weeks

    # Aggregate private repos into one "restricted" series
    if private_repos and token:
        print(f"Aggregating {len(private_repos)} private repos into '{RESTRICTED_NAME}'")
        agg_weeks = [0]*WEEKS
        for repo in private_repos:
            owner = repo["owner"]["login"]; name = repo["name"]
            weeks = repo_commit_activity(owner, name, token)
            if not weeks:
                weeks = [0]*WEEKS
            if len(weeks) < WEEKS:
                weeks = [0]*(WEEKS-len(weeks)) + weeks
            for i, val in enumerate(weeks):
                agg_weeks[i] += val
        repo_weekly[RESTRICTED_NAME] = agg_weeks
        repo_order = repos_to_query + [RESTRICTED_NAME]
    else:
        repo_order = repos_to_query

    contrib_grid = build_contrib_grid(repo_weekly, repo_order)

        # --- IMPORTANT: determine the left label width (based on headers and plot labels) ---
    # base label width from heatmap (repo name area)
    base_label_w = max(10, max((len(r) for r in repo_order), default=10))
    base_label_w = min(base_label_w, 28)

    # Build weekly totals (public + restricted)
    weekly_totals: List[float] = [0.0] * WEEKS
    for name in repo_order:
        weeks = repo_weekly.get(name, [0] * WEEKS)
        if len(weeks) < WEEKS:
            weeks = [0] * (WEEKS - len(weeks)) + weeks
        for i, v in enumerate(weeks):
            weekly_totals[i] += float(v)

    # Determine numeric formatting for plot labels (so we can reserve enough left space)
    if not any(weekly_totals):
        scaled_series = [0.0] * (WEEKS * 2)
        scale_suffix = ""
        fmt_w = 7; dec_places = 1
        fmt_template = f"{{:{fmt_w}.{dec_places}f}} "
        offset_label_len = len(fmt_template.format(0.0))
    else:
        # duplicate each week to match heatmap spacing (2 chars per week)
        series_points = []
        for w in weekly_totals:
            series_points.append(w)
            series_points.append(w)
        max_val = max(series_points)
        if max_val >= 1_000_000:
            scale = 1_000_000.0; scale_suffix = "M"; dec_places = 1
        elif max_val >= 1_000:
            scale = 1_000.0; scale_suffix = "K"; dec_places = 1
        else:
            scale = 1.0; scale_suffix = ""; dec_places = 1
        # IMPORTANT: do NOT subtract the mean — plot actual (non-negative) totals
        scaled_series = [x / scale for x in series_points]
        fmt_w = 7
        fmt_template = f"{{:{fmt_w}.{dec_places}f}} "
        offset_label_len = len(fmt_template.format(max(scaled_series or [0.0])))

    # Final left label width: ensure enough room for both heatmap repo names and plot labels
    final_label_w = max(base_label_w, max(6, min(28, offset_label_len - 1)))

    # Rebuild heatmap with the forced left-label width so it lines up with the plot
    contrib_grid = build_contrib_grid(repo_weekly, repo_order, label_w_override=final_label_w)

    # --- Build the line chart ---
    if not any(weekly_totals):
        ascii_plot = "(no activity data)"
    else:
        # scaled_series already computed (actual totals duplicated per week, not mean-centered)
        # Ensure length fits 2*WEEKS
        target_len = WEEKS * 2
        if len(scaled_series) != target_len:
            if len(scaled_series) > target_len:
                scaled_series = scaled_series[-target_len:]
            else:
                scaled_series = ([0.0] * (target_len - len(scaled_series))) + scaled_series

        # Build label format and offset; offset must be (final_label_w + 1) so weeks start right after heatmap's name + space
        offset_for_plot = final_label_w + 1
        cfg = {"height": PLOT_HEIGHT, "format": fmt_template, "offset": offset_for_plot}
        ascii_plot_body = plot_with_mean(scaled_series, cfg)

        # Build X-axis labels (one label per week, repeated as 'char + space' to match 2 chars/week)
        now = datetime.now(timezone.utc)
        axis_cells = []
        for i in range(WEEKS):
            if i % 4 == 0:
                dt = now - timedelta(days=(WEEKS - 1 - i) * 7)
                axis_cells.append(dt.strftime("%b")[0])
            else:
                axis_cells.append(" ")
        # axis_line must start at the same offset used for plot
        axis_line = " " * offset_for_plot + ''.join(ch + " " for ch in axis_cells)

        # NOTE: user requested removing the header line for the plot
        ascii_plot = ascii_plot_body + "\n" + axis_line


    # Write the README
    readme = build_readme(ascii_table, contrib_grid, ascii_plot)
    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)
    print("README.md updated.")

if __name__ == "__main__":
    main()
