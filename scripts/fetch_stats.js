// scripts/fetch_stats.js
// Fetch all accessible repos, find user's contributor entry and languages, store cache.
// Usage: GH_PAT env required. USERNAME optional (defaults to chasenunez).

const { ghFetch, ghFetchAll } = require('./utils/api');
const { readJSON, writeJSON, detectChanges } = require('./utils/cache');
const fs = require('fs');
const path = require('path');

const USERNAME = process.env.USERNAME || 'chasenunez';
const TOKEN = process.env.GH_PAT;
if (!TOKEN) {
  console.error('GH_PAT environment variable is required');
  process.exit(1);
}

const OUT_STATS = 'stats_cache.json';
const OUT_LAST = 'last_totals.json';

(async () => {
  try {
    // 1) list repos user can access (owner + collaborator). Use /user/repos?affiliation=owner,collaborator,organization_member
    const repoList = await ghFetchAll('/user/repos?affiliation=owner,collaborator,organization_member', TOKEN);

    const newCache = {};
    const newTotals = {};

    for (const repo of repoList) {
      // Skip forks optionally; keep them if you want commit counts from forks
      if (repo.archived) continue; // skip archived for clarity
      const full = repo.full_name; // e.g. owner/repo
      try {
        // contributors stats (may return 202; api wrapper handles retry)
        const contributors = await ghFetch(`/repos/${repo.owner.login}/${repo.name}/stats/contributors`, TOKEN);
        // find user's contributor entry
        const me = Array.isArray(contributors) ? contributors.find(c => c.author && c.author.login === USERNAME) : null;
        // languages
        const langs = await ghFetch(`/repos/${repo.owner.login}/${repo.name}/languages`, TOKEN);

        if (me) {
          // keep weeks (array of {w, a, d, c}) where w: unix timestamp (seconds), c: commits
          newCache[full] = {
            private: !!repo.private,
            totalCommits: me.total || 0,
            weeks: me.weeks || [],
            languages: langs || {}
          };
          newTotals[full] = me.total || 0;
        } else {
          // Optionally include repos where user has no commits (skip to reduce noise)
          // If you want them, uncomment:
          // newCache[full] = { private: !!repo.private, totalCommits: 0, weeks: [], languages: langs || {} };
          // newTotals[full] = 0;
        }
      } catch (err) {
        console.warn(`Skipping ${full} due to error: ${err.message}`);
        continue;
      }
    }

    const prevTotals = readJSON(OUT_LAST, {});
    // Write caches
    writeJSON(OUT_STATS, newCache);
    writeJSON(OUT_LAST, newTotals);

    if (!detectChanges(prevTotals, newTotals)) {
      console.log('No changes in totals since last run.');
      // still write cache so that timestamps are fresh
    } else {
      console.log('Changes detected: will re-generate visuals.');
    }

    console.log('Wrote', OUT_STATS);
  } catch (err) {
    console.error('fetch_stats error:', err);
    process.exit(2);
  }
})();
