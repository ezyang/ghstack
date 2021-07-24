#!/usr/bin/env python3

import logging
import re
import textwrap
from typing import List, Optional, Set

import ghstack.diff
import ghstack.git
import ghstack.github
import ghstack.github_utils
import ghstack.gpg_sign
import ghstack.shell
from ghstack.types import GitCommitHash

RE_GHSTACK_SOURCE_ID = re.compile(r'^ghstack-source-id: (.+)\n?', re.MULTILINE)


def main(*,
         commits: Optional[List[str]] = None,
         github: ghstack.github.GitHubEndpoint,
         sh: Optional[ghstack.shell.Shell] = None,
         repo_owner: Optional[str] = None,
         repo_name: Optional[str] = None,
         github_url: str,
         remote_name: str) -> GitCommitHash:
    # If commits is empty, we unlink the entire stack
    #
    # For now, we only process commits on our current
    # stack, because we have no way of knowing how to
    # "restack" for other commits.

    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    default_branch = ghstack.github_utils.get_github_repo_info(
        github=github,
        sh=sh,
        repo_owner=repo_owner,
        repo_name=repo_name,
        github_url=github_url,
        remote_name=remote_name,
    )["default_branch"]

    # Parse the commits
    parsed_commits: Optional[Set[GitCommitHash]] = None
    if commits:
        parsed_commits = set()
        for c in commits:
            parsed_commits.add(GitCommitHash(sh.git("rev-parse", c)))

    base = GitCommitHash(sh.git("merge-base", f"{remote_name}/{default_branch}", "HEAD"))

    # compute the stack of commits in chronological order (does not
    # include base)
    stack = ghstack.git.split_header(
        sh.git("rev-list", "--reverse", "--header", "^" + base, "HEAD"))

    # sanity check the parsed_commits
    if parsed_commits is not None:
        stack_commits = set()
        for s in stack:
            stack_commits.add(s.commit_id())
        invalid_commits = parsed_commits - stack_commits
        if invalid_commits:
            raise RuntimeError(
                "unlink can only process commits which are on the "
                "current stack; these commits are not:\n{}"
                .format("\n".join(invalid_commits)))

    # Run the interactive rebase.  Don't start rewriting until we
    # hit the first commit that needs it.
    head = base
    rewriting = False

    for s in stack:
        commit_id = s.commit_id()
        should_unlink = parsed_commits is None or commit_id in parsed_commits
        if not rewriting and not should_unlink:
            # Advance HEAD without reconstructing commit
            head = commit_id
            continue

        rewriting = True
        commit_msg = s.commit_msg()
        logging.debug("-- commit_msg:\n{}".format(textwrap.indent(commit_msg, '   ')))
        if should_unlink:
            commit_msg = RE_GHSTACK_SOURCE_ID.sub(
                '',
                ghstack.diff.re_pull_request_resolved_w_sp(github_url).sub('', commit_msg)
            )
            logging.debug("-- edited commit_msg:\n{}".format(
                textwrap.indent(commit_msg, '   ')))
        head = GitCommitHash(sh.git(
            "commit-tree",
            *ghstack.gpg_sign.gpg_args_if_necessary(sh),
            s.tree(),
            "-p", head,
            input=commit_msg))

    sh.git('reset', '--soft', head)

    logging.info("""
Diffs successfully unlinked!

To undo this operation, run:

    git reset --soft {}
""".format(s.commit_id()))

    return head
