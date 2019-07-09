#!/usr/bin/env python3

import ghstack.shell
import ghstack.github
import ghstack.github_utils

import logging
import re


def main(pull_request: str,
         github: ghstack.github.GitHubEndpoint,
         sh: ghstack.shell.Shell,
         ) -> None:

    params = ghstack.github_utils.parse_pull_request(pull_request)
    pr_result = github.graphql("""
        query ($owner: String!, $name: String!, $number: Int!) {
            repository(name: $name, owner: $owner) {
                pullRequest(number: $number) {
                    headRefName
                }
            }
        }
    """, **params)
    head_ref = pr_result["data"]["repository"]["pullRequest"]["headRefName"]
    orig_ref = re.sub(r'/head$', '/orig', head_ref)
    if orig_ref == head_ref:
        logging.warning("The ref {} doesn't look like a ghstack reference".format(head_ref))

    # TODO: Handle remotes correctly too (so this subsumes hub)

    sh.git("fetch", "origin")
    sh.git("checkout", "origin/" + orig_ref)
