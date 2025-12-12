// scripts/generate_visualizations.js
// Uses data/stats_cache.json and builds two responsive SVG files into visuals/

const fs = require('fs');
const path = require('path');
const d3 = require('d3');
const { JSDOM } = require('jsdom');
const { buildFigure1Data, buildFigure2Data } = require('./utils/transform');

const DATA_FILE = path.join(process.cwd(), 'data', 'stats_cache.json');
const OUT_DIR = path.join(process.cwd(), 'visuals');
if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

if (!fs.existsSync(DATA_FILE)) {
  console.error('Data file not found. Run npm run fetch first.');
  process.exit(1);
}
const cache = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));

// Build figure 1 data (traffic)
const { traffic: fig1Traffic, topRepos } = buildFigure1Data(cache, process.env.USERNAME || 'chasenunez', 365, 10);

// Convert dates to Date objects (if strings)
fig1Traffic.forEach(d => { d.date = new Date(d.date); });

// ---- Figure 1: Ridgeline / stacked density (top 10 repos) ----
function makeFigure1(traffic, outPath) {
  // prepare dates array (unique sorted)
  const dateKeys = Array.from(new Set(traffic.map(d => +d.date))).sort((a,b)=>a-b).map(v=>new Date(v));
  const groups = d3.groups(traffic, d => d.name)
    .map(([name, values]) => {
      const map = new Map(values.map(d => [+d.date, d.value]));
      return { name, values: dateKeys.map(dt => map.get(+dt) || 0) };
    });

  const overlap = 8;
  const width = 960;
  const height = groups.length * 17;
  const marginTop = 40, marginRight = 20, marginBottom = 30, marginLeft = 140;

  const x = d3.scaleTime()
    .domain(d3.extent(dateKeys))
    .range([marginLeft, width - marginRight]);

  const y = d3.scalePoint()
    .domain(groups.map(d => d.name))
    .range([marginTop, height - marginBottom]);

  const z = d3.scaleLinear()
    .domain([0, d3.max(groups, d => d3.max(d.values))]).nice()
    .range([0, -overlap * y.step()]);

  const area = d3.area()
    .curve(d3.curveBasis)
    .defined(d => d !== undefined)
    .x((d,i) => x(dateKeys[i]))
    .y0(0)
    .y1(d => z(d));

  const line = area.lineY1();

  const dom = new JSDOM(`<svg xmlns="http://www.w3.org/2000/svg"></svg>`);
  const svg = d3.select(dom.window.document.querySelector('svg'))
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('preserveAspectRatio', 'xMinYMin meet')
    .attr('style', 'max-width: 100%; height: auto; display:block;');

  // axes
  svg.append('g')
    .attr('transform', `translate(0,${height - marginBottom})`)
    .call(d3.axisBottom(x).ticks(width / 80).tickSizeOuter(0));

  svg.append('g')
    .attr('transform', `translate(${marginLeft},0)`)
    .call(d3.axisLeft(y).tickSize(0).tickPadding(4))
    .call(g => g.select('.domain').remove());

  const group = svg.append('g')
    .selectAll('g')
    .data(groups)
    .join('g')
      .attr('transform', d => `translate(0,${y(d.name) + 1})`);

  // brand colors: primary GitHub green + gradient darker stroke
  group.append('path')
    .attr('fill', '#5FED83')
    .attr('d', d => area(d.values));

  group.append('path')
    .attr('fill', 'none')
    .attr('stroke', '#08872B')
    .attr('d', d => line(d.values));

  // Title label
  svg.append('text')
    .attr('x', marginLeft)
    .attr('y', 20)
    .attr('font-size', 14)
    .attr('font-weight', '600')
    .text('Top 10 repos — commit density (last 365 days)');

  fs.writeFileSync(outPath, dom.serialize());
  console.log('Wrote', outPath);
}

// ---- Figure 2: weekly stacked area chart (languages) ----
function makeFigure2(weeklyData, outPath) {
  if (!weeklyData || weeklyData.length === 0) {
    console.warn('No weekly data for figure 2');
    // write an empty placeholder svg
    fs.writeFileSync(outPath, `<svg viewBox="0 0 600 100" style="max-width:100%;height:auto;"><text x="10" y="20">No data</text></svg>`);
    return;
  }

  // Determine language keys
  const keys = Object.keys(weeklyData[0]).filter(k => k !== 'date');

  const width = 960, height = 420;
  const margin = { top: 20, right: 20, bottom: 30, left: 60 };
  const x = d3.scaleUtc()
    .domain(d3.extent(weeklyData, d => d.date))
    .range([margin.left, width - margin.right]);

  const stack = d3.stack().keys(keys);
  const series = stack(weeklyData);

  const y = d3.scaleLinear()
    .domain([0, d3.max(series, s => d3.max(s, d => d[1]))]).nice()
    .range([height - margin.bottom, margin.top]);

  // color scale (use schemeTableau10 fallback; override 'restricted' to a dark green)
  const color = d3.scaleOrdinal()
    .domain(keys)
    .range(d3.schemeTableau10);

  const area = d3.area()
    .x(d => x(d.data.date))
    .y0(d => y(d[0]))
    .y1(d => y(d[1]));

  const dom = new JSDOM(`<svg xmlns="http://www.w3.org/2000/svg"></svg>`);
  const svg = d3.select(dom.window.document.querySelector('svg'))
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('preserveAspectRatio', 'xMinYMin meet')
    .attr('style', 'max-width: 100%; height: auto; display:block;');

  svg.append('g')
    .attr('transform', `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).ticks(height / 80))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('.tick line').clone()
      .attr('x2', width - margin.left - margin.right)
      .attr('stroke-opacity', 0.08));

  svg.append('g')
    .selectAll('path')
    .data(series)
    .join('path')
      .attr('fill', d => (d.key === 'restricted' ? '#104C35' : color(d.key)))
      .attr('d', area)
    .append('title')
      .text(d => d.key);

  svg.append('g')
    .attr('transform', `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x).tickSizeOuter(0));

  svg.append('text')
    .attr('x', margin.left)
    .attr('y', 16)
    .attr('font-size', 14)
    .attr('font-weight', '600')
    .text('Weekly language composition (last 52 weeks)');

  fs.writeFileSync(outPath, dom.serialize());
  console.log('Wrote', outPath);
}

// Prepare figure 1: map repo names to readable short name (we use repo full name or repo name)
makeFigure1(fig1Traffic, path.join(OUT_DIR, 'fig1_top_repos.svg'));

// Prepare figure 2 data
const fig2Weekly = buildFigure2Data(cache, process.env.USERNAME || 'chasenunez', 52);
makeFigure2(fig2Weekly, path.join(OUT_DIR, 'fig2_langs_weekly.svg'));
