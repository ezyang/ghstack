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

    sh.git("fetch", "--prune", remote_name)
    sh.git("checkout", remote_name + "/" + orig_ref)
