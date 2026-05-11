#!/usr/bin/env python3

import asyncio
import sys
from typing import List, Optional, Tuple

import ghstack.diff
import ghstack.github
import ghstack.github_utils
import ghstack.shell


async def _resolve_refs(
    *,
    github: ghstack.github.GitHubEndpoint,
    params: ghstack.github_utils.GitHubPullRequestParams,
) -> Tuple[str, str]:
    pr_result = await github.graphql(
        """
        query ($owner: String!, $name: String!, $number: Int!) {
            repository(name: $name, owner: $owner) {
                pullRequest(number: $number) {
                    headRefName
                    baseRefName
                }
            }
        }
    """,
        **params,
    )
    pr = pr_result["data"]["repository"]["pullRequest"]
    return pr["headRefName"], pr["baseRefName"]


async def main(
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    remote_name: str,
    github_url: str,
    args: List[str],
    pull_request: Optional[str] = None,
) -> None:
    if pull_request is not None:
        # Explicit PR: fetch from remote and show what's there.  HEAD isn't
        # involved; no synthesized pending-changes commit.
        params = await ghstack.github_utils.parse_pull_request(
            pull_request, sh=sh, remote_name=remote_name
        )
        await sh.agit("fetch", "--prune", remote_name)
        show_pending = False
    else:
        # Infer PR from HEAD's Pull-Request trailer; show the log relative
        # to the local understanding of the remote state (no fetch).
        commit_msg = await sh.agit("log", "-1", "--format=%B", "HEAD")
        pr = ghstack.diff.PullRequestResolved.search(commit_msg, github_url)
        if pr is None:
            raise RuntimeError(
                "HEAD commit is not associated with a ghstack pull request "
                "(no Pull-Request trailer found). Check out the commit for the "
                "PR you want to log, or pass the PR explicitly."
            )
        params = {
            "github_url": pr.github_url,
            "owner": pr.owner,
            "name": pr.repo,
            "number": pr.number,
        }
        show_pending = True

    head_ref, base_ref = await _resolve_refs(github=github, params=params)

    remote_head = f"{remote_name}/{head_ref}"
    remote_base = f"{remote_name}/{base_ref}"

    tip = remote_head
    if show_pending:
        # If the local HEAD tree differs from the remote head tree, synthesize
        # a disposable commit on top of the remote head so pending changes
        # show up as the newest commit in `git log`.
        local_tree = await sh.agit("rev-parse", "HEAD^{tree}")
        remote_tree = await sh.agit("rev-parse", f"{remote_head}^{{tree}}")
        if local_tree != remote_tree:
            tip = await sh.agit(
                "commit-tree",
                local_tree,
                "-p",
                remote_head,
                input="Local pending changes (not yet submitted)\n",
            )

    # ^remote_base restricts the walk to the head chain (each head commit is a
    # merge of the previous head and a base-update commit; excluding everything
    # reachable from the base ref drops the base-update side).
    #
    # --diff-merges=remerge replays the merge and diffs the stored tree
    # against the auto-merge result, which for ghstack isolates just the
    # user's code edits from the base-update changes that got folded in.
    # This only affects merge commits (non-merges still need -p).  Requires
    # git 2.35+.  Users who prefer a portable alternative can pass
    # --diff-merges=cc to override.
    log_args = ["--diff-merges=remerge", tip]
    if await sh.agit("rev-parse", "--verify", "--quiet", remote_base, exitcode=True):
        log_args.append(f"^{remote_base}")
    log_args.extend(args)

    if sys.stdout.isatty():
        # Let git manage its own pager.
        proc = await asyncio.create_subprocess_exec("git", "log", *log_args, cwd=sh.cwd)
        await proc.wait()
    else:
        # In test/piped contexts, capture and write to sys.stdout so the
        # caller can intercept it.
        out = await sh.agit("log", *log_args)
        if out:
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
