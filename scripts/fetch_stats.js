// scripts/fetch_stats.js
// Robust fetcher: prefer /stats/contributors, but FALLBACK to listing commits (author + since) to compute per-week counts.
// Produces data/stats_cache.json and data/last_totals.json in the same format expected by the rest of the pipeline.

const path = require('path');
const fs = require('fs');
const { ghFetch, ghFetchAll } = require('./utils/api');
const { readJSON, writeJSON, detectChanges } = require('./utils/cache');

const USERNAME = process.env.USERNAME || 'chasenunez';
const TOKEN = process.env.GH_PAT;
if (!TOKEN) {
  console.error('GH_PAT environment variable is required');
  process.exit(1);
}

const DATA_DIR = path.join(process.cwd(), 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const OUT_STATS = path.join('data', 'stats_cache.json');
const OUT_LAST = path.join('data', 'last_totals.json');

// Cache-first guard: skip API calls if cache is fresh
const CACHE_FILE = path.join(DATA_DIR, 'stats_cache.json');
const MAX_CACHE_AGE_MS = process.env.MAX_CACHE_AGE_MS ? Number(process.env.MAX_CACHE_AGE_MS) : 7 * 24 * 60 * 60 * 1000; // 7 days

try {
  if (fs.existsSync(CACHE_FILE)) {
    const stat = fs.statSync(CACHE_FILE);
    const ageMs = Date.now() - stat.mtimeMs;
    console.log(`Cache age: ${Math.round(ageMs/1000)}s (max allowed ${Math.round(MAX_CACHE_AGE_MS/1000)}s)`);
    if (ageMs < MAX_CACHE_AGE_MS) {
      console.log('Cache is fresh — skipping API calls.');
      process.exit(0);
    }
  }
} catch (e) {
  console.warn('Cache check failed; continuing to fetch:', e.message);
}

// Helper: get ISO of one year ago
function isoSinceDays(days=365) {
  const d = new Date(Date.now() - days * 24*3600*1000);
  return d.toISOString();
}

// Helper: convert commit timestamps to week-start unix seconds
function weekStartUnix(ts) {
  const d = new Date(ts);
  // normalize to UTC week start (Sun 00:00:00) - match GitHub stats which uses week start as UNIX timestamp of start of week (Sunday)
  const day = d.getUTCDay(); // 0 (Sun) - 6
  const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - (day * 86400000);
  return Math.floor(start / 1000);
}

// Fallback: count commits via commits API for an author since given date
async function countCommitsPerWeek(repoOwner, repoName, author, sinceIso) {
  const pathBase = `/repos/${repoOwner}/${repoName}/commits?author=${encodeURIComponent(author)}&since=${encodeURIComponent(sinceIso)}`;
  const commits = await ghFetchAll(pathBase, TOKEN, { timeout: 20000 });
  // commits are array of commit objects with commit.author.date (or author.date)
  const counts = new Map();
  for (const c of commits) {
    // prefer commit.author.date (commit metadata) fallback to author.date
    let dateStr = (c.commit && c.commit.author && c.commit.author.date) || (c.author && c.author.date) || null;
    if (!dateStr) continue;
    const ts = Date.parse(dateStr);
    const wk = weekStartUnix(ts);
    counts.set(wk, (counts.get(wk) || 0) + 1);
  }
  // Convert to array of {w, c} sorted by week
  const arr = Array.from(counts.entries()).map(([w,c]) => ({ w: Number(w), c: Number(c) }))
                 .sort((a,b) => a.w - b.w);
  return { total: arr.reduce((s,x)=>s+x.c,0), weeks: arr };
}

(async () => {
  try {
    console.log(`Starting fetch_stats for ${USERNAME} at ${new Date().toISOString()}`);

    // List accessible repos (owner, collaborator, org_member)
    const repoList = await ghFetchAll('/user/repos?affiliation=owner,collaborator,organization_member', TOKEN, { timeout: 20000 });
    console.log(`Found ${repoList.length} repos (fetched from /user/repos).`);

    const newCache = {};
    const newTotals = {};
    const sinceIso = isoSinceDays(365);

    let processed = 0;
    for (const repo of repoList) {
      // Skip archived repos to save time
      if (repo.archived) {
        console.log(`Skipping archived: ${repo.full_name}`);
        continue;
      }
      // Optional: skip forks if you don't want to count them
      // if (repo.fork) continue;

      // Safety: stop if process running too long? (let GH timeout the job if needed)
      processed++;
      const owner = repo.owner.login;
      const name = repo.name;
      const full = repo.full_name;
      console.log(`Processing (${processed}/${repoList.length}): ${full}`);

      // Fetch languages (short timeout)
      let langs = {};
      try {
        langs = await ghFetch(`/repos/${owner}/${name}/languages`, TOKEN, { timeout: 10000 });
      } catch (err) {
        console.warn(`Failed to fetch languages for ${full}: ${err.message}`);
        langs = {};
      }

      // First try: contributors stats endpoint (fast if available)
      try {
        const contribs = await ghFetch(`/repos/${owner}/${name}/stats/contributors`, TOKEN, { timeout: 25000, max202: 2 });
        if (Array.isArray(contribs)) {
          // find user entry
          const me = contribs.find(c => c.author && c.author.login === USERNAME);
          if (me && Array.isArray(me.weeks)) {
            // Normalise weeks to {w,c}
            const weeks = me.weeks.map(w => ({ w: Number(w.w), c: Number(w.c || 0) })).filter(Boolean);
            newCache[full] = { private: !!repo.private, totalCommits: me.total || 0, weeks, languages: langs || {} };
            newTotals[full] = me.total || 0;
            console.log(`Used /stats/contributors for ${full}: total=${newTotals[full]}, weeks=${weeks.length}`);
            continue; // next repo
          } else {
            console.log(`/stats returned but no contributor record for ${USERNAME} in ${full}`);
          }
        } else {
          console.log(`/stats returned non-array for ${full}`);
        }
      } catch (err) {
        // If we hit a GH_STATS_202 or other error, we'll fallback
        console.warn(`contributors stats not usable for ${full}: ${err.message}. Falling back to commits listing.`);
      }

      // Fallback: list commits authored by USERNAME since one year and count per-week
      try {
        const { total, weeks } = await countCommitsPerWeek(owner, name, USERNAME, sinceIso);
        if (total > 0) {
          newCache[full] = { private: !!repo.private, totalCommits: total, weeks: weeks, languages: langs || {} };
          newTotals[full] = total;
          console.log(`Fallback commits counted for ${full}: total=${total}, weeks=${weeks.length}`);
        } else {
          // no commits in the last year from this user — skip (but you can include zero entry if desired)
          console.log(`No commits by ${USERNAME} in last year for ${full}; skipping.`);
        }
      } catch (err) {
        console.warn(`Failed to list commits for ${full}: ${err.message}`);
        // skip repo on error
        continue;
      }
    }

    // Persist caches even if empty (so we know script ran)
    const prevTotals = readJSON(OUT_LAST, {});
    writeJSON(path.basename(OUT_STATS), newCache); // our cache utils write into data/
    writeJSON(path.basename(OUT_LAST), newTotals);

    if (!detectChanges(prevTotals, newTotals)) {
      console.log('No changes in totals since last run.');
    } else {
      console.log('Changes detected since last run; visuals should be regenerated.');
    }

    console.log(`fetch_stats complete. Processed ${processed} repos. Wrote ${OUT_STATS}.`);
  } catch (err) {
    console.error('fetch_stats fatal error:', err);
    process.exit(1);
  }
})();
