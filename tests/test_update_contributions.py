import copy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

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
        data = [{"repo": "upstream/library", "number": 1,
                 "title": '<img src=x> | [click](https://example.com)\n**text**'}]
        table = sync.render_table(data, {})
        row = table.splitlines()[-1]
        self.assertEqual(row.count("|"), 5)
        self.assertIn("&lt;img src=x&gt;", row)
        self.assertNotIn("[click]", row)
        self.assertNotIn("**text**", row)

    def test_curated_descriptions_and_project_names_are_preserved(self):
        data = [{"repo": "upstream/library", "number": 1, "title": "fix: technical title"}]
        overrides = {"project_names": {"upstream/library": "Library"},
                     "descriptions": {"https://github.com/upstream/library/pull/1": "Clear summary."}}
        table = sync.render_table(data, overrides)
        self.assertIn("[Library]", table)
        self.assertIn("Clear summary.", table)
        self.assertNotIn("technical title", table)


if __name__ == "__main__":
    unittest.main()
