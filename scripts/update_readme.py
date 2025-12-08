#!/usr/bin/env python3

import os
import sys
import time
import re
import json
import unicodedata
from datetime import datetime, timezone, timedelta
from math import ceil, floor, isnan
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
try:
    import wcwidth
    _HAS_WCWIDTH = True
except Exception:
    wcwidth = None
    _HAS_WCWIDTH = False

try:
    import plotille
    _HAS_PLOTILLE = True
except Exception:
    plotille = None
    _HAS_PLOTILLE = False


def getTimeOfDay(hour):
    hour = int(hour)
    if 0 <= hour < 12:
        return "Morning"
    elif 12 <= hour < 18:
        return "Afternoon"
    elif 18 <= hour < 21:
        return "Evening"
    else:
        return "Night"

USERNAME = "chasenunez"

# Default color: black. Set to a hex string like "#00aa00" to change.
COLOR_HEX = "#8dc990"        # overall fallback color (default black)
BRAILLE_COLOR = "#8dc990"    # fall back to COLOR_HEX when None
TOTAL_LINE_COLOR = "#8dc990" # color for the total/mean line (green by default)

# Enable / disable coloring easily
ENABLE_COLOR = True

DAY = datetime.now().strftime("%A")
DATECONSTRUCT = datetime.now().strftime("%A %d %B, %Y")
TIMECONSTRUCT = datetime.now().strftime("%H")
MINUTECONSTRUCT = datetime.now().strftime("%M")
APPROXTIME = getTimeOfDay(TIMECONSTRUCT)
HEADERA = f"⢀⣠⣴⣾⣿ Updated {DAY} {APPROXTIME} At {TIMECONSTRUCT}:{MINUTECONSTRUCT} CEST ⣿⣷⣦⣄⡀"
HEADERB = "Commits Per-Week With Annual Average"
HEADERC = "Commit Allocation Among Most Active Projects"
HEADERD = "Commit Allocation By Hour Of The Day (right) and By Day Of Week (left)"
HEADERE = "Recently Active Project Details"
LINE = "▔"
TOP_N = 10
WEEKS = 42
MAX_WIDTH = 100
LINE_LENGTH = 110
RESTRICTED_NAME = "restricted"
PLOT_HEIGHT = 10
PLOT_FORMAT = "{:8.1f} "
BLANK = "⠀"   # U+2800 BRAILLE PATTERN BLANK
SHADES = ["","⡀","⡁","⡑","⡕","⡝","⣝","⣽","⣿"]
GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})",
})
CACHE_FILE = ".commit_activity_cache.json"
STATS_MAX_ATTEMPTS = 8

def auth_token() -> Optional[str]:
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

def gh_get(url: str, params: dict=None, token: str=None, timeout: int=30) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp

def _get_paginated(url: str, params: dict=None, token: str=None) -> List[dict]:
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

def _retry_stats_get(url: str, token: str=None) -> Optional[requests.Response]:
    attempt = 0
    wait = 1.0
    while attempt < STATS_MAX_ATTEMPTS:
        try:
            r = SESSION.get(url, headers={"Authorization": f"token {token}"} if token else {}, timeout=20)
        except requests.RequestException:
            return None
        if r.status_code == 202:
            time.sleep(wait)
            attempt += 1
            wait = min(wait * 2.0, 10.0)
            continue
        try:
            r.raise_for_status()
        except Exception:
            return None
        return r
    return None

def repo_commit_activity(owner: str, repo: str, token: str=None) -> List[int]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    r = _retry_stats_get(url, token=token)
    if r is not None:
        try:
            data = r.json()
            if isinstance(data, list):
                weeks = [int(item.get("total", 0)) for item in data]
                if len(weeks) >= WEEKS:
                    return weeks[-WEEKS:]
                return [0] * (WEEKS - len(weeks)) + weeks
        except Exception:
            pass
    return [0] * WEEKS

def get_commit_count(owner: str, repo: str, token: str=None) -> int:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    try:
        r = gh_get(url, params={"per_page":1}, token=token)
    except Exception:
        return 0
    link = r.headers.get("Link", "")
    if link:
        m = re.search(r'[&?]page=(\d+)>; rel="last"', link)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    try:
        commits = r.json()
        if isinstance(commits, list):
            return len(commits)
    except Exception:
        pass
    return 0

def get_branch_count(owner: str, repo: str, token: str=None) -> int:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/branches"
    try:
        r = gh_get(url, params={"per_page":1}, token=token)
    except Exception:
        return 0
    link = r.headers.get("Link", "")
    if link:
        m = re.search(r'[&?]page=(\d+)>; rel="last"', link)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
    try:
        data = r.json()
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return 0

def fetch_languages(owner: str, repo: str, token: str=None) -> Dict[str,int]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/languages"
    try:
        r = gh_get(url, token=token)
        return r.json() or {}
    except Exception:
        return {}

def wcswidth_fallback(s: str) -> int:
    if s is None:
        return 0
    total = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        ea = unicodedata.east_asian_width(ch)
        total += 2 if ea in ("W", "F") else 1
    return total

def wcswidth(s: str) -> int:
    if _HAS_WCWIDTH:
        try:
            return wcwidth.wcswidth(s)
        except Exception:
            return wcswidth_fallback(s)
    else:
        return wcswidth_fallback(s)

def pad_to_width(s: str, target: int, align: str='left') -> str:
    """
    Pad/truncate string `s` to display width `target`.
    Uses the global BLANK character for padding so all figures use the same glyph.
    Align options: left/right/center.
    """
    # compute visible width
    cur = wcswidth(s)
    if cur == target:
        return s
    if cur < target:
        pad = target - cur
        if align == 'left':
            return s + (BLANK * pad)
        elif align == 'right':
            return (BLANK * pad) + s
        else:
            left = pad//2
            right = pad - left
            return (BLANK * left) + s + (BLANK * right)
    # cur > target -> truncate by characters while measuring widths
    out = ""
    acc = 0
    for ch in s:
        ch_w = wcswidth(ch)
        if acc + ch_w > target:
            break
        out += ch
        acc += ch_w
    # if we have room for an ellipsis char, append
    if acc < target and len(out) < len(s):
        # try to add single-character ellipsis "…"
        if acc + wcswidth("…") <= target:
            out += "…"
            acc += wcswidth("…")
    # pad if still short (use BLANK)
    if acc < target:
        out += BLANK * (target - acc)
    return out


def load_cache() -> Dict[str, List[int]]:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
            out = {}
            for k,v in raw.items():
                if isinstance(v, list):
                    vv = [int(x) for x in v]
                    if len(vv) < WEEKS:
                        vv = [0]*(WEEKS - len(vv)) + vv
                    else:
                        vv = vv[-WEEKS:]
                    out[k] = vv
            return out
    except Exception:
        return {}

def save_cache(cache: Dict[str, List[int]]) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    except Exception:
        pass

def month_initials_for_weeks(weeks: int, use_three_letter: bool=False) -> List[str]:
    labels: List[str] = []
    now = datetime.now(timezone.utc)
    last_month = None
    last_label = None
    for i in range(weeks):
        dt = now - timedelta(days=(weeks-1-i)*7)
        if dt.month != last_month:
            m = dt.strftime("%b")
            if use_three_letter:
                lab = m
            else:
                lab = m[0]
                if last_label is not None and lab == last_label:
                    if len(m) > 1 and m[1] != last_label:
                        lab = m[1]
                    elif len(m) > 2 and m[2] != last_label:
                        lab = m[2]
            labels.append(lab)
            last_month = dt.month
            last_label = labels[-1]
        else:
            labels.append(" ")
    return labels
def build_contrib_grid(repo_weekly: Dict[str,List[int]],
                       repo_order: List[str],
                       label_w: Optional[int]=None,
                       repo_urls: Optional[Dict[str,str]]=None) -> Tuple[str,int]:
    """
    Build ASCII heat map (rows = repos, cols = weeks) using SHADES.
    Uses BLANK for all padding so columns line up exactly on GitHub.
    Returns (grid_string, label_w_used).
    """
    if label_w is None:
        label_w = max(10, max((len(r) for r in repo_order), default=10))
        label_w = min(label_w, 28)
    else:
        label_w = max(10, min(label_w, 28))

    # Determine slot width from SHADES using wcswidth (accurate if wcwidth available)
    glyph_widths = [max(1, wcswidth(s)) for s in SHADES]
    slot_w = max(1, max(glyph_widths))
    sep = BLANK  # use braille-blank as the separator between slots

    def render_slot(sym: str) -> str:
        """Render symbol padded to slot_w display columns using BLANK."""
        cur = wcswidth(sym)
        if not isinstance(cur, int) or cur <= 0:
            cur = 1
        pad = slot_w - cur
        if pad <= 0:
            return sym
        return sym + (BLANK * pad)

    lines: List[str] = []
    for repo in repo_order:
        weeks = repo_weekly.get(repo, [0]*WEEKS)
        if len(weeks) < WEEKS:
            weeks = [0] * (WEEKS - len(weeks)) + weeks
        max_val = max(weeks) or 1
        slots = []
        for w in weeks:
            ratio = w / max_val if max_val else 0
            idx = int(round(ratio * (len(SHADES) - 1)))
            idx = max(0, min(len(SHADES) - 1, idx))
            slots.append(render_slot(SHADES[idx]))

        # label by display-width (use pad_to_width which now pads with BLANK)
        visible_name = repo
        if wcswidth(visible_name) > label_w:
            truncated = ""
            acc = 0
            for ch in visible_name:
                wch = wcswidth(ch)
                if acc + wch > label_w - 1:
                    break
                truncated += ch
                acc += wch
            visible = truncated + "…"
            visible = pad_to_width(visible, label_w, align='right')
        else:
            visible = pad_to_width(visible_name, label_w, align='right')

        # use BLANK after the '┤' so the overall grid uses BLANK only for spacing
        row = f"{visible}┤{BLANK}" + sep.join(slots)
        lines.append(row)

    # Build axis: use same slot_w spacing and separator
    axis_cells = month_initials_for_weeks(WEEKS, use_three_letter=False)
    axis_slots = [pad_to_width(ch, slot_w, align='center') for ch in axis_cells]
    axis_line = (BLANK * label_w) + BLANK + sep.join(axis_slots)
    lines.append(axis_line)

    # Legend using same spacing
    legend_slots = [pad_to_width(s, slot_w, align='center') for s in SHADES]
    legend = (BLANK * label_w) + "low" + BLANK + sep.join(legend_slots) + BLANK + "high"
    lines.append("")
    lines.append(legend)

    return "\n".join(lines), label_w


from typing import List, Tuple
def make_ascii_table_with_links(rows: List[dict], max_repo_name_width: int = None) -> Tuple[str,int,int]:
    cols = ["Repository", "Main Language", "Total Bytes", "Total Commits", "Last Commit Date", "Branches"]
    data_rows = []
    for r in rows:
        data_rows.append([
            r.get("name_text", ""),
            r.get("language", ""),
            str(r.get("size", "0")),
            str(r.get("commits", "0")),
            r.get("last_commit", ""),
            str(r.get("branches", "0")),
        ])
    inner_widths = [len(h) for h in cols]
    for dr in data_rows:
        for i, cell in enumerate(dr):
            inner_widths[i] = max(inner_widths[i], len(cell))
    if data_rows:
        longest_repo = max(len(dr[0]) for dr in data_rows)
        inner_widths[0] = max(inner_widths[0], longest_repo)
    if max_repo_name_width is not None:
        inner_widths[0] = min(inner_widths[0], max(1, max_repo_name_width))
    PAD = 2
    widths = [w + PAD for w in inner_widths]
    def total_table_width(col_widths: List[int]) -> int:
        return sum(col_widths) + (len(col_widths) + 1)
    target_total = MAX_WIDTH if MAX_WIDTH and MAX_WIDTH > 0 else total_table_width(widths)
    current_total = total_table_width(widths)
    min_inner = [max(1, len(h)) for h in cols]
    min_widths = [m + PAD for m in min_inner]
    if current_total < target_total:
        extra = target_total - current_total
        i = 0
        n = len(widths)
        while extra > 0 and n > 0:
            widths[i % n] += 1
            extra -= 1
            i += 1
    elif current_total > target_total:
        excess = current_total - target_total
        def reduce_col(idx, amount):
            nonlocal excess
            can = widths[idx] - min_widths[idx]
            take = min(can, amount)
            widths[idx] -= take
            excess -= take
            return take
        if len(widths) > 0:
            reduce_col(0, excess)
        for idx in range(1, len(widths)):
            if excess <= 0:
                break
            reduce_col(idx, excess)
        if excess > 0:
            for idx in range(len(widths)):
                if excess <= 0:
                    break
                allowed_min = PAD + 1
                can = widths[idx] - allowed_min
                if can > 0:
                    take = min(can, excess)
                    widths[idx] -= take
                    excess -= take
    inner_widths = [w - PAD for w in widths]
    def clip(text: str, inner: int) -> str:
        if len(text) <= inner:
            return text
        if inner <= 0:
            return ""
        if inner == 1:
            return text[:1]
        return text[:max(1, inner-1)] + "…"
    D_H = '═'; D_V = '║'
    TL = '╔'; TR = '╗'; BL = '╚'; BR = '╝'
    TSEP = '╦'; MSEP = '╬'; BSEP = '╩'; LSEP = '╠'; RSEP = '╣'
    def build_top():
        parts = [TL]
        for i, w in enumerate(widths):
            parts.append(D_H * w)
            parts.append(TSEP if i < len(widths)-1 else TR)
        return "".join(parts)
    def build_mid():
        parts = [LSEP]
        for i, w in enumerate(widths):
            parts.append(D_H * w)
            parts.append(MSEP if i < len(widths)-1 else RSEP)
        return "".join(parts)
    def build_bottom():
        parts = [BL]
        for i, w in enumerate(widths):
            parts.append(D_H * w)
            parts.append(BSEP if i < len(widths)-1 else BR)
        return "".join(parts)
    top_line = build_top()
    mid_line = build_mid()
    bottom_line = build_bottom()
    def join_cells(cell_texts: List[str]) -> str:
        parts = [D_V]
        for txt in cell_texts:
            parts.append(txt)
            parts.append(D_V)
        return "".join(parts)
    lines = [top_line]
    header_cells = []
    for i, h in enumerate(cols):
        header_cells.append(" " + h.center(inner_widths[i]) + " ")
    lines.append(join_cells(header_cells))
    lines.append(mid_line)
    for orig in rows:
        name = orig.get("name_text", "")
        url = orig.get("name_url", "")
        inner0 = inner_widths[0]
        clipped = clip(name, inner0)
        if url:
            anchor = f'<a href="{url}">{clipped}</a>'
            padding = " " * (inner0 - len(clipped))
            repo_cell = " " + anchor + padding + " "
        else:
            repo_cell = " " + clipped.ljust(inner0) + " "
        match = None
        for dr in data_rows:
            if dr[0] == name:
                match = dr
                break
        if match is None:
            match = [""] * len(cols)
        other_cells = [repo_cell]
        for i in range(1, len(cols)):
            other_cells.append(" " + match[i].center(inner_widths[i]) + " ")
        lines.append(join_cells(other_cells))
        lines.append(mid_line)
    if lines[-1] == mid_line:
        lines[-1] = bottom_line
    else:
        lines.append(bottom_line)
    table_str = "\n".join(lines)
    return table_str, len(top_line), len(lines)

# ----------------------------
# New dual histogram builder
# ----------------------------
def build_dual_histogram(hours: List[float],
                         timestamps: List[datetime],
                         max_width: int = MAX_WIDTH,
                         label_day_w: int = 4,
                         day_label_names: Optional[List[str]] = None) -> str:
    """
    Build a composite histogram:
      - Right: commits by hour (24 rows), bars extend right from center.
      - Left: commits by day of week (7 bars, each drawn on 3 rows -> 21 rows), bars extend left from center and are vertically centered
    Output width obeys max_width.
    """
    if not timestamps and not hours:
        return "(no commit timestamps)"

    # prepare counts
    counts_hr = [0] * 24
    for h in hours:
        try:
            idx = int(h) % 24
        except Exception:
            continue
        counts_hr[idx] += 1

    counts_day = [0] * 7  # Monday=0 .. Sunday=6
    for dt in timestamps:
        try:
            d = dt.weekday()
            counts_day[d] += 1
        except Exception:
            continue

    # labels
    if day_label_names is None:
        day_label_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    # center separators (two adjacent to denote two axes)
    center_sep = " ┤├ "  # length 4, visually: left-axis (┤) and right-axis (├) adjacent with padding
    center_sep_len = wcswidth(center_sep)

    # hour label area on right (space + two digits) -> we give it width 3: ' 00'
    hour_label_w = 3

    # compute available bar widths (left/right must be same)
    remaining = max_width - label_day_w - center_sep_len - hour_label_w
    # leave at least 2 chars for each side
    left_bar_w = right_bar_w = max(2, remaining // 2)

    # adjust if too large to keep full width within max_width (in case odd remainder)
    total_len = label_day_w + left_bar_w + center_sep_len + right_bar_w + hour_label_w
    if total_len > max_width:
        # shrink right_bar_w first
        over = total_len - max_width
        right_bar_w = max(1, right_bar_w - over)
        total_len = label_day_w + left_bar_w + center_sep_len + right_bar_w + hour_label_w

    # choose bar glyph
    braille_full = '⣿'
    block_full = '█'
    bar_char = braille_full if braille_full else block_full

    max_hr = max(counts_hr) if counts_hr else 1
    max_day = max(counts_day) if counts_day else 1
    if max_hr == 0:
        max_hr = 1
    if max_day == 0:
        max_day = 1

    # We'll produce exactly 24 rows (hours 0..23). Day-of-week block: 7 * 3 = 21 rows, centered inside 24
    rows_total = 24
    day_block_rows = 7 * 3
    top_pad = (rows_total - day_block_rows) // 2  # usually 1
    # bottom_pad = rows_total - day_block_rows - top_pad

    lines: List[str] = []
    for row in range(rows_total):
        # determine day-side content
        day_row_relative = row - top_pad
        if 0 <= day_row_relative < day_block_rows:
            day_idx = day_row_relative // 3  # which day (0..6)
            sub = day_row_relative % 3  # 0,1,2: middle line (sub==1) will carry label
            # bar length for this day
            c = counts_day[day_idx]
            day_bar_len = int(round((c / max_day) * left_bar_w)) if max_day else 0
            # left bars extend left from separator, so we right-pad with bar_char at the right edge of the left-bar area
            left_bar = " " * (left_bar_w - day_bar_len) + (bar_char * day_bar_len)
            if sub == 1:
                day_label = pad_to_width(day_label_names[day_idx], label_day_w, align='right')
            else:
                # blank label line (keep width)
                day_label = " " * label_day_w
        else:
            # outside day-block
            left_bar = " " * left_bar_w
            day_label = " " * label_day_w

        # hour-side content (row maps directly to hour)
        hr = row % 24
        c_hr = counts_hr[hr]
        hr_bar_len = int(round((c_hr / max_hr) * right_bar_w)) if max_hr else 0
        right_bar = (bar_char * hr_bar_len) + " " * (right_bar_w - hr_bar_len)
        hour_label = f"{hr:02d}"
        hour_label = " " + hour_label  # keep hour_label_w == 3

        # combine
        line = f"{day_label}{left_bar}{center_sep}{right_bar}{hour_label}"
        # ensure total visible width does not exceed max_width (trim if necessary)
        # Use wcswidth to check and if necessary trim rightmost padding
        visible_len = wcswidth(line)
        if visible_len > max_width:
            # try to trim spaces from right_bar area first
            excess = visible_len - max_width
            if excess > 0 and right_bar_w - hr_bar_len >= excess:
                # reduce right padding
                right_bar = (bar_char * hr_bar_len) + " " * (right_bar_w - hr_bar_len - excess)
                line = f"{day_label}{left_bar}{center_sep}{right_bar}{hour_label}"
            else:
                # fallback: truncate to max_width characters visually
                # naive truncation by bytes is OK for most terminals here
                # produce a best-effort cropping:
                cropped = ""
                acc = 0
                for ch in line:
                    ch_w = wcswidth(ch)
                    if acc + ch_w > max_width:
                        break
                    cropped += ch
                    acc += ch_w
                line = cropped

        lines.append(line)

    # legend + extras: show small legend for left/right bar scale
    legend_left = f"Left: commits by day (each day drawn 3 rows), Right: commits by hour"
    # Ensure legend fits, else crop
    if wcswidth(legend_left) > max_width:
        # crop
        legend_left = legend_left[:max_width]

    lines.append("")
    lines.append(pad_to_width(legend_left, max_width, align='left'))

    return "\n".join(lines)

# ----------------------------
# plot_with_mean & other helpers (kept mostly as-is)
# ----------------------------
def _safe_isnan(x) -> bool:
    try:
        return isnan(float(x))
    except Exception:
        return False

def plot_with_mean(series, cfg=None) -> str:
    """
    ASCII line plot of a series with dotted mean line.
    Produces a canvas filled with BLANK so it lines up with the heatmap.
    Returns the full block (DO NOT rstrip — keep trailing BLANKs for alignment).
    """
    if not series:
        return ""
    if not isinstance(series[0], list):
        if all(_safe_isnan(x) for x in series):
            return ""
        series = [list(series)]
    flat = [x for s in series for x in s if not _safe_isnan(x)]
    if not flat:
        return ""
    cfg = cfg or {}
    minimum = cfg.get('min', min(flat))
    maximum = cfg.get('max', max(flat))
    symbols = cfg.get('symbols', ['┼','┤','╶','╴','─','╰','╭','╮','╯','│'])
    interval = maximum - minimum if (maximum - minimum) != 0 else 1.0
    fmt = cfg.get('format', PLOT_FORMAT)
    try:
        offset = cfg.get('offset', max(8, len(fmt.format(maximum))))
    except Exception:
        offset = cfg.get('offset', 12)
    height = cfg.get('height', PLOT_HEIGHT)
    ratio = height / (maximum - minimum) if (maximum - minimum) else 1.0
    min2 = int(floor(minimum * ratio))
    max2 = int(ceil(maximum * ratio))
    def clamp(x):
        try:
            xv = float(x)
        except Exception:
            xv = minimum
        return min(max(xv, minimum), maximum)
    def scaled(y):
        return int(round(clamp(y) * ratio) - min2)
    rows = max2 - min2
    width = max(len(s) for s in series) + offset
    # build canvas filled with BLANK (not ASCII space)
    result = [[BLANK]*width for _ in range(rows+1)]
    # y-axis labels (these may contain ASCII digits etc.)
    for y in range(min2, max2+1):
        try:
            label = fmt.format(maximum - ((y-min2) * interval / (rows if rows else 1)))
        except Exception:
            label = fmt.format(maximum)
        pos = max(offset - len(label), 0)
        line_idx = y - min2
        for idx,ch in enumerate(label):
            if pos + idx < width:
                result[line_idx][pos+idx] = ch
        # axis glyph: put the axis char at offset-1 (keep ASCII glyph)
        result[line_idx][offset-1] = symbols[0] if y == 0 else symbols[1]
    # first point marker
    try:
        if not _safe_isnan(series[0][0]):
            result[rows - scaled(series[0][0])][offset-1] = symbols[0]
    except Exception:
        pass
    # plot lines
    for s in series:
        for x in range(len(s)-1):
            d0 = s[x]; d1 = s[x+1]
            if _safe_isnan(d0) and _safe_isnan(d1):
                continue
            if _safe_isnan(d0):
                result[rows - scaled(d1)][x + offset] = symbols[2]; continue
            if _safe_isnan(d1):
                result[rows - scaled(d0)][x + offset] = symbols[3]; continue
            y0 = scaled(d0); y1 = scaled(d1)
            if y0 == y1:
                result[rows - y0][x + offset] = symbols[4]
                continue
            result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
            result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]
            for yy in range(min(y0,y1)+1, max(y0,y1)):
                result[rows - yy][x + offset] = symbols[9]
    # dotted mean line (use ASCII '┄' but write into BLANK canvas)
    mean_val = sum(flat) / len(flat)
    mean_row = None
    try:
        mean_scaled = scaled(mean_val)
        mean_row = max(0, min(rows, rows - mean_scaled))
        for c in range(offset, width):
            if result[mean_row][c] == BLANK:
                result[mean_row][c] = '┄'
    except Exception:
        mean_row = None
    # optional mean label placement (same logic, checks BLANK)
    mean_label = cfg.get('mean_label', None)
    if mean_label and mean_row is not None:
        label = f" {mean_label} "
        L = len(label)
        placed = False
        for start in range(offset, width - L + 1):
            ok = True
            for k in range(L):
                if result[mean_row][start + k] != BLANK:
                    ok = False
                    break
            if ok:
                for k, ch in enumerate(label):
                    result[mean_row][start + k] = ch
                placed = True
                break
        if not placed:
            for dr in range(1, 4):
                for r in (mean_row - dr, mean_row + dr):
                    if r < 0 or r > rows:
                        continue
                    for start in range(offset, width - L + 1):
                        ok = True
                        for k in range(L):
                            if result[r][start + k] != BLANK:
                                ok = False
                                break
                        if ok:
                            for k, ch in enumerate(label):
                                result[r][start + k] = ch
                            placed = True
                            break
                    if placed:
                        break
                if placed:
                    break
    # return lines exactly (do NOT rstrip); keep trailing BLANKs for alignment
    return "\n".join("".join(row) for row in result)


def fetch_commits_limited(owner: str, repo: str, token: Optional[str], max_commits: int = 300) -> List[dict]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    per_page = 100
    page = 1
    commits = []
    while len(commits) < max_commits:
        params = {"per_page": per_page, "page": page}
        try:
            r = SESSION.get(url, headers={"Authorization": f"token {token}"} if token else {}, params=params, timeout=20)
            if r.status_code == 404:
                break
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            commits.extend(data)
            if len(data) < per_page:
                break
            page += 1
        except Exception:
            break
    return commits[:max_commits]

def parse_commit_datetime(commit_obj: dict) -> Optional[datetime]:
    try:
        date_str = None
        if isinstance(commit_obj.get('commit'), dict):
            date_str = commit_obj['commit'].get('author', {}).get('date')
        if not date_str:
            date_str = commit_obj.get('author', {}).get('date') if isinstance(commit_obj.get('author'), dict) else None
        if not date_str:
            return None
        if date_str.endswith('Z'):
            date_str = date_str.replace('Z', '+00:00')
        return datetime.fromisoformat(date_str)
    except Exception:
        return None

def fetch_commit_timestamps_for_repos(repo_pairs: List[Tuple[str,str]], token: Optional[str], per_repo_limit: int = 300, max_workers: int = 6) -> List[datetime]:
    timestamps: List[datetime] = []
    def worker(pair):
        owner, repo = pair
        commits = fetch_commits_limited(owner, repo, token, max_commits=per_repo_limit)
        out = []
        for c in commits:
            dt = parse_commit_datetime(c)
            if dt:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                out.append(dt)
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(worker, p): p for p in repo_pairs}
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    timestamps.extend(res)
            except Exception:
                pass
    return timestamps

def build_commit_hour_values(timestamps: List[datetime], tz: Optional[timezone]=None) -> List[float]:
    out = []
    for dt in timestamps:
        if tz:
            dt = dt.astimezone(tz)
        else:
            dt = dt.astimezone(timezone.utc)
        hour = dt.hour + dt.minute/60.0 + dt.second/3600.0
        out.append(hour)
    return out

def build_readme(ascii_table: str, contrib_grid: str, ascii_plot: str, ascii_hist: str) -> str:
    return (
        "<pre>\n"
        f"{HEADERB: ^{LINE_LENGTH}}\n"
        f"{LINE:▔^{LINE_LENGTH}}\n\n"
        f"{ascii_plot}\n\n\n"

        f"{HEADERC: ^{LINE_LENGTH}}\n"
        f"{LINE:▔^{LINE_LENGTH}}\n\n"
        f"{contrib_grid}\n\n\n"

        f"{HEADERD: ^{LINE_LENGTH}}\n"
        f"{LINE:▔^{LINE_LENGTH}}\n\n"
        f"{ascii_hist}\n\n\n"

        f"{HEADERE: ^{LINE_LENGTH}}\n"
        f"{LINE:▔^{LINE_LENGTH}}\n\n"
        f"{ascii_table}\n\n\n"

        f"{HEADERA: ^{LINE_LENGTH}}\n"
        "</pre>\n"
    )

def build_rows_for_table(repos: List[dict], token: Optional[str]) -> List[dict]:
    def worker(r: dict) -> dict:
        owner = None
        if isinstance(r.get('owner'), dict):
            owner = r['owner'].get('login')
        name = r.get('name') or r.get('full_name') or ''
        html_url = r.get('html_url', '')
        language = r.get('language') or ''
        size = r.get('size', 0)
        commits = 0
        branches = 0
        try:
            if owner and name:
                commits = get_commit_count(owner, name, token)
                branches = get_branch_count(owner, name, token)
        except Exception:
            commits = 0
            branches = 0
        last = r.get('pushed_at') or r.get('updated_at') or ''
        last_commit = ''
        if last:
            try:
                if last.endswith('Z'):
                    dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
                else:
                    dt = datetime.fromisoformat(last)
                last_commit = dt.strftime('%Y-%m-%d')
            except Exception:
                last_commit = last
        return {
            'name_text': name,
            'name_url': html_url,
            'language': language,
            'size': size,
            'commits': commits,
            'last_commit': last_commit,
            'branches': branches,
        }
    results = [None] * len(repos)
    if not repos:
        return []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(repos)))) as ex:
        futures = {ex.submit(worker, r): idx for idx, r in enumerate(repos)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception:
                results[idx] = None
    return [r for r in results if r]

def _apply_ascii_coloring_on_block(block: str) -> str:
    out = block
    braille_chars = [c for c in SHADES if c]
    braille_color_to_use = BRAILLE_COLOR if BRAILLE_COLOR is not None else COLOR_HEX
    if ENABLE_COLOR and braille_color_to_use:
        for ch in set(braille_chars):
            if ch:
                out = out.replace(ch, f'<code style="color : {braille_color_to_use}">{ch}</code>')
    if ENABLE_COLOR:
        if TOTAL_LINE_COLOR:
            out = out.replace('┄', f'<code style="color : {TOTAL_LINE_COLOR}">┄</code>')
        elif COLOR_HEX:
            out = out.replace('┄', f'<code style="color : {COLOR_HEX}">┄</code>')
    return out

def _apply_ascii_coloring(readme_str: str) -> str:
    if not ENABLE_COLOR:
        return readme_str
    m = re.search(r'(<pre[^>]*>)(.*?)(</pre>)', readme_str, flags=re.S)
    if not m:
        return _apply_ascii_coloring_on_block(readme_str)
    prefix = readme_str[:m.start()]
    head = m.group(1)
    pre_content = m.group(2)
    tail_tag = m.group(3)
    suffix = readme_str[m.end():]
    new_pre = _apply_ascii_coloring_on_block(pre_content)
    return prefix + head + new_pre + tail_tag + suffix

def main():
    token = auth_token()
    try:
        all_repos = _get_paginated(f"{GITHUB_API}/user/repos" if token else f"{GITHUB_API}/users/{USERNAME}/repos",
                                   params={"sort":"updated","direction":"desc"}, token=token)
    except Exception as e:
        print("Failed to fetch repositories:", e, file=sys.stderr)
        sys.exit(1)
    all_repos.sort(key=lambda x: x.get("updated_at",""), reverse=True)
    top_repos = all_repos[:TOP_N]
    public_repos = [r for r in top_repos if not r.get("private")]
    private_repos = [r for r in top_repos if r.get("private")]
    rows = build_rows_for_table(public_repos, token)
    repo_urls = {r["name_text"]: r.get("name_url", "") for r in rows}
    ascii_table, ascii_width, ascii_height = make_ascii_table_with_links(rows)
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
                weeks = [0]*(WEEKS - len(weeks)) + weeks
            repo_weekly[repo] = weeks
    if private_repos and token:
        agg_weeks = [0]*WEEKS
        for repo in private_repos:
            owner = repo.get("owner", {}).get("login") if repo.get('owner') else None
            name = repo.get("name")
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
    cache = load_cache()
    updated_cache = dict(cache)
    for repo in list(repo_weekly.keys()):
        series = repo_weekly[repo]
        total = sum(series)
        nonzero_count = sum(1 for x in series if x != 0)
        use_cache = False
        if total == 0 or nonzero_count <= 1:
            cached = cache.get(repo)
            if cached and sum(cached) > 0:
                repo_weekly[repo] = cached
                use_cache = True
        if not use_cache and sum(series) > 0:
            updated_cache[repo] = series
    save_cache(updated_cache)
    weekly_totals = [0.0]*WEEKS
    for weeks in repo_weekly.values():
        if len(weeks) < WEEKS:
            weeks = [0]*(WEEKS-len(weeks)) + weeks
        for i, v in enumerate(weeks):
            weekly_totals[i] += float(v)
    repo_pairs = []
    for r in top_repos:
        owner = r.get('owner')
        if isinstance(owner, dict):
            login = owner.get('login')
        else:
            login = None
        name = r.get('name')
        if login and name:
            repo_pairs.append((login, name))
    print(f"Fetching timestamps for commits across {len(repo_pairs)} repos (up to 300 commits per repo)...")
    timestamps = fetch_commit_timestamps_for_repos(repo_pairs, token, per_repo_limit=300, max_workers=6)
    hours = build_commit_hour_values(timestamps)
    native_label_w = max(10, max((len(r) for r in repo_order), default=10))
    native_label_w = min(native_label_w, 28)
    if not weekly_totals or all(v == 0 for v in weekly_totals):
        ascii_plot = "(no activity data)"
        contrib_grid, used_label_w = build_contrib_grid(repo_weekly, repo_order, label_w=native_label_w, repo_urls=repo_urls)
    else:
        series_points = []
        for w in weekly_totals:
            series_points += [w, w]
        raw_max = max(series_points) if series_points else 0.0
        if raw_max >= 1_000_000:
            scale, suffix = 1_000_000.0, "M"
        elif raw_max >= 1_000:
            scale, suffix = 1_000.0, "K"
        else:
            scale, suffix = 1.0, ""
        scaled_series = [x/scale for x in series_points]
        maximum_scaled = max(scaled_series) if scaled_series else 1.0
        if maximum_scaled <= 0:
            maximum_scaled = 1.0
        fmt_w, fmt_p = 7, 1
        label_fmt = f"{{:{fmt_w}.{fmt_p}f}} "
        offset_len = len(label_fmt.format(maximum_scaled))
        req_w = offset_len + len(scaled_series) + 1
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
            max_pts = max(6, ascii_width - offset_len - 1)
            scaled_series = scaled_series[-max_pts:]
            req_w = offset_len + len(scaled_series) + 1
        left = max(offset_len, native_label_w + 1)
        used_label_w = left - 1
        contrib_grid, _ = build_contrib_grid(repo_weekly, repo_order, label_w=used_label_w, repo_urls=repo_urls)
        cfg = {"height": PLOT_HEIGHT, "format": label_fmt, "offset": left, "min": 0.0, "max": maximum_scaled,
               "mean_label": "long-term mean"}
        ascii_body = plot_with_mean(scaled_series, cfg)
        axis_labels = month_initials_for_weeks(WEEKS, use_three_letter=False)
        axis_line = BLANK * left + "".join(ch + BLANK for ch in axis_labels)
        ascii_plot = "\n" + ascii_body + "\n" + axis_line

    # build the new combined histogram (days left, hours right)
    ascii_hist = build_dual_histogram(hours, timestamps, max_width=MAX_WIDTH, label_day_w=4)

    readme = build_readme(ascii_table, contrib_grid, ascii_plot, ascii_hist)
    readme = _apply_ascii_coloring(readme)

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)
    print("README.md updated.")

if __name__ == "__main__":
    main()
