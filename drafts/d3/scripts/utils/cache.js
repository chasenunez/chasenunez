// scripts/utils/cache.js
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(process.cwd(), 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

function readJSON(filename, defaultValue = null) {
  const p = path.join(DATA_DIR, filename);
  if (!fs.existsSync(p)) return defaultValue;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    return defaultValue;
  }
}

function writeJSON(filename, obj) {
  const p = path.join(DATA_DIR, filename);
  fs.writeFileSync(p, JSON.stringify(obj, null, 2));
}

function detectChanges(prevTotals = {}, newTotals = {}) {
  // prevTotals/newTotals: { repoFullName: totalCommits }
  const keys = new Set([...Object.keys(prevTotals), ...Object.keys(newTotals)]);
  for (const k of keys) {
    if ((prevTotals[k] || 0) !== (newTotals[k] || 0)) return true;
  }
  return false;
}

module.exports = { readJSON, writeJSON, detectChanges };
