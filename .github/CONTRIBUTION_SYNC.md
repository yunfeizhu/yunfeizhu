# Contribution synchronization

The `Sync open-source contributions` workflow runs daily at **06:00 Asia/Shanghai**
(`22:00 UTC` on the preceding day). It can also be started from the Actions tab
using **Run workflow** on `main`.

The updater searches for merged PRs authored by `yunfeizhu`, excludes repositories
owned by `yunfeizhu`, and verifies the PR's target repository and merge status.
Only public repositories with a license recognized by GitHub are included.
The table has one row per target repository. `project_order` in
`scripts/contribution-overrides.json` controls the project order; new projects
follow in alphabetical repository order. `project_summaries` and `project_names`
provide the curated descriptions and display names. Projects without a summary
use their latest merged PR title (or its existing `descriptions` override),
prefixed with `Latest:`. These summaries can be updated as contributions evolve.

Each row shows up to **6 PRs**, sorted by merge date, newest first, with three
links per line. Larger groups include a **View all N** link to that repository's
merged PRs authored by `yunfeizhu`. The heading totals count every verified PR
and project, including PRs omitted from the compact display. The global link
below the table remains available for browsing all external contributions.

Only the block between `CONTRIBUTIONS:START` and `CONTRIBUTIONS:END` in `README.md`
is regenerated. No change means no commit. Failed requests, incomplete search
results, and unexpectedly empty results leave the existing README intact.
Statistics cards are separate and retain their account-wide scope.

The workflow uses the repository's temporary `GITHUB_TOKEN`, with `contents: write`
only for this job. No personal access token or additional secret is required.
It does not run or check out code from the repositories being listed.

To run locally with GitHub CLI authentication:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update_contributions.py --check
python3 scripts/update_contributions.py
```

GitHub schedules can be delayed. Public-repository schedules are disabled after
60 days without repository activity; if that happens, re-enable this workflow in
the Actions tab. See [GitHub's scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
