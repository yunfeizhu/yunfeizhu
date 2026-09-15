"""Refresh the profile's external merged PRs using GitHub's public API."""

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
USERNAME = "yunfeizhu"
START = "<!-- CONTRIBUTIONS:START -->"
END = "<!-- CONTRIBUTIONS:END -->"
QUERY = f"is:pr is:merged author:{USERNAME} is:public -user:{USERNAME}"
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class SyncError(RuntimeError):
    pass


def github_api(endpoint, **params):
    command = ["gh", "api", "--method", "GET", endpoint]
    for key, value in params.items():
        command.extend(["-f", f"{key}={value}"])
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise SyncError(f"GitHub request failed for {endpoint}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def search_candidates(api):
    """Fetch every result, refusing partial searches instead of publishing a subset."""
    candidates = {}
    expected_total = None
    page = 1
    while True:
        result = api("search/issues", q=QUERY, per_page=100, page=page,
                     sort="created", order="desc")
        if result.get("incomplete_results"):
            raise SyncError("GitHub returned incomplete search results; keeping the README.")
        total = result["total_count"]
        if total > 1000:
            raise SyncError("Search exceeds GitHub's 1,000-result limit; keeping the README.")
        if expected_total is not None and total != expected_total:
            raise SyncError("Search changed during pagination; retry on the next run.")
        expected_total = total
        items = result["items"]
        for item in items:
            prefix = "https://api.github.com/repos/"
            url = item["repository_url"]
            repo = url.removeprefix(prefix)
            if not url.startswith(prefix) or not REPO_PATTERN.fullmatch(repo):
                raise SyncError("Unexpected repository in GitHub search response.")
            number = item["number"]
            if not isinstance(number, int) or number <= 0:
                raise SyncError("Unexpected pull request number.")
            candidates[(repo, number)] = (repo, number)
        if page * 100 >= total:
            break
        if not items:
            raise SyncError("GitHub pagination ended early; keeping the README.")
        page += 1
    if len(candidates) != expected_total:
        raise SyncError("GitHub search omitted or duplicated results; keeping the README.")
    return list(candidates.values())


def collect_contributions(api):
    projects = {}
    contributions = []
    for repo, number in search_candidates(api):
        # Check the target repository, not the author's fork containing the branch.
        if repo.split("/", 1)[0].casefold() == USERNAME.casefold():
            continue
        pr = api(f"repos/{repo}/pulls/{number}")
        base = pr["base"]["repo"]
        if (pr["user"]["login"].casefold() != USERNAME.casefold()
                or not pr["merged"] or not pr["merged_at"]
                or base["owner"]["login"].casefold() == USERNAME.casefold()
                or base["private"]):
            continue
        if base["full_name"].casefold() != repo.casefold():
            raise SyncError("PR target changed during synchronization; keeping the README.")
        if repo not in projects:
            projects[repo] = api(f"repos/{repo}")
        project = projects[repo]
        license_id = (project.get("license") or {}).get("spdx_id")
        if (project["private"]
                or project["owner"]["login"].casefold() == USERNAME.casefold()
                or not license_id or license_id == "NOASSERTION"):
            continue
        if project["full_name"].casefold() != repo.casefold():
            raise SyncError("Repository changed during synchronization; keeping the README.")
        # Parse dates before modifying files so malformed API data cannot corrupt the table.
        datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
        contributions.append({"repo": project["full_name"], "number": number,
                              "title": pr["title"], "merged_at": pr["merged_at"]})
    return sorted(contributions, key=lambda p: (p["merged_at"], p["repo"], p["number"]),
                  reverse=True)


def table_text(value):
    """Treat PR titles as text, including pipes, HTML, and Markdown metacharacters."""
    value = escape(" ".join(value.split()), quote=False)
    return re.sub(r"[\\|`*_\[\]!@]", lambda match: f"&#{ord(match.group())};", value)


def render_table(contributions, overrides):
    lines = ["| Project | Contribution | Pull request | Status |",
             "| --- | --- | :---: | :---: |"]
    for pr in contributions:
        repo, number = pr["repo"], pr["number"]
        url = f"https://github.com/{repo}/pull/{number}"
        label = overrides.get("project_names", {}).get(repo, repo.split("/", 1)[1])
        title = overrides.get("descriptions", {}).get(url, pr["title"])
        lines.append(
            f"| **[{table_text(label)}](https://github.com/{repo})** | "
            f"{table_text(title)} | [#{number}]({url}) | "
            '<img src="./assets/contributions/merged.svg" alt="Merged" width="68" height="22"> |'
        )
    return "\n".join(lines)


def replace_table(readme, table):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise SyncError("Expected exactly one pair of contribution markers; keeping the README.")
    start = readme.index(START) + len(START)
    end = readme.index(END)
    if start >= end:
        raise SyncError("Contribution markers are reversed; keeping the README.")
    return readme[:start] + "\n\n" + table + "\n\n" + readme[end:]


def sync_profile(readme_path, overrides, api=github_api, check=False):
    original = readme_path.read_text(encoding="utf-8")
    replace_table(original, "")  # Validate boundaries before making network calls.
    contributions = collect_contributions(api)
    if not contributions:
        raise SyncError("No verified external contributions returned; keeping the existing table.")
    updated = replace_table(original, render_table(contributions, overrides))
    changed = updated != original
    if changed and not check:
        readme_path.write_text(updated, encoding="utf-8")
    print(f"Verified {len(contributions)} external merged PRs. "
          f"README {'needs an update' if check and changed else 'updated' if changed else 'unchanged'}.")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Query GitHub without modifying files")
    args = parser.parse_args()
    overrides = json.loads((ROOT / "scripts/contribution-overrides.json").read_text(encoding="utf-8"))
    try:
        sync_profile(ROOT / "README.md", overrides, check=args.check)
    except (SyncError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Contribution sync failed: {error}") from error


if __name__ == "__main__":
    main()
