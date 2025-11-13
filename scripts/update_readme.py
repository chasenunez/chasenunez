#!/usr/bin/env python3
"""
scripts/update_readme.py

- Produces an ASCII-style table (text-art box) with clickable repo links (uses HTML anchors),
  and a contributions-style grid showing weekly commit density over the last 52 weeks.
- Adds a 'restricted' aggregated row for private repos if GH_PAT is provided in environment.
- Writes README.md containing:
    Most Recent Repository Updates            <- plain text header
    <pre>ASCII table with clickable links</pre>
    commit density by date/project           <- plain text header
    <pre>contributions grid</pre>

Auth:
 - Provide GH_PAT (repo-scoped PAT) via environment to include private repos and commit activity for them.
 - The script falls back to GITHUB_TOKEN for public data only.

Requirements:
    pip install requests
"""
from __future__ import annotations
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from math import floor
from typing import Dict, List, Tuple

import requests

# ---------- Configuration ----------
USERNAME = "chasenunez"
TOP_N = 10
WEEKS = 47  # 52 weeks = 1 year
SHADES = [" ", "░", "▒", "▓", "█"]  # intensity glyphs low->high
STATS_MAX_RETRIES = 6
STATS_RETRY_SLEEP = 1.5  # seconds
# -----------------------------------

GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})",
})


def auth_token() -> str | None:
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def gh_get(url: str, params: dict | None = None, token: str | None = None) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {})
    resp.raise_for_status()
    return resp


def fetch_repos_for_user(token: str | None = None, per_page: int = 300) -> List[dict]:
    """
    If a token is provided, call /user/repos to get private repos too (affiliation owner).
    Otherwise call /users/{USERNAME}/repos for public repos.
    """
    if token:
        url = f"{GITHUB_API}/user/repos"
        params = {"per_page": per_page, "sort": "updated", "direction": "desc", "affiliation": "owner"}
    else:
        url = f"{GITHUB_API}/users/{USERNAME}/repos"
        params = {"per_page": per_page, "sort": "updated", "direction": "desc"}
    r = gh_get(url, params=params, token=token)
    return r.json()


def repo_commit_activity(owner: str, repo: str, token: str | None = None) -> List[int]:
    """
    Return the last WEEKS weekly commit totals (oldest->newest) using /repos/{owner}/{repo}/stats/commit_activity.
    Retries on 202 Accepted while GitHub computes the stats.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    attempt = 0
    while attempt < STATS_MAX_RETRIES:
        try:
            r = gh_get(url, token=token)
        except requests.HTTPError:
            # treat errors (e.g., 404, private unauthorized) as zeros
            return [0] * WEEKS
        if r.status_code == 202:
            attempt += 1
            time.sleep(STATS_RETRY_SLEEP)
            continue
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return [0] * WEEKS
        weeks = [int(w.get("total", 0)) for w in data]
        if len(weeks) >= WEEKS:
            return weeks[-WEEKS:]
        pad = [0] * (WEEKS - len(weeks))
        return pad + weeks
    return [0] * WEEKS


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
    except requests.HTTPError as e:
        # some repos may return 404 or unauthorized for branches; treat as 0
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
    """
    rows: list of dicts containing:
        - name_text (visible plain name)
        - name_url (repo html url)
        - language, size, commits, last_commit, branches
    Returns (table_string, table_width_chars, table_height_lines).
    The table_string uses HTML anchors for repo names; the string is intended to be placed inside <pre>...</pre>.
    """
    cols = ["Repository", "Main Language", "Total Size (bytes)", "Total Commits", "Date of Last Commit", "Branches"]
    data_rows = []
    for r in rows:
        data_rows.append([
            r["name_text"],      # visible name (we'll insert anchor in output)
            r["language"],
            str(r["size"]),
            str(r["commits"]),
            r["last_commit"],
            str(r.get("branches", "0"))
        ])

    # compute widths based on visible content lengths
    widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    widths = [w + 2 for w in widths]  # add 1 space padding both sides

    # build top border
    top_line = "+" + "+".join(["-" * w for w in widths]) + "+"
    lines = [top_line]

    # header centered
    header_cells = []
    for i, c in enumerate(cols):
        content = " " + c.center(widths[i] - 2) + " "
        header_cells.append(content)
    lines.append("|" + "|".join(header_cells) + "|")
    lines.append(top_line)

    # data rows; for the first (Repository) column, include an <a href="...">name</a> anchor
    for row in rows:
        cells = []
        # Repository column (index 0)
        vis_name = row["name_text"]
        url = row.get("name_url", "")
        inner_w0 = widths[0] - 2
        # ensure visible name fits; if not, truncate for centering calculation and visual
        if len(vis_name) > inner_w0:
            vis_name_display = vis_name[:inner_w0 - 1] + "…"
        else:
            vis_name_display = vis_name
        left_pad = (inner_w0 - len(vis_name_display)) // 2
        right_pad = inner_w0 - len(vis_name_display) - left_pad
        repo_cell = " " + (" " * left_pad) + f'<a href="{url}">{vis_name_display}</a>' + (" " * right_pad) + " "
        cells.append(repo_cell)

        # other columns — center text
        other = [row["language"], str(row["size"]), str(row["commits"]), row["last_commit"], str(row.get("branches", "0"))]
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
    """
    repo_weekly: repo -> list of WEEKS ints (oldest->newest)
    repo_order: order of rows
    Returns grid as multiline string; left labels are plain text, grid cells are shade characters.
    """
    label_w = max(10, max((len(r) for r in repo_order), default=10))
    label_w = min(label_w, 28)
    cols = WEEKS

    lines = []
    for repo in repo_order:
        weeks = repo_weekly.get(repo, [0] * cols)
        max_val = max(weeks) or 1
        row_cells = []
        for w in weeks:
            ratio = w / max_val if max_val > 0 else 0.0
            idx = int(round(ratio * (len(SHADES) - 1)))
            idx = max(0, min(len(SHADES) - 1, idx))
            row_cells.append(SHADES[idx])
        # build visible truncated/padded repo name
        vis_name = repo
        if len(vis_name) > label_w:
            vis_name = vis_name[: label_w - 1] + "…"
        else:
            vis_name = vis_name.ljust(label_w)
        lines.append(f"{vis_name} {' '.join(row_cells)}")

    legend = " " * label_w + " " + " ".join(SHADES[1:]) + "  (low->high)"
    lines.append(legend)

    # small week axis labels (month initials) every 4 weeks
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


def main() -> None:
    token = auth_token()
    try:
        all_repos = fetch_repos_for_user(token=token, per_page=300)
    except Exception as e:
        print("Failed to fetch repositories:", e, file=sys.stderr)
        sys.exit(1)

    public_repos = [r for r in all_repos if not r.get("private")]
    private_repos = [r for r in all_repos if r.get("private")]

    top_public = public_repos[:TOP_N]
    rows = build_rows_for_table(top_public, token)

    ascii_table, ascii_width, ascii_height = make_ascii_table_with_links(rows)

    repo_weekly: Dict[str, List[int]] = {}
    repo_order: List[str] = []

    for r in rows:
        name = r["name_text"]
        owner = USERNAME
        repo_weekly[name] = repo_commit_activity(owner, name, token=token)
        repo_order.append(name)

    restricted_name = "restricted"
    if private_repos and token:
        agg = [0] * WEEKS
        for repo in private_repos:
            owner = repo["owner"]["login"]
            name = repo["name"]
            weekly = repo_commit_activity(owner, name, token=token)
            for i in range(WEEKS):
                agg[i] += weekly[i]
        repo_weekly[restricted_name] = agg
        repo_order.append(restricted_name)
    else:
        if token:
            repo_weekly[restricted_name] = [0] * WEEKS
            repo_order.append(restricted_name)

    grid = build_contrib_grid(repo_weekly, repo_order)

    # Compose README: include headers, ASCII table (inside <pre>), header for graph, and grid (inside <pre>)
    readme = (
        "<pre>\n"
        "┏━               ┏━┓┏━╸┏━╸┏━╸┏┓╻╺┳╸╻  ╻ ╻   ┏━┓┏━╸╺┳╸╻╻ ╻┏━╸   ┏━┓┏━╸┏━┓┏━┓┏━┓╻╺┳╸┏━┓┏━┓╻┏━╸┏━┓               ━┓\n"
        "┃                ┣┳┛┣╸ ┃  ┣╸ ┃┗┫ ┃ ┃  ┗┳┛   ┣━┫┃   ┃ ┃┃┏┛┣╸    ┣┳┛┣╸ ┣━┛┃ ┃┗━┓┃ ┃ ┃ ┃┣┳┛┃┣╸ ┗━┓                ┃\n"
        "┗━               ╹┗╸┗━╸┗━╸┗━╸╹ ╹ ╹ ┗━╸ ╹    ╹ ╹┗━╸ ╹ ╹┗┛ ┗━╸   ╹┗╸┗━╸╹  ┗━┛┗━┛╹ ╹ ┗━┛╹┗╸╹┗━╸┗━┛               ━┛\n"
        "</pre>\n"
        #"recently active repositories:"
        "<pre>\n"
        f"{ascii_table}\n"
        "</pre>\n\n"
        "<pre>\n"
        "┏━              ┏━┓┏━╸╺┳╸╻╻ ╻╻╺┳╸╻ ╻   ╺┳┓┏━╸┏┓╻┏━┓╻╺┳╸╻ ╻   ╺┳╸╻ ╻┏━┓┏━┓╻ ╻┏━╸╻ ╻   ╺┳╸╻┏┳┓┏━╸               ━┓\n"
        "┃               ┣━┫┃   ┃ ┃┃┏┛┃ ┃ ┗┳┛    ┃┃┣╸ ┃┗┫┗━┓┃ ┃ ┗┳┛    ┃ ┣━┫┣┳┛┃ ┃┃ ┃┃╺┓┣━┫    ┃ ┃┃┃┃┣╸                 ┃\n"
        "┗━              ╹ ╹┗━╸ ╹ ╹┗┛ ╹ ╹  ╹    ╺┻┛┗━╸╹ ╹┗━┛╹ ╹  ╹     ╹ ╹ ╹╹┗╸┗━┛┗━┛┗━┛╹ ╹    ╹ ╹╹ ╹┗━╸               ━┛\n"
        "</pre>\n"#"activity density through time:\n\n"
        "<pre>\n"
        f"{grid}\n"
        "</pre>\n"
    )

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)

    print("README.md updated.")


if __name__ == "__main__":
    main()
