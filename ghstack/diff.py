#!/usr/bin/env python3

from dataclasses import dataclass
from ghstack.typing import GhNumber, GitHubNumber, GitTreeHash
import ghstack.shell
from typing import Optional
import re
from abc import ABCMeta, abstractmethod


RE_RAW_METADATA = re.compile(
    r'gh-metadata: (?P<owner>[^/]+) (?P<repo>[^/]+) (?P<number>[0-9]+) '
    r'gh/(?P<username>[a-zA-Z0-9-]+)/(?P<ghnum>[0-9]+)/head', re.MULTILINE)


@dataclass
class GhMetadata:
    # Owner of the repository this diff was submitted to
    owner: str

    # Name of the repository this was submitted to
    repo: str

    # The GitHub PR number of this diff
    number: GitHubNumber

    # GitHub username of person who originally submitted this diff
    username: str

    # The ghstack number identifying this diff
    ghnum: GhNumber

    @staticmethod
    def search(s: str) -> Optional['GhMetadata']:
        m = RE_RAW_METADATA.search(s)
        if m is None:
            return None
        return GhMetadata(
            owner=m.group("owner"),
            repo=m.group("repo"),
            number=GitHubNumber(int(m.group("number"))),
            username=m.group("username"),
            ghnum=GhNumber(m.group("ghnum")),
        )


class Patch(metaclass=ABCMeta):
    """
    Abstract representation of a patch, i.e., some actual
    change between two trees.
    """
    @abstractmethod
    def apply(self, sh: ghstack.shell.Shell, h: GitTreeHash) -> GitTreeHash:
        pass


@dataclass
class Diff:
    """
    An abstract representation of a diff.  Diffs can come from
    git or hg.
    """
    # Title of the diff
    title: str

    # Detailed description of the diff.  Includes the title.
    summary: str

    # Unique identifier representing the commit in question (may be a
    # Git/Mercurial commit hash; the important thing is that it can be
    # used as a unique identifier.)
    oid: str

    # The contents of gh-metadata.  They are None if we haven't ever
    # submitted this diff to ghstack (i.e., there is no gh-metadata
    # line).
    gh_metadata: Optional[GhMetadata]

    # Function which applies this diff to the input tree, producing a
    # new tree.  There will only be two implementations of this:
    #
    #   - Git: A no-op function, which asserts that GitTreeHash is some
    #     known tree and then returns a fixed GitTreeHash (since we
    #     already know exactly what tree we want.)
    #
    #   - Hg: A function which applies some patch to the git tree
    #     giving you the result.
    #
    # This function is provided a shell whose cwd is the Git repository
    # that the tree hashes live in.
    #
    # NB: I could have alternately represented this as
    # Optional[GitTreeHash] + Optional[UnifiedDiff] but that would
    # require me to read out diff into memory and I don't really want
    # to do that if I don't have to.
    patch: Patch
