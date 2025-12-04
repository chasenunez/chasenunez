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

COLOR_HEX = "#000000"        # overall fallback color (default black)
BRAILLE_COLOR = "#8dc990"       # default to None -> falls back to COLOR_HEX
TOTAL_LINE_COLOR = "#8dc990" # color for the total/mean line (green by default)

USE_SVG = True # Toggle this to True to produce a colored SVG

# SVG file settings
SVG_FILENAME = "readme_ascii.svg"
SVG_FONT_SIZE = 14           # px; increase to make text larger
SVG_LINE_HEIGHT = 1.15       # line height multiplier (relative to font size)
SVG_PADDING = 8              # px padding around content
SVG_FONT_FAMILY = 'DejaVu Sans Mono, "Courier New", monospace'

DAY = datetime.now().strftime("%A")
DATECONSTRUCT = datetime.now().strftime("%A %d %B, %Y")
TIMECONSTRUCT = datetime.now().strftime("%H")
MINUTECONSTRUCT = datetime.now().strftime("%M")
APPROXTIME = getTimeOfDay(TIMECONSTRUCT)
HEADERA = f"⢀⣠⣴⣾⣿ Updated {DAY} {APPROXTIME} At {TIMECONSTRUCT}:{MINUTECONSTRUCT} CEST ⣿⣷⣦⣄⡀"
HEADERB = "Commits Per-Week With Annual Average"
HEADERC = "Commit Allocation Among Most Active Projects"
HEADERD = "Commit Allocation By Hour Of The Day"
HEADERE = "Recently Active Project Details"
LINE = "▔"
TOP_N = 10
WEEKS = 42
MAX_WIDTH = 100
LINE_LENGTH = 110
RESTRICTED_NAME = "restricted"
PLOT_HEIGHT = 10
PLOT_FORMAT = "{:8.1f} "
SHADES = ["","⡀","⡁","⡑","⡕","⡝","⣝","⣽","⣿"]
GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})",
})
CACHE_FILE = ".commit_activity_cache.json"
STATS_MAX_ATTEMPTS = 8


def _xml_escape(s: str) -> str:
    """Escape XML special chars for text nodes."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

def _svg_color_for_char(ch: str) -> Optional[str]:
    """Return color hex for a character or None for default (COLOR_HEX)."""
    # braille characters from SHADES (skip empty)
    braille_set = set(c for c in SHADES if c)
    if ch in braille_set:
        return BRAILLE_COLOR or COLOR_HEX
    if ch == '┄':
        return TOTAL_LINE_COLOR or COLOR_HEX
    return COLOR_HEX

def generate_svg_from_pre(pre_text: str, filename: str = SVG_FILENAME,
                          font_size: int = SVG_FONT_SIZE,
                          line_height: float = SVG_LINE_HEIGHT,
                          padding: int = SVG_PADDING,
                          font_family: str = SVG_FONT_FAMILY) -> None:
    """
    Generate a simple SVG that renders the ASCII in `pre_text`.
    The function splits on lines and emits one <text> per line with nested <tspan>
    segments for differently-colored character runs.
    """
    if not pre_text:
        return

    lines = pre_text.splitlines()
    if not lines:
        return

    # compute size estimates
    max_cols = max(len(l) for l in lines)
    char_width_est = font_size * 0.62   # approximate char width for monospace
    width = int(padding*2 + max_cols * char_width_est)
    line_h = font_size * line_height
    height = int(padding*2 + len(lines) * line_h)

    # Build SVG content
    svg_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        # background (optional transparent)
        # f'<rect width="100%" height="100%" fill="white" />',
    ]

    # For each line create a <text> at appropriate y; inside it add <tspan> chunks
    x = padding
    y_start = padding + font_size  # first baseline
    for i, line in enumerate(lines):
        y = y_start + i * line_h
        # create chunks of same color to minimize tspans
        if not line:
            # empty line: still need a tspan with a single space so that SVG preserves it
            svg_parts.append(
                f'<text x="{x}" y="{y}" xml:space="preserve" font-family="{font_family}" font-size="{font_size}px">'
                f'<tspan fill="{COLOR_HEX}"> </tspan></text>'
            )
            continue

        # accumulate sequence of (color, text) segments
        segments = []
        cur_color = _svg_color_for_char(line[0])
        cur_seg = line[0]
        for ch in line[1:]:
            c = _svg_color_for_char(ch)
            if c == cur_color:
                cur_seg += ch
            else:
                segments.append((cur_color or COLOR_HEX, cur_seg))
                cur_color = c
                cur_seg = ch
        segments.append((cur_color or COLOR_HEX, cur_seg))

        # Build text line with tspans
        line_tspans = []
        for col, seg in segments:
            seg_escaped = _xml_escape(seg)
            # If color is None (shouldn't be), use COLOR_HEX as fallback
            col_attr = col or COLOR_HEX or "#000000"
            line_tspans.append(f'<tspan fill="{col_attr}">{seg_escaped}</tspan>')

        svg_parts.append(
            f'<text x="{x}" y="{y}" xml:space="preserve" font-family="{font_family}" font-size="{font_size}px">'
            + "".join(line_tspans) + '</text>'
        )

    svg_parts.append('</svg>')
    svg_str = "\n".join(svg_parts)

    # Write file
    try:
        with open(filename, "w", encoding="utf-8") as fh:
            fh.write(svg_str)
    except Exception:
        # best-effort: ignore write errors
        pass

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
    cur = wcswidth(s)
    if cur == target:
        return s
    if cur < target:
        pad = target - cur
        if align == 'left':
            return s + " " * pad
        elif align == 'right':
            return " " * pad + s
        else:
            left = pad//2
            right = pad - left
            return " " * left + s + " " * right
    out = ""
    acc = 0
    for ch in s:
        ch_w = wcswidth(ch)
        if acc + ch_w > target:
            break
        out += ch
        acc += ch_w
    if acc < target and len(out) < len(s):
        if acc + wcswidth("…") <= target:
            out += "…"
            acc += wcswidth("…")
    if acc < target:
        out += " " * (target - acc)
    return out

def _wrap_color(s: str, hex_color: Optional[str]) -> str:
    """Return s wrapped in an inline span with color if hex_color is set, else s."""
    if not hex_color:
        return s
    # keep it minimal — we rely on README being HTML <pre> so span is allowed where not sanitized
    return f'<span style="color:{hex_color}">{s}</span>'

def _apply_ascii_coloring(readme_str: str) -> str:
    """
    Replace braille glyphs and the mean/total-line glyph with colored spans.
    This is a simple global replace; run just before saving the README.
    """
    # If no colors are configured, just return unchanged
    if not COLOR_HEX and not BRAILLE_COLOR and not TOTAL_LINE_COLOR:
        return readme_str

    out = readme_str

    # Make a set of braille glyphs from SHADES (ignore empty strings)
    braille_chars = [c for c in SHADES if c]
    braille_color_to_use = BRAILLE_COLOR or COLOR_HEX

    if braille_color_to_use:
        # Replace each braille glyph with a colored span
        # note: doing global replace because bars are repeated characters
        for ch in set(braille_chars):
            if ch:
                out = out.replace(ch, _wrap_color(ch, braille_color_to_use))

    # Color the mean/total line glyph used in plot_with_mean: '┄'
    # (If you used a different glyph for the mean line change below.)
    if TOTAL_LINE_COLOR:
        out = out.replace('┄', _wrap_color('┄', TOTAL_LINE_COLOR))
    elif COLOR_HEX:
        out = out.replace('┄', _wrap_color('┄', COLOR_HEX))

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

def build_contrib_grid(repo_weekly: Dict[str,List[int]],
                       repo_order: List[str],
                       label_w: Optional[int]=None,
                       repo_urls: Optional[Dict[str,str]]=None) -> Tuple[str,int]:
    if label_w is None:
        label_w = max(10, max((len(r) for r in repo_order), default=10))
        label_w = min(label_w, 28)
    else:
        label_w = max(10, min(label_w, 28))
    glyph_widths = [max(1, wcswidth(s)) for s in SHADES]
    slot_w = max(1, max(glyph_widths))
    sep = " "
    def render_slot(sym: str) -> str:
        cur = wcswidth(sym)
        if cur < 0:
            cur = 1
        if cur >= slot_w:
            return sym
        return sym + (" " * (slot_w - cur))
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
        row = f"{visible}┤ " + sep.join(slots)
        lines.append(row)
    axis_cells = month_initials_for_weeks(WEEKS, use_three_letter=False)
    axis_slots = [pad_to_width(ch, slot_w, align='center') for ch in axis_cells]
    axis_line = " " * label_w + " " + sep.join(axis_slots)
    lines.append(axis_line)
    legend_slots = [pad_to_width(s, slot_w, align='center') for s in SHADES]
    legend = " " * label_w + "low " + sep.join(legend_slots) + "  high"
    lines.append("")
    lines.append(legend)
    return "\n".join(lines), label_w

def _safe_isnan(x) -> bool:
    try:
        return isnan(float(x))
    except Exception:
        return False

def plot_with_mean(series, cfg=None) -> str:
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
    result = [[' ']*width for _ in range(rows+1)]
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
        result[line_idx][offset-1] = symbols[0] if y == 0 else symbols[1]
    try:
        if not _safe_isnan(series[0][0]):
            result[rows - scaled(series[0][0])][offset-1] = symbols[0]
    except Exception:
        pass
    for s in series:
        for x in range(len(s)-1):
            d0 = s[x]; d1 = s[x+1]
            if _safe_isnan(d0) and _safe_isnan(d1):
                continue
            if _safe_isnan(d0):
                result[rows - scaled(d1)][x + offset] = symbols[2]
                continue
            if _safe_isnan(d1):
                result[rows - scaled(d0)][x + offset] = symbols[3]
                continue
            y0 = scaled(d0); y1 = scaled(d1)
            if y0 == y1:
                result[rows - y0][x + offset] = symbols[4]
                continue
            result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
            result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]
            for yy in range(min(y0,y1)+1, max(y0,y1)):
                result[rows - yy][x + offset] = symbols[9]
    mean_val = sum(flat) / len(flat)
    try:
        mean_scaled = scaled(mean_val)
        mean_row = max(0, min(rows, rows - mean_scaled))
        for c in range(offset, width):
            if result[mean_row][c] == ' ':
                result[mean_row][c] = '┄'
    except Exception:
        mean_row = None
    mean_label = cfg.get('mean_label', None)
    if mean_label and mean_row is not None:
        label = f" {mean_label} "
        L = len(label)
        placed = False
        for start in range(offset, width - L + 1):
            ok = True
            for k in range(L):
                if result[mean_row][start + k] != ' ':
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
                            if result[r][start + k] != ' ':
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
    return "\n".join("".join(row).rstrip() for row in result)

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

def build_histogram_ascii(hours: List[float], max_width: int = MAX_WIDTH, label_w: Optional[int] = None, use_braille: bool = True) -> str:
    if not hours:
        return '(no commit timestamps)'
    if label_w is None:
        label_w = 2
    counts = [0] * 24
    for h in hours:
        try:
            idx = int(h) % 24
        except Exception:
            continue
        counts[idx] += 1
    longest_count_len = len(str(max(counts))) if counts else 1
    reserved = label_w + 2 + 1 + longest_count_len
    bar_space = max(1, max_width - reserved)
    if _HAS_PLOTILLE:
        try:
            hist_str = plotille.hist(hours, bins=24, width=max(10, bar_space))
            out_lines = []
            for line in hist_str.splitlines():
                out_lines.append(line)
            lines = []
            for hr in range(24):
                label = pad_to_width(f'{hr:02d}', label_w, align='right')
                bar = ''
                count = counts[hr]
                lines.append(f'{label}┤ {bar} {count}')
            return '\n'.join(lines)
        except Exception:
            pass
    max_count = max(counts) if counts else 0
    braille_full = '⣿'
    block_full = '█'
    bar_char = braille_full if use_braille else block_full
    lines = []
    for hr in range(24):
        c = counts[hr]
        if max_count:
            bar_len = int(round((c / max_count) * bar_space))
        else:
            bar_len = 0
        bar = bar_char * bar_len
        label = pad_to_width(f'{hr:02d}', label_w, align='right')
        lines.append(f'{label}┤ {bar} {c}')
    return '\n'.join(lines)


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
        axis_line = " " * left + "".join(ch + " " for ch in axis_labels)
        ascii_plot = "\n" + ascii_body + "\n" + axis_line
        ascii_hist = build_histogram_ascii(hours, max_width=MAX_WIDTH, label_w=used_label_w, use_braille=True)
        readme = build_readme(ascii_table, contrib_grid, ascii_plot, ascii_hist)

    if USE_SVG:
        # Extract the raw content inside <pre>...</pre>
        m = re.search(r'<pre>(.*?)</pre>', readme, flags=re.S)
        if m:
            pre_content = m.group(1).lstrip("\n").rstrip("\n")
        else:
            pre_content = readme

        # Generate SVG from the ASCII inside the pre block
        generate_svg_from_pre(pre_content, filename=SVG_FILENAME,
                              font_size=SVG_FONT_SIZE, line_height=SVG_LINE_HEIGHT, padding=SVG_PADDING,
                              font_family=SVG_FONT_FAMILY)

        # Build README: show SVG image and include the original ASCII in a collapsible block
        readme = (
            f'<p><img src="{SVG_FILENAME}" alt="Commit activity ASCII visualization" /></p>\n\n'
            f'<details>\n<summary>Show ASCII</summary>\n\n'
            f'<pre>\n{pre_content}\n</pre>\n\n'
            f'</details>\n'
        )
    else:
        # existing behavior: apply inline HTML coloring (may be sanitized by GitHub)
        readme = _apply_ascii_coloring(readme)

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)
    print("README.md updated.")


if __name__ == "__main__":
    main()