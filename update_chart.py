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

INDEX_HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>GitHub Repos Sunburst — {user}</title>
<style>
  body { font-family: Inter, Arial, Helvetica, sans-serif; margin: 0; padding: 12px; background: transparent; color:#222; }
  #container { width: 1400px; max-width: 100%; margin: 0 auto; display: flex; gap: 18px; align-items: flex-start; }
  #left { flex: 2 1 0; min-width: 360px; }
  #right { flex: 1 1 0; min-width: 300px; }
  svg { width: 100%; height: 800px; display: block; }
  .tooltip { position: absolute; pointer-events: none; background: rgba(0,0,0,0.75); color: white; padding: 6px 8px; border-radius: 4px; font-size: 12px; }
  .stats { font-size: 13px; color: #444; margin-bottom: 8px; }
  .label-small { font-size: 12px; color: #666; margin-top: 6px; }
  .nodata { font-size: 16px; color: #666; padding: 40px; text-align: center; }
</style>
</head>
<body>
<h2>Repos &amp; languages — visual</h2>
<div id="container">
  <div id="left">
    <div class="stats" id="stats">Loading...</div>
    <div id="chart"></div>
    <div id="label" class="label-small">Click a wedge to zoom / click center to reset.</div>
  </div>
  <div id="right">
    <div id="barchart"></div>
    <div id="barlabel" class="label-small">Repository commit counts (top repos)</div>
  </div>
</div>

<!-- inlined data -->
<script id="flare-data" type="application/json">
{json_data}
</script>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function () {
  const raw = document.getElementById('flare-data').textContent;
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    console.error('Failed to parse inlined JSON', e);
    document.getElementById('stats').textContent = 'Failed to load data.';
    document.getElementById('label').textContent = 'Failed to load data.';
    return;
  }

  // Ensure data shapes
  const children = payload.children || [];
  let languages = payload.language_totals || [];
  const commits = payload.commits || [];

  // If no language_totals provided, compute from children
  if (!languages || languages.length === 0) {
    const agg = {};
    (children || []).forEach(repo => {
      (repo.children || []).forEach(lang => {
        agg[lang.name] = (agg[lang.name] || 0) + (lang.size || 0);
      });
    });
    languages = Object.entries(agg).map(([k,v]) => ({name:k, size:v}));
    languages.sort((a,b)=>b.size-a.size);
  }

  const totalLangBytes = languages.reduce((s,d)=>s+(d.size||0),0);
  const totalRepos = children.length;
  const totalCommits = commits.reduce((s,c)=>s+(c.commits||0),0);

  // Show stats top so user sees numbers even if chart empty
  document.getElementById('stats').textContent = `Repos: ${totalRepos} · Languages: ${languages.length} · Language bytes: ${totalLangBytes.toLocaleString()} · Commits total: ${totalCommits.toLocaleString()}`;

  // If there's no useful data at all, show message
  if ((!languages || languages.length===0) && (!commits || commits.length===0)) {
    document.getElementById('chart').innerHTML = '<div class="nodata">No language or commit data available.</div>';
    return;
  }

  // Group small languages into "Other" for readability
  const TOP_N = 12;
  let langs = languages.slice(0, TOP_N);
  if (languages.length > TOP_N) {
    const otherSum = languages.slice(TOP_N).reduce((s,d)=>s+(d.size||0),0);
    if (otherSum > 0) langs.push({name: 'Other', size: otherSum});
  }

  // LEFT: Sunburst — languages proportion
  const leftW = 900, leftH = 800, leftR = Math.min(leftW,leftH)/2;
  const leftSvg = d3.select('#chart').append('svg').attr('viewBox', [-leftW/2, -leftH/2, leftW, leftH]);

  const rootData = { name: 'root', children: langs.map(d=>({name:d.name, size:d.size})) };
  const root = d3.hierarchy(rootData).sum(d => d.size || 0);

  d3.partition().size([2*Math.PI, root.height + 1])(root);

  // create color scale (tableau + fallback)
  const baseColors = d3.schemeTableau10.slice();
  const color = d3.scaleOrdinal().domain(langs.map(d=>d.name)).range(baseColors.concat(d3.range(20).map(i => d3.interpolateRainbow(i/20))));

  const arc = d3.arc()
    .startAngle(d=>d.x0)
    .endAngle(d=>d.x1)
    .padAngle(d=>Math.min((d.x1-d.x0)/2, 0.01))
    .padRadius(leftR*1.5)
    .innerRadius(d=>d.y0*(leftR/(d.depth+1)))
    .outerRadius(d=>Math.max(d.y0*(leftR/(d.depth+1)), d.y1*(leftR/(d.depth+1))));

  const g = leftSvg.append('g');

  const nodes = root.descendants().slice(1);
  if (nodes.length === 0) {
    leftSvg.append('text').attr('text-anchor','middle').attr('y',0).text('No language data to display');
  } else {
    const slices = g.selectAll('path').data(nodes).join('path')
      .attr('d', d=>arc(d))
      .attr('fill', d => color(d.data.name))
      .attr('stroke', '#fff')
      .attr('stroke-width', 1)
      .style('cursor','pointer')
      .on('mouseover', (event,d) => {
        const t = document.getElementById('label');
        t.textContent = `${d.data.name} — ${d.value.toLocaleString()} bytes`;
      })
      .on('mouseout', () => {
        document.getElementById('label').textContent = 'Click a wedge to zoom / click center to reset.';
      })
      .on('click', clicked);

    slices.append('title').text(d => `${d.data.name}\n${d.value}`);

    // labels on top-level only
    const labelG = g.append('g').attr('pointer-events','none').attr('text-anchor','middle');
    labelG.selectAll('text').data(root.children || []).join('text')
      .attr('dy','0.35em')
      .attr('transform', d => {
        const x = (d.x0 + d.x1)/2 * 180 / Math.PI;
        const y = (d.y0 + d.y1)/2 * leftR/(d.depth+1);
        return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
      })
      .text(d => d.data.name)
      .style('font-size','12px');

    function clicked(p) {
      const rootd = p;
      root.each(d=>d.target = {
        x0: Math.max(0, Math.min(2*Math.PI, (d.x0 - rootd.x0) * (2*Math.PI)/(rootd.x1-rootd.x0))),
        x1: Math.max(0, Math.min(2*Math.PI, (d.x1 - rootd.x0) * (2*Math.PI)/(rootd.x1-rootd.x0))),
        y0: Math.max(0, d.y0 - rootd.depth),
        y1: Math.max(0, d.y1 - rootd.depth)
      });
      const t = g.transition().duration(750);
      slices.transition(t)
        .tween('data', d => {
          const i = d3.interpolate(d.current, d.target);
          return t => d.current = i(t);
        })
        .attrTween('d', d => () => arc(d.current));
    }
  }

  // RIGHT: commits bar chart (top 20)
  const rightW = 450, rightH = 800, margin = {top:20,right:10,bottom:20,left:140};
  const innerW = rightW - margin.left - margin.right, innerH = rightH - margin.top - margin.bottom;
  const rightSvg = d3.select('#barchart').append('svg').attr('viewBox', [0,0,rightW,rightH]);
  const barG = rightSvg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const commitsToShow = (commits || []).slice(0,20);
  if (!commitsToShow.length) {
    barG.append('text').attr('x', innerW/2).attr('y', innerH/2).attr('text-anchor','middle').text('No commit data');
  } else {
    const x = d3.scaleLinear().range([0,innerW]).domain([0, d3.max(commitsToShow, d=>d.commits)||1]);
    const y = d3.scaleBand().range([0,innerH]).padding(0.12).domain(commitsToShow.map(d=>d.name));
    barG.selectAll('rect').data(commitsToShow).join('rect')
      .attr('y', d=>y(d.name)).attr('height', y.bandwidth()).attr('x',0).attr('width', d=>x(d.commits)).attr('fill','#4C78A8');
    barG.selectAll('text.label').data(commitsToShow).join('text')
      .attr('class','label').attr('x', -8).attr('y', d=>y(d.name)+y.bandwidth()/2).attr('dy','0.35em').attr('text-anchor','end').text(d=>d.name).style('font-size','12px');
    const xAxis = d3.axisBottom(x).ticks(4).tickFormat(d3.format('~s'));
    barG.append('g').attr('transform', `translate(0,${innerH})`).call(xAxis);
  }

})();
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
    html_text = INDEX_HTML_TEMPLATE.replace("{json_data}", json_text).replace("{user}", GH_USER)
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
