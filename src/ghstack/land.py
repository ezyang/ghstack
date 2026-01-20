#!/usr/bin/env python3

import logging
import re
from typing import List, Tuple

import ghstack.git
import ghstack.github
import ghstack.github_utils
import ghstack.shell
from ghstack.diff import PullRequestResolved
from ghstack.types import GitCommitHash


def lookup_pr_to_orig_ref_and_closed(
    github: ghstack.github.GitHubEndpoint, *, owner: str, name: str, number: int
) -> Tuple[str, bool]:
    pr_result = github.graphql(
        """
        query ($owner: String!, $name: String!, $number: Int!) {
            repository(name: $name, owner: $owner) {
                pullRequest(number: $number) {
                    headRefName
                    closed
                }
            }
        }
    """,
        owner=owner,
        name=name,
        number=number,
    )
    pr = pr_result["data"]["repository"]["pullRequest"]
    head_ref = pr["headRefName"]
    closed = pr["closed"]
    assert isinstance(head_ref, str)
    orig_ref = re.sub(r"/head$", "/orig", head_ref)
    if orig_ref == head_ref:
        raise RuntimeError(
            "The ref {} doesn't look like a ghstack reference".format(head_ref)
        )
    return orig_ref, closed


def main(
    pull_request: str,
    remote_name: str,
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    github_url: str,
    *,
    force: bool = False,
) -> None:

    # We land the entire stack pointed to by a URL.
    # Local state is ignored; PR is source of truth
    # Furthermore, the parent commits of PR are ignored: we always
    # take the canonical version of the patch from any given pr

    params = ghstack.github_utils.parse_pull_request(
        pull_request, sh=sh, remote_name=remote_name
    )
    default_branch = ghstack.github_utils.get_github_repo_info(
        github=github,
        sh=sh,
        repo_owner=params["owner"],
        repo_name=params["name"],
        github_url=github_url,
        remote_name=remote_name,
    )["default_branch"]

    needs_force = False
    try:
        protection = github.get(
            f"repos/{params['owner']}/{params['name']}/branches/{default_branch}/protection"
        )
        if not protection["allow_force_pushes"]["enabled"]:
            raise RuntimeError(
                """\
Default branch {default_branch} is protected, and doesn't allow force pushes.
ghstack land does not work.  You will not be able to land your ghstack; please
resubmit your PRs using the normal pull request flow.

See https://github.com/ezyang/ghstack/issues/50 for more details, or
to complain to the ghstack authors."""
            )
        else:
            needs_force = True
    except ghstack.github.NotFoundError:
        pass

    orig_ref, closed = lookup_pr_to_orig_ref_and_closed(
        github,
        owner=params["owner"],
        name=params["name"],
        number=params["number"],
    )

    if closed:
        raise RuntimeError("PR is already closed, cannot land it!")

    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    # Get up-to-date
    sh.git("fetch", "--prune", remote_name)
    remote_orig_ref = remote_name + "/" + orig_ref
    base = GitCommitHash(
        sh.git("merge-base", f"{remote_name}/{default_branch}", remote_orig_ref)
    )

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
    sh.git("checkout", f"{remote_name}/{default_branch}")

    try:
        # Compute the metadata for each commit
        stack_orig_refs: List[Tuple[str, PullRequestResolved]] = []
        for s in stack:
            pr_resolved = s.pull_request_resolved
            # We got this from GitHub, this better not be corrupted
            assert pr_resolved is not None

            ref, closed = lookup_pr_to_orig_ref_and_closed(
                github,
                owner=pr_resolved.owner,
                name=pr_resolved.repo,
                number=pr_resolved.number,
            )
            if closed and not force:
                continue
            stack_orig_refs.append((ref, pr_resolved))

        # OK, actually do the land now
        for orig_ref, _ in stack_orig_refs:
            try:
                sh.git("cherry-pick", f"{remote_name}/{orig_ref}")
            except BaseException:
                sh.git("cherry-pick", "--abort")
                raise

        # All good! Push!
        maybe_force_arg = []
        if needs_force:
            maybe_force_arg = ["--force-with-lease"]
        sh.git(
            "push", *maybe_force_arg, remote_name, f"HEAD:refs/heads/{default_branch}"
        )

        # Advance base to head to "close" the PR for all PRs.
        # This happens after the cherry-pick and push, because the cherry-picks
        # can fail (merge conflict) and the push can also fail (race condition)

        # TODO: It might be helpful to advance orig to reflect the true
        # state of upstream at the time we are doing the land, and then
        # directly *merge* head into base, so that the PR accurately
        # reflects what we ACTUALLY merged to master, as opposed to
        # this synthetic thing I'm doing right now just to make it look
        # like the PR got closed

        for orig_ref, pr_resolved in stack_orig_refs:
            # TODO: regex here so janky
            base_ref = re.sub(r"/orig$", "/base", orig_ref)
            head_ref = re.sub(r"/orig$", "/head", orig_ref)
            sh.git(
                "push", remote_name, f"{remote_name}/{head_ref}:refs/heads/{base_ref}"
            )
            github.notify_merged(pr_resolved)

        # Delete the branches
        for orig_ref, _ in stack_orig_refs:
            # TODO: regex here so janky
            base_ref = re.sub(r"/orig$", "/base", orig_ref)
            head_ref = re.sub(r"/orig$", "/head", orig_ref)
            try:
                sh.git("push", remote_name, "--delete", orig_ref, base_ref)
            except RuntimeError:
                # Whatever, keep going
                logging.warning("Failed to delete branch, continuing", exc_info=True)
            # Try deleting head_ref separately since often after it's merged it doesn't exist anymore
            try:
                sh.git("push", remote_name, "--delete", head_ref)
            except RuntimeError:
                # Whatever, keep going
                logging.warning("Failed to delete branch, continuing", exc_info=True)

    finally:
        sh.git("checkout", prev_ref)
