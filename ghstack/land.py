#!/usr/bin/env python3

import ghstack.shell
import ghstack.github
import ghstack.github_utils
import ghstack.git
from ghstack.typing import GitCommitHash

import logging
import re


def lookup_pr_to_orig_ref(github: ghstack.github.GitHubEndpoint, owner: str, name: str, number: int) -> str:
    pr_result = github.graphql("""
        query ($owner: String!, $name: String!, $number: Int!) {
            repository(name: $name, owner: $owner) {
                pullRequest(number: $number) {
                    headRefName
                }
            }
        }
    """, owner=owner, name=name, number=number)
    head_ref = pr_result["data"]["repository"]["pullRequest"]["headRefName"]
    assert isinstance(head_ref, str)
    orig_ref = re.sub(r'/head$', '/orig', head_ref)
    if orig_ref == head_ref:
        logging.warning("The ref {} doesn't look like a ghstack reference".format(head_ref))
    return orig_ref


def main(pull_request: str,
         github: ghstack.github.GitHubEndpoint,
         sh: ghstack.shell.Shell,
         github_url: str) -> None:

    # We land the entire stack pointed to by a URL.
    # Local state is ignored; PR is source of truth
    # Furthermore, the parent commits of PR are ignored: we always
    # take the canonical version of the patch from any given pr

    params = ghstack.github_utils.parse_pull_request(pull_request)
    orig_ref = lookup_pr_to_orig_ref(github, **params)

    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    # Get up-to-date
    sh.git("fetch", "origin")
    remote_orig_ref = "origin/" + orig_ref
    base = GitCommitHash(sh.git("merge-base", "origin/master", remote_orig_ref))

    # compute the stack of commits in chronological order (does not
    # include base)
    stack = ghstack.git.parse_header(
        sh.git("rev-list", "--reverse", "--header", "^" + base, remote_orig_ref),
        github_url=github_url,
    )

    # Switch working copy
    try:
        prev_ref = sh.git("symbolic-ref", "--short", "HEAD")
    except RuntimeError:
        prev_ref = sh.git("rev-parse", "HEAD")

    # If this fails, we don't have to reset
    sh.git("checkout", "origin/master")

    try:
        # Compute the metadata for each commit
        stack_orig_refs = []
        for s in stack:
            pr_resolved = s.pull_request_resolved
            # We got this from GitHub, this better not be corrupted
            assert pr_resolved is not None

            stack_orig_refs.append(lookup_pr_to_orig_ref(
                github,
                owner=pr_resolved.owner,
                name=pr_resolved.repo,
                number=pr_resolved.number))

        # OK, actually do the land now
        for sref in stack_orig_refs:
            try:
                sh.git("cherry-pick", "origin/" + sref)
            except BaseException:
                sh.git("cherry-pick", "--abort")
                raise

        # All good! Push!
        sh.git("push", "origin", "HEAD:refs/heads/master")

    finally:
        sh.git("checkout", prev_ref)
