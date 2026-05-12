#!/usr/bin/env python3

import asyncio
import logging
import re
from typing import Iterable

import ghstack.github
import ghstack.github_utils
import ghstack.shell


async def _fetch_refs(
    sh: ghstack.shell.Shell, *, remote_name: str, refs: Iterable[str]
) -> None:
    refspecs = [
        f"+refs/heads/{ref}:refs/remotes/{remote_name}/{ref}"
        for ref in sorted(set(refs))
    ]
    await sh.agit("fetch", "--prune", remote_name, *refspecs)


async def main(
    pull_request: str,
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    remote_name: str,
    same_base: bool = False,
) -> None:

    params = await ghstack.github_utils.parse_pull_request(
        pull_request, sh=sh, remote_name=remote_name
    )
    head_ref_task = asyncio.ensure_future(github.get_head_ref(**params))

    if same_base:
        repo_info_task = asyncio.ensure_future(
            ghstack.github_utils.get_github_repo_info(
                github=github,
                sh=sh,
                repo_owner=params["owner"],
                repo_name=params["name"],
                github_url=params["github_url"],
                remote_name=remote_name,
            )
        )
        head_ref, repo_info = await asyncio.gather(head_ref_task, repo_info_task)
    else:
        head_ref = await head_ref_task
        repo_info = None

    orig_ref = re.sub(r"/head$", "/orig", head_ref)
    if orig_ref == head_ref:
        logging.warning(
            "The ref {} doesn't look like a ghstack reference".format(head_ref)
        )

    # TODO: Handle remotes correctly too (so this subsumes hub)

    # If --same-base is specified, check if checkout would change the merge-base
    if same_base:
        assert repo_info is not None
        default_branch = repo_info["default_branch"]
        default_branch_ref = f"{remote_name}/{default_branch}"

        # Get current merge-base with default branch
        current_base = await sh.agit("merge-base", default_branch_ref, "HEAD")
    else:
        current_base = None
        default_branch_ref = None

    refs_to_fetch = [orig_ref]
    if same_base:
        assert repo_info is not None
        refs_to_fetch.append(repo_info["default_branch"])
    await _fetch_refs(sh, remote_name=remote_name, refs=refs_to_fetch)

    # If --same-base is specified, check what the new merge-base would be
    if same_base:
        assert default_branch_ref is not None
        assert current_base is not None
        target_ref = remote_name + "/" + orig_ref
        new_base = await sh.agit("merge-base", default_branch_ref, target_ref)

        if current_base != new_base:
            raise RuntimeError(
                f"Checkout would change merge-base from {current_base[:8]} to {new_base[:8]}, "
                f"aborting due to --same-base flag"
            )

    await sh.agit("checkout", remote_name + "/" + orig_ref)
