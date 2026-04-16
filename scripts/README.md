# update-readme script

`update_readme.py` renders a README showing GitHub activity as ASCII / braille
graphics. It is designed to run daily from `.github/workflows/update_readme.yml`.

## Visual sections (top to bottom)

1. **Repository summary** — box-drawing table of repo metadata for the top-N
   most recently updated repos.
2. **Language mix** — single horizontal stacked bar of language byte shares
   across *all* public repos (not just the recently-active top-N), with
   segments below 2% collapsed into `Other`. The bar is always exactly
   `LINE_LENGTH` columns wide; the legend is greedy-wrapped to that same width
   so it never spills past the table border. Each legend entry annotates the
   language with the number of repos it appears in (a "diversity" signal).
   Languages in `EXCLUDED_LANGUAGES` (HTML by default) are dropped since they
   are typically template boilerplate.
3. **Day-of-week distribution** — horizontal histogram of contributions
   aggregated per weekday, driven by the GraphQL `contributionsCollection`
   endpoint (authoritative source; includes private contributions when authed).

## Data sources

| View                      | Endpoint                                         |
| ------------------------- | ------------------------------------------------ |
| Repo table                | REST `/user/repos` (+ commits, branches headers) |
| Language mix (all repos)  | REST `/repos/:o/:r/languages`                    |
| Weekday histogram         | GraphQL `user.contributionsCollection`           |

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
| `EXCLUDED_LANGUAGES` | Languages to drop from the language-mix bar.       |
| `LINE_LENGTH`     | Target width for the rendered README.                 |
| `README_OUT`      | Path of the README to overwrite.                      |

## Running locally

```sh
pip install -r requirements.txt
export GH_PAT="ghp_..."
python scripts/update_readme.py             # writes README.md
python scripts/update_readme.py --print     # render to stdout only
pytest -q                                   # run the rendering tests
```

## Tests

Rendering is verified via `pytest` under `tests/`. Tests use synthetic fixtures
and do not hit the network. The GitHub Actions workflow runs tests **after**
rendering — a failing test blocks the commit+push step.
