#!/usr/bin/env python3
"""
update_readme.py

Render a compact GitHub "portfolio table" README. Every repo with a push in
the last ``ACTIVE_WINDOW_DAYS`` days is listed with:

* Main Language  — primary language (HTML falls back to the largest non-HTML
  language since HTML is almost always template/GH-Pages boilerplate).
* Total Bytes    — repo size reported by the listing endpoint.
* Total Commits  — counted cheaply via the ``Link: rel="last"`` header trick.
* Lifespan       — days between the first commit's author date and today.
* Team Size      — unique contributors to the default branch.

Data sources
------------
* REST ``/user/repos`` (or ``/users/:u/repos`` when unauthenticated) — full
  repo list; we filter it client-side by ``pushed_at``.
* REST ``/repos/:o/:r/commits`` — total count + oldest commit date. A single
  ``per_page=1`` request carries both pieces of information via the
  ``Link`` header; we then fetch just the last page (still ``per_page=1``)
  to read the oldest commit. Two round trips per repo.
* REST ``/repos/:o/:r/contributors`` — unique contributor count (same
  Link trick, one round trip).
* REST ``/repos/:o/:r/languages`` — only consulted when the primary language
  is HTML, to find a better runner-up.

Auth
----
``GH_PAT`` / ``GITHUB_TOKEN`` / ``GH_TOKEN`` from the environment. Without a
token the script falls back to public data for ``USERNAME``.

Output
------
``README.md`` — wrapped in ``<pre>`` so box-drawing + column alignment
survive GitHub's markdown renderer.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Callable, List, Optional, Tuple

import requests

try:
    import wcwidth as _wcwidth
except ImportError:  # pragma: no cover - fallback tested via wcswidth()
    _wcwidth = None


# ---------------------------------------------------------------------------
# Config — safe to tune
# ---------------------------------------------------------------------------
USERNAME = "chasenunez"
ACTIVE_WINDOW_DAYS = 90          # ~6 months of "recently active"
LINE_LENGTH = 112                 # target width of the rendered dashboard
README_OUT = "README.md"

# Network tuning.
GITHUB_REST = "https://api.github.com"
HTTP_TIMEOUT = 30
METADATA_WORKERS = 8              # concurrent repos processed at once


# ===========================================================================
# Display-width helpers
# ===========================================================================
def wcswidth(s: str) -> int:
    """Return the display width of ``s`` in monospace columns.

    Uses the ``wcwidth`` package when available (handles combining marks and
    wide East-Asian characters correctly) and falls back to a reasonable
    ``unicodedata``-based approximation otherwise.
    """
    if _wcwidth is not None:
        try:
            w = _wcwidth.wcswidth(s)
            if w is not None and w >= 0:
                return w
        except Exception:
            pass
    total = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def pad_to_width(s: str, target: int, align: str = "left") -> str:
    """Pad or truncate ``s`` to exactly ``target`` display columns."""
    cur = wcswidth(s)
    if cur == target:
        return s
    if cur < target:
        pad = target - cur
        if align == "right":
            return " " * pad + s
        if align == "center":
            left = pad // 2
            return " " * left + s + " " * (pad - left)
        return s + " " * pad

    # Truncate with a trailing ellipsis.
    if target <= 0:
        return ""
    if target == 1:
        return s[:1]
    acc, out = 0, ""
    for ch in s:
        w = max(1, wcswidth(ch))
        if acc + w > target - 1:
            break
        out += ch
        acc += w
    out += "…"
    acc += 1
    if acc < target:
        out += " " * (target - acc)
    return out


# ===========================================================================
# HTTP / auth
# ===========================================================================
def auth_token() -> Optional[str]:
    """Return the first populated token env var, or ``None``."""
    for name in ("GH_PAT", "GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(name)
        if v:
            return v
    return None


def make_session(token: Optional[str]) -> requests.Session:
    """Create a ``requests.Session`` with sensible defaults for the GH API."""
    s = requests.Session()
    s.headers.update({
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"update-readme ({USERNAME})",
    })
    if token:
        s.headers["Authorization"] = f"token {token}"
    return s


def gh_get(session: requests.Session, url: str,
           params: Optional[dict] = None) -> requests.Response:
    """GET wrapper that raises on any non-2xx response."""
    r = session.get(url, params=params or {}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def gh_paginated(session: requests.Session, url: str,
                 params: Optional[dict] = None) -> List[dict]:
    """Follow ``Link: rel="next"`` pagination and return a flat list."""
    items: List[dict] = []
    params = dict(params or {})
    params.setdefault("per_page", 100)
    next_url: Optional[str] = url
    cur_params: Optional[dict] = params
    while next_url:
        r = gh_get(session, next_url, params=cur_params)
        data = r.json()
        if not isinstance(data, list):
            break
        items.extend(data)
        m = _LINK_NEXT_RE.search(r.headers.get("Link", ""))
        next_url = m.group(1) if m else None
        cur_params = None  # the next URL already encodes its own params
    return items


# ---------------------------------------------------------------------------
# Link-header helpers — cheap "count" and "oldest item" via rel="last"
# ---------------------------------------------------------------------------
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_LINK_LAST_RE = re.compile(r'<([^>]+)>;\s*rel="last"')
_PAGE_PARAM_RE = re.compile(r'[?&]page=(\d+)')


def _link_last_url(resp: requests.Response) -> Optional[str]:
    m = _LINK_LAST_RE.search(resp.headers.get("Link", ""))
    return m.group(1) if m else None


def _link_last_page(resp: requests.Response) -> Optional[int]:
    """Extract the ``?page=N`` number from the response's ``rel=last`` URL."""
    url = _link_last_url(resp)
    if not url:
        return None
    m = _PAGE_PARAM_RE.search(url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def count_via_link(session: requests.Session, url: str) -> int:
    """Approximate item count using ``per_page=1`` + ``rel=last``.

    This is O(1) API calls regardless of how many items exist — perfect for
    "how many commits / contributors does this repo have?" queries.
    """
    try:
        r = gh_get(session, url, params={"per_page": 1})
    except Exception:
        return 0
    last_page = _link_last_page(r)
    if last_page is not None:
        return last_page
    # No rel=last means the response fits on a single page.
    try:
        data = r.json()
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


# ===========================================================================
# Data fetching
# ===========================================================================
def fetch_repos(session: requests.Session, token: Optional[str]) -> List[dict]:
    """Return the user's repos sorted by most recently pushed."""
    url = f"{GITHUB_REST}/user/repos" if token else f"{GITHUB_REST}/users/{USERNAME}/repos"
    repos = gh_paginated(session, url, params={"sort": "pushed", "direction": "desc"})
    repos.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    return repos


def _parse_github_ts(ts: str) -> Optional[datetime]:
    """Parse a GitHub ISO-8601 timestamp (``Z``-suffixed) to an aware datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_recently_active(repos: List[dict], *, now: datetime,
                           window_days: int) -> List[dict]:
    """Return repos whose ``pushed_at`` falls within ``window_days`` of ``now``."""
    cutoff = now.timestamp() - window_days * 86400
    out: List[dict] = []
    for r in repos:
        ts = _parse_github_ts(r.get("pushed_at") or r.get("updated_at") or "")
        if ts is not None and ts.timestamp() >= cutoff:
            out.append(r)
    return out


def fetch_non_html_primary(session: requests.Session, owner: str, repo: str
                           ) -> Optional[str]:
    """Return the largest non-HTML language, or ``None`` if none exists."""
    try:
        r = gh_get(session, f"{GITHUB_REST}/repos/{owner}/{repo}/languages")
        langs = r.json()
    except Exception:
        return None
    if not isinstance(langs, dict):
        return None
    for lang, _ in sorted(langs.items(), key=lambda kv: kv[1], reverse=True):
        if lang.strip().lower() != "html":
            return lang
    return None


def _commit_author_date(commit: dict) -> Optional[date]:
    """Extract the author date from a REST commit object as a ``date``."""
    try:
        iso = (((commit.get("commit") or {}).get("author") or {}).get("date") or "")
        if not iso:
            return None
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except Exception:
        return None


def fetch_commit_stats(session: requests.Session, owner: str, repo: str
                       ) -> Tuple[int, Optional[date]]:
    """Return ``(total_commits, first_commit_date)`` for ``owner/repo``.

    Strategy:
        1. GET ``/commits?per_page=1`` — the response body is the newest
           commit; the ``Link: rel="last"`` header's ``page=N`` equals the
           total commit count.
        2. GET the rel="last" URL — the body is exactly one commit (the
           oldest), because per_page=1 is preserved in the Link URL.

    Handles the documented 409 on empty repositories gracefully.
    """
    url = f"{GITHUB_REST}/repos/{owner}/{repo}/commits"
    try:
        r = gh_get(session, url, params={"per_page": 1})
    except requests.HTTPError as exc:
        # 409 Conflict = empty repo; any other error is treated as "unknown".
        return 0, None
    except Exception:
        return 0, None

    last_url = _link_last_url(r)
    if last_url is None:
        # No pagination → this one commit is the only (therefore oldest) commit.
        try:
            data = r.json()
        except Exception:
            return 0, None
        if not (isinstance(data, list) and data):
            return 0, None
        return len(data), _commit_author_date(data[0])

    total = _link_last_page(r) or 1
    try:
        r_last = gh_get(session, last_url)
        data = r_last.json()
    except Exception:
        return total, None
    if isinstance(data, list) and data:
        return total, _commit_author_date(data[0])
    return total, None


def fetch_team_size(session: requests.Session, owner: str, repo: str) -> int:
    """Count unique contributors via the Link ``rel=last`` trick."""
    return count_via_link(session, f"{GITHUB_REST}/repos/{owner}/{repo}/contributors")


# ===========================================================================
# Row building — one concurrent worker per repo
# ===========================================================================
def build_repo_rows(session: requests.Session, repos: List[dict],
                    *, today: date,
                    max_workers: int = METADATA_WORKERS) -> List[dict]:
    """Fetch per-repo metadata concurrently and return render-ready rows.

    Each returned dict has the keys consumed by :func:`render_repo_table`:
    ``name_text``, ``name_url``, ``language``, ``size``, ``commits``,
    ``lifespan_days``, ``team_size``, ``private``.

    Failed repos are silently dropped — the surrounding dashboard is more
    useful with partial data than with an error bubble.
    """
    if not repos:
        return []

    def one(r: dict) -> Optional[dict]:
        owner = (r.get("owner") or {}).get("login")
        name = r.get("name") or ""
        if not owner or not name:
            return None

        language = r.get("language") or ""
        if language.strip().lower() == "html":
            language = fetch_non_html_primary(session, owner, name) or language

        commits, first_date = fetch_commit_stats(session, owner, name)
        team_size = fetch_team_size(session, owner, name)
        lifespan = (today - first_date).days if first_date else None

        return {
            "owner": owner,
            "name_text": name,
            "name_url": r.get("html_url", ""),
            "language": language or "—",
            "size": int(r.get("size") or 0),
            "commits": commits,
            "lifespan_days": lifespan,
            "team_size": team_size,
            "private": bool(r.get("private")),
        }

    rows: List[Optional[dict]] = [None] * len(repos)
    workers = max(1, min(max_workers, len(repos)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(one, r): i for i, r in enumerate(repos)}
        for fut in as_completed(futures):
            try:
                rows[futures[fut]] = fut.result()
            except Exception:
                pass
    return [r for r in rows if r]


# ===========================================================================
# Rendering — repo metadata table
# ===========================================================================
TABLE_COLS: List[str] = [
    "Repository", "Main Language", "Total Bytes",
    "Total Commits", "Lifespan", "Team Size",
]
# Parallel list of formatter callables — one per display column. Each
# receives the raw row dict and returns the string shown in the cell.
TABLE_FORMATTERS: List[Callable[[dict], str]] = [
    lambda r: str(r.get("name_text") or ""),
    lambda r: str(r.get("language") or "—"),
    lambda r: f"{int(r.get('size') or 0):,}",
    lambda r: f"{int(r.get('commits') or 0):,}",
    lambda r: _fmt_lifespan(r.get("lifespan_days")),
    lambda r: f"{int(r.get('team_size') or 0):,}",
]


def _fmt_lifespan(days: Optional[int]) -> str:
    if days is None:
        return "—"
    if days < 1:
        return "<1 d"
    return f"{days:,} d"


def _distribute_widths(cells: List[List[str]], target_width: int) -> List[int]:
    """Compute per-column inner widths that make the table exactly target wide.

    Rules:
    * Start from the wider of (header length, widest cell in the column).
    * Grow: distribute slack round-robin across all columns.
    * Shrink: eat from the Repository column first, then from others (never
      below each column's header-length floor).
    """
    PAD = 2
    n = len(TABLE_COLS)
    inner = [len(c) for c in TABLE_COLS]
    for row in cells:
        for i, v in enumerate(row):
            inner[i] = max(inner[i], len(v))
    widths = [w + PAD for w in inner]
    total = sum(widths) + n + 1  # borders between and on edges

    if total < target_width:
        extra = target_width - total
        i = 0
        while extra > 0:
            widths[i % n] += 1
            extra -= 1
            i += 1
    elif total > target_width:
        excess = total - target_width
        floors = [len(c) + PAD for c in TABLE_COLS]
        # Shrink repo-name column first.
        take = min(excess, max(0, widths[0] - floors[0]))
        widths[0] -= take
        excess -= take
        # Then distribute remaining shrink across the other columns.
        i = 1
        while excess > 0 and any(widths[j] > floors[j] for j in range(1, n)):
            j = i % n
            if j != 0 and widths[j] > floors[j]:
                widths[j] -= 1
                excess -= 1
            i += 1
    return [w - PAD for w in widths]


def _clip(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` chars with a trailing ellipsis."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:1]
    return text[: width - 1] + "…"


def render_repo_table(rows: List[dict], target_width: int = LINE_LENGTH) -> str:
    """Render ``rows`` as a Unicode box-drawing table.

    The repository column carries an ``<a>`` tag when ``name_url`` is
    present; the HTML is emitted after padding so column alignment in the
    ``<pre>`` block is preserved (GitHub's renderer hides the tags
    themselves but keeps their text content).
    """
    if not rows:
        return ""

    cells: List[List[str]] = [
        [fmt(r) for fmt in TABLE_FORMATTERS] for r in rows
    ]
    inner = _distribute_widths(cells, target_width)

    # Box-drawing glyphs.
    H, V = "═", "║"
    TL, TR, BL, BR = "╔", "╗", "╚", "╝"
    T_SEP, M_SEP, B_SEP, L_SEP, R_SEP = "╦", "╬", "╩", "╠", "╣"

    def border(left: str, mid: str, right: str) -> str:
        parts = [left]
        for i, w in enumerate(inner):
            parts.append(H * (w + 2))  # +2 for the 1-col pad on each side
            parts.append(mid if i < len(inner) - 1 else right)
        return "".join(parts)

    top = border(TL, T_SEP, TR)
    middle = border(L_SEP, M_SEP, R_SEP)
    bottom = border(BL, B_SEP, BR)

    # Header.
    lines = [
        top,
        V + V.join(" " + c.center(inner[i]) + " " for i, c in enumerate(TABLE_COLS)) + V,
        middle,
    ]

    # Data rows.
    for row_cells, raw in zip(cells, rows):
        repo_clipped = _clip(row_cells[0], inner[0])
        url = raw.get("name_url", "")
        if url:
            anchor = f'<a href="{url}">{repo_clipped}</a>'
            repo_cell = " " + anchor + " " * (inner[0] - len(repo_clipped)) + " "
        else:
            repo_cell = " " + repo_clipped.ljust(inner[0]) + " "

        other_cells = [
            " " + _clip(row_cells[i], inner[i]).center(inner[i]) + " "
            for i in range(1, len(TABLE_COLS))
        ]
        lines.append(V + repo_cell + V + V.join(other_cells) + V)
        lines.append(middle)

    lines[-1] = bottom
    return "\n".join(lines)


# ===========================================================================
# README assembly
# ===========================================================================
def build_readme(sections: dict, *, now: Optional[datetime] = None,
                 active_window_days: int = ACTIVE_WINDOW_DAYS) -> str:
    """Wrap ``sections`` in the dashboard's header/footer chrome."""
    now = now or datetime.now(timezone.utc)
    months = max(1, active_window_days // 30)
    header_top = (
        "┏━┓╻ ╻┏┳┓┏┳┓┏━┓┏━┓╻ ╻   ┏━┓┏━╸   ┏━┓┏━╸┏━╸┏━╸┏┓╻╺┳╸   ┏━┓┏━╸╺┳╸╻╻ ╻╻╺┳╸╻ ╻\n"
        "┗━┓┃ ┃┃┃┃┃┃┃┣━┫┣┳┛┗┳┛   ┃ ┃┣╸    ┣┳┛┣╸ ┃  ┣╸ ┃┗┫ ┃    ┣━┫┃   ┃ ┃┃┏┛┃ ┃ ┗┳┛\n"
        "┗━┛┗━┛╹ ╹╹ ╹╹ ╹╹┗╸ ╹    ┗━┛╹     ╹┗╸┗━╸┗━╸┗━╸╹ ╹ ╹    ╹ ╹┗━╸ ╹ ╹┗┛ ╹ ╹  ╹ "
    )
    
    header_bot = f"⠉⠛⠿⢿⣿ Updated {now.strftime('%A %Y-%m-%d %H:%M UTC')} ⣿⡿⠿⠛⠉"
    rule = "▔" * LINE_LENGTH

    def centered(text: str) -> str:
        return pad_to_width(text, LINE_LENGTH, "center")

    subheader = f"Repositories Active in the Last {months} Months"
    parts = [
        "<pre>",
        *[centered(line) for line in header_top.splitlines()],
        "",
        centered(subheader),
        "",
        sections.get("table", ""),
        "",
        centered(header_bot),
        "</pre>",
        "",
    ]
    return "\n".join(parts)


# ===========================================================================
# CLI
# ===========================================================================
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a GitHub activity README.")
    p.add_argument("--output", default=README_OUT,
                   help="Path to write the README to.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="Print the rendered README to stdout instead of writing.")
    p.add_argument("--window-days", type=int, default=ACTIVE_WINDOW_DAYS,
                   help="Only list repos pushed within this many days.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    token = auth_token()
    print(
        f"Using auth token (length {len(token)})." if token
        else "No token — falling back to unauthenticated public data.",
        file=sys.stderr,
    )
    session = make_session(token)
    now = datetime.now(timezone.utc)

    try:
        all_repos = fetch_repos(session, token)
    except Exception as exc:
        print(f"Failed to fetch repositories: {exc}", file=sys.stderr)
        return 1

    active = filter_recently_active(all_repos, now=now, window_days=args.window_days)
    # print(
    #     f"Found {len(active)} / {len(all_repos)} repos active in the last "
    #     f"{args.window_days} days.",
                                                                
    #     file=sys.stderr,
    # )
    

    rows = build_repo_rows(session, active, today=now.date())
    public_rows = [r for r in rows if not r.get("private")]

    sections = {"table": render_repo_table(public_rows)}
    readme = build_readme(sections, now=now, active_window_days=args.window_days)

    if args.print_only:
        sys.stdout.write(readme)
        return 0
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(readme)
    print(f"Wrote {args.output}.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
