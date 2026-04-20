# update-readme script

`update_readme.py` renders a README containing a single Unicode box-drawing
table of every repository with a push in the last `ACTIVE_WINDOW_DAYS` days
(default 180, i.e. ~6 months). It is designed to run daily from
`.github/workflows/update_readme.yml`.

## Columns

| Column         | Meaning                                                        |
| -------------- | -------------------------------------------------------------- |
| Repository     | Repo name, linked to its GitHub page via an inline `<a>` tag.  |
| Main Language  | Primary language; HTML falls back to the largest non-HTML one. |
| Total Bytes    | `size` field reported by the repo listing endpoint.            |
| Total Commits  | All-time commit count, fetched in O(1) calls (see below).      |
| Lifespan       | Days between the first commit's author date and today.         |
| Team Size      | Unique contributors to the default branch.                     |

## Data sources

| View             | Endpoint                                                     |
| ---------------- | ------------------------------------------------------------ |
| Repo list        | REST `/user/repos` (authed) or `/users/:u/repos` (public)    |
| Commit count     | REST `/repos/:o/:r/commits?per_page=1` + `Link: rel="last"`  |
| First commit     | Follow-up GET to the `rel="last"` URL (still `per_page=1`)   |
| Team size        | REST `/repos/:o/:r/contributors?per_page=1` + `rel="last"`   |
| Language fixup   | REST `/repos/:o/:r/languages` (only when primary == HTML)    |

The `rel="last"` trick means each repo needs at most **3 API calls** (one
commits lookup, one oldest-commit fetch, one contributors count), regardless
of how large the repo is. All repos are processed concurrently via a
`ThreadPoolExecutor` (`METADATA_WORKERS = 8` by default).

## Authentication

Set `GH_PAT` (or `GITHUB_TOKEN` / `GH_TOKEN`) in the environment. Without a
token the script falls back to public data for `USERNAME`. A token is
required for private repos to appear (they will still be filtered out of
the final table unless you remove the `not r.get("private")` check in
`main()`).

Minimum scopes for a classic PAT: `repo` (full) if you want private repos
visible to the script; otherwise `public_repo` is enough. Fine-grained PATs
need `Contents: Read` and `Metadata: Read` on the repos of interest.

## Config (top of `update_readme.py`)

| Name                 | Default      | Meaning                                          |
| -------------------- | ------------ | ------------------------------------------------ |
| `USERNAME`           | `chasenunez` | Account to query when no token is available.    |
| `ACTIVE_WINDOW_DAYS` | `180`        | Repos pushed within this window are included.    |
| `LINE_LENGTH`        | `112`        | Target width of the rendered dashboard.          |
| `METADATA_WORKERS`   | `8`          | Concurrent repos processed at once.              |
| `HTTP_TIMEOUT`       | `30`         | Per-request timeout in seconds.                  |
| `README_OUT`         | `README.md`  | File to overwrite with the rendered output.      |

## Running locally

```sh
pip install -r requirements.txt
export GH_PAT="ghp_..."
python scripts/update_readme.py                # writes README.md
python scripts/update_readme.py --print        # render to stdout only
python scripts/update_readme.py --window-days 90   # last 3 months
pytest -q                                      # run the tests
```

## Tests

Rendering and filtering logic are covered by `pytest` under `tests/`. Tests
use synthetic fixtures and do not hit the network. The GitHub Actions
workflow runs `pytest` **after** rendering, so a failing test blocks the
commit+push step.
