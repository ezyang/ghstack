#!/usr/bin/env python3

import logging
import re

import ghstack.github
import ghstack.github_utils
import ghstack.shell


def main(
    pull_request: str,
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    remote_name: str,
    same_base: bool = False,
) -> None:

    params = ghstack.github_utils.parse_pull_request(
        pull_request, sh=sh, remote_name=remote_name
    )
    head_ref = github.get_head_ref(**params)
    orig_ref = re.sub(r"/head$", "/orig", head_ref)
    if orig_ref == head_ref:
        logging.warning(
            "The ref {} doesn't look like a ghstack reference".format(head_ref)
        )

    # TODO: Handle remotes correctly too (so this subsumes hub)

    # If --same-base is specified, check if checkout would change the merge-base
    if same_base:
        # Get the default branch name from the repo
        repo_info = ghstack.github_utils.get_github_repo_info(
            github=github,
            sh=sh,
            repo_owner=params["owner"],
            repo_name=params["name"],
            github_url=params["github_url"],
            remote_name=remote_name,
        )
        default_branch = repo_info["default_branch"]
        default_branch_ref = f"{remote_name}/{default_branch}"

        # Get current merge-base with default branch
        current_base = sh.git("merge-base", default_branch_ref, "HEAD")
    else:
        current_base = None
        default_branch_ref = None

    sh.git("fetch", "--prune", remote_name)

    # If --same-base is specified, check what the new merge-base would be
    if same_base:
        target_ref = remote_name + "/" + orig_ref
        new_base = sh.git("merge-base", default_branch_ref, target_ref)

        if current_base != new_base:
            raise RuntimeError(
                f"Checkout would change merge-base from {current_base[:8]} to {new_base[:8]}, "
                f"aborting due to --same-base flag"
            )

    sh.git("checkout", remote_name + "/" + orig_ref)
