// scripts/utils/api.js
// Robust GitHub API helper with timeouts and bounded retries for 202 responses.

const fetch = require('node-fetch');

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * ghFetch - single request with abort timeout and 202-handling/backoff
 *
 * Options:
 *  - timeout: milliseconds before aborting the HTTP request (default: 30000)
 *  - max202: number of times to retry on 202 responses (default: 4)
 *  - maxNetworkRetries: number of network/timeout retries (default: 2)
 *  - qs: query string (e.g. '?per_page=100&page=1'), optional
 */
async function ghFetch(path, token, opts = {}) {
  const url = path.startsWith('http') ? path : `https://api.github.com${path}`;
  const timeout = opts.timeout || 30000;
  const max202 = (typeof opts.max202 === 'number') ? opts.max202 : 4;
  const maxNetworkRetries = (typeof opts.maxNetworkRetries === 'number') ? opts.maxNetworkRetries : 2;
  const headers = Object.assign({
    'Accept': 'application/vnd.github+json',
    'Authorization': `Bearer ${token}`,
    'X-GitHub-Api-Version': '2022-11-28'
  }, opts.headers || {});
  const qs = opts.qs || '';

  let attempt = 0;
  let networkAttempts = 0;

  while (true) {
    attempt++;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    let res;
    try {
      res = await fetch(url + qs, {
        method: opts.method || 'GET',
        headers,
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timer);
      // handle AbortError from timeout or network errors
      networkAttempts++;
      if (networkAttempts <= maxNetworkRetries) {
        const backoff = 500 * Math.pow(2, networkAttempts - 1);
        console.warn(`Network error or timeout fetching ${url} (attempt ${networkAttempts}). Retrying after ${backoff}ms: ${err.message}`);
        await sleep(backoff);
        continue;
      }
      // give up after retries
      throw new Error(`Request to ${url} failed after ${networkAttempts} network attempts: ${err.message}`);
    }
    clearTimeout(timer);

    // If stats endpoint is computing, GitHub returns 202
    if (res.status === 202) {
      if (attempt > max202) {
        // give up and return a special object so caller can decide
        const text = await res.text().catch(() => '');
        const msg = `GitHub returned 202 repeatedly for ${url} (attempt ${attempt}). Last response: ${text.slice(0,200)}`;
        const err = new Error(msg);
        err.code = 'GH_STATS_202';
        throw err;
      }
      // exponential-ish backoff: start small, grow
      const wait = Math.min(30000, 1500 * Math.pow(1.8, attempt - 1));
      console.log(`GitHub returned 202 for ${url}, retrying after ${Math.round(wait)}ms (attempt ${attempt}/${max202})`);
      await sleep(wait);
      continue;
    }

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      const err = new Error(`GitHub API error ${res.status} ${res.statusText} for ${url}: ${text.slice(0,300)}`);
      err.status = res.status;
      throw err;
    }

    // successful response
    try {
      return await res.json();
    } catch (err) {
      throw new Error(`Failed to parse JSON from ${url}: ${err.message}`);
    }
  }
}

/**
 * ghFetchAll - paginated fetch helper, returns concatenated array.
 * Stops if a page fetch fails; caller will receive an error and can decide.
 */
async function ghFetchAll(pathBase, token, opts = {}) {
  const results = [];
  let page = 1;
  while (true) {
    const qs = `${pathBase.includes('?') ? '&' : '?'}per_page=100&page=${page}`;
    try {
      const chunk = await ghFetch(pathBase, token, Object.assign({}, opts, { qs }));
      if (!Array.isArray(chunk) || chunk.length === 0) break;
      results.push(...chunk);
      if (chunk.length < 100) break;
      page++;
    } catch (err) {
      // Surface the error with context
      throw new Error(`Failed to fetch page ${page} for ${pathBase}: ${err.message}`);
    }
  }
  return results;
}

module.exports = { ghFetch, ghFetchAll };
