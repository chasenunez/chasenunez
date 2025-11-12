#!/usr/bin/env python3
"""
update_chart.py
Fetch GitHub repo languages for user chasenunez, produce docs/flare.json
and docs/sunburst.png (static fallback). Works in GitHub Actions with env:
  GH_USER (username) -- default 'chasenunez'
  GH_PAT  (personal access token) -- required if private repos should be seen
"""

import os, sys, time, json, math
from pathlib import Path
import requests

# Plotly for the static PNG
import plotly.express as px
import pandas as pd

GH_USER = os.environ.get("GH_USER", "chasenunez")
GH_TOKEN = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")

if not GH_TOKEN:
    print("Warning: no GH_PAT or GITHUB_TOKEN found in env. Public repos only will be visible.", file=sys.stderr)

API_BASE = "https://api.github.com"

OUT_DIR = Path("docs")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def paginated_get(url, params=None, headers=None):
    params = params or {}
    headers = headers or {}
    headers["Accept"] = "application/vnd.github.v3+json"
    if GH_TOKEN:
        headers["Authorization"] = f"token {GH_TOKEN}"
    results = []
    page = 1
    while True:
        params.update({"per_page": 100, "page": page})
        resp = requests.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            raise SystemExit(f"GitHub API error {resp.status_code}: {resp.text}")
        chunk = resp.json()
        if not chunk:
            break
        results.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
        time.sleep(0.1)
    return results

def get_user_repos(user):
    url = f"{API_BASE}/users/{user}/repos"
    # if token present and belongs to the same user, /user/repos could be used to include private.
    repos = paginated_get(url)
    # sometimes private repos for the authenticated user are only available via /user/repos; we continue with this.
    return repos

def get_repo_languages(owner, repo):
    url = f"{API_BASE}/repos/{owner}/{repo}/languages"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"token {GH_TOKEN}"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(f"Language fetch failed for {owner}/{repo}: {r.status_code}", file=sys.stderr)
        return {}
    return r.json()  # {language: bytes, ...}

def build_hierarchy(repos):
    root = {"name": "root", "children": []}
    private_bucket = {}
    for r in repos:
        name = r["name"]
        is_private = r.get("private", False)
        owner = r["owner"]["login"]
        langs = get_repo_languages(owner, name)
        total_bytes = sum(langs.values()) if langs else 0
        if is_private:
            # accumulate
            for lang, b in (langs or {}).items():
                private_bucket[lang] = private_bucket.get(lang, 0) + b
        else:
            repo_node = {"name": name, "children": []}
            if not langs:
                # placeholder small slice so repo is visible
                repo_node["children"].append({"name": "No code detected", "size": 1})
            else:
                for lang, b in langs.items():
                    repo_node["children"].append({"name": lang, "size": int(b)})
            root["children"].append(repo_node)

    # Add private aggregated node if present
    if private_bucket:
        repo_node = {"name": "Private", "children": []}
        for lang, b in private_bucket.items():
            repo_node["children"].append({"name": lang, "size": int(b)})
        root["children"].append(repo_node)

    return root

def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {path}")

def build_plotly_sunburst(data, out_png):
    """
    Build a sunburst using plotly.graph_objects with explicit ids to avoid
    duplicate-label ambiguity. Writes PNG, SVG, and HTML fallbacks, and prints diagnostics.
    """
    import os
    import plotly.graph_objects as go

    # Helper: flatten hierarchy into lists of ids/labels/parents/values
    labels = []
    parents = []
    ids = []
    values = []

    def safe_id(prefix, name):
        # create a deterministic unique id (replace spaces/slashes)
        base = f"{prefix}::{name}"
        return base.replace(" ", "_").replace("/", "_").replace("\\", "_")

    root_name = data.get("name", "root")
    root_id = safe_id("node", root_name)
    labels.append(root_name)
    parents.append("")    # root has no parent
    ids.append(root_id)
    values.append(0)      # root value unused when branchvalues='total'

    # iterate repos (children of root)
    for repo in data.get("children", []):
        repo_name = repo.get("name", "repo")
        repo_id = safe_id("repo", repo_name)
        labels.append(repo_name)
        parents.append(root_id)
        ids.append(repo_id)
        # compute repo total as sum of its children
        repo_total = 0
        for child in repo.get("children", []):
            repo_total += int(child.get("size", 0) or 0)
        # ensure repo has at least 1 so it's visible
        values.append(max(1, repo_total))

        # languages (children)
        for child in repo.get("children", []):
            lang_name = child.get("name", "lang")
            lang_id = safe_id(repo_name, lang_name)
            labels.append(lang_name)
            parents.append(repo_id)
            ids.append(lang_id)
            # ensure every leaf has at least 1 byte so it's visible to renderer
            values.append(max(1, int(child.get("size", 0) or 0)))

    # Basic diagnostics
    print("Sunburst arrays length:", len(labels), "labels,", len(ids), "ids,", len(values), "values")
    # show first few entries
    for i in range(min(8, len(labels))):
        print(f"  {i}: id={ids[i]} label={labels[i]} parent={parents[i]} val={values[i]}")

    # Build the graph_objects Sunburst explicitly
    fig = go.Figure(
        go.Sunburst(
            ids=ids,
            labels=labels,
            parents=parents,
            values=values,
            branchvalues="total",
            maxdepth=3,
            hovertemplate="<b>%{label}</b><br>value=%{value}<extra></extra>"
        )
    )

    # layout
    fig.update_layout(
        margin=dict(t=10, l=10, r=10, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white"
    )

    # write outputs (SVG, PNG, HTML)
    png_path = Path(out_png)
    svg_path = png_path.with_suffix(".svg")
    html_path = png_path.with_suffix(".html")

    try:
        # SVG first (vector)
        fig.write_image(str(svg_path), width=1400, height=900)  # uses kaleido
        print("Wrote SVG:", svg_path, "size:", svg_path.stat().st_size if svg_path.exists() else "MISSING")
    except Exception as e:
        print("SVG write exception:", e, file=sys.stderr)

    try:
        # PNG
        fig.write_image(str(png_path), width=1400, height=900)
        print("Wrote PNG:", png_path, "size:", png_path.stat().st_size if png_path.exists() else "MISSING")
    except Exception as e:
        print("PNG write exception:", e, file=sys.stderr)

    try:
        # HTML fallback (self-contained interactive)
        fig.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)
        print("Wrote HTML:", html_path, "size:", html_path.stat().st_size if html_path.exists() else "MISSING")
    except Exception as e:
        print("HTML write exception:", e, file=sys.stderr)

    # Always write a small debug file summarising sizes
    debug_lines = []
    for p in [svg_path, png_path, html_path]:
        if p.exists():
            debug_lines.append(f"{p.name}: {p.stat().st_size} bytes")
        else:
            debug_lines.append(f"{p.name}: NOT CREATED")
    dbg_path = OUT_DIR / "sunburst_debug.txt"
    dbg_path.write_text("\n".join(debug_lines))
    print("Debug file:", dbg_path)
    for l in debug_lines:
        print(l)


def update_readme(png_path, pages_url=None):
    readme_path = Path("README.md")
    header = f"HEADER"
    img_md = f"![Sunburst](docs/sunburst_screenshot.png)\n\n"
    pages_md = ""
    if pages_url:
        pages_md = f"ARABIS"
    content = header + pages_md + img_md + "ALPINA"
    readme_path.write_text(content)
    print(f"Updated {readme_path}")

def main():
    print("Fetching repos for user:", GH_USER)
    repos = get_user_repos(GH_USER)
    # If the token belongs to the authenticated user and you want to include private repos,
    # you can call /user/repos instead. The workflow uses a token with repo scope so private
    # owned repos will already be present if the token belongs to the same user.
    print(f"Found {len(repos)} repos (first 10 shown): {[r['name'] for r in repos[:10]]}")
    hierarchy = build_hierarchy(repos)
    # Save JSON for the interactive D3 page
    json_out = OUT_DIR / "flare.json"
    save_json(hierarchy, json_out)
    # Create static PNG fallback
    png_out = OUT_DIR / "sunburst.png"
    try:
        build_plotly_sunburst(hierarchy, png_out)
    except Exception as e:
        print("Failed to build PNG via Plotly:", e, file=sys.stderr)

    # Optional: link to GitHub Pages location (you must enable Pages in settings to serve /docs)
    repo = os.environ.get("GITHUB_REPOSITORY") or f"{GH_USER}/{GH_USER}"
    pages_url = f"https://{GH_USER}.github.io/{GH_USER}/"  # default if repo is named username/username
    update_readme(png_out.as_posix(), pages_url)

if __name__ == "__main__":
    main()
