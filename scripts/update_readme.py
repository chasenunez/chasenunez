#!/usr/bin/env python3
"""
scripts/update_readme.py

Produces:
 - Markdown table of TOP_N most recently-updated PUBLIC repositories for USERNAME
 - A contributions-like grid (rows = repos from the table, plus "restricted" aggregated private repos),
   columns = last 52 weeks (oldest -> newest). Each cell shows commit density for that repo-week.

Authentication:
 - If GH_PAT is present in environment, it will be used (recommended; required to access private repos).
 - Otherwise GITHUB_TOKEN will be used (public-only).

Place this in your profile repo and run from GitHub Actions (pass GH_PAT secret into env).
Requires: pip install requests
"""

from __future__ import annotations
import os
import sys
import time
import math
import textwrap
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import requests

# ---------- Configuration ----------
USERNAME = "chasenunez"
TOP_N = 10
# shading characters from low -> high intensity
SHADES = [" ", "░", "▒", "▓", "█"]
# how many attempts to wait for stats endpoint to compute
STATS_MAX_RETRIES = 6
STATS_RETRY_SLEEP = 1.5  # seconds
# -----------------------------------

GITHUB_API = "https://api.github.com"
SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"update-readme-script ({USERNAME})",
    }
)


def auth_token() -> str | None:
    # Prefer GH_PAT (user PAT with repo scope) to include private repos; fallback to GITHUB_TOKEN
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def gh_get(url: str, params: dict | None = None, token: str | None = None) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    resp = SESSION.get(url, headers=headers, params=params or {})
    resp.raise_for_status()
    return resp


def fetch_repos_for_user(token: str | None = None, per_page: int = 100) -> List[dict]:
    """
    If token present and belongs to user, use /user/repos to include private repos.
    Otherwise fall back to /users/{USERNAME}/repos for public repos only.
    """
    if token:
        url = f"{GITHUB_API}/user/repos"
        params = {"per_page": per_page, "sort": "updated", "direction": "desc", "affiliation": "owner"}
    else:
        url = f"{GITHUB_API}/users/{USERNAME}/repos"
        params = {"per_page": per_page, "sort": "updated", "direction": "desc"}
    r = gh_get(url, params=params, token=token)
    return r.json()  # a list of repo dicts


def repo_commit_activity(owner: str, repo: str, token: str | None = None) -> List[int]:
    """
    Return list of 52 integers (weekly commit totals for last year) using:
      GET /repos/{owner}/{repo}/stats/commit_activity
    This endpoint returns weeks as dicts with 'week' (unix epoch start) and 'total'.
    It may return 202 if the data is being generated—retry in that case.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/stats/commit_activity"
    attempt = 0
    while attempt < STATS_MAX_RETRIES:
        try:
            r = gh_get(url, token=token)
        except requests.HTTPError as e:
            # If an error (e.g., 404) return 52 zeros
            code = getattr(e.response, "status_code", None)
            # If private and unauthorized, treat as zeros
            return [0] * 52
        if r.status_code == 202:
            # data being generated, wait and retry
            attempt += 1
            time.sleep(STATS_RETRY_SLEEP)
            continue
        data = r.json()
        # Data should be a list of 52 week objects; if not, normalize to 52 zeros
        if not isinstance(data, list) or len(data) == 0:
            return [0] * 52
        # Some repos return more/less; we will take the last 52 weeks (data is ordered oldest->newest)
        weeks = [int(w.get("total", 0)) for w in data]
        if len(weeks) >= 52:
            return weeks[-52:]
        # pad left if fewer than 52
        pad = [0] * (52 - len(weeks))
        return pad + weeks
    # if we exhausted retries, return zeros
    return [0] * 52


def build_markdown_table(rows: List[dict]) -> str:
    headers = [
        "**Repository**",
        "**Main Language (pct)**",
        "**Total Size (bytes)**",
        "**Total Commits**",
        "**Date of Last Commit**",
    ]
    md_lines = []
    md_lines.append("| " + " | ".join(headers) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        md_lines.append(
            f"| {r['name_md']} | {r['language']} | {r['size']} | {r['commits']} | {r['last_commit']} |"
        )
    return "\n".join(md_lines)


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


def get_commit_count(owner: str, repo: str, token: str | None = None) -> int:
    # small helper: estimate commits using commits endpoint (per_page=1 + Link header)
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
        import re

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


def format_repo_name_cell(name: str, max_width: int = 20) -> str:
    # truncate or pad repository name to fixed width for the left label column in the grid
    if len(name) > max_width - 1:
        return name[: max_width - 3] + "…"
    return name.ljust(max_width)


def build_contrib_grid(repo_weekly: Dict[str, List[int]], repo_order: List[str]) -> str:
    """
    repo_weekly: mapping repo_name -> list of 52 ints (weekly totals oldest->newest)
    repo_order: list of repo_names in desired output order
    Returns a multiline string where each line is:
      <repo-name-fixed-width> <52-char row of shade chars>
    """
    # left label width
    label_w = max(10, max((len(r) for r in repo_order), default=10))
    label_w = min(label_w, 28)  # cap to avoid extremely wide labels
    # we want each week's cell to be one char; 52 columns
    cols = 52

    lines = []
    for repo in repo_order:
        weeks = repo_weekly.get(repo, [0] * cols)
        # compute max for this repo to scale intensities per repo
        max_val = max(weeks) or 1
        row_chars = []
        for w in weeks:
            # compute intensity index 0..(len(SHADES)-1)
            # use ratio = w / max_val
            ratio = w / max_val if max_val > 0 else 0.0
            # map ratio to bucket
            idx = int(round(ratio * (len(SHADES) - 1)))
            idx = max(0, min(len(SHADES) - 1, idx))
            row_chars.append(SHADES[idx])
        label = format_repo_name_cell(repo, max_width=label_w)
        lines.append(f"{label} {' '.join(row_chars)}")  # spaces between cells improves readability
    # add a final legend line (no extra descriptive text per your request—only tiny legend row with shades)
    legend = " " * label_w + " " + " ".join(SHADES[1:]) + "  (low -> high)"
    lines.append(legend)
    # also add a small week axis (months approx) — compute week timestamps from oldest to newest using current date
    # We'll provide year ticks every ~4 weeks to avoid clutter
    now = datetime.now(timezone.utc)
    # compute the starting week's unix epoch for the first column by looking at today's week start and subtracting 51 weeks
    # Calculate Monday-based week starts (as GitHub commit_activity uses week starting on Sunday; but alignment inside README is approximate)
    # For display only approximate month ticks
    # Build axis with small labels every 4th column
    axis_cells = []
    for i in range(cols):
        if i % 4 == 0:
            # approximate month label at that column using date
            # compute days offset = (52 - 1 - i) * 7 to go back from now to that week's start
            days_back = (cols - 1 - i) * 7
            dt = now - timedelta(days=days_back)
            axis_cells.append(dt.strftime("%b")[0])  # 1-letter month initial to keep axis compact
        else:
            axis_cells.append(" ")
    axis_line = " " * label_w + " " + " ".join(axis_cells)
    # Put axis below grid
    result = "\n".join(lines) + "\n" + axis_line
    return result


# helper: need timedelta import
from datetime import timedelta


def main() -> None:
    token = auth_token()
    try:
        all_repos = fetch_repos_for_user(token=token, per_page=200)
    except Exception as e:
        print("Failed to fetch repositories:", e, file=sys.stderr)
        sys.exit(1)

    # Separate public and private repos (if token was supplied we got both)
    public_repos = [r for r in all_repos if not r.get("private")]
    private_repos = [r for r in all_repos if r.get("private")]

    # Build table rows from most recently updated public repos (sorted by updated at already)
    top_public = public_repos[:TOP_N]
    rows = []
    for repo in top_public:
        owner = repo["owner"]["login"]
        name = repo["name"]
        html_url = repo.get("html_url", f"https://github.com/{owner}/{name}")
        # languages
        langs = {}
        try:
            langs = gh_get(f"{GITHUB_API}/repos/{owner}/{name}/languages", token=token).json()
        except Exception:
            langs = {}
        total_bytes = sum(langs.values()) if langs else 0
        if langs and total_bytes > 0:
            sorted_langs = sorted(langs.items(), key=lambda x: x[1], reverse=True)
            top_lang, top_bytes = sorted_langs[0]
            pct = (top_bytes / total_bytes) * 100 if total_bytes else 0
            lang_label = f"{top_lang} ({pct:.0f}%)"
        else:
            lang_label = "Unknown (0%)"
        commits = get_commit_count(owner, name, token=token)
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

    # Build markdown table
    md_table = build_markdown_table(rows)

    # Build weekly commit data for each repo in rows
    repo_weekly: Dict[str, List[int]] = {}
    repo_order: List[str] = []

    # For each public repo in table, fetch commit_activity
    for r in rows:
        name = r["name_text"]
        owner = USERNAME
        repo_weekly[name] = repo_commit_activity(owner, name, token=token)
        repo_order.append(name)

    # Build "restricted" aggregated row from private repos if any and if token available
    restricted_name = "restricted"
    if private_repos and token:
        agg = [0] * 52
        for repo in private_repos:
            owner = repo["owner"]["login"]
            name = repo["name"]
            weekly = repo_commit_activity(owner, name, token=token)
            # sum into agg
            for i in range(52):
                agg[i] += weekly[i]
        repo_weekly[restricted_name] = agg
        # place restricted row at the top (optional), here we'll add it after the public repos
        repo_order.append(restricted_name)
    else:
        # If no token or no private repos, still include restricted as zeros so the grid has same rows if desired
        if token:
            repo_weekly[restricted_name] = [0] * 52
            repo_order.append(restricted_name)

    # Build grid string
    grid = build_contrib_grid(repo_weekly, repo_order)

    # Compose README content: only the Markdown table and the grid (grid in pre/code)
    readme = md_table + "\n\n" + "<pre><code class=\"language-text\">\n" + grid + "\n</code></pre>\n"

    with open("README.md", "w", encoding="utf-8") as fh:
        fh.write(readme)

    print("README.md updated.")


if __name__ == "__main__":
    main()
