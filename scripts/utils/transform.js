// scripts/utils/transform.js
// Accepts the cache format produced by fetch_stats.js and yields two datasets:
//  - for figure 1: array of { name, date (ISO string), value }
//  - for figure 2: array of { date: Date, <language>: count, ... } (weekly)
// Also returns topN repo names.
const d3 = require('d3'); // useful for time manipulations if needed

/**
 * buildFigure1Data(cache, username, daysBack)
 * - cache: object keyed by repoFullName -> { private, totalCommits, weeks: [{w, c}], ... }
 * - returns: { traffic: [{name, date, value}, ...], topRepos: [names...] }
 */
function buildFigure1Data(cache, username, daysBack = 365, topN = 10) {
  const cutoff = Date.now() - daysBack * 24 * 3600 * 1000;
  // compute totals per repo across last `daysBack`
  const repoTotals = [];
  for (const [repo, info] of Object.entries(cache)) {
    const weeks = (info.weeks || []).filter(w => (w.w * 1000) >= cutoff);
    const total = weeks.reduce((s, w) => s + (w.c || 0), 0);
    repoTotals.push({ repo, total, weeks });
  }
  repoTotals.sort((a, b) => b.total - a.total);
  const top = repoTotals.slice(0, topN).map(r => r.repo);

  const traffic = [];
  // produce entries: one entry per week per repo (date = week start)
  for (const r of repoTotals.filter(r => top.includes(r.repo))) {
    for (const w of r.weeks) {
      traffic.push({
        name: r.repo,
        date: new Date(w.w * 1000).toISOString(), // keep ISO for later parsing
        value: w.c
      });
    }
  }
  return { traffic, topRepos: top };
}

/**
 * buildFigure2Data(cache, username, weeksBack=52)
 * - aggregates commits per-week by language (using repo primary language)
 * - for private repos we map to "restricted"
 * - returns an array sorted by date: [{ date: Date, 'Python': 12, 'JavaScript': 5, 'restricted': 3, ... }]
 */
function buildFigure2Data(cache, username, weeksBack = 52) {
  // Build week buckets keyed by ISO date 'YYYY-MM-DD' of the week start
  const weekMap = new Map();
  const weekMillis = 7 * 24 * 3600 * 1000;
  const cutoff = Date.now() - weeksBack * weekMillis;

  for (const [repo, info] of Object.entries(cache)) {
    // Determine primary language for repo: largest byte count in info.languages
    let lang = 'Other';
    if (info.languages && Object.keys(info.languages).length > 0) {
      const kv = Object.entries(info.languages).sort((a, b) => b[1] - a[1]);
      lang = kv[0][0];
    }
    if (info.private) lang = 'restricted';

    for (const w of info.weeks || []) {
      const ts = w.w * 1000;
      if (ts < cutoff) continue;
      // week start ISO date
      const weekStart = new Date(ts);
      const iso = weekStart.toISOString().slice(0,10);
      if (!weekMap.has(iso)) weekMap.set(iso, {});
      const bucket = weekMap.get(iso);
      bucket[lang] = (bucket[lang] || 0) + (w.c || 0);
    }
  }

  // Convert to sorted array and ensure each object has all language keys (fill 0)
  const sortedDates = Array.from(weekMap.keys()).sort();
  const languages = Array.from(new Set(
    Array.from(weekMap.values()).flatMap(o => Object.keys(o))
  ));
  const data = sortedDates.map(date => {
    const base = { date: new Date(date) };
    const bucket = weekMap.get(date);
    for (const lang of languages) {
      base[lang] = bucket[lang] || 0;
    }
    return base;
  });

  return data;
}

module.exports = { buildFigure1Data, buildFigure2Data };
