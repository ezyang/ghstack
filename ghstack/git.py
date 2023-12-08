#!/usr/bin/env python3

import re
from functools import cached_property
from typing import List, Pattern

import ghstack.diff
import ghstack.shell
from ghstack.types import GitCommitHash, GitTreeHash

RE_RAW_COMMIT_ID = re.compile(r"^(?P<boundary>-?)(?P<commit>[a-f0-9]+)$", re.MULTILINE)
RE_RAW_AUTHOR = re.compile(
    r"^author (?P<author>(?P<name>[^<]+?) <(?P<email>[^>]+)>)", re.MULTILINE
)
RE_RAW_PARENT = re.compile(r"^parent (?P<commit>[a-f0-9]+)$", re.MULTILINE)
RE_RAW_TREE = re.compile(r"^tree (?P<tree>.+)$", re.MULTILINE)
RE_RAW_COMMIT_MSG_LINE = re.compile(r"^    (?P<line>.*)$", re.MULTILINE)


class CommitHeader(object):
    """
    Represents the information extracted from `git rev-list --header`
    """

    # The unparsed output from git rev-list --header
    raw_header: str

    def __init__(self, raw_header: str):
        self.raw_header = raw_header

    def _search_group(self, regex: Pattern[str], group: str) -> str:
        m = regex.search(self.raw_header)
        assert m
        return m.group(group)

    @cached_property
    def tree(self) -> GitTreeHash:
        return GitTreeHash(self._search_group(RE_RAW_TREE, "tree"))

    @cached_property
    def title(self) -> str:
        return self._search_group(RE_RAW_COMMIT_MSG_LINE, "line")

    @cached_property
    def commit_id(self) -> GitCommitHash:
        return GitCommitHash(self._search_group(RE_RAW_COMMIT_ID, "commit"))

    @cached_property
    def boundary(self) -> bool:
        return self._search_group(RE_RAW_COMMIT_ID, "boundary") == "-"

    @cached_property
    def parents(self) -> List[GitCommitHash]:
        return [
            GitCommitHash(m.group("commit"))
            for m in RE_RAW_PARENT.finditer(self.raw_header)
        ]

    @cached_property
    def author(self) -> str:
        return self._search_group(RE_RAW_AUTHOR, "author")

    @cached_property
    def author_name(self) -> str:
        return self._search_group(RE_RAW_AUTHOR, "name")

    @cached_property
    def author_email(self) -> str:
        return self._search_group(RE_RAW_AUTHOR, "email")

    @cached_property
    def commit_msg(self) -> str:
        return "\n".join(
            m.group("line") for m in RE_RAW_COMMIT_MSG_LINE.finditer(self.raw_header)
        )


def split_header(s: str) -> List[CommitHeader]:
    return list(map(CommitHeader, s.split("\0")[:-1]))


def convert_header(h: CommitHeader, github_url: str) -> ghstack.diff.Diff:
    return ghstack.diff.Diff(
        title=h.title,
        summary=h.commit_msg,
        oid=h.commit_id,
        source_id=h.tree,
        pull_request_resolved=ghstack.diff.PullRequestResolved.search(
            h.raw_header, github_url
        ),
        tree=h.tree,
        author_name=h.author_name,
        author_email=h.author_email,
        boundary=h.boundary,
    )


def parse_header(s: str, github_url: str) -> List[ghstack.diff.Diff]:
    return [convert_header(h, github_url) for h in split_header(s)]
