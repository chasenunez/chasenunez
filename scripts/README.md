# update-readme script

`update_readme.py` renders a README showing GitHub activity as ASCII / braille
graphics. It is designed to run daily from `.github/workflows/update_readme.yml`.

## Visual sections (top to bottom)

1. **Daily contributions calendar** — 7 rows × 53 weeks, GitHub-style, driven by
   the GraphQL `contributionsCollection` endpoint. This is the authoritative
   source of activity (public + private when authenticated).
2. **Day-of-week distribution** — horizontal histogram of contributions
   aggregated per weekday.
3. **Per-repo weekly activity** — braille heat grid, one row per top-N
   repository, using `/stats/commit_activity`. Private repos in the top-N are
   summed into a single `restricted` row.
4. **Language mix** — single horizontal stacked bar of language byte shares
   across public repos, with segments below 2% collapsed into `Other`.
5. **Repository summary** — box-drawing table of repo metadata.

## Data sources

| View                           | Endpoint                                         |
| ------------------------------ | ------------------------------------------------ |
| Daily calendar + weekday hist  | GraphQL `user.contributionsCollection`           |
| Per-repo weekly heat grid      | REST `/repos/:o/:r/stats/commit_activity`        |
| Language mix                   | REST `/repos/:o/:r/languages`                    |
| Table                          | REST `/user/repos` (+ commits, branches headers) |

`/stats/commit_activity` is unreliable (returns `202` or `200 []` while GitHub
is still computing), so every non-empty response is persisted to
`.activity_cache.json`. The cache is **committed to the repo** so it survives
between workflow runs; a stale cache is a far better default than an empty grid.

## Authentication

Set `GH_PAT` (or `GITHUB_TOKEN` / `GH_TOKEN`) in the environment. Without a
token the script falls back to public data for `USERNAME`. A token is required
for private contributions to show up in the calendar.

Minimum scopes for a classic PAT: `read:user` + `repo` (the latter only if you
want private repo activity included). Fine-grained PATs need
`Contents: Read` and `Metadata: Read` on the repos of interest.

## Config

Top of `update_readme.py`:

| Name              | Meaning                                               |
| ----------------- | ----------------------------------------------------- |
| `USERNAME`        | Account to query when no token is available.          |
| `TOP_N`           | Repos to pull metadata for (most recently updated).   |
| `WEEKS_PER_REPO`  | Columns in the per-repo heat grid.                    |
| `CALENDAR_WEEKS`  | Columns in the daily calendar.                        |
| `LINE_LENGTH`     | Target width for the rendered README.                 |
| `CACHE_FILE`      | Path of the persisted weekly-commits cache.           |
| `README_OUT`      | Path of the README to overwrite.                      |

## Running locally

```sh
pip install -r requirements.txt
export GH_PAT="ghp_..."
python scripts/update_readme.py             # writes README.md + cache
python scripts/update_readme.py --print     # render to stdout only
pytest -q                                   # run the rendering tests
```

## Tests

Rendering is verified via `pytest` under `tests/`. Tests use synthetic fixtures
and do not hit the network. The GitHub Actions workflow runs tests **after**
rendering — a failing test blocks the commit+push step.
