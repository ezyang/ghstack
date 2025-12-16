#!/usr/bin/env python3

import unittest

import ghstack.github_utils


class TestParsePullRequest(unittest.TestCase):
    def test_github_url_basic(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://github.com/pytorch/pytorch/pull/169404"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_github_url_trailing_slash(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://github.com/pytorch/pytorch/pull/169404/"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_github_url_files_suffix(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://github.com/pytorch/pytorch/pull/169404/files"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_github_url_commits_suffix(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://github.com/pytorch/pytorch/pull/169404/commits"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_github_url_commits_with_sha(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://github.com/pytorch/pytorch/pull/169404/commits/abc123def"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_pytorch_hud_url_basic(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://hud.pytorch.org/pr/169404"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_pytorch_hud_url_trailing_slash(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://hud.pytorch.org/pr/169404/"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "pytorch")
        self.assertEqual(result["name"], "pytorch")
        self.assertEqual(result["number"], 169404)

    def test_different_owner_repo(self) -> None:
        result = ghstack.github_utils.parse_pull_request(
            "https://github.com/facebook/react/pull/12345"
        )
        self.assertEqual(result["github_url"], "github.com")
        self.assertEqual(result["owner"], "facebook")
        self.assertEqual(result["name"], "react")
        self.assertEqual(result["number"], 12345)

    def test_invalid_url_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            ghstack.github_utils.parse_pull_request("not-a-valid-url")

    def test_invalid_hud_url_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            ghstack.github_utils.parse_pull_request("https://hud.pytorch.org/not-pr/123")


if __name__ == "__main__":
    unittest.main()
