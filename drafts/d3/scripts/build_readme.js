// scripts/build_readme.js
// Robust README builder: uses README.template.md if present; otherwise creates a fallback template.
// Ensures visuals exist, inserts PNG (preferred) or SVG tags with responsive styling, writes README.md.
// If "sharp" is available it will convert SVG -> PNG automatically.

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
const FIG1_SVG = path.join(VIS_DIR, 'fig1_top_repos.svg');
const FIG2_SVG = path.join(VIS_DIR, 'fig2_langs_weekly.svg');
const FIG1_PNG = path.join(VIS_DIR, 'fig1_top_repos.png');
const FIG2_PNG = path.join(VIS_DIR, 'fig2_langs_weekly.png');

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
ensurePlaceholderSvg(FIG1_SVG);
ensurePlaceholderSvg(FIG2_SVG);

// Utility: try to convert SVG -> PNG using sharp if available.
// Returns true if PNG was created (or already exists), false otherwise.
async function ensurePngFromSvg(svgPath, pngPath, options = {}) {
  // If the PNG already exists and is newer than the SVG, keep it
  try {
    if (fs.existsSync(pngPath) && fs.existsSync(svgPath)) {
      const pStat = fs.statSync(pngPath);
      const sStat = fs.statSync(svgPath);
      if (pStat.mtimeMs >= sStat.mtimeMs) {
        // PNG up-to-date
        return true;
      }
    }
  } catch (e) {
    // ignore and try convert
  }

  // Try to require sharp
  let sharp;
  try {
    sharp = require('sharp');
  } catch (err) {
    console.warn('sharp not installed; skipping SVG->PNG conversion. To enable conversion, run: npm install --save sharp');
    return false;
  }

  // Read SVG buffer
  let svgBuffer;
  try {
    svgBuffer = fs.readFileSync(svgPath);
  } catch (err) {
    console.warn(`Could not read SVG at ${svgPath}: ${err.message}`);
    return false;
  }

  // Use the SVG's viewBox or fallback width/height if available to size PNG
  // We'll attempt to parse a numeric width from viewBox or width attr
  let width = options.width || 1200;
  let height = options.height || null;

  try {
    const svgText = svgBuffer.toString('utf8');
    // try to extract viewBox like: viewBox="0 0 960 420"
    const vbMatch = svgText.match(/viewBox=["']?([\d\.\-]+\s+[\d\.\-]+\s+[\d\.\-]+\s+[\d\.\-]+)["']?/i);
    if (vbMatch) {
      const parts = vbMatch[1].trim().split(/\s+/).map(Number);
      if (parts.length === 4 && !Number.isNaN(parts[2])) {
        width = Math.round(parts[2]);
        if (!height && !Number.isNaN(parts[3])) height = Math.round(parts[3]);
      }
    } else {
      // try width/height attributes
      const wMatch = svgText.match(/width=["']?([\d\.]+)(px)?["']?/i);
      const hMatch = svgText.match(/height=["']?([\d\.]+)(px)?["']?/i);
      if (wMatch) width = Math.round(Number(wMatch[1]));
      if (hMatch) height = Math.round(Number(hMatch[1]));
    }
  } catch (e) {
    // ignore parse errors; use defaults
  }

  // If height not set, set a proportional default (16:9 fallback)
  if (!height && width) {
    height = Math.round((width * 9) / 16);
  }

  try {
    // Convert using sharp. We let sharp choose a sensible density; pass SVG buffer directly.
    await sharp(svgBuffer)
      .resize(width, height, { fit: 'contain' })
      .png({ quality: 90 })
      .toFile(pngPath);
    console.log(`Converted ${path.basename(svgPath)} → ${path.basename(pngPath)} (${width}x${height})`);
    return true;
  } catch (err) {
    console.warn(`SVG->PNG conversion failed for ${svgPath}: ${err.message}`);
    return false;
  }
}

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

// Async main wrapper so we can await sharp conversions
(async () => {
  // Attempt to create PNGs from SVGs (if sharp is available)
  let fig1HasPng = false;
  let fig2HasPng = false;
  try {
    fig1HasPng = await ensurePngFromSvg(FIG1_SVG, FIG1_PNG);
  } catch (e) {
    console.warn('Error converting FIG1:', e.message);
    fig1HasPng = false;
  }
  try {
    fig2HasPng = await ensurePngFromSvg(FIG2_SVG, FIG2_PNG);
  } catch (e) {
    console.warn('Error converting FIG2:', e.message);
    fig2HasPng = false;
  }

  // Build markdown snippets for the two figures. Prefer PNG if available for GitHub README display.
  const relVisDir = path.relative(CWD, VIS_DIR) || 'visuals';
  const fig1File = fig1HasPng ? `${relVisDir}/fig1_top_repos.png` : `${relVisDir}/fig1_top_repos.svg`;
  const fig2File = fig2HasPng ? `${relVisDir}/fig2_langs_weekly.png` : `${relVisDir}/fig2_langs_weekly.svg`;

  // Use standard Markdown image syntax (works well on GitHub)
  const fig1Md = `\n\n![Top repos commits](${fig1File})\n\n`;
  const fig2Md = `\n\n![Languages weekly composition](${fig2File})\n\n`;

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
})();
