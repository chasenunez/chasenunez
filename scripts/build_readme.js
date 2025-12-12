// scripts/build_readme.js
// Robust README builder: uses README.template.md if present; otherwise creates a fallback template.
// Ensures visuals exist, inserts <img> tags with responsive styling, writes README.md.

const fs = require('fs');
const path = require('path');

const CWD = process.cwd();
const TEMPLATE = path.join(CWD, 'README.template.md');
const TEMPLATE_ALTERNATES = [
  path.join(CWD, 'README_template.md'),
  path.join(CWD, 'readme.template.md'),
  path.join(CWD, 'README.TEMPLATE.md'),
  path.join(CWD, 'readme.md.template')
];
const OUT = path.join(CWD, 'README.md');
const VIS_DIR = path.join(CWD, 'visuals');
const FIG1 = path.join(VIS_DIR, 'fig1_top_repos.svg');
const FIG2 = path.join(VIS_DIR, 'fig2_langs_weekly.svg');

// Ensure visuals dir exists
if (!fs.existsSync(VIS_DIR)) {
  fs.mkdirSync(VIS_DIR, { recursive: true });
}

// Helper to write a tiny placeholder svg if a real one is missing
function ensurePlaceholderSvg(p) {
  if (!fs.existsSync(p)) {
    const placeholder = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 120" style="max-width:100%;height:auto;display:block;">
  <rect width="100%" height="100%" fill="#f6f8fa" />
  <text x="50%" y="50%" alignment-baseline="middle" text-anchor="middle" fill="#666" font-family="sans-serif" font-size="14">
    Placeholder: ${path.basename(p)}
  </text>
</svg>
`.trim();
    fs.writeFileSync(p, placeholder, 'utf8');
    console.log(`Wrote placeholder SVG: ${p}`);
  }
}

// Ensure placeholder SVGs exist so README embed won't break
ensurePlaceholderSvg(FIG1);
ensurePlaceholderSvg(FIG2);

// Find a template path (preferred TEMPLATE first, then alternates)
let templatePath = null;
if (fs.existsSync(TEMPLATE)) {
  templatePath = TEMPLATE;
} else {
  for (const alt of TEMPLATE_ALTERNATES) {
    if (fs.existsSync(alt)) {
      templatePath = alt;
      break;
    }
  }
}

// If still missing, create a default template in repo root and use it
if (!templatePath) {
  const defaultTemplate = `# {{USERNAME}} — GitHub Activity

This README is automatically updated weekly with a summary of my recent GitHub activity (commits, repositories, and language composition).

## Top repositories (commit density)
<!-- FIGURE_1 -->

## Languages (weekly composition)
<!-- FIGURE_2 -->

---

_Last update: <!-- LAST_UPDATED -->_
`;

  try {
    fs.writeFileSync(TEMPLATE, defaultTemplate, 'utf8');
    templatePath = TEMPLATE;
    console.warn('README.template.md was missing. A default template has been created at README.template.md. Please customize if desired.');
  } catch (err) {
    console.error('Failed to write default README.template.md:', err);
    process.exit(1);
  }
}

// Read the template
let content;
try {
  content = fs.readFileSync(templatePath, 'utf8');
} catch (err) {
  console.error(`Failed to read template at ${templatePath}:`, err);
  process.exit(1);
}

// Build markdown snippets for the two figures. Use relative visuals paths for GitHub rendering.
const relVisDir = path.relative(CWD, VIS_DIR) || 'visuals';
const fig1Rel = `${relVisDir}/fig1_top_repos.svg`;
const fig2Rel = `${relVisDir}/fig2_langs_weekly.svg`;

const fig1Md = `\n\n<img src="${fig1Rel}" alt="Top repos commits" style="max-width:100%;height:auto;">\n\n`;
const fig2Md = `\n\n<img src="${fig2Rel}" alt="Languages weekly composition" style="max-width:100%;height:auto;">\n\n`;

// Replace placeholders (if present) or append to template
if (content.includes('<!-- FIGURE_1 -->')) {
  content = content.replace('<!-- FIGURE_1 -->', fig1Md);
} else {
  content += '\n\n## Top repositories (commit density)\n' + fig1Md;
}

if (content.includes('<!-- FIGURE_2 -->')) {
  content = content.replace('<!-- FIGURE_2 -->', fig2Md);
} else {
  content += '\n\n## Languages (weekly composition)\n' + fig2Md;
}

// LAST_UPDATED replacement: replace marker or add at bottom
const now = (new Date()).toISOString();
if (content.includes('<!-- LAST_UPDATED -->')) {
  content = content.replace('<!-- LAST_UPDATED -->', now);
} else {
  content += `\n\n_Last update: ${now}_\n`;
}

// Write README.md
try {
  fs.writeFileSync(OUT, content, 'utf8');
  console.log('Wrote', OUT);
} catch (err) {
  console.error('Failed to write README.md:', err);
  process.exit(1);
}
