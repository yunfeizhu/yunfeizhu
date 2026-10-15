import copy
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_contributions as sync


def project(repo="upstream/library", private=False, license_id="MIT"):
    return {"full_name": repo, "owner": {"login": repo.split("/")[0]},
            "private": private, "license": {"spdx_id": license_id} if license_id else None}


def pull(number=1, repo="upstream/library", author="yunfeizhu", merged=True,
         merged_at="2026-09-15T00:00:00Z", title="Fix a bug"):
    return {"number": number, "user": {"login": author}, "merged": merged,
            "merged_at": merged_at if merged else None, "title": title,
            "base": {"repo": project(repo)}, "head": {"repo": project("yunfeizhu/library")}}


def contribution(number=1, repo="upstream/library", merged_at="2026-09-15T00:00:00Z",
                 title="Fix a bug"):
    return {"repo": repo, "number": number, "title": title, "merged_at": merged_at}


class FakeGitHub:
    def __init__(self, prs, projects=None, incomplete=False):
        self.prs = prs
        self.projects = projects or {}
        self.incomplete = incomplete
        self.calls = []

    def __call__(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == "search/issues":
            items = [{"number": pr["number"],
                      "repository_url": "https://api.github.com/repos/" + pr["base"]["repo"]["full_name"]}
                     for pr in self.prs]
            start = (params["page"] - 1) * 100
            return {"total_count": len(items), "incomplete_results": self.incomplete,
                    "items": items[start:start + 100]}
        if "/pulls/" in endpoint:
            repo, number = endpoint.removeprefix("repos/").split("/pulls/")
            return copy.deepcopy(next(pr for pr in self.prs
                                      if pr["number"] == int(number)
                                      and pr["base"]["repo"]["full_name"] == repo))
        repo = endpoint.removeprefix("repos/")
        return copy.deepcopy(self.projects.get(repo, project(repo)))


class ContributionSyncTests(unittest.TestCase):
    def test_external_target_is_included_even_when_source_branch_is_own_fork(self):
        api = FakeGitHub([pull()])
        self.assertEqual(sync.collect_contributions(api)[0]["repo"], "upstream/library")
        self.assertIn("-user:yunfeizhu", api.calls[0][1]["q"])

    def test_own_targets_other_authors_and_unmerged_prs_are_excluded(self):
        api = FakeGitHub([pull(1), pull(2, repo="YunFeiZhu/own"),
                          pull(3, author="somebody-else"), pull(4, merged=False)])
        self.assertEqual([p["number"] for p in sync.collect_contributions(api)], [1])
        self.assertFalse(any(endpoint.startswith("repos/YunFeiZhu/") for endpoint, _ in api.calls))

    def test_private_and_unlicensed_projects_are_excluded(self):
        for metadata in [project(private=True), project(license_id=None),
                         project(license_id="NOASSERTION")]:
            with self.subTest(metadata=metadata):
                self.assertEqual(sync.collect_contributions(
                    FakeGitHub([pull()], {"upstream/library": metadata})), [])

    def test_base_repository_privacy_is_checked_before_repository_lookup(self):
        pr = pull()
        pr["base"]["repo"]["private"] = True
        api = FakeGitHub([pr])
        self.assertEqual(sync.collect_contributions(api), [])
        self.assertFalse(any(e == "repos/upstream/library" for e, _ in api.calls))

    def test_pagination_collects_more_than_one_page_and_uses_merge_order(self):
        prs = [pull(i, merged_at="2026-01-01T00:00:00Z") for i in range(1, 102)]
        prs[0]["merged_at"] = "2026-09-15T00:00:00Z"
        api = FakeGitHub(prs)
        data = sync.collect_contributions(api)
        self.assertEqual(len(data), 101)
        self.assertEqual(data[0]["number"], 1)
        self.assertEqual([p["page"] for e, p in api.calls if e == "search/issues"], [1, 2])
        self.assertEqual(sum(e == "repos/upstream/library" for e, _ in api.calls), 1)

    def test_partial_empty_or_failed_responses_never_modify_existing_readme(self):
        def fail(endpoint, **params):
            raise sync.SyncError("API unavailable")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            original = "Intro\n" + sync.START + "\nexisting table\n" + sync.END + "\nGaming\n"
            for api in [FakeGitHub([pull()], incomplete=True), FakeGitHub([]), fail]:
                path.write_text(original)
                with self.assertRaises(sync.SyncError):
                    sync.sync_profile(path, {}, api)
                self.assertEqual(path.read_text(), original)

    def test_invalid_marker_boundaries_are_rejected(self):
        for source in ["no markers", sync.END + sync.START,
                       sync.START + sync.START + sync.END]:
            with self.subTest(source=source), self.assertRaises(sync.SyncError):
                sync.replace_table(source, "new table")

    def test_only_generated_block_changes_and_identical_runs_are_noops(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            before = "# My profile\nGames, images and other content.\n" + sync.START
            after = sync.END + "\nOther links and statistics.\n"
            path.write_text(before + "\nold table\n" + after)
            self.assertTrue(sync.sync_profile(path, {}, FakeGitHub([pull()]), check=True))
            self.assertIn("old table", path.read_text())
            self.assertTrue(sync.sync_profile(path, {}, FakeGitHub([pull()])))
            updated = path.read_text()
            self.assertTrue(updated.startswith(before))
            self.assertTrue(updated.endswith(after))
            self.assertFalse(sync.sync_profile(path, {}, FakeGitHub([pull()])))
            self.assertEqual(path.read_text(), updated)

    def test_untrusted_titles_cannot_add_table_columns_html_or_markdown_links(self):
        data = [contribution(title='<img src=x> | [click](https://example.com)\n**text**')]
        table = sync.render_table(data, {})
        row = table.splitlines()[-1]
        self.assertEqual(row.count("|"), 4)
        self.assertIn("&lt;img src=x&gt;", row)
        self.assertNotIn("[click]", row)
        self.assertNotIn("**text**", row)

    def test_curated_descriptions_and_project_names_are_preserved(self):
        data = [contribution(title="fix: technical title")]
        overrides = {"project_names": {"upstream/library": "Library"},
                     "descriptions": {"https://github.com/upstream/library/pull/1": "Clear summary."}}
        table = sync.render_table(data, overrides)
        self.assertIn("[Library]", table)
        self.assertIn("Clear summary.", table)
        self.assertNotIn("technical title", table)

    def test_groups_use_full_repository_identity_and_configured_order(self):
        data = [contribution(repo="zeta/library"), contribution(2, repo="featured/project"),
                contribution(repo="alpha/library"), contribution(repo="featured/project")]
        table = sync.render_table(data, {"project_order": ["missing/project", "featured/project"]})
        rows = [line for line in table.splitlines() if line.startswith("| **[")]
        self.assertEqual(len(rows), 3)
        self.assertIn("https://github.com/featured/project)", rows[0])
        self.assertIn("https://github.com/alpha/library)", rows[1])
        self.assertIn("https://github.com/zeta/library)", rows[2])
        self.assertIn("4 merged PRs across 3 external open-source projects", table)
        self.assertNotIn("merged.svg", table)
        self.assertEqual(table, sync.render_table(list(reversed(data)), {
            "project_order": ["missing/project", "featured/project"]}))

    def test_group_shows_latest_six_by_merge_date_not_pr_number(self):
        data = [contribution(number, merged_at=f"2026-09-{16 - number:02d}T00:00:00Z")
                for number in range(1, 9)]
        table = sync.render_table(list(reversed(data)), {})
        self.assertEqual(re.findall(r"\[#(\d+)\]", table), ["1", "2", "3", "4", "5", "6"])
        self.assertIn("8 merged PRs across 1 external open-source projects", table)
        self.assertIn("View&nbsp;all&nbsp;8", table)
        url = re.search(r"\[View[^]]+\]\(([^)]+)\)", table).group(1)
        self.assertEqual(urlparse(url).path, "/upstream/library/pulls")
        self.assertEqual(parse_qs(urlparse(url).query), {
            "q": ["is:pr is:merged author:yunfeizhu"]})

    def test_view_all_only_appears_when_a_group_exceeds_six_prs(self):
        for count in [1, 3, 6, 7]:
            with self.subTest(count=count):
                table = sync.render_table([contribution(i) for i in range(1, count + 1)], {})
                self.assertEqual(len(re.findall(r"\[#\d+\]", table)), min(count, 6))
                self.assertEqual("View&nbsp;all" in table, count > 6)

    def test_pr_links_wrap_in_groups_of_three(self):
        for count in [1, 3, 4, 6]:
            with self.subTest(count=count):
                table = sync.render_table([contribution(i) for i in range(1, count + 1)], {})
                links = table.splitlines()[-1].split(" | ")[2].removesuffix(" |")
                chunks = links.split("<br>")
                self.assertEqual(len(chunks), (count + 2) // 3)
                for chunk in chunks:
                    numbers = re.findall(r"\[#\d+\]", chunk)
                    self.assertLessEqual(len(numbers), 3)
                    self.assertEqual(chunk.count("&nbsp;·&nbsp;"), len(numbers) - 1)

    def test_project_summary_overrides_latest_title_and_is_escaped(self):
        overrides = {"project_names": {"upstream/library": "A | B"},
                     "project_summaries": {"upstream/library": "<b>Summary</b> | [link](url)"}}
        row = sync.render_table([contribution(title="Not the summary")], overrides).splitlines()[-1]
        self.assertEqual(row.count("|"), 4)
        self.assertIn("&lt;b&gt;Summary&lt;/b&gt;", row)
        self.assertIn("A &#124; B", row)
        self.assertNotIn("[link]", row)
        self.assertNotIn("Not the summary", row)

    def test_new_projects_use_latest_merged_pr_as_explicit_fallback(self):
        data = [contribution(99, merged_at="2026-09-01T00:00:00Z", title="Older work"),
                contribution(1, title="Recent work")]
        table = sync.render_table(data, {})
        self.assertIn("Latest: Recent work", table)
        self.assertNotIn("Older work", table)

    def test_new_merged_pr_updates_existing_group_without_adding_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            path.write_text(sync.START + "\nold table\n" + sync.END)
            prs = [pull(i, merged_at="2026-09-01T00:00:00Z") for i in range(1, 8)]
            sync.sync_profile(path, {}, FakeGitHub(prs))
            prs.append(pull(8))
            self.assertTrue(sync.sync_profile(path, {}, FakeGitHub(prs)))
            updated = path.read_text()
            self.assertEqual(updated.count("| **["), 1)
            self.assertIn("View&nbsp;all&nbsp;8", updated)
            self.assertEqual(re.findall(r"\[#(\d+)\]", updated), ["8", "7", "6", "5", "4", "3"])
            self.assertFalse(sync.sync_profile(path, {}, FakeGitHub(prs)))


if __name__ == "__main__":
    unittest.main()
