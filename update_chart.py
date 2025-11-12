#!/usr/bin/env python3
"""
update_chart.py
Fetch GitHub repo languages and commit counts for user chasenunez,
create docs/flare.json and docs/index.html (inline JSON).
"""

import os
import sys
import time
import json
import re
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
    if GH_TOKEN:
        url = f"{API_BASE}/user/repos"
        repos = paginated_get(url)
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

def estimate_commit_count(owner, repo):
    """
    Try to estimate commit count for a repo.
    Strategy:
      1) GET /repos/{owner}/{repo}/commits?per_page=1 and read Link header rel="last" page number -> number of commits
      2) fallback: GET /repos/{owner}/{repo}/stats/contributors and sum 'total' contributions (may return 202)
      3) final fallback: 0
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"token {GH_TOKEN}"
    commits_url = f"{API_BASE}/repos/{owner}/{repo}/commits"
    try:
        r = requests.get(commits_url, params={"per_page": 1}, headers=headers, timeout=30)
    except Exception as e:
        print("Commit request exception:", e, file=sys.stderr)
        return 0

    if r.status_code == 200:
        link = r.headers.get("Link", "")
        if link:
            # Look for last page number
            m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
            if m:
                try:
                    return int(m.group(1))
                except:
                    pass
        # no Link header: commits <= 1
        try:
            data = r.json()
            return len(data)
        except:
            return 0
    # If API returns 409 or others, try stats contributors endpoint
    stats_url = f"{API_BASE}/repos/{owner}/{repo}/stats/contributors"
    try:
        s = requests.get(stats_url, headers=headers, timeout=30)
        if s.status_code == 200:
            contribs = s.json()
            total = sum(c.get("total", 0) for c in contribs)
            return total
        else:
            # If 202 (processing) or other, we cannot get a reliable number
            return 0
    except Exception as e:
        print("Contributors stats exception:", e, file=sys.stderr)
        return 0

def build_hierarchy_and_metrics(repos):
    root = {"name": "root", "children": []}
    language_totals = {}
    commits_list = []
    private_bucket = {}

    for r in repos:
        name = r["name"]
        owner = r["owner"]["login"]
        is_private = r.get("private", False)
        langs = get_repo_languages(owner, name) or {}
        # accumulate language totals
        if is_private:
            for lang, b in langs.items():
                private_bucket[lang] = private_bucket.get(lang, 0) + b
        else:
            children = []
            if not langs:
                children.append({"name": "No code detected", "size": 1})
            else:
                for lang, b in langs.items():
                    children.append({"name": lang, "size": int(b)})
                    language_totals[lang] = language_totals.get(lang, 0) + int(b)
            root["children"].append({"name": name, "children": children})

        # estimate commits (do for both public and private if accessible)
        commits = estimate_commit_count(owner, name)
        commits_list.append({"name": name, "commits": int(commits)})

    # include private bucket as one repo
    if private_bucket:
        children = [{"name": lang, "size": int(b)} for lang, b in private_bucket.items()]
        for lang, b in private_bucket.items():
            language_totals[lang] = language_totals.get(lang, 0) + int(b)
        root["children"].append({"name": "Private", "children": children})

    # prepare sorted language totals
    lang_items = [{"name": k, "size": v} for k, v in language_totals.items()]
    lang_items.sort(key=lambda x: x["size"], reverse=True)

    # prepare commits sorted
    commits_list.sort(key=lambda x: x["commits"], reverse=True)

    return root, lang_items, commits_list

def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {path}")

INDEX_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>GitHub Repos Sunburst — {user}</title>
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 12px; background: transparent; color:#222; }}
  #container {{ width: 1400px; max-width: 100%; margin: 0 auto; display: flex; gap: 20px; align-items: flex-start; }}
  #left, #right {{ flex: 1 1 0; min-width: 320px; }}
  #left {{ flex: 2 1 0; }}
  svg {{ width: 100%; height: 800px; display: block; }}
  .tooltip {{ position: absolute; pointer-events: none; background: rgba(0,0,0,0.75); color: white; padding: 6px 8px; border-radius: 4px; font-size: 12px; }}
  .label-small {{ font-size: 12px; color: #444; }}
  button {{ margin: 8px; }}
</style>
</head>
<body>
<h2>Repos &amp; languages — interactive visual (left: languages; right: commits)</h2>
<div id="container">
  <div id="left">
    <div id="chart"></div>
    <div id="label" class="label-small">Click a wedge to zoom / click center to reset.</div>
  </div>
  <div id="right">
    <div id="barchart"></div>
    <div id="barlabel" class="label-small">Repository commit counts (top by commits)</div>
  </div>
</div>

<!-- inlined data -->
<script id="flare-data" type="application/json">
{json_data}
</script>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function () {{
  // parse inlined JSON
  const raw = document.getElementById('flare-data').textContent;
  let data;
  try {{
    data = JSON.parse(raw);
  }} catch (e) {{
    console.error('Failed to parse inlined JSON', e);
    document.getElementById('label').textContent = 'Failed to load data.';
    return;
  }}

  // prepare aggregated language data (if available) or compute from children
  let languages = data.language_totals || (function() {{
    const m = {{}};
    (data.children||[]).forEach(repo => {{
      (repo.children||[]).forEach(lang => {{
        m[lang.name] = (m[lang.name] || 0) + (lang.size || 0);
      }});
    }});
    return Object.entries(m).map(([k,v]) => ({{name:k, size:v}})).sort((a,b)=>b.size-a.size);
  }})();

  const commits = data.commits || (data.commits_list || []);

  // LEFT: sunburst (languages)
  const leftWidth = 900, leftHeight = 800, leftRadius = Math.min(leftWidth, leftHeight)/2;
  const leftSvg = d3.select("#chart").append("svg")
    .attr("viewBox", [-leftWidth/2, -leftHeight/2, leftWidth, leftHeight]);

  // build a tiny hierarchy: root -> languages
  const langRoot = d3.hierarchy({name:'root', children: languages})
    .sum(d => d.size || 0);

  const partition = d3.partition().size([2*Math.PI, langRoot.height + 1]);
  partition(langRoot);

  // color scale using d3 scheme
  const color = d3.scaleOrdinal(d3.schemeTableau10);

  const arc = d3.arc()
    .startAngle(d => d.x0)
    .endAngle(d => d.x1)
    .padAngle(d => Math.min((d.x1 - d.x0) / 2, 0.01))
    .padRadius(leftRadius * 1.5)
    .innerRadius(d => d.y0 * (leftRadius / (d.depth+1)))
    .outerRadius(d => Math.max(d.y0 * (leftRadius / (d.depth+1)), d.y1 * (leftRadius / (d.depth+1))));

  const g = leftSvg.append("g");

  const slices = g.selectAll("path")
    .data(langRoot.descendants().slice(1))
    .join("path")
      .attr("d", d => arc(d))
      .attr("fill", d => color(d.data.name))
      .attr("stroke", "#fff")
      .attr("stroke-width", 1)
      .style("cursor", "pointer");

  slices.append("title").text(d => `${d.data.name}\\n${d.value}`);

  // add labels only for top-level languages
  const labels = g.append("g")
    .attr("pointer-events", "none")
    .attr("text-anchor", "middle")
    .selectAll("text")
    .data(langRoot.children || [])
    .join("text")
      .attr("dy", "0.35em")
      .attr("transform", d => {{
        const x = (d.x0 + d.x1)/2 * 180 / Math.PI;
        const y = (d.y0 + d.y1)/2 * leftRadius / (d.depth+1);
        return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
      }})
      .text(d => d.data.name)
      .style("font-size", "12px");

  // RIGHT: bar chart of commits
  const rightWidth = 500, rightHeight = 800;
  const rightSvg = d3.select("#barchart").append("svg")
    .attr("viewBox", [0, 0, rightWidth, rightHeight]);

  const commitsToShow = (commits || []).slice(0, 20); // top 20
  const margin = {top: 20, right: 10, bottom: 20, left: 140};
  const innerW = rightWidth - margin.left - margin.right;
  const innerH = rightHeight - margin.top - margin.bottom;

  const x = d3.scaleLinear().range([0, innerW]);
  const y = d3.scaleBand().range([0, innerH]).padding(0.1);

  x.domain([0, d3.max(commitsToShow, d => d.commits || 0) || 1]);
  y.domain(commitsToShow.map(d => d.name));

  const barG = rightSvg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  barG.selectAll("rect")
    .data(commitsToShow)
    .join("rect")
      .attr("y", d => y(d.name))
      .attr("height", y.bandwidth())
      .attr("x", 0)
      .attr("width", d => x(d.commits || 0))
      .attr("fill", "#4C78A8");

  barG.selectAll("text.label")
    .data(commitsToShow)
    .join("text")
      .attr("class", "label")
      .attr("x", -8)
      .attr("y", d => y(d.name) + y.bandwidth()/2)
      .attr("dy", "0.35em")
      .attr("text-anchor", "end")
      .text(d => d.name)
      .style("font-size", "12px");

  // x axis (counts) at bottom
  const xAxis = d3.axisBottom(x).ticks(4).tickFormat(d3.format("~s"));
  barG.append("g")
    .attr("transform", `translate(0,${innerH})`)
    .call(xAxis);

  // Done
}})();
</script>
</body>
</html>
"""

def write_index_with_inline_json(hierarchy, lang_items, commits_list, path):
    # Build a payload that contains the data the page expects
    payload = {
        "children": hierarchy.get("children", []),
        "language_totals": lang_items,
        "commits": commits_list
    }
    json_text = json.dumps(payload, indent=2)
    html_text = INDEX_HTML_TEMPLATE.format(user=GH_USER, json_data=json_text)
    path.write_text(html_text, encoding="utf-8")
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
    hierarchy, lang_items, commits_list = build_hierarchy_and_metrics(repos)
    json_out = OUT_DIR / "flare.json"
    save_json({"children": hierarchy.get("children", []) , "language_totals": lang_items, "commits": commits_list}, json_out)
    index_out = OUT_DIR / "index.html"
    write_index_with_inline_json(hierarchy, lang_items, commits_list, index_out)
    update_readme()
    print("Done.")

if __name__ == "__main__":
    main()
