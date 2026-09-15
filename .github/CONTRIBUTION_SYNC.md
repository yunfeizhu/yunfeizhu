# Contribution synchronization

The `Sync open-source contributions` workflow runs daily at **06:00 Asia/Shanghai**
(`22:00 UTC` on the preceding day). It can also be started from the Actions tab
using **Run workflow** on `main`.

The updater searches for merged PRs authored by `yunfeizhu`, excludes repositories
owned by `yunfeizhu`, and verifies the PR's target repository and merge status.
Only public repositories with a license recognized by GitHub are included.
The table is sorted by merge date, newest first. Existing summaries and display
names are preserved in `scripts/contribution-overrides.json`; new rows use the
PR title and repository name returned by GitHub.

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
