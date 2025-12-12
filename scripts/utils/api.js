// scripts/utils/api.js
const fetch = require('node-fetch');

const DEFAULT_HEADERS = (token) => ({
  'Accept': 'application/vnd.github+json',
  'Authorization': `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28'
});

// Simple GET wrapper with pagination and retries for 202 compute responses
async function ghFetch(path, token, opts = {}) {
  const url = path.startsWith('http') ? path : `https://api.github.com${path}`;
  const headers = Object.assign({}, DEFAULT_HEADERS(token), opts.headers || {});
  const res = await fetch(url + (opts.qs || ''), { headers, method: opts.method || 'GET' });
  if (res.status === 202) {
    // GitHub is computing statistics for this endpoint; wait and retry a few times
    await new Promise(r => setTimeout(r, 1500));
    return ghFetch(path, token, opts);
  }
  if (!res.ok) {
    const txt = await res.text();
    const err = new Error(`GitHub API ${res.status} ${res.statusText}: ${url} - ${txt}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// iterate paginated endpoints (per_page=100)
async function ghFetchAll(pathBase, token) {
  let page = 1;
  const results = [];
  while (true) {
    const pagePath = `${pathBase}${pathBase.includes('?') ? '&' : '?'}per_page=100&page=${page}`;
    const chunk = await ghFetch(pagePath, token);
    if (!Array.isArray(chunk) || chunk.length === 0) break;
    results.push(...chunk);
    if (chunk.length < 100) break;
    page++;
  }
  return results;
}

module.exports = { ghFetch, ghFetchAll };
