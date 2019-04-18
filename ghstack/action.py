#!/usr/bin/env python3

import ghstack.shell
import ghstack.github

from typing import Optional
import re
import logging


RE_PR_URL = re.compile(r'^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>[0-9]+)/?$')


def main(pull_request: str,
         github: ghstack.github.GitHubEndpoint,
         sh: Optional[ghstack.shell.Shell] = None,
         close: bool = False,
         ) -> None:
    m = RE_PR_URL.match(pull_request)
    if not m:
        raise RuntimeError("Did not understand PR argument.  PR must be URL")

    owner = m.group("owner")
    repo = m.group("repo")
    number = int(m.group("number"))

    pr_result = github.graphql("""
        query ($owner: String!, $repo: String!, $number: Int!) {
            repository(name: $repo, owner: $owner) {
                pullRequest(number: $number) {
                    id
                }
            }
        }
    """, owner=owner, repo=repo, number=number)
    pr_id = pr_result["data"]["repository"]["pullRequest"]["id"]

    if close:
        logging.info("Closing {}/{}#{}".format(owner, repo, number))
        github.graphql("""
            mutation ($input: ClosePullRequestInput!) {
                closePullRequest(input: $input) {
                    clientMutationId
                }
            }
        """, input={"pullRequestId": pr_id, "clientMutationId": "A"})
