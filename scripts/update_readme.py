#!/usr/bin/env python3
"""
scripts/update_readme.py

- Uses the GitHub API to fetch repositories for `USERNAME`.
- Builds:
    * an ASCII table (monospace) of the TOP_N most recently-updated repos
    * a Markdown table of the same repos (clickable repo links)
    * a stacked horizontal ASCII bar that shows language proportions across all repos
- Writes README.md containing ONLY the ASCII table (in a <pre><code> block),
  the Markdown table, and the stacked-language bar (also in a <pre><code> block).

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
TOP_N = 10                # now uses 10 most-recently-updated repos
SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"update-readme-script ({USERNAME})",
    }
)


def gh_get(url: str, token: str | None = None, params: dict | None = None) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {})
    resp.raise_for_status()
    return resp


def fetch_repos(user: str, token: str | None = None, per_page: int = 100) -> List[dict]:
    url = f"{GITHUB_API}/users/{user}/repos"
    params = {"per_page": per_page, "sort": "updated", "direction": "desc"}
    r = gh_get(url, token, params)
    return r.json()


def fetch_languages(owner: str, repo: str, token: str | None = None) -> Dict[str, int]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/languages"
    try:
        r = gh_get(url, token)
        return r.json() or {}
    except requests.HTTPError:
        return {}


def get_commit_count(owner: str, repo: str, token: str | None = None) -> int:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    params = {"per_page": 1}
    try:
        r = gh_get(url, token, params)
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
    except ValueError:
        pass
    return 0


def iso_to_date(s: str | None) -> str:
    if not s:
        return ""
    try:
        normalized = s.rstrip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return s


def build_markdown_table(rows: List[dict]) -> str:
    headers = [
        "**Repository**",
        "**Main Language (pct)**",
        "**Total Size (bytes)**",
        "**Total Commits**",
        "**Date of Last Commit**",
    ]
    md = []
    md.append("| " + " | ".join(headers) + " |")
    md.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        md.append(
            f"| {r['name_md']} | {r['language']} | {r['size']} | {r['commits']} | {r['last_commit']} |"
        )
    return "\n".join(md)


def make_ascii_table(rows: List[dict]) -> Tuple[str, int, int]:
    cols = [
        "Repository",
        "Main Language",
        "Total Size (bytes)",
        "Total Commits",
        "Date of Last Commit",
    ]
    data_rows = []
    for r in rows:
        plain_name = r["name_text"].replace("\t", "    ")
        data_rows.append(
            [plain_name, r["language"].replace("\t", "    "), str(r["size"]), str(r["commits"]), r["last_commit"]]
        )

    widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    widths = [w + 2 for w in widths]  # 1 space padding each side
    top_line = "+" + "+".join(["-" * w for w in widths]) + "+"
    lines = [top_line]
    header_cells = []
    for i, c in enumerate(cols):
        s = " " + c.center(widths[i] - 2) + " "
        header_cells.append(s)
    lines.append("|" + "|".join(header_cells) + "|")
    lines.append(top_line)
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


def generate_stacked_bar(lang_totals: Dict[str, int], total_width_chars: int, inner_height: int | None = None,
                         top_k: int = 8) -> str:
    """
    Build a stacked horizontal bar occupying the same width as total_width_chars (including borders).
    - top_k: number of top languages to show; remaining languages are grouped into "Other".
    - inner_height: number of inner rows for the box; if None, choose a reasonable small height (5).
    """
    total_bytes = sum(lang_totals.values()) or 1

    # Prepare language list: top_k languages, others collapsed into "Other"
    sorted_langs = sorted([(k, v) for k, v in lang_totals.items() if v > 0], key=lambda x: x[1], reverse=True)
    if not sorted_langs:
        return "(no language data to display)\n"

    display = sorted_langs[:top_k]
    rest = sorted_langs[top_k:]
    if rest:
        other_bytes = sum(v for (_k, v) in rest)
        display.append(("Other", other_bytes))

    # Dimensions
    inner_w = max(20, total_width_chars - 2)  # inside width
    if inner_height is None:
        inner_h = 5
    else:
        inner_h = max(3, inner_height - 2)  # convert outer height to inner if an outer was given
    inner_h = max(3, inner_h)

    # compute widths (raw and integer), ensure sum == inner_w
    raw_widths = [v / total_bytes * inner_w for (_k, v) in display]
    int_widths = [max(1, floor(x)) for x in raw_widths]
    # fix rounding
    while sum(int_widths) < inner_w:
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

    # Build column blocks: for each language make inner_h rows of that width
    columns: List[List[str]] = []
    for (lang, bytes_count), w in zip(display, int_widths):
        pct = bytes_count / total_bytes * 100
        label = f"{lang} ({pct:.0f}%)"
        block = [" " * w for _ in range(inner_h)]
        # place label in middle row if it fits
        mid = inner_h // 2
        lab = label if len(label) <= w else label[:w]
        start = max(0, (w - len(lab)) // 2)
        row = list(block[mid])
        # use block char for background and then overlay text
        for i in range(w):
            row[i] = "█"
        # overlay label in center
        row[start:start + len(lab)] = list(lab)
        block[mid] = "".join(row)
        # fill other rows entirely with block char
        for r in range(inner_h):
            if r != mid:
                block[r] = "█" * w
        columns.append(block)

    # assemble full box
    top = "┌" + "─" * inner_w + "┐"
    bottom = "└" + "─" * inner_w + "┘"
    out_lines = [top]
    for r in range(inner_h):
        row_pieces = [columns[c][r] for c in range(len(columns))]
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

    # Prepare top-TOP_N repos (repos are already sorted by updated desc)
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
        rows.append(
            {
                "name_md": f"[{name}]({html_url})",
                "name_text": name,
                "language": lang_label,
                "size": total_bytes,
                "commits": commits,
                "last_commit": last_commit,
            }
        )

    ascii_table, ascii_width, ascii_height = make_ascii_table(rows)
    md_table = build_markdown_table(rows)

    # Use ascii_height as outer height for bar; pass ascii_height to compute inner height
    lang_bar = generate_stacked_bar(all_lang_totals, ascii_width, inner_height=ascii_height, top_k=8)

    # README contains only the ASCII table (pre/code), the Markdown table, and the stacked bar (pre/code)
    readme_content = (
        f"<pre><code class=\"language-text\">\n"
        f"{ascii_table}\n"
        f"</code></pre>\n\n"
        f"{md_table}\n\n"
        f"<pre><code class=\"language-text\">\n"
        f"{lang_bar}\n"
        f"</code></pre>\n"
    )

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme_content)

    print("README.md updated.")


if __name__ == "__main__":
    main()
