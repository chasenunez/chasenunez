#!/usr/bin/env python3
"""
update_chart.py
Fetch GitHub repo languages for user chasenunez, produce docs/flare.json,
and write docs/index.html with the JSON inlined so the page loads without fetch.
"""

import os
import sys
import time
import json
from pathlib import Path
import requests
import html

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
    root["children"].sort(key=lambda repo: sum(child.get("size",0) for child in repo.get("children",[])), reverse=True)
    return root

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
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 20px; display:flex; flex-direction:column; align-items:center; background:white; color:#222; }}
  #chart {{ width: 100%; max-width: 1100px; height: 800px; }}
  svg {{ width: 100%; height: 100%; }}
  .center-label {{ font-size: 14px; text-anchor: middle; fill: #222; }}
  .tooltip {{ position: absolute; pointer-events: none; background: rgba(0,0,0,0.75); color: white; padding: 6px 8px; border-radius: 4px; font-size: 12px; }}
  button {{ margin: 8px; }}
</style>
</head>
<body>
<h2>Repos &amp; languages — interactive sunburst</h2>
<div>
  <button id="resetBtn">Reset</button>
</div>
<div id="chart"></div>
<div id="label" style="margin-top:8px">Click a wedge to zoom / click center to reset.</div>
<div id="tooltip" class="tooltip" style="display:none"></div>
<!-- inlined data -->
<script id="flare-data" type="application/json">
{json_data}
</script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function () {{
  const raw = document.getElementById('flare-data').textContent;
  let data;
  try {{
    data = JSON.parse(raw);
  }} catch (e) {{
    console.error('Failed to parse inlined JSON', e);
    document.getElementById('label').textContent = 'Failed to load data.';
    return;
  }}

  const width = 1100, height = 800, radius = Math.min(width, height) / 2;
  const chart = d3.select("#chart");
  const svg = chart.append("svg")
    .attr("viewBox", [-width/2, -height/2, width, height]);

  const g = svg.append("g");

  const partition = (data) => {{
    const root = d3.hierarchy(data)
      .sum(d => d.size || 0)
      .sort((a, b) => b.value - a.value);
    return d3.partition()
      .size([2 * Math.PI, root.height + 1])
      (root);
  }};

  const arc = d3.arc()
    .startAngle(d => d.x0)
    .endAngle(d => d.x1)
    .padAngle(d => Math.min((d.x1 - d.x0) / 2, 0.01))
    .padRadius(radius * 1.5)
    .innerRadius(d => d.y0 * (radius / (d.depth+1)))
    .outerRadius(d => Math.max(d.y0 * (radius / (d.depth+1)), d.y1 * (radius / (d.depth+1))));

  function colorFor(name) {{
    let h = 0;
    for (let i=0;i<name.length;i++) h = (h<<5)-h + name.charCodeAt(i);
    const hue = Math.abs(h) % 360;
    return `hsl(${{hue}} 60% 55%)`;
  }}

  const tooltip = d3.select("#tooltip");

  const root = partition(data);
  root.each(d => d.current = d);

  const slice = g.append("g")
    .selectAll("path")
    .data(root.descendants().slice(1))
    .join("path")
      .attr("fill", d => colorFor(d.data.name || d.data))
      .attr("fill-opacity", 1)
      .attr("d", d => arc(d.current))
      .style("cursor", "pointer")
      .on("click", clicked)
      .on("mouseover", (event,d) => {{
         tooltip.style("display", "block")
           .html(`<strong>${{d.data.name}}</strong><br/>${{d.value}} bytes`);
      }})
      .on("mousemove", (event) => {{
         tooltip.style("left", (event.pageX + 10) + "px").style("top", (event.pageY + 10) + "px");
      }})
      .on("mouseout", () => tooltip.style("display","none"));

  slice.append("title").text(d => `${{d.ancestors().map(d => d.data.name).reverse().join(" / ")}}\\n${{d.value}}`);

  const labelGroup = g.append("g")
    .attr("pointer-events", "none")
    .attr("text-anchor", "middle")
    .selectAll("text")
    .data(root.descendants().slice(1))
    .join("text")
      .attr("dy", "0.35em")
      .attr("transform", d => {{
        const x = (d.x0 + d.x1) / 2 * 180 / Math.PI;
        const y = (d.y0 + d.y1) / 2 * radius / (d.depth+1);
        return `rotate(${{x - 90}}) translate(${{y}},0) rotate(${{x < 180 ? 0 : 180}})`;
      }})
      .text(d => d.depth === 1 ? d.data.name : (d.depth === 2 ? d.data.name : ""))
      .style("font-size", "12px");

  function clicked(p) {{
    if (!p) return;
    const rootd = p;
    root.each(d => d.target = {{
      x0: Math.max(0, Math.min(2 * Math.PI, (d.x0 - rootd.x0) * (2*Math.PI) / (rootd.x1 - rootd.x0))),
      x1: Math.max(0, Math.min(2 * Math.PI, (d.x1 - rootd.x0) * (2*Math.PI) / (rootd.x1 - rootd.x0))),
      y0: Math.max(0, d.y0 - rootd.depth),
      y1: Math.max(0, d.y1 - rootd.depth)
    }});

    const t = g.transition().duration(750);

    slice.transition(t)
        .tween("data", d => {{
          const i = d3.interpolate(d.current, d.target);
          return t => d.current = i(t);
        }})
        .attrTween("d", d => () => arc(d.current));
  }}

  d3.select("#resetBtn").on("click", () => clicked(root));
  svg.on("click", (event) => {{
    if (event.target.tagName === 'svg' || event.target.tagName === 'DIV') clicked(root);
  }});
}})();
</script>
</body>
</html>
"""

def write_index_with_inline_json(hierarchy, path):
    # embed the JSON safely
    json_text = json.dumps(hierarchy, indent=2)
    html_text = INDEX_HTML_TEMPLATE.format(user=GH_USER, json_data=html.escape(json_text))
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
    hierarchy = build_hierarchy(repos)
    json_out = OUT_DIR / "flare.json"
    save_json(hierarchy, json_out)
    index_out = OUT_DIR / "index.html"
    write_index_with_inline_json(hierarchy, index_out)
    update_readme()
    print("Done.")

if __name__ == "__main__":
    main()
