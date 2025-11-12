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
    Robustly build a sunburst and write PNG, SVG, and an interactive HTML fallback.
    Also write a debug file with file sizes so CI logs show whether files are present.
    """
    import os
    # Convert hierarchical JSON into a dataframe of labels/parents/values
    labels = []
    parents = []
    values = []
    # root
    root_name = data.get("name", "root")
    labels.append(root_name)
    parents.append("")
    values.append(0)

    for repo in data.get("children", []):
        repo_name = repo.get("name", "unknown")
        labels.append(repo_name)
        parents.append(root_name)
        # value = sum of children sizes
        s = 0
        for child in repo.get("children", []):
            s += child.get("size", 0)
        values.append(s if s > 0 else 1)
        for child in repo.get("children", []):
            labels.append(child.get("name", "lang"))
            parents.append(repo_name)
            # ensure every leaf has at least 1 so the renderer shows it
            values.append(max(1, int(child.get("size", 0))))

    df = pd.DataFrame({"labels": labels, "parents": parents, "values": values})

    # Create figure and make layout explicit (white background, margins)
    fig = px.sunburst(df, names="labels", parents="parents", values="values", branchvalues="total")
    fig.update_traces(maxdepth=3)
    fig.update_layout(
        margin=dict(t=20, l=20, r=20, b=20),
        paper_bgcolor="white",
        plot_bgcolor="white",
        uniformtext=dict(minsize=10, mode="hide")
    )

    # Force use of kaleido engine for static export
    png_path = Path(out_png)
    svg_path = png_path.with_suffix(".svg")
    html_path = png_path.with_suffix(".html")

    try:
        # write SVG first (often more reliable than PNG)
        fig.write_image(str(svg_path), width=1400, height=900, engine="kaleido")
        # then write PNG explicitly via kaleido
        fig.write_image(str(png_path), width=1400, height=900, scale=1, engine="kaleido")
        # create a standalone interactive HTML fallback (self-contained)
        fig.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)
    except Exception as e:
        print("WARN: plotly write_image/write_html raised exception:", e, file=sys.stderr)

    # Write debug info about generated files (size in bytes)
    debug_lines = []
    for p in [svg_path, png_path, html_path]:
        if p.exists():
            try:
                sz = p.stat().st_size
                debug_lines.append(f"{p.name}: {sz} bytes")
            except Exception as ee:
                debug_lines.append(f"{p.name}: exists but size-check failed: {ee}")
        else:
            debug_lines.append(f"{p.name}: NOT CREATED")

    dbg_path = OUT_DIR / "sunburst_debug.txt"
    dbg_path.write_text("\n".join(debug_lines))
    print("Wrote debug info:", dbg_path)
    for line in debug_lines:
        print(line)


def update_readme(png_path, pages_url=None):
    readme_path = Path("README.md")
    header = f"## My repositories — sunburst (data last updated automatically)\n\n"
    img_md = f"![Sunburst]({png_path})\n\n"
    pages_md = ""
    if pages_url:
        pages_md = f"Interactive version: [Open sunburst]({pages_url})\n\n"
    content = header + pages_md + img_md + "\nThis file is updated automatically by GitHub Actions.\n"
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
