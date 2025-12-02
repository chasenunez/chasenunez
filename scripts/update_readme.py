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
# try to use wcwidth if installed for accurate terminal widths
try:
    import wcwidth
    _HAS_WCWIDTH = True
except Exception:
    wcwidth = None
    _HAS_WCWIDTH = False

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

# ---------- Configuration ----------
USERNAME = "chasenunez"

DAY = datetime.now().strftime("%A")
DATECONSTRUCT = datetime.now().strftime("%A %d %B, %Y")
TIMECONSTRUCT = datetime.now().strftime("%H")
MINUTECONSTRUCT = datetime.now().strftime("%M")
APPROXTIME = getTimeOfDay(TIMECONSTRUCT)

HEADERA = "Detailed Composition Of Recently Active Repos"
HEADERB = "Weekly Commit Intensity Among Recently Active Repositories"
HEADERC = f"Annual(ish) Activity Breakdown as of {DAY} {APPROXTIME} at {TIMECONSTRUCT}:{MINUTECONSTRUCT} CEST"

LINE = "━"

TOP_N = 10
WEEKS = 42
MAX_WIDTH = 110
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

# cache file path for fallback commit_activity
CACHE_FILE = ".commit_activity_cache.json"
# how many /stats retries before giving up
STATS_MAX_ATTEMPTS = 8

# -----------------------------------

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
    """
    Stronger retry/backoff for GitHub /stats endpoints that sometimes return 202 while generating.
    """
    attempt = 0
    wait = 1.0
    while attempt < STATS_MAX_ATTEMPTS:
        try:
            r = SESSION.get(url, headers={"Authorization": f"token {token}"} if token else {}, timeout=20)
        except requests.RequestException:
            return None
        if r.status_code == 202:
            # still being generated on server side
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
    """
    Request weekly commit counts for the repo (oldest -> newest).
    if we cannot obtain non-empty data we return a list of zeros.
    """
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
    # fallback: return zeros (caller may apply cache fallback)
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
            return int(m.group(1))
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
            return int(m.group(1))
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

# ----------------------------
# Small display-width helpers
# ----------------------------
def wcswidth_fallback(s: str) -> int:
    """Fallback display width: combining marks 0, East Asian W/F -> 2, else 1."""
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
    """Pad/truncate string `s` to display width `target`. Align options: left/right/center."""
    # compute visible width
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
    # pad if still short
    if acc < target:
        out += " " * (target - acc)
    return out

# ----------------------------
# Cache helpers
# ----------------------------
def load_cache() -> Dict[str, List[int]]:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
            # ensure lists are proper length
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

# ----------------------------
# Month label helper
# ----------------------------
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
                    # pick a different char from the month name if collision with previous
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

# ----------------------------
# Table builder
# ----------------------------
from typing import List, Tuple
def make_ascii_table_with_links(rows: List[dict], max_repo_name_width: int = None) -> Tuple[str,int,int]:
    # --- lightweight table code adapted from your latest version ---
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
# Heatmap builder 
# ----------------------------
def build_contrib_grid(repo_weekly: Dict[str,List[int]],
                       repo_order: List[str],
                       label_w: Optional[int]=None,
                       repo_urls: Optional[Dict[str,str]]=None) -> Tuple[str,int]:
    """
    Build ASCII heat map (rows = repos, cols = weeks) using SHADES.
    Ensures each week column uses a fixed 'slot' so rows and x-axis align.
    """
    # label width handling
    if label_w is None:
        label_w = max(10, max((len(r) for r in repo_order), default=10))
        label_w = min(label_w, 28)
    else:
        label_w = max(10, min(label_w, 28))

    # Determine slot width from SHADES using wcswidth (accurate if wcwidth available)
    glyph_widths = [max(1, wcswidth(s)) for s in SHADES]
    slot_w = max(1, max(glyph_widths))
    sep = " "  # space between slots; per-week width will be slot_w + len(sep)

    def render_slot(sym: str) -> str:
        # Render symbol and pad to slot_w visible columns
        cur = wcswidth(sym)
        if cur < 0:
            cur = 1
        if cur >= slot_w:
            # if it's wider than slot_w, we attempt to trim to slot_w by picking a simpler symbol
            # fallback: use the last shade (highest intensity) truncated to single char if needed
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
        # label by display-width (use pad_to_width)
        visible_name = repo
        if wcswidth(visible_name) > label_w:
            # truncate by characters to fit label_w
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

    # axis (use same slot_w spacing)
    axis_cells = month_initials_for_weeks(WEEKS, use_three_letter=False)
    axis_slots = [pad_to_width(ch, slot_w, align='center') for ch in axis_cells]
    axis_line = " " * label_w + " " + sep.join(axis_slots)
    lines.append(axis_line)

    # legend
    legend_slots = [pad_to_width(s, slot_w, align='center') for s in SHADES]
    legend = " " * label_w + "low " + sep.join(legend_slots) + "  high"
    lines.append("")
    lines.append(legend)

    return "\n".join(lines), label_w

# ----------------------------
# plot_with_mean
# ----------------------------
def plot_with_mean(series, cfg=None) -> str:
    if not series:
        return ""
    if not isinstance(series[0], list):
        if all(isnan(x) for x in series):
            return ""
        series = [series]
    flat = [x for s in series for x in s if not isnan(x)]
    if not flat:
        return ""
    cfg = cfg or {}
    minimum = cfg.get('min', min(flat))
    maximum = cfg.get('max', max(flat))
    symbols = cfg.get('symbols', ['┼','┤','╶','╴','─','╰','╭','╮','╯','│'])
    interval = maximum - minimum if (maximum - minimum) != 0 else 1.0
    offset = cfg.get('offset', max(8, len(cfg.get('format',PLOT_FORMAT).format(maximum))))
    height = cfg.get('height', PLOT_HEIGHT)
    ratio = height / (maximum - minimum) if (maximum - minimum) else 1.0
    min2 = int(floor(minimum * ratio))
    max2 = int(ceil(maximum * ratio))
    def clamp(x): return min(max(x, minimum), maximum)
    def scaled(y): return int(round(clamp(y) * ratio) - min2)
    rows = max2 - min2
    width = max(len(s) for s in series) + offset
    result = [[' ']*width for _ in range(rows+1)]
    # y-axis labels
    for y in range(min2, max2+1):
        label = cfg.get('format',PLOT_FORMAT).format(maximum - ((y-min2) * interval / (rows if rows else 1)))
        pos = max(offset - len(label), 0)
        line_idx = y - min2
        for idx,ch in enumerate(label):
            if pos + idx < width:
                result[line_idx][pos+idx] = ch
        result[line_idx][offset-1] = symbols[0] if y == 0 else symbols[1]
    # first point marker
    try:
        if not isnan(series[0][0]):
            result[rows - scaled(series[0][0])][offset-1] = symbols[0]
    except Exception:
        pass
    # plot lines
    for s in series:
        for x in range(len(s)-1):
            d0 = s[x]; d1 = s[x+1]
            if isnan(d0) and isnan(d1):
                continue
            if isnan(d0):
                result[rows - scaled(d1)][x + offset] = symbols[2]; continue
            if isnan(d1):
                result[rows - scaled(d0)][x + offset] = symbols[3]; continue
            y0 = scaled(d0); y1 = scaled(d1)
            if y0 == y1:
                result[rows - y0][x + offset] = symbols[4]
                continue
            result[rows - y1][x + offset] = symbols[5] if y0 > y1 else symbols[6]
            result[rows - y0][x + offset] = symbols[7] if y0 > y1 else symbols[8]
            for yy in range(min(y0,y1)+1, max(y0,y1)):
                result[rows - yy][x + offset] = symbols[9]
    # dotted mean line
    mean_val = sum(flat) / len(flat)
    try:
        mean_scaled = scaled(mean_val)
        mean_row = max(0, min(rows, rows - mean_scaled))
        for c in range(offset, width):
            if result[mean_row][c] == ' ':
                result[mean_row][c] = '┄'
    except Exception:
        mean_row = None

    # attempt to place mean label if supplied
    mean_label = cfg.get('mean_label', None)
    if mean_label and mean_row is not None:
        label = f" {mean_label} "
        L = len(label)
        # search from left after offset for run of L spaces on mean_row
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
        # if not placed, try rows above/below up to +/-3
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

# ----------------------------
# README builder and main flow
# ----------------------------
def build_readme(ascii_table: str, contrib_grid: str, ascii_plot: str) -> str:
    return (
        "<pre>\n"
        f"{HEADERC: ^{MAX_WIDTH}}\n"
        f"{LINE:━^{MAX_WIDTH}}\n"
        f"{ascii_plot}\n"
        f"{contrib_grid}\n\n\n"
        f"{HEADERA: ^{MAX_WIDTH}}\n"
        f"{LINE:━^{MAX_WIDTH}}\n\n"                                                                                                               
        f"{ascii_table}\n"
        "</pre>\n"
    )

def build_rows_for_table(repos: List[dict], token: str=None) -> List[dict]:
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

    # fetch commit activity in parallel
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

    # aggregate private into restricted
    if private_repos and token:
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

    # --- CACHE FALLBACK LOGIC: if a repo's series is all zeros or extremely small,
    # try to use the last cached good series (if available). After this, save updated cache.
    cache = load_cache()
    updated_cache = dict(cache)  # we'll update entries that look good
    for repo in list(repo_weekly.keys()):
        series = repo_weekly[repo]
        total = sum(series)
        nonzero_count = sum(1 for x in series if x != 0)
        # heuristics: if total == 0 OR less than a couple of non-zero weeks, prefer cache
        use_cache = False
        if total == 0 or nonzero_count <= 1:
            cached = cache.get(repo)
            if cached and sum(cached) > 0:
                # use cached series for stability (reporting)
                repo_weekly[repo] = cached
                use_cache = True
        # if we have a good series now (sum>0), update the cache
        if not use_cache and sum(series) > 0:
            updated_cache[repo] = series
    # write cache back
    save_cache(updated_cache)

    # build weekly totals
    weekly_totals = [0.0]*WEEKS
    for weeks in repo_weekly.values():
        if len(weeks) < WEEKS:
            weeks = [0]*(WEEKS-len(weeks)) + weeks
        for i, v in enumerate(weeks):
            weekly_totals[i] += float(v)

    # prepare heatmap label width and slot alignment
    native_label_w = max(10, max((len(r) for r in repo_order), default=10))
    native_label_w = min(native_label_w, 28)

    if not weekly_totals or all(v == 0 for v in weekly_totals):
        ascii_plot = "(no activity data)"
        contrib_grid, used_label_w = build_contrib_grid(repo_weekly, repo_order, label_w=native_label_w, repo_urls=repo_urls)
    else:
        # duplicate columns for two columns/week behavior
        series_points = []
        for w in weekly_totals:
            series_points += [w, w]

        # absolute counts from 0..max (no centering)
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

        # compute global left margin and build heatmap with that label width
        left = max(offset_len, native_label_w + 1)
        used_label_w = left - 1
        contrib_grid, _ = build_contrib_grid(repo_weekly, repo_order, label_w=used_label_w, repo_urls=repo_urls)

        # build ascii plot with mean label insertion
        cfg = {"height": PLOT_HEIGHT, "format": label_fmt, "offset": left, "min": 0.0, "max": maximum_scaled,
               "mean_label": "long-term mean"}
        ascii_body = plot_with_mean(scaled_series, cfg)

        # x-axis aligned to slot width used in heatmap: to be safe rebuild axis using 1-char + space pattern
        axis_labels = month_initials_for_weeks(WEEKS, use_three_letter=False)
        axis_line = " " * left + "".join(ch + " " for ch in axis_labels)

        ascii_plot = "\n" + ascii_body + "\n" + axis_line

    readme = build_readme(ascii_table, contrib_grid, ascii_plot)
    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)
    print("README.md updated.")

if __name__ == "__main__":
    main()
