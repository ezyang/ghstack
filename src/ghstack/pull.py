#!/usr/bin/env python3

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import ghstack.checkout
import ghstack.diff
import ghstack.github
import ghstack.github_utils
import ghstack.shell
import ghstack.submit


async def _run_git_for_status(
    sh: ghstack.shell.Shell, args: List[str]
) -> Tuple[int, str]:
    ghstack.shell.log_command(["git", *args])
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=sh.cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    assert proc.returncode is not None
    return proc.returncode, out.decode(errors="backslashreplace")


async def _resolve_params(
    *,
    pull_request: Optional[str],
    github_url: str,
    sh: ghstack.shell.Shell,
    remote_name: str,
) -> ghstack.github_utils.GitHubPullRequestParams:
    if pull_request is not None:
        return await ghstack.github_utils.parse_pull_request(
            pull_request, sh=sh, remote_name=remote_name
        )

    commit_msg = await sh.agit("log", "-1", "--format=%B", "HEAD")
    pr = ghstack.diff.PullRequestResolved.search(commit_msg, github_url)
    if pr is None:
        raise RuntimeError(
            "HEAD commit is not associated with a ghstack pull request "
            "(no Pull-Request trailer found). Check out the commit for the "
            "PR you want to pull, or pass the PR explicitly."
        )
    return {
        "github_url": pr.github_url,
        "owner": pr.owner,
        "name": pr.repo,
        "number": pr.number,
    }


def _replace_source_id(commit_msg: str, source_id: str) -> str:
    line = f"ghstack-source-id: {source_id}\n"
    if ghstack.submit.RE_GHSTACK_SOURCE_ID.search(commit_msg) is None:
        return commit_msg.rstrip() + "\n" + line
    return ghstack.submit.RE_GHSTACK_SOURCE_ID.sub(line, commit_msg)


async def _state_path(sh: ghstack.shell.Shell) -> str:
    path = await sh.agit("rev-parse", "--git-path", "GHSTACK_PULL")
    return path if os.path.isabs(path) else sh.abspath(path)


async def _read_state(sh: ghstack.shell.Shell) -> Dict[str, Any]:
    path = await _state_path(sh)
    if not os.path.exists(path):
        raise RuntimeError("No ghstack pull conflict in progress.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def _write_state(sh: ghstack.shell.Shell, state: Dict[str, Any]) -> None:
    path = await _state_path(sh)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)
        f.write("\n")


async def _clear_state(sh: ghstack.shell.Shell) -> None:
    path = await _state_path(sh)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


async def _find_head_with_tree(
    sh: ghstack.shell.Shell, *, remote_head: str, tree: str
) -> str:
    log = await sh.agit("log", "--first-parent", "--format=%H %T", remote_head)
    for line in log.splitlines():
        commit, commit_tree = line.split()
        if commit_tree == tree:
            return commit
    raise RuntimeError(
        "Could not find the previously checked out ghstack head commit. "
        "The local ghstack-source-id does not appear in the remote head history."
    )


async def _is_worktree_clean(sh: ghstack.shell.Shell) -> bool:
    return bool(
        await sh.agit("diff", "--quiet", exitcode=True)
        and await sh.agit("diff", "--cached", "--quiet", exitcode=True)
    )


async def _finish_pull(sh: ghstack.shell.Shell, state: Dict[str, Any]) -> None:
    unmerged = await sh.agit("ls-files", "-u")
    if unmerged:
        raise RuntimeError(
            "There are still unresolved merge conflicts. Resolve them and run "
            "`ghstack pull --continue` again."
        )
    if not await sh.agit("diff", "--quiet", exitcode=True):
        raise RuntimeError(
            "There are unstaged changes. Stage the resolved files with `git add`, "
            "then run `ghstack pull --continue` again."
        )

    merged_tree = await sh.agit("write-tree")
    pulled_commit_msg = _replace_source_id(
        state["commit_msg"], state["remote_source_id"]
    )
    pulled_orig = await sh.agit(
        "commit-tree",
        "-p",
        state["parent"],
        merged_tree,
        input=pulled_commit_msg,
        env={
            "GIT_AUTHOR_NAME": state["author_name"],
            "GIT_AUTHOR_EMAIL": state["author_email"],
        },
    )
    await sh.agit("checkout", pulled_orig)
    await _clear_state(sh)


async def main(
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    remote_name: str,
    github_url: str,
    pull_request: Optional[str] = None,
    continue_: bool = False,
) -> None:
    if continue_:
        await _finish_pull(sh, await _read_state(sh))
        return

    params = await _resolve_params(
        pull_request=pull_request,
        github_url=github_url,
        sh=sh,
        remote_name=remote_name,
    )
    head_ref = await github.get_head_ref(**params)
    orig_ref = re.sub(r"/head$", "/orig", head_ref)
    if orig_ref == head_ref:
        raise RuntimeError(f"The ref {head_ref} doesn't look like a ghstack reference")

    await ghstack.checkout._fetch_refs(
        sh, remote_name=remote_name, refs=[head_ref, orig_ref]
    )
    remote_head = f"{remote_name}/{head_ref}"
    remote_orig = f"{remote_name}/{orig_ref}"

    if await sh.agit("merge-base", "--is-ancestor", "HEAD", remote_orig, exitcode=True):
        await sh.agit("checkout", remote_orig)
        await _clear_state(sh)
        return

    state_path = await _state_path(sh)
    if os.path.exists(state_path):
        raise RuntimeError(
            "A ghstack pull conflict is already in progress. Resolve it and run "
            "`ghstack pull --continue`."
        )

    if not await _is_worktree_clean(sh):
        raise RuntimeError(
            "Working tree has uncommitted changes; commit or stash them first."
        )

    local_commit_msg = await sh.agit("log", "-1", "--format=%B", "HEAD")
    m_local_source_id = ghstack.submit.RE_GHSTACK_SOURCE_ID.search(local_commit_msg)
    if m_local_source_id is None:
        raise RuntimeError(
            "HEAD has no ghstack-source-id trailer, so ghstack cannot determine "
            "which remote head version your local changes are based on."
        )
    local_source_id = m_local_source_id.group(1)

    old_head = await _find_head_with_tree(
        sh, remote_head=remote_head, tree=local_source_id
    )
    local_tree = await sh.agit("rev-parse", "HEAD^{tree}")
    local_imputed_head = await sh.agit(
        "commit-tree",
        "-p",
        old_head,
        local_tree,
        input="Local changes for ghstack pull\n\n[ghstack-poisoned]\n",
    )

    returncode, merge_tree_output = await _run_git_for_status(
        sh,
        ["merge-tree", "--write-tree", "--messages", remote_head, local_imputed_head],
    )
    merged_tree = merge_tree_output.splitlines()[0] if returncode == 0 else None

    remote_orig_commit_msg = await sh.agit("log", "-1", "--format=%B", remote_orig)
    m_remote_source_id = ghstack.submit.RE_GHSTACK_SOURCE_ID.search(
        remote_orig_commit_msg
    )
    remote_source_id = (
        m_remote_source_id.group(1)
        if m_remote_source_id is not None
        else await sh.agit("rev-parse", f"{remote_orig}^{{tree}}")
    )
    remote_orig_parent = await sh.agit("rev-parse", f"{remote_orig}^")

    author_name = await sh.agit("log", "-1", "--format=%an", "HEAD")
    author_email = await sh.agit("log", "-1", "--format=%ae", "HEAD")
    state = {
        "parent": remote_orig_parent,
        "remote_source_id": remote_source_id,
        "commit_msg": local_commit_msg,
        "author_name": author_name,
        "author_email": author_email,
    }

    if returncode != 0:
        await _write_state(sh, state)
        recursive_returncode, recursive_output = await _run_git_for_status(
            sh, ["merge-recursive", old_head, "--", local_imputed_head, remote_head]
        )
        if recursive_returncode == 0:
            await _finish_pull(sh, state)
            return
        raise RuntimeError(
            "Automatic ghstack pull merge failed. Resolve the conflicts, then run "
            "`ghstack pull --continue`.\n" + recursive_output
        )

    pulled_commit_msg = _replace_source_id(local_commit_msg, remote_source_id)
    assert merged_tree is not None
    pulled_orig = await sh.agit(
        "commit-tree",
        "-p",
        remote_orig_parent,
        merged_tree,
        input=pulled_commit_msg,
        env={
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
        },
    )
    await sh.agit("checkout", pulled_orig)
