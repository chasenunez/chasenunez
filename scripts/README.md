# update-readme script

This script (`update_readme.py`) fetches recent GitHub activity for a user (or for the authenticated user when a token is provided), assembles ASCII charts and an ASCII table summarizing repository activity, and writes an updated `README.md` in the repository root.

I wrote the script to be robust and simple: it avoids injecting inline color styles (GitHub sanitizes those so they won't show consistently). The output is placed inside a `<pre>...</pre>` block so spacing is preserved and repo links are clickable.

is it flashy? no. is it colorful? no. 
But is it effecient? also no.

## What this does (high level)
- Fetches the most recently updated repositories (configurable `TOP_N`).
- Queries `/stats/commit_activity` for weekly commit counts (last `WEEKS` weeks).
- Builds:
  - an ASCII "area" plot showing weekly totals (with mean line),
  - a compact braille-based contribution grid per repo,
  - an ASCII table listing repo name, language, bytes, commits, last pushed date, and branch count,
  - an hourly histogram showing commit timestamps (from up to `per_repo_limit` most recent commits per repo).
- Writes a `README.md` that embeds the ASCII output inside `<pre>` so it renders well in GitHub.


## Requirements
- Python 3.8+ recommended
- `requests`
- Optional but recommended for nicer width handling: `wcwidth`
- Optional: `plotille` (if installed, it can produce a fancier histogram fallback)

Install required packages:

```bash
pip install requests wcwidth plotille
````

(If you don't want `plotille` or `wcwidth`, the script still works with sensible fallbacks.)


## Authentication / tokens

* For unauthenticated runs the script fetches public info for `USERNAME` (default set inside the script).
* For private repos and higher rate limits, provide a Personal Access Token (PAT) via environment variable:

  * `GH_PAT` or `GITHUB_TOKEN` or `GH_TOKEN`.

Token scopes:

* To read private repo info you need `repo` scope. For public data only, no scope is needed.

Example (POSIX shell):

```bash
export GH_PAT="ghp_xxx..."
python update_readme.py
```


## Where to customize (safe to change)

Open `update_readme.py` and edit the top `Config` block:

* `USERNAME` — username to fetch when you don't supply a token.
* `TOP_N` — how many of the most recently updated repos to summarise.
* `WEEKS` — how many weekly buckets from `/stats/commit_activity` to display.
* `PLOT_HEIGHT` — height of the ASCII area plot (rows).
* `MAX_WIDTH` / `LINE_LENGTH` — tune layout width preferences.
* `CACHE_FILE` — file where I store recent commit_activity arrays to handle GitHub stats inconsistencies.
* `README_OUT` — path of the output file written by the script (default `README.md`).

These are high-level presentation parameters and safe to edit as needed.


## Things that should NOT be changed (unless you know what you are doing)

* `GITHUB_API` (unless you target a GitHub Enterprise instance; in that case adapt URLs).
* The logic that builds `repo_weekly` and the caching logic if you want consistent behaviour.
* The names/format of the cache file if you rely on the cache elsewhere.


## GitHub Action example

You can run this on a schedule with a GitHub Action. Below is a minimal workflow you can drop into `.github/workflows/update-readme.yml`:

```yaml
name: Update README with activity
on:
  schedule:
    - cron: '0 */6 * * *'   # every 6 hours (adjust as desired)
  workflow_dispatch:

jobs:
  update-readme:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.x'
      - name: Install deps
        run: pip install requests wcwidth plotille
      - name: Run update script
        env:
          GH_PAT: ${{ secrets.GH_PAT }}   # set this in your repo secrets if you need private access
        run: python script/update_readme.py
      - name: Commit updated README
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add README.md
          git commit -m "Automated README update: repo activity" || echo "No changes"
          git push
```

If you only need public repo data for a single user, you can omit the token.


## Notes, tips & troubleshooting

* The GitHub `/stats/*` endpoints can return `202` while GitHub generates stats. The script retries (exponential backoff) but sometimes the endpoint remains empty; that's why I maintain a small JSON cache of last-known series.
* Rate limits: without a token you'll hit lower rate limits; with a token you get higher limits.
* The script preserves anchor tags for repo links inside the ASCII table to keep them clickable in the README.
* If the ASCII table seems too wide on small screens, reduce `LINE_LENGTH` and/or `TOP_N`.
* If you want color in a rendered terminal, run the script locally and pipe output into a terminal-friendly renderer. I removed the inline coloring because GitHub strips those attributes; the core data is preserved.

