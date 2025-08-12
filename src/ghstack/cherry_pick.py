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
    stack: bool = False,
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

    sh.git("fetch", "--prune", remote_name)

    if stack:
        # Cherry-pick the entire stack from merge-base to the commit
        remote_orig_ref = remote_name + "/" + orig_ref

        # Find the merge-base with the main branch
        repo_info = ghstack.github_utils.get_github_repo_info(
            github=github,
            sh=sh,
            github_url=params["github_url"],
            remote_name=remote_name,
        )
        main_branch = f"{remote_name}/{repo_info['default_branch']}"

        # Get merge-base between the commit and main branch
        merge_base = sh.git("merge-base", main_branch, remote_orig_ref).strip()

        # Get all commits from merge-base to the target commit
        commit_list = (
            sh.git("rev-list", "--reverse", f"{merge_base}..{remote_orig_ref}")
            .strip()
            .split("\n")
        )

        if not commit_list or commit_list == [""]:
            raise RuntimeError("No commits found to cherry-pick in the specified range")

        logging.info(f"Cherry-picking {len(commit_list)} commits from stack")
        for commit in commit_list:
            sh.git("cherry-pick", commit)
            logging.info(f"Cherry-picked {commit}")
    else:
        # Cherry-pick just the single commit
        remote_orig_ref = remote_name + "/" + orig_ref
        sh.git("cherry-pick", remote_orig_ref)
        logging.info(f"Cherry-picked {orig_ref}")
