#!/usr/bin/env python3
"""
Enhanced update_readme.py with fixes for private repos, heatmap data, and chart width.
Align heatmap and line chart x-axes by using a global left margin.
Ensure line chart y-axis runs from 0 to max (no negative values).
"""
import os, sys, time, re
from datetime import datetime, timezone, timedelta
from math import ceil, floor, isnan
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ---------- Configuration ----------
USERNAME = "chasenunez"
HEADERA = "Most Recently Active Repositories"
HEADERB = "Commit Density For Recently Active Repositories"
HEADERC = "Weekly Commit Distribution Relative To Long-Term Mean"
LINE = "━"
TOP_N = 10            # Number of top repositories to include (including private)
WEEKS = 42            # Number of weeks to show in charts
MAX_WIDTH = 110       # Max characters wide for all figures (table, heatmap, plot)
RESTRICTED_NAME = "restricted_access"
AVG_BYTES_PER_LINE = 40.0
PLOT_HEIGHT = 10
PLOT_FORMAT = "{:8.1f} "
SHADES = ["⠁","⠃","⠇","⠏","⠟","⠿","⡿","⣿"]
#SHADES = ["□", "░", "▒", "▓", "█"]  # For heat map (low→high intensity)
GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})",
})
# -----------------------------------

def month_initials_for_weeks(weeks: int, use_three_letter: bool=False) -> List[str]:
    """
    Return a list of length `weeks` with month labels aligned to weekly columns.
    We place a label only when the calendar month changes compared to the last
    labeled column (avoids multiple labels inside the same month).
    If use_three_letter=True we return e.g. "Mar"/"Apr", else a single initial "M"/"A".
    """
    labels: List[str] = []
    now = datetime.now(timezone.utc)
    last_month = None
    for i in range(weeks):
        dt = now - timedelta(days=(weeks-1-i)*7)
        if dt.month != last_month:
            m = dt.strftime("%b")  # 'Mar', 'May', ...
            labels.append(m if use_three_letter else m[0])
            last_month = dt.month
        else:
            labels.append(" ")
    return labels

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
    Using /user/repos (authenticated) returns both public and private repos.
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
    while attempt < 5:
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
    Get weekly commit counts for the repo (last up to 52 weeks).
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

# def make_ascii_table_with_links(rows: List[dict], max_repo_name_width: int=None) -> Tuple[str,int,int]:
#     """
#     Build an ASCII table (with Markdown links in repo names).
#     Optionally cap the repository name column to max_repo_name_width characters.
#     Returns (table_str, table_width, table_height).
#     """
#     cols = ["Repository", "Main Language", "Total Bytes", "Total Commits", "Last Commit Date", "Branches"]
#     data_rows = []
#     for r in rows:
#         data_rows.append([
#             r.get("name_text",""),
#             r.get("language",""),
#             str(r.get("size","0")),
#             str(r.get("commits","0")),
#             r.get("last_commit",""),
#             str(r.get("branches","0"))
#         ])
#     # Compute column widths
#     widths = [len(c) for c in cols]
#     for row in data_rows:
#         for i, cell in enumerate(row):
#             widths[i] = max(widths[i], len(cell))
#     widths = [w+2 for w in widths]
#     # Cap first column if needed
#     if max_repo_name_width and widths:
#         inner0 = widths[0] - 2
#         if inner0 > max_repo_name_width:
#             widths[0] = max_repo_name_width + 2
#     # Build table lines
#     top_line = "+" + "+".join("-"*w for w in widths) + "+"
#     lines = [top_line]
#     header = []
#     for i,c in enumerate(cols):
#         header.append(" " + c.center(widths[i]-2) + " ")
#     lines.append("|" + "|".join(header) + "|")
#     lines.append(top_line)
#     # Rows
#     for r in rows:
#         cells = []
#         vis_name = r.get("name_text","")
#         url = r.get("name_url","")
#         inner0 = widths[0] - 2
#         # Truncate visible repo name if needed
#         if len(vis_name) > inner0:
#             vis_name = vis_name[:inner0-1] + "…"
#         # Pad name into column
#         name_padded = vis_name.ljust(inner0)
#         repo_cell = " " + name_padded + " "
#         # Replace visible text with Markdown link
#         if url:
#             repo_cell = repo_cell.replace(vis_name, f'<a href="{url}">{vis_name}</a>')
#         cells.append(repo_cell)
#         # Other columns
#         for i,val in enumerate([r.get("language",""),
#                                 str(r.get("size","0")),
#                                 str(r.get("commits","0")),
#                                 r.get("last_commit",""),
#                                 str(r.get("branches","0"))], start=1):
#             w = widths[i] - 2
#             cells.append(" " + val.center(w) + " ")
#         lines.append("|" + "|".join(cells) + "|")
#         lines.append(top_line)
#     table_str = "\n".join(lines)
#     return table_str, len(top_line), len(lines)

from typing import List, Tuple #this is a beast. There is probably a better way to do this, but I couldn't think of it.

def make_ascii_table_with_links(rows: List[dict], max_repo_name_width: int = None) -> Tuple[str,int,int]:
    """
    Build a box-drawing ASCII table (with Markdown/HTML links in repo names).
    The table's TOTAL width (including outer borders) will be MAX_WIDTH (global).
    If content requires more space than MAX_WIDTH, the function will first truncate the
    Repository column (with an ellipsis) and then other columns down to a reasonable minimum.
    Returns (table_str, table_width, table_height).
    """
    # Column headers
    cols = ["Repository", "Main Language", "Total Bytes", "Total Commits", "Last Commit Date", "Branches"]
    # Prepare data rows
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

    # Basic widths = max(header, content) for each column (inner width = content width)
    inner_widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            inner_widths[i] = max(inner_widths[i], len(cell))
    # Add horizontal padding: 1 space left + 1 space right -> +2 per column when rendering cell content
    pad = 2
    widths = [w + pad for w in inner_widths]  # visible cell width including padding

    # If user supplied max_repo_name_width, cap first column inner width (content only)
    if max_repo_name_width and widths:
        inner0 = widths[0] - pad
        if inner0 > max_repo_name_width:
            widths[0] = max_repo_name_width + pad
            inner_widths[0] = max_repo_name_width

    # Table total width calculation helper (outer borders add 2 chars: left and right)
    def total_table_width(col_widths):
        return sum(col_widths) + (len(col_widths) + 1)  # each column separated by 1 vertical char, plus two outer borders

    # Ensure totals align to MAX_WIDTH: we will expand or shrink columns while keeping content fit
    # MINIMUM inner width for a column (content area) is max(len(header), 1)
    min_inner = [max(1, len(c)) for c in cols]
    min_widths = [m + pad for m in min_inner]

    current_total = total_table_width(widths)
    target_total = MAX_WIDTH if MAX_WIDTH and MAX_WIDTH > 0 else current_total

    # If current total less than target, pad columns to reach target (distribute extra space)
    if current_total < target_total:
        extra = target_total - current_total
        # Distribute extra to columns from left to right (favor Repository first)
        i = 0
        n = len(widths)
        while extra > 0:
            widths[i % n] += 1
            extra -= 1
            i += 1
    # If current total greater than target, we need to shrink columns (prefer to shrink Repository first)
    elif current_total > target_total:
        excess = current_total - target_total
        # Attempt to reduce Repository inner width first (keep at least 1 char + ellipsis support)
        def reduce_col(idx, amount):
            nonlocal excess
            # ensure we do not go below min_widths[idx]
            can_reduce = widths[idx] - min_widths[idx]
            take = min(can_reduce, amount)
            widths[idx] -= take
            excess -= take
            return take

        # Reduce repo column aggressively first
        if len(widths) > 0:
            reduce_col(0, excess)
        # If still excess, reduce other columns left->right
        for i in range(1, len(widths)):
            if excess <= 0:
                break
            reduce_col(i, excess)
        # If still excess after that (rare), force minimal widths (may still be > target if MAX_WIDTH too small)
        if excess > 0:
            # try a final sweep to reduce to 1 inner char if possible
            for i in range(len(widths)):
                if excess <= 0:
                    break
                allowed_min = pad + 1
                can_reduce = widths[i] - allowed_min
                if can_reduce > 0:
                    take = min(can_reduce, excess)
                    widths[i] -= take
                    excess -= take
        # After reductions, we must handle that repo visible names may need truncation

    # At this point widths are set. Derive inner widths (content area) again
    inner_widths = [w - pad for w in widths]

    # A helper to truncate visible repo name with ellipsis if needed
    def clip_repo_name(name: str, inner0: int) -> str:
        if len(name) <= inner0:
            return name
        if inner0 <= 0:
            return ""
        if inner0 == 1:
            return name[:1]
        # reserve 1 char for ellipse
        return name[:max(1, inner0-1)] + "…"

    # Box-drawing glyph sets (we'll use three visual weights)
    glyph = {
        'double': {
            'h': '═', 'v': '║',
            'tl': '╔', 'tr': '╗', 'bl': '╚', 'br': '╝',
            'tsep': '╦', 'msep': '╬', 'bsep': '╩', 'lsep': '╠', 'rsep': '╣'
        },
        'heavy': {
            'h': '━', 'v': '┃',
            'tl': '┏', 'tr': '┓', 'bl': '┗', 'br': '┛',
            'tsep': '┳', 'msep': '╋', 'bsep': '┻', 'lsep': '┣', 'rsep': '┫'
        },
        'light': {
            'h': '─', 'v': '│',
            'tl': '┌', 'tr': '┐', 'bl': '└', 'br': '┘',
            'tsep': '┬', 'msep': '┼', 'bsep': '┴', 'lsep': '├', 'rsep': '┤'
        },
        # Some mixed junctions (double <-> heavy/light). This set is not exhaustive but covers many junctions.
        'mix': {
            # double horizontal meets heavy vertical (top border mixing)
            ('double_h', 'heavy_v'): '╤',
            ('double_h', 'light_v'): '╦',
            # double bottom meets heavy vertical
            ('double_b', 'heavy_v'): '╧',
            ('double_b', 'light_v'): '╩',
            # heavy horizontal meets double vertical (rare)
            ('heavy_h', 'double_v'): '╟',
            # heavy horizontal meets light vertical
            ('heavy_h', 'light_v'): '┯',
            # light horizontal meets double vertical
            ('light_h', 'double_v'): '╨',
            # light horizontal meets heavy vertical
            ('light_h', 'heavy_v'): '┠',
            # cross mixes -- fallback
            ('double_h', 'double_v'): '╬',
            ('heavy_h', 'heavy_v'): '╋',
            ('light_h', 'light_v'): '┼',
        }
    }

    # Which vertical separators are "heavy"? left-most column verticals should be heavy.
    # Outer left and right vertical borders are double.
    # Internal vertical separators (between non-left columns) are light.
    # So vertical style per separator index (there are len(cols)+1 separators: left outer, between 0-1, between 1-2, ..., right outer)
    vert_styles = []
    ncols = len(cols)
    for sep_idx in range(ncols + 1):
        if sep_idx == 0 or sep_idx == ncols:
            vert_styles.append('double')  # outer
        elif sep_idx == 1:
            vert_styles.append('heavy')   # between repo and next -> left-most column uses heavy separator
        else:
            vert_styles.append('light')   # interior separators

    # Horizontal styles for each horizontal line:
    # - top outer: double
    # - header top (line between top outer and header cell top): we'll use heavy for header cell top/bottom but the very top remains double
    # - header bottom (separator after header row): heavy
    # - rows separators (between data rows): light
    # - bottom outer: double
    # We'll need: top_line (double), header_sep (heavy), row_sep (light), bottom_line (double)
    # Helper to build a horizontal border line (top/header_sep/row_sep/bottom)
    def build_horizontal_line(h_style_key: str) -> str:
        """
        h_style_key in {'top', 'header', 'row', 'bottom'}
        returns a full string line including outer border characters
        """
        if h_style_key == 'top' or h_style_key == 'bottom':
            h_style = 'double'
            left_corner = glyph['double']['tl'] if h_style_key == 'top' else glyph['double']['bl']
            right_corner = glyph['double']['tr'] if h_style_key == 'top' else glyph['double']['br']
            between_default = glyph['double']['tsep'] if h_style_key == 'top' else glyph['double']['bsep']
            h_char = glyph['double']['h']
        elif h_style_key == 'header':
            h_style = 'heavy'
            left_corner = glyph['double']['lsep']  # mixing double outer with heavy header; pick a reasonable char
            right_corner = glyph['double']['rsep']
            between_default = glyph['heavy']['tsep']  # use heavy separators for header internal
            h_char = glyph['heavy']['h']
            # But we want leftmost (outer-left vs heavy vertical) mixing: handle per junction below
        else:  # 'row' internal
            h_style = 'light'
            left_corner = glyph['light']['lsep']
            right_corner = glyph['light']['rsep']
            between_default = glyph['light']['tsep']
            h_char = glyph['light']['h']

        parts = []
        # left-most corner
        parts.append(left_corner)
        # For each column, append horizontal run and then appropriate junction depending on the vertical style at that separator
        for col_idx, w in enumerate(widths):
            parts.append(h_char * w)
            # determine the vertical style for the separator after this column (sep index = col_idx+1)
            sep_idx = col_idx + 1
            vstyle = vert_styles[sep_idx]  # 'double'/'heavy'/'light'
            # Determine junction char between current horizontal style and vertical style vstyle.
            # We'll try to look up in mix map; if not present, fallback to style's default separators.
            key = (f"{h_style}_h", f"{vstyle}_v")
            junction = None
            if key in glyph['mix']:
                junction = glyph['mix'][key]
            else:
                # fallback heuristics
                if h_style == 'double' and vstyle == 'double':
                    junction = glyph['double']['tsep'] if h_style_key == 'top' else (glyph['double']['bsep'] if h_style_key == 'bottom' else glyph['double']['msep'])
                elif h_style == 'heavy' and vstyle == 'heavy':
                    junction = glyph['heavy']['tsep']
                elif h_style == 'light' and vstyle == 'light':
                    junction = glyph['light']['tsep']
                else:
                    # mixed: pick a reasonable visual: if vertical is double prefer double junction, if vertical heavy prefer heavy junction
                    if vstyle == 'double':
                        junction = glyph['double']['tsep']
                    elif vstyle == 'heavy':
                        junction = glyph['heavy']['tsep']
                    else:
                        junction = glyph['light']['tsep']
            parts.append(junction)
        # join all parts into a line
        return "".join(parts)

    # Build the top line, header separator, row separator, bottom line
    top_line = build_horizontal_line('top')
    header_sep_line = build_horizontal_line('header')
    row_sep_line = build_horizontal_line('row')
    bottom_line = build_horizontal_line('bottom')

    lines = []
    lines.append(top_line)

    # Build header row (centered text)
    header_cells = []
    for i, col in enumerate(cols):
        inner = inner_widths[i]
        header_text = col.center(inner)
        header_cells.append(" " + header_text + " ")
    # Build header line using vertical separators with styles:
    def build_row_line(cell_texts: List[str], is_header=False, leftmost_heavy=True) -> str:
        parts = []
        # left outer vertical border (double)
        parts.append(glyph['double']['v'])
        for i, cell in enumerate(cell_texts):
            parts.append(cell)
            sep_idx = i + 1
            vstyle = vert_styles[sep_idx]
            # choose vertical glyph
            if vstyle == 'double':
                parts.append(glyph['double']['v'])
            elif vstyle == 'heavy':
                parts.append(glyph['heavy']['v'])
            else:
                parts.append(glyph['light']['v'])
        return "".join(parts)

    lines.append(build_row_line(header_cells, is_header=True))
    lines.append(header_sep_line)

    # Build data rows
    for r in data_rows:
        # prepare repo name (may contain link)
        vis_name = r[0]
        url = ""
        # The original code allowed r.get("name_url","") in input rows list-of-dicts; here we don't have that in data_rows, so we need to
        # rely on the original rows list. We'll extract url from rows by index: find same r in rows list by matching name_text if available.
        # Simpler: if original rows are the same order as data_rows, we can use rows list directly.
        # Find original row index by matching name_text
        matching_url = ""
        for orig in rows:
            if orig.get("name_text","") == r[0]:
                matching_url = orig.get("name_url","")
                break
        inner0 = inner_widths[0]
        clipped = clip_repo_name(r[0], inner0)
        # Build repo cell text and replace visible repo substring with link if present
        repo_display = clipped.ljust(inner0)
        if matching_url:
            # We replace raw visible text only; keep the padding around it.
            # For simplicity place the link where the visible text is (no padding inside link)
            link_text = f'<a href="{matching_url}">{clipped}</a>'
            # Pad link to cell width if link is shorter/longer than inner0
            # Note: HTML link length may be longer than inner0; visually it will be fine in rendered Markdown but we keep padding by characters.
            # We'll center/left-pad the link similarly to previous behavior (left aligned)
            cell0 = " " + link_text.ljust(inner0) + " "
        else:
            cell0 = " " + repo_display + " "
        # Other columns centered
        other_cells = []
        other_cells.append(cell0)
        for i, val in enumerate(r[1:], start=1):
            w = inner_widths[i]
            other_cells.append(" " + val.center(w) + " ")

        # Build the row line with correct vertical separators
        lines.append(build_row_line(other_cells))

        # after each data row, append a row separator (light) unless it's the last row, where we'll put bottom at the end
        # choose row_sep_line between rows
        # For better visuals, use light separators between data rows; heavy/light mixing handled by build_horizontal_line
        lines.append(row_sep_line)

    # Replace the final trailing row separator with bottom_line (swap last element)
    if lines[-1] == row_sep_line:
        lines[-1] = bottom_line
    else:
        lines.append(bottom_line)

    table_str = "\n".join(lines)
    return table_str, len(top_line), len(lines)


def build_contrib_grid(repo_weekly: Dict[str,List[int]], repo_order: List[str], label_w: Optional[int]=None) -> Tuple[str,int]:
    """
    Build an ASCII heat map (rows = repos, cols = weeks) using SHADES.
    Each row is scaled so that its max maps to '█'.
    Returns (grid_string, label_w_used).
    If label_w is provided, use it; otherwise compute the label width (min 10, max 28).
    """
    # compute label width if not provided
    if label_w is None:
        label_w = max(10, max((len(r) for r in repo_order), default=10))
        label_w = min(label_w, 28)
    else:
        # ensure within reasonable bounds
        label_w = max(10, min(label_w, 28))
    lines = []
    for repo in repo_order:
        weeks = repo_weekly.get(repo, [0]*WEEKS)
        if len(weeks) < WEEKS:
            weeks = [0]*(WEEKS-len(weeks)) + weeks
        max_val = max(weeks) or 1
        cells = []
        for w in weeks:
            ratio = w / max_val if max_val else 0
            idx = int(round(ratio*(len(SHADES)-1)))
            idx = max(0, min(len(SHADES)-1, idx))
            cells.append(SHADES[idx])
        name = repo
        if len(name) > label_w:
            name = name[:label_w-1] + "…"
        else:
            name = name.ljust(label_w)
        lines.append(f"{name} {' '.join(cells)}")
    now = datetime.now(timezone.utc)
     # old code that used i % 4 == 0 ...
    axis_cells = month_initials_for_weeks(WEEKS, use_three_letter=False)
    axis_line = " " * label_w + " " + " ".join(axis_cells)
    lines.append(axis_line)
    # Legend and time axis (month initials every 4 weeks)
    legend = " "*label_w + "low " + " ".join(SHADES) + "  high"
    legend_centered = legend.center(MAX_WIDTH)
    lines.append("") #adding some space
    #lines.append(legend_centered)
    lines.append(legend)
    return "\n".join(lines), label_w

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

def plot_with_mean(series, cfg=None) -> str:
    """ASCII line plot of a series (or multiple series) with a dotted mean line."""
    if not series:
        return ""
    if not isinstance(series[0], list):
        if all(isnan(x) for x in series):
            return ""
        series = [series]
    # Flatten for scale calculations
    flat = [x for s in series for x in s if not isnan(x)]
    if not flat:
        return ""
    cfg = cfg or {}
    minimum = cfg.get('min', min(flat))
    maximum = cfg.get('max', max(flat))
    symbols = cfg.get('symbols', ['┼','┤','╶','╴','─','╰','╭','╮','╯','│'])
    interval = maximum - minimum
    offset = cfg.get('offset', max(8, len(cfg.get('format',PLOT_FORMAT).format(maximum))))
    height = cfg.get('height', PLOT_HEIGHT)
    ratio = height / interval if interval else 1
    min2 = int(floor(minimum*ratio))
    max2 = int(ceil(maximum*ratio))
    def clamp(x): return min(max(x, minimum), maximum)
    def scaled(y): return int(round(clamp(y)*ratio) - min2)
    rows = max2 - min2
    width = max(len(s) for s in series) + offset
    result = [[' ']*width for _ in range(rows+1)]
    # Y-axis labels
    for y in range(min2, max2+1):
        label = cfg.get('format',PLOT_FORMAT).format(maximum - ((y-min2)*interval/(rows if rows else 1)))
        pos = max(offset - len(label), 0)
        line_idx = y - min2
        for idx,ch in enumerate(label):
            if pos+idx < width:
                result[line_idx][pos+idx] = ch
        result[line_idx][offset-1] = symbols[0] if y==0 else symbols[1]
    # First point marker
    try:
        if not isnan(series[0][0]):
            result[rows-scaled(series[0][0])][offset-1] = symbols[0]
    except:
        pass
    # Plot lines
    for s in series:
        for x in range(len(s)-1):
            d0 = s[x]; d1 = s[x+1]
            if isnan(d0) and isnan(d1):
                continue
            if isnan(d0):
                result[rows-scaled(d1)][x+offset] = symbols[2]; continue
            if isnan(d1):
                result[rows-scaled(d0)][x+offset] = symbols[3]; continue
            y0 = scaled(d0); y1 = scaled(d1)
            if y0 == y1:
                result[rows-y0][x+offset] = symbols[4]
                continue
            result[rows-y1][x+offset] = symbols[5] if y0>y1 else symbols[6]
            result[rows-y0][x+offset] = symbols[7] if y0>y1 else symbols[8]
            for yy in range(min(y0,y1)+1, max(y0,y1)):
                result[rows-yy][x+offset] = symbols[9]
    # Dotted mean line
    mean_val = sum(flat)/len(flat)
    try:
        mean_scaled = scaled(mean_val)
        mean_row = max(0, min(rows, rows-mean_scaled))
        for c in range(offset, width):
            if result[mean_row][c] == ' ':
                result[mean_row][c] = '┄'
    except:
        pass
    return "\n".join("".join(row).rstrip() for row in result)

def build_readme(ascii_table: str, contrib_grid: str, ascii_plot: str) -> str:
    """Combine ASCII components into the final README markdown (inside a <pre> block)."""
    return (
        "<pre>\n"
        f"{HEADERB: ^{MAX_WIDTH}}\n"
        f"{LINE:━^{MAX_WIDTH}}\n\n" 
        f"{contrib_grid}\n\n\n"
        f"{HEADERC: ^{MAX_WIDTH}}\n"
        f"{LINE:━^{MAX_WIDTH}}\n\n"
        f"{ascii_plot}\n\n\n"
        f"{HEADERA: ^{MAX_WIDTH}}\n"
        f"{LINE:━^{MAX_WIDTH}}\n\n"                                                                                                               
        f"{ascii_table}\n"
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

    # --- ALIGNMENT PREP: compute native heatmap label width (without yet rendering)
    native_label_w = max(10, max((len(r) for r in repo_order), default=10))
    native_label_w = min(native_label_w, 28)

    # Build aggregated weekly totals for all repos
    weekly_totals = [0.0]*WEEKS
    for weeks in repo_weekly.values():
        if len(weeks) < WEEKS:
            weeks = [0]*(WEEKS-len(weeks)) + weeks
        for i, v in enumerate(weeks):
            weekly_totals[i] += float(v)

    # Build the line chart series (2 columns per week)
    if not weekly_totals or all(v==0 for v in weekly_totals):
        ascii_plot = "(no activity data)"
        # With no activity, still build the heatmap with the native label width computed above
        contrib_grid, used_label_w = build_contrib_grid(repo_weekly, repo_order, label_w=native_label_w)
    else:
        # create duplicated columns (2 columns per week)
        series_points = []
        for w in weekly_totals:
            series_points += [w, w]  # duplicate for 2 columns/week

        # Plot absolute counts from 0..max (no centering)
        raw_max = max(series_points) if series_points else 0.0
        # Scale units to K/M if needed
        if raw_max >= 1_000_000:
            scale, suffix = 1_000_000.0, "M"
        elif raw_max >= 1_000:
            scale, suffix = 1_000.0, "K"
        else:
            scale, suffix = 1.0, ""
        scaled_series = [x/scale for x in series_points]
        maximum_scaled = max(scaled_series) if scaled_series else 0.0
        if maximum_scaled <= 0:
            maximum_scaled = 1.0

        # Figure out numeric label format so that y-axis labels fit
        fmt_w, fmt_p = 7, 1
        label_fmt = f"{{:{fmt_w}.{fmt_p}f}} "
        # Compute offset_len from the *maximum* label, to ensure space for largest label
        offset_len = len(label_fmt.format(maximum_scaled))
        req_w = offset_len + len(scaled_series) + 1

        # Try to reduce precision/width to fit ascii_table width (ascii_width)
        # We'll compute left later considering heatmap; for now use ascii_width as constraint
        while req_w > ascii_width and fmt_p > 0:
            fmt_p -= 1
            label_fmt = f"{{:{fmt_w}.{fmt_p}f}} "
            offset_len = len(label_fmt.format(maximum_scaled))
            req_w = offset_len + len(scaled_series) + 1
        while req_w > ascii_width and fmt_w > 4:
            fmt_w -= 1
            label_fmt = f"{{:{fmt_w}.{fmt_p}f}} "
            offset_len = len(label_fmt.format(maximum_scaled))
            req_w = offset_len + len(scaled_series) + 1
        if req_w > ascii_width:
            # Trim rightmost points to fit
            max_pts = max(6, ascii_width - offset_len - 1)
            scaled_series = scaled_series[-max_pts:]
            req_w = offset_len + len(scaled_series) + 1

        # Now compute the global left margin to align heatmap and plot:
        # heatmap first data column index would be (label_w + 1)
        # Ensure left >= offset_len and left >= native_label_w + 1
        left = max(offset_len, native_label_w + 1)
        # Build the heatmap using label_w = left - 1 so first data column lines up with left
        used_label_w = left - 1
        contrib_grid, _ = build_contrib_grid(repo_weekly, repo_order, label_w=used_label_w)

        # Prepare plot config, forcing min=0 and max=maximum_scaled
        cfg = {"height": PLOT_HEIGHT, "format": label_fmt, "offset": left, "min": 0.0, "max": maximum_scaled}
        ascii_body = plot_with_mean(scaled_series, cfg)

        # Append x-axis (month initials). We need axis to match duplicated columns (one char + space per week -> 2 chars)
        axis_labels = month_initials_for_weeks(WEEKS, use_three_letter=False)
        # Keep the "one-char + space per week" layout:
        axis_line = " " * left + "".join(ch + " " for ch in axis_labels)

        ascii_plot = "\n" + ascii_body + "\n" + axis_line

    # If ascii_plot was the no-activity case, we already built contrib_grid above.
    if 'contrib_grid' not in locals():
        contrib_grid, used_label_w = build_contrib_grid(repo_weekly, repo_order, label_w=native_label_w)

    # Write the README
    readme = build_readme(ascii_table, contrib_grid, ascii_plot)
    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)
    print("README.md updated.")

if __name__ == "__main__":
    main()
