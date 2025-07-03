#!/usr/bin/env python3

import re
from dataclasses import dataclass
from typing import Optional, Pattern

from ghstack.types import GitHubNumber, GitTreeHash

RE_GH_METADATA = re.compile(
    r"gh-metadata: (?P<owner>[^/]+) (?P<repo>[^/]+) (?P<number>[0-9]+) "
    r"gh/(?P<username>[a-zA-Z0-9-]+)/(?P<ghnum>[0-9]+)/head",
    re.MULTILINE,
)


RAW_PULL_REQUEST_RESOLVED = (
    r"(Pull Request resolved|Pull-Request-resolved|Pull-Request): "
    r"https://{github_url}/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>[0-9]+)"
)


def re_pull_request_resolved(github_url: str) -> Pattern[str]:
    return re.compile(RAW_PULL_REQUEST_RESOLVED.format(github_url=github_url))


def re_pull_request_resolved_w_sp(github_url: str) -> Pattern[str]:
    return re.compile(r"\n*" + RAW_PULL_REQUEST_RESOLVED.format(github_url=github_url))


@dataclass
class PullRequestResolved:
    owner: str
    repo: str
    number: GitHubNumber
    github_url: str

    def url(self) -> str:
        return "https://{}/{}/{}/pull/{}".format(
            self.github_url, self.owner, self.repo, self.number
        )

    @staticmethod
    def search(s: str, github_url: str) -> Optional["PullRequestResolved"]:
        m = re_pull_request_resolved(github_url).search(s)
        if m is not None:
            return PullRequestResolved(
                owner=m.group("owner"),
                repo=m.group("repo"),
                number=GitHubNumber(int(m.group("number"))),
                github_url=github_url,
            )
        m = RE_GH_METADATA.search(s)
        if m is not None:
            return PullRequestResolved(
                owner=m.group("owner"),
                repo=m.group("repo"),
                number=GitHubNumber(int(m.group("number"))),
                github_url=github_url,
            )
        return None


@dataclass
class Diff:
    """
    An abstract representation of a diff.  Typically represents git commits,
    but we may also virtually be importing diffs from other VCSes, hence
    the agnosticism.
    """

    # Title of the diff
    title: str

    # Detailed description of the diff.  Includes the title.
    summary: str

    # Unique identifier representing the commit in question (may be a
    # Git/Mercurial commit hash; the important thing is that it can be
    # used as a unique identifier.)
    oid: str

    # Unique identifier representing the commit in question, but it
    # is *invariant* to changes in commit message / summary.  In Git,
    # a valid identifier would be the tree hash of the commit (rather
    # than the commit hash itself); in Phabricator it could be the
    # version of the diff.
    #
    # It is OK for this source id to wobble even if the tree stays the
    # same.  This simply means we will think there are changes even
    # if there aren't any, which should be safe (but just generate
    # annoying updates).  What we would like is for the id to quiesce:
    # if you didn't rebase your hg rev, the source id is guaranteed to
    # be the same.
    source_id: str

    # The contents of 'Pull-Request'.  This is None for
    # diffs that haven't been submitted by ghstack.  For BC reasons,
    # this also accepts gh-metadata.
    pull_request_resolved: Optional[PullRequestResolved]

    # A git tree hash that represents the contents of this diff, if it
    # were applied in Git.
    #
    # TODO: Constructing these tree hashes if they're not already in Git
    # is a somewhat involved process, as you have to actually construct
    # the git tree object (it's not guaranteed to exist already).  I'm
    # offloading this work onto the ghimport/ghexport tools.
    tree: GitTreeHash

    # The name and email of the author, used so we can preserve
    # authorship information when constructing a rebased commit
    author_name: Optional[str]
    author_email: Optional[str]

    # If this isn't actually a diff; it's a boundary commit (not part
    # of the stack) that we've parsed for administrative purposes
    boundary: bool
