// scripts/build_readme.js
const fs = require('fs');
const path = require('path');

const TEMPLATE = path.join(process.cwd(), 'README.template.md');
const OUT = path.join(process.cwd(), 'README.md');
const VIS_DIR = 'visuals';

if (!fs.existsSync(TEMPLATE)) {
  console.error('README.template.md missing.');
  process.exit(1);
}

let content = fs.readFileSync(TEMPLATE, 'utf8');

// Insert figure 1
const fig1Path = `${VIS_DIR}/fig1_top_repos.svg`;
const fig2Path = `${VIS_DIR}/fig2_langs_weekly.svg`;

const fig1Md = `\n\n<img src="${fig1Path}" alt="Top repos commits" style="max-width:100%;height:auto;">\n\n`;
const fig2Md = `\n\n<img src="${fig2Path}" alt="Languages weekly composition" style="max-width:100%;height:auto;">\n\n`;

// Replace markers
content = content.replace('<!-- FIGURE_1 -->', fig1Md);
content = content.replace('<!-- FIGURE_2 -->', fig2Md);

// Last updated
const now = (new Date()).toISOString();
content = content.replace('<!-- LAST_UPDATED -->', now);

// Write README
fs.writeFileSync(OUT, content);
console.log('Wrote', OUT);
