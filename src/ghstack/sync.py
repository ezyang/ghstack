#!/usr/bin/env python3

import logging
import re
import textwrap
from typing import Optional

import ghstack.git
import ghstack.github
import ghstack.github_utils
import ghstack.gpg_sign
import ghstack.shell
import ghstack.trailers
from ghstack.types import GitCommitHash

RE_STACK = re.compile(r"Stack.*:\r?\n(\* [^\r\n]+\r?\n)+")


async def main(
    *,
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None,
    github_url: str,
    remote_name: str,
) -> GitCommitHash:
    repo_info = await ghstack.github_utils.get_github_repo_info(
        github=github,
        sh=sh,
        repo_owner=repo_owner,
        repo_name=repo_name,
        github_url=github_url,
        remote_name=remote_name,
    )
    repo_owner = repo_info["name_with_owner"]["owner"]
    repo_name = repo_info["name_with_owner"]["name"]
    default_branch = repo_info["default_branch"]

    base = GitCommitHash(
        await sh.agit("merge-base", f"{remote_name}/{default_branch}", "HEAD")
    )

    stack = ghstack.git.split_header(
        await sh.agit("rev-list", "--reverse", "--header", "^" + base, "HEAD")
    )

    if not stack:
        raise RuntimeError("No commits in stack")

    head = base
    rewriting = False

    for s in stack:
        diff = ghstack.git.convert_header(s, github_url)

        if diff.pull_request_resolved is None:
            if not rewriting:
                head = s.commit_id
            else:
                head = GitCommitHash(
                    await sh.agit(
                        "commit-tree",
                        *(await ghstack.gpg_sign.gpg_args_if_necessary(sh)),
                        s.tree,
                        "-p",
                        head,
                        input=s.commit_msg,
                    )
                )
            continue

        pr = diff.pull_request_resolved
        assert pr.owner == repo_owner
        assert pr.repo == repo_name

        pr_result = await github.graphql(
            """
            query ($owner: String!, $name: String!, $number: Int!) {
                repository(owner: $owner, name: $name) {
                    pullRequest(number: $number) {
                        body
                        title
                    }
                }
            }
            """,
            owner=repo_owner,
            name=repo_name,
            number=pr.number,
        )
        r = pr_result["data"]["repository"]["pullRequest"]

        pr_title = r["title"]
        pr_body = r["body"]

        pr_body = RE_STACK.sub("", pr_body)
        pr_body = pr_body.strip()

        subject, body, trailers = ghstack.trailers.parse_message(s.commit_msg)

        new_subject = pr_title

        new_body = pr_body

        new_msg = new_subject
        if new_body:
            new_msg += "\n\n" + new_body
        if trailers:
            new_msg += "\n\n" + trailers

        if new_msg == s.commit_msg and not rewriting:
            head = s.commit_id
            continue

        rewriting = True
        logging.debug("-- old commit_msg:\n%s", textwrap.indent(s.commit_msg, "   "))
        logging.debug("-- new commit_msg:\n%s", textwrap.indent(new_msg, "   "))
        head = GitCommitHash(
            await sh.agit(
                "commit-tree",
                *(await ghstack.gpg_sign.gpg_args_if_necessary(sh)),
                s.tree,
                "-p",
                head,
                input=new_msg,
            )
        )

    if rewriting:
        await sh.agit("reset", "--soft", head)
        logging.info(
            "\nCommit messages successfully synced from PR descriptions!\n\n"
            "To undo this operation, run:\n\n"
            "    git reset --soft %s\n",
            s.commit_id,
        )
    else:
        logging.info("\nCommit messages already up to date.")

    return head
