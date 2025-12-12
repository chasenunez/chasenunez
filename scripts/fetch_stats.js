// scripts/fetch_stats.js
// Fetch all accessible repos, find user's contributor entry and languages, store cache.
// Adds global runtime guard and handles GH 202 responses gracefully.

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

// --- cache-first guard: do not call GitHub API if cache is fresh ---
const CACHE_FILE = path.join(process.cwd(), 'data', 'stats_cache.json');
const MAX_CACHE_AGE_MS = process.env.MAX_CACHE_AGE_MS ? Number(process.env.MAX_CACHE_AGE_MS) : 7 * 24 * 60 * 60 * 1000; // 7 days default

try {
  if (fs.existsSync(CACHE_FILE)) {
    const stat = fs.statSync(CACHE_FILE);
    const ageMs = Date.now() - stat.mtimeMs;
    console.log(`Cache file ${CACHE_FILE} exists (age ${Math.round(ageMs/1000)}s). Max allowed age ${Math.round(MAX_CACHE_AGE_MS/1000)}s.`);
    if (ageMs < MAX_CACHE_AGE_MS) {
      console.log('Cache is fresh — skipping API calls and exiting early.');
      process.exit(0); // success; skip API calls
    } else {
      console.log('Cache is stale — proceeding to fetch new stats from GitHub API.');
    }
  } else {
    console.log(`Cache file ${CACHE_FILE} not found — will fetch from GitHub API.`);
  }
} catch (e) {
  console.warn('Cache check failed:', e.message, '— proceeding to fetch from API.');
}

const OUT_STATS = 'stats_cache.json';
const OUT_LAST = 'last_totals.json';

// global run-time guard (milliseconds). If exceeded, script will stop cleanly.
const GLOBAL_TIMEOUT_MS = process.env.FETCH_GLOBAL_TIMEOUT_MS ? Number(process.env.FETCH_GLOBAL_TIMEOUT_MS) : 10 * 60 * 1000; // 10 minutes
const startTs = Date.now();

function elapsed() {
  return Date.now() - startTs;
}

(async () => {
  try {
    console.log(`fetch_stats starting for user=${USERNAME} at ${new Date().toISOString()}`);
    // 1) list repos user can access (owner + collaborator + org_member)
    const repoList = await ghFetchAll('/user/repos?affiliation=owner,collaborator,organization_member', TOKEN, { timeout: 20000 });
    console.log(`Discovered ${repoList.length} repos (note: some may be forks/archived and will be skipped).`);

    const newCache = {};
    const newTotals = {};
    let processed = 0;

    for (const repo of repoList) {
      // Check global timeout
      if (elapsed() > GLOBAL_TIMEOUT_MS) {
        console.warn(`fetch_stats: global timeout ${GLOBAL_TIMEOUT_MS}ms exceeded; stopping further repo processing.`);
        break;
      }

      // Skip archived repos to save time
      if (repo.archived) {
        console.log(`Skipping archived: ${repo.full_name}`);
        continue;
      }

      const full = repo.full_name; // e.g. owner/repo
      processed++;

      try {
        // Note: the stats endpoints may return 202 while computing; ghFetch enforces a max202 retry and will throw if not ready.
        const contributors = await ghFetch(`/repos/${repo.owner.login}/${repo.name}/stats/contributors`, TOKEN, { timeout: 30000, max202: 3 });
        // find user's contributor entry
        const me = Array.isArray(contributors) ? contributors.find(c => c.author && c.author.login === USERNAME) : null;
      // fetch languages with a short timeout
        const langs = await ghFetch(`/repos/${repo.owner.login}/${repo.name}/languages`, TOKEN, { timeout: 15000 });

        if (me) {
          newCache[full] = {
            private: !!repo.private,
            totalCommits: me.total || 0,
            weeks: me.weeks || [],
            languages: langs || {}
          };
          newTotals[full] = me.total || 0;
          console.log(`Processed ${full}: commits=${newTotals[full]} languages=${Object.keys(langs||{}).join(',') || 'none'}`);
        } else {
          // no contributions from this user for this repo -- skip
          // optional: collect zero-entry if you prefer
          console.log(`No contributions found for ${full}, skipping.`);
        }
      } catch (err) {
        // Handle specific GH 202-too-long case gracefully (code set in api.js)
        if (err && err.message && err.message.includes('GH_STATS_202')) {
          console.warn(`Stats not ready for ${full} (GitHub still computing). Skipping this repo this run.`);
        } else {
          console.warn(`Error processing ${full}: ${err.message}`);
        }
        // continue to next repo (do not throw)
        continue;
      }
    }

    // Persist caches
    const prevTotals = readJSON(OUT_LAST, {});
    writeJSON(OUT_STATS, newCache);
    writeJSON(OUT_LAST, newTotals);

    if (!detectChanges(prevTotals, newTotals)) {
      console.log('No changes in totals since last run.');
    } else {
      console.log('Changes detected since last run.');
    }

    console.log(`fetch_stats completed. Processed ${processed} repos in ${Math.round(elapsed()/1000)}s. Wrote ${OUT_STATS}.`);
  } catch (err) {
    console.error('fetch_stats error:', err);
    process.exit(2);
  }
})();
