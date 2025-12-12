// scripts/utils/transform.js
// No direct dependency on d3 here; only pure JS data transforms.

function buildFigure1Data(cache, username, daysBack = 365, topN = 10) {
  const cutoff = Date.now() - daysBack * 24 * 3600 * 1000;
  const repoTotals = [];
  for (const [repo, info] of Object.entries(cache)) {
    const weeks = (info.weeks || []).filter(w => (w.w * 1000) >= cutoff);
    const total = weeks.reduce((s, w) => s + (w.c || 0), 0);
    repoTotals.push({ repo, total, weeks });
  }
  repoTotals.sort((a, b) => b.total - a.total);
  const top = repoTotals.slice(0, topN).map(r => r.repo);

  const traffic = [];
  for (const r of repoTotals.filter(r => top.includes(r.repo))) {
    for (const w of r.weeks) {
      traffic.push({
        name: r.repo,
        date: new Date(w.w * 1000).toISOString(),
        value: w.c
      });
    }
  }
  return { traffic, topRepos: top };
}

function buildFigure2Data(cache, username, weeksBack = 52) {
  const weekMap = new Map();
  const weekMillis = 7 * 24 * 3600 * 1000;
  const cutoff = Date.now() - weeksBack * weekMillis;

  for (const [repo, info] of Object.entries(cache)) {
    let lang = 'Other';
    if (info.languages && Object.keys(info.languages).length > 0) {
      const kv = Object.entries(info.languages).sort((a, b) => b[1] - a[1]);
      lang = kv[0][0];
    }
    if (info.private) lang = 'restricted';

    for (const w of info.weeks || []) {
      const ts = w.w * 1000;
      if (ts < cutoff) continue;
      const weekStart = new Date(ts);
      const iso = weekStart.toISOString().slice(0,10);
      if (!weekMap.has(iso)) weekMap.set(iso, {});
      const bucket = weekMap.get(iso);
      bucket[lang] = (bucket[lang] || 0) + (w.c || 0);
    }
  }

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
