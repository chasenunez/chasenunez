#!/usr/bin/env python3
"""
scripts/update_readme.py

Fetch top-5 most recently-updated repositories for a GitHub user,
build a Markdown table and an ASCII table, create an ASCII language
distribution figure sized to match the ASCII table, write README.md,
and exit. Intended to run in GitHub Actions (uses GITHUB_TOKEN from env
if available).

Requirements:
    pip install requests
"""

from __future__ import annotations
import os
import re
import sys
from datetime import datetime
from math import floor
from typing import Dict, List, Tuple

import requests

GITHUB_API = "https://api.github.com"
USERNAME = "chasenunez"   # change if needed
TOP_N = 5
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": f"update-readme-script ({USERNAME})"
})


def gh_get(url: str, token: str | None = None, params: dict | None = None) -> requests.Response:
    """
    GET wrapper that optionally adds an Authorization header.
    Raises HTTPError on non-2xx responses (caller may catch).
    """
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {})
    resp.raise_for_status()
    return resp


def fetch_repos(user: str, token: str | None = None, per_page: int = 100) -> List[dict]:
    """
    Fetch public repos for a user, sorted by update time (desc).
    """
    url = f"{GITHUB_API}/users/{user}/repos"
    params = {"per_page": per_page, "sort": "updated", "direction": "desc"}
    r = gh_get(url, token, params)
    return r.json()


def fetch_languages(owner: str, repo: str, token: str | None = None) -> Dict[str, int]:
    """
    Call /repos/{owner}/{repo}/languages and return a dict language->bytes.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/languages"
    try:
        r = gh_get(url, token)
        return r.json() or {}
    except requests.HTTPError:
        return {}


def get_commit_count(owner: str, repo: str, token: str | None = None) -> int:
    """
    Estimate total commits using commits endpoint with per_page=1 and the Link header.
    If the repo is empty (HTTP 409), return 0. If Link header absent, return len(returned_commits).
    Note: Using per_page=1 makes the 'last' page number equal to total commits.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    params = {"per_page": 1}
    try:
        r = gh_get(url, token, params)
    except requests.HTTPError as e:
        # empty repository will sometimes return 409 with message "Git Repository is empty."
        if getattr(e.response, "status_code", None) == 409:
            return 0
        # treat other errors as 0 (caller can decide)
        return 0

    link = r.headers.get("Link", "")
    if link:
        # find page number in rel="last"
        m = re.search(r'[&?]page=(\d+)>;\s*rel="last"', link)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    # Fallback: return number of commits on this (single) page (0 or 1)
    try:
        commits = r.json()
        if isinstance(commits, list):
            return len(commits)
    except ValueError:
        pass
    return 0


def iso_to_date(s: str | None) -> str:
    """
    Convert ISO timestamp to YYYY-MM-DD. Handles trailing 'Z' and timezone offsets.
    """
    if not s:
        return ""
    try:
        # Replace trailing 'Z' with '+00:00' so fromisoformat can parse it reliably
        normalized = s.rstrip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return s


def build_markdown_table(rows: List[dict]) -> str:
    """
    Build a Markdown table for rows where each row has:
      name_md, language, size, commits, last_commit
    Column headers are bolded.
    """
    headers = ["**Repository**", "**Main Language (pct)**", "**Total Size (bytes)**", "**Total Commits**", "**Date of Last Commit**"]
    md = []
    md.append("| " + " | ".join(headers) + " |")
    md.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        md.append(f"| {r['name_md']} | {r['language']} | {r['size']} | {r['commits']} | {r['last_commit']} |")
    return "\n".join(md)


def make_ascii_table(rows: List[dict]) -> Tuple[str, int, int]:
    """
    Create a monospace ASCII table and return:
      (table_string, table_width_chars, table_height_lines)
    The width returned equals the length of the top border line (so can be used to size other boxes).
    """
    cols = ["Repository", "Main Language", "Total Size (bytes)", "Total Commits", "Date of Last Commit"]
    data_rows = []
    for r in rows:
        data_rows.append([
            r["name_text"],
            r["language"],
            str(r["size"]),
            str(r["commits"]),
            r["last_commit"]
        ])
    widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    # add padding (1 space left/right)
    widths = [w + 2 for w in widths]
    # top border line
    top_line = "+" + "+".join(["-" * w for w in widths]) + "+"
    lines = [top_line]
    # header
    header_cells = []
    for i, c in enumerate(cols):
        s = " " + c.center(widths[i] - 2) + " "
        header_cells.append(s)
    lines.append("|" + "|".join(header_cells) + "|")
    lines.append(top_line)
    # data rows
    for row in data_rows:
        cells = []
        for i, cell in enumerate(row):
            s = " " + cell.center(widths[i] - 2) + " "
            cells.append(s)
        lines.append("|" + "|".join(cells) + "|")
        lines.append(top_line)
    table_str = "\n".join(lines)
    table_width = len(top_line)
    table_height = len(lines)
    return table_str, table_width, table_height


def generate_language_figure(lang_totals: Dict[str, int], total_width_chars: int, total_height_lines: int) -> str:
    """
    Create an ASCII box figure representing language proportions.
    total_width_chars should be the full-line width (including the two border chars),
    total_height_lines is used to determine inner height; we compute inner box of size (width-2) x (height-2).
    """
    total_bytes = sum(lang_totals.values()) or 1
    # inner dimensions inside box borders
    inner_w = max(10, total_width_chars - 2)
    inner_h = max(6, total_height_lines - 2)

    # Filter and sort languages by bytes desc
    langs = sorted([(k, v) for k, v in lang_totals.items() if v > 0], key=lambda x: x[1], reverse=True)
    if not langs:
        return "(no language data to display)\n"

    # raw proportional widths
    raw_widths = [v / total_bytes * inner_w for (_k, v) in langs]
    int_widths = [max(1, floor(x)) for x in raw_widths]

    # adjust rounding to match inner_w exactly
    while sum(int_widths) < inner_w:
        # add to column with largest fractional remainder
        fracs = [(i, raw_widths[i] - int_widths[i]) for i in range(len(int_widths))]
        fracs.sort(key=lambda x: x[1], reverse=True)
        int_widths[fracs[0][0]] += 1
    while sum(int_widths) > inner_w:
        fracs = [(i, raw_widths[i] - int_widths[i]) for i in range(len(int_widths))]
        fracs.sort(key=lambda x: x[1])
        for idx, _ in fracs:
            if int_widths[idx] > 1:
                int_widths[idx] -= 1
                break

    # Build per-language vertical blocks (each block is inner_h rows of width int_widths[i])
    columns: List[List[str]] = []
    for (lang, bytes_count), w in zip(langs, int_widths):
        pct = bytes_count / total_bytes * 100
        label = f"{lang} ({pct:.0f}%)"
        # build blank block
        block = [" " * w for _ in range(inner_h)]
        # place label centered on middle row, truncated if needed
        mid = inner_h // 2
        lab = label if len(label) <= w else label[:w]
        start = max(0, (w - len(lab)) // 2)
        row = list(block[mid])
        row[start:start + len(lab)] = lab
        block[mid] = "".join(row)
        columns.append(block)

    # assemble lines by horizontally concatenating columns
    top = "┌" + "─" * inner_w + "┐"
    bottom = "└" + "─" * inner_w + "┘"
    out_lines = [top]
    for row_idx in range(inner_h):
        row_pieces = [columns[c][row_idx] for c in range(len(columns))]
        out_lines.append("│" + "".join(row_pieces) + "│")
    out_lines.append(bottom)
    return "\n".join(out_lines)


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        repos = fetch_repos(USERNAME, token)
    except Exception as e:
        print(f"Failed to fetch repos for {USERNAME}: {e}", file=sys.stderr)
        sys.exit(1)

    if not repos:
        print(f"No repositories found for user {USERNAME}", file=sys.stderr)
        sys.exit(1)

    # Aggregate language bytes across all fetched repos
    all_lang_totals: Dict[str, int] = {}
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        langs = fetch_languages(owner, name, token)
        for k, v in langs.items():
            all_lang_totals[k] = all_lang_totals.get(k, 0) + (v or 0)

    # Prepare top-N rows (already sorted by updated desc because fetch_repos requested that)
    top_repos = repos[:TOP_N]
    rows = []
    for repo in top_repos:
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
        commits = get_commit_count(owner, name, token)
        last_commit = iso_to_date(repo.get("pushed_at"))
        rows.append({
            "name_md": f"[{name}]({html_url})",
            "name_text": name,
            "language": lang_label,
            "size": total_bytes,
            "commits": commits,
            "last_commit": last_commit
        })

    ascii_table, ascii_width, ascii_height = make_ascii_table(rows)
    md_table = build_markdown_table(rows)
    lang_figure = generate_language_figure(all_lang_totals, ascii_width, ascii_height)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    readme_content = f"""# Profile README — auto updated

_Last updated: {now}_

## 5 Most recently updated repositories

Below is an ASCII-style table **and** a Markdown table; the Markdown table is easier to click/read, the ASCII table is preserved for monospace display.

<details>
<summary>ASCII table (click to expand)</summary>

{ascii_table}
</details>

### Markdown table
{md_table}

---

## Language distribution across all repositories (ASCII figure)

{lang_figure}


---

*This README is updated automatically by a GitHub Action once a day.*
"""

    # Write README.md (overwrite)
    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme_content)

    print("README.md updated.")


if __name__ == "__main__":
    main()
