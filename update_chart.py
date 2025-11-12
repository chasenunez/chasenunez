#!/usr/bin/env python3
"""
update_chart.py
Fetch GitHub repo languages for user chasenunez, produce docs/flare.json.
This script intentionally does not attempt static rendering (we use D3 + Puppeteer).
"""

import os
import sys
import time
import json
from pathlib import Path
import requests

GH_USER = os.environ.get("GH_USER", "chasenunez")
GH_TOKEN = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")

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
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            raise SystemExit(f"GitHub API error {resp.status_code}: {resp.text}")
        chunk = resp.json()
        if not chunk:
            break
        results.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return results

def get_user_repos(user):
    # Use /user/repos if token belongs to user and you want private repos included
    if GH_TOKEN:
        url = f"{API_BASE}/user/repos"
        repos = paginated_get(url)
        # Filter to repos owned by GH_USER
        repos = [r for r in repos if r.get("owner", {}).get("login") == user]
    else:
        url = f"{API_BASE}/users/{user}/repos"
        repos = paginated_get(url)
    return repos

def get_repo_languages(owner, repo):
    url = f"{API_BASE}/repos/{owner}/{repo}/languages"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"token {GH_TOKEN}"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        print(f"Language fetch failed for {owner}/{repo}: {r.status_code} - {r.text}", file=sys.stderr)
        return {}
    return r.json()

def build_hierarchy(repos):
    root = {"name": "root", "children": []}
    private_bucket = {}
    for r in repos:
        name = r["name"]
        is_private = r.get("private", False)
        owner = r["owner"]["login"]
        langs = get_repo_languages(owner, name)
        if is_private:
            for lang, b in (langs or {}).items():
                private_bucket[lang] = private_bucket.get(lang, 0) + b
        else:
            children = []
            if not langs:
                children.append({"name": "No code detected", "size": 1})
            else:
                for lang, b in langs.items():
                    children.append({"name": lang, "size": int(b)})
            root["children"].append({"name": name, "children": children})
    if private_bucket:
        children = [{"name": lang, "size": int(b)} for lang, b in private_bucket.items()]
        root["children"].append({"name": "Private", "children": children})
    # Optionally sort repos by total size descending
    root["children"].sort(key=lambda repo: sum(child.get("size",0) for child in repo.get("children",[])), reverse=True)
    return root

def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {path}")

def update_readme(png_name="docs/sunburst.png"):
    readme_path = Path("README.md")
    ts = int(time.time())
    img_md = f"![Sunburst]({png_name}?t={ts})\n\n"
    pages_url = f"https://{GH_USER}.github.io/{GH_USER}/"
    content = (
        "# chasenunez — profile\n\n"
        "## Repositories — sunburst (automatically updated)\n\n"
        f"Interactive: {pages_url}\n\n"
        f"{img_md}"
        "This file is updated automatically by GitHub Actions.\n"
    )
    readme_path.write_text(content)
    print(f"Updated {readme_path}")

def main():
    print("Fetching repos for user:", GH_USER)
    repos = get_user_repos(GH_USER)
    print(f"Found {len(repos)} repos (first 10): {[r['name'] for r in repos[:10]]}")
    hierarchy = build_hierarchy(repos)
    json_out = OUT_DIR / "flare.json"
    save_json(hierarchy, json_out)
    update_readme()
    print("Done.")

if __name__ == "__main__":
    main()
