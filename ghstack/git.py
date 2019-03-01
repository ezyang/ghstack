#!/usr/bin/env python3

from typing import NewType, Pattern, Match, List, Optional
import re


# Actually, sometimes we smuggle revs in here.  It doesn't seem to
# matter at the moment, but it might be good to make a better
# distinction here.
# commit 3f72e04eeabcc7e77f127d3e7baf2f5ccdb148ee
GitCommitHash = NewType('GitCommitHash', str)

# tree 3f72e04eeabcc7e77f127d3e7baf2f5ccdb148ee
GitTreeHash = NewType('GitTreeHash', str)


RE_RAW_COMMIT_ID = re.compile(r'^(?P<commit>[a-f0-9]+)$', re.MULTILINE)
RE_RAW_AUTHOR = re.compile(r'^author (?P<name>[^<]+?) <(?P<email>[^>]+)>',
                           re.MULTILINE)
RE_RAW_PARENT = re.compile(r'^parent (?P<commit>[a-f0-9]+)$', re.MULTILINE)
RE_RAW_TREE = re.compile(r'^tree (?P<tree>.+)$', re.MULTILINE)
RE_RAW_COMMIT_MSG_LINE = re.compile(r'^    (?P<line>.*)$', re.MULTILINE)
RE_RAW_METADATA = re.compile(
    r'^    gh-metadata: (?P<owner>[^/]+) (?P<repo>[^/]+) (?P<number>[0-9]+) '
    r'gh/(?P<username>[a-zA-Z0-9-]+)/(?P<diffid>[0-9]+)/head$', re.MULTILINE)


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

    def tree(self) -> GitTreeHash:
        return GitTreeHash(self._search_group(RE_RAW_TREE, "tree"))

    def title(self) -> str:
        return self._search_group(RE_RAW_COMMIT_MSG_LINE, "line")

    def commit_id(self) -> GitCommitHash:
        return GitCommitHash(
            self._search_group(RE_RAW_COMMIT_ID, "commit"))

    def parents(self) -> List[GitCommitHash]:
        return [GitCommitHash(m.group("commit"))
                for m in RE_RAW_PARENT.finditer(self.raw_header)]

    def author(self) -> str:
        return self._search_group(RE_RAW_AUTHOR, "author")

    def commit_msg(self) -> str:
        return '\n'.join(
            m.group("line")
            for m in RE_RAW_COMMIT_MSG_LINE.finditer(self.raw_header))

    def match_metadata(self) -> Optional[Match[str]]:
        return RE_RAW_METADATA.search(self.raw_header)


def split_header(s: str) -> List[CommitHeader]:
    return list(map(CommitHeader, s.split("\0")[:-1]))
