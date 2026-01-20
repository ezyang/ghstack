#!/usr/bin/env python3

import logging
import re
from typing import Dict, List, Optional, Tuple

import click

import ghstack.diff
import ghstack.github
import ghstack.github_utils
import ghstack.shell
from ghstack.types import GhNumber


# Regex to match ghstack branch names: gh/{username}/{number}/{kind}
RE_GHSTACK_BRANCH = re.compile(
    r"^(?:refs/(?:heads|remotes/[^/]+)/)?gh/([^/]+)/([0-9]+)/(.+)$"
)


def parse_ghstack_branch(ref: str) -> Optional[Tuple[str, GhNumber, str]]:
    """
    Parse a ghstack branch reference.

    Returns (username, ghnum, kind) if it's a valid ghstack branch, None otherwise.
    """
    m = RE_GHSTACK_BRANCH.match(ref)
    if m:
        return (m.group(1), GhNumber(m.group(2)), m.group(3))
    return None


def get_pr_number_for_ghnum(
    sh: ghstack.shell.Shell,
    remote_name: str,
    username: str,
    ghnum: GhNumber,
    github_url: str,
) -> Optional[int]:
    """
    Get the GitHub PR number associated with a ghstack ghnum by reading the orig branch.

    Returns the PR number if found, None otherwise.
    """
    orig_ref = f"{remote_name}/gh/{username}/{ghnum}/orig"

    try:
        # Try to get the commit message from the orig branch
        commit_msg = sh.git("log", "-1", "--format=%B", orig_ref)
    except RuntimeError:
        # Branch doesn't exist or can't be read
        return None

    # Use ghstack's own PullRequestResolved.search() to find the PR
    # This handles all formats: "Pull Request resolved:", "Pull-Request-resolved:", "Pull-Request:"
    # as well as the legacy "gh-metadata:" format
    pr_resolved = ghstack.diff.PullRequestResolved.search(commit_msg, github_url)
    if pr_resolved is not None:
        return int(pr_resolved.number)

    return None


def find_pr_by_head_ref(
    github: ghstack.github.GitHubEndpoint,
    repo_owner: str,
    repo_name: str,
    head_ref: str,
) -> Optional[Tuple[int, bool]]:
    """
    Find a PR by its head ref name.

    Returns (pr_number, is_closed) if found, None if no PR exists for this head ref.
    Raises on API errors (fail loudly).
    """
    # Query for PRs with this head ref
    result = github.graphql(
        """
        query ($owner: String!, $name: String!, $headRefName: String!) {
            repository(name: $name, owner: $owner) {
                pullRequests(headRefName: $headRefName, first: 1) {
                    nodes {
                        number
                        closed
                    }
                }
            }
        }
        """,
        owner=repo_owner,
        name=repo_name,
        headRefName=head_ref,
    )
    prs = result["data"]["repository"]["pullRequests"]["nodes"]
    if not prs:
        return None
    pr = prs[0]
    return (pr["number"], pr["closed"])


def check_pr_closed(
    github: ghstack.github.GitHubEndpoint,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
) -> bool:
    """
    Check if a PR is closed.

    Returns True if the PR is closed, False if it's open.
    Raises if the PR doesn't exist or on API errors (fail loudly).
    """
    result = github.graphql(
        """
        query ($owner: String!, $name: String!, $number: Int!) {
            repository(name: $name, owner: $owner) {
                pullRequest(number: $number) {
                    closed
                }
            }
        }
        """,
        owner=repo_owner,
        name=repo_name,
        number=pr_number,
    )
    pr = result["data"]["repository"]["pullRequest"]
    if pr is None:
        # PR doesn't exist - treat as closed (it was deleted)
        return True
    return pr["closed"]


def main(
    *,
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    github_url: str,
    remote_name: str,
    dry_run: bool = False,
    clean_local: bool = False,
    username: Optional[str] = None,
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None,
    force: bool = False,
) -> List[str]:
    """
    Clean up orphan ghstack branches.

    An orphan branch is a ghstack-managed branch whose associated PR has been
    closed (either merged or manually closed).

    Args:
        github: GitHub API endpoint
        sh: Shell for executing git commands
        github_url: GitHub URL (e.g., 'github.com')
        remote_name: Name of the remote (e.g., 'origin')
        dry_run: If True, list branches without deleting
        clean_local: If True, also prune local tracking branches
        username: If provided, only clean branches for this user
        repo_owner: Repository owner (inferred from remote if not provided)
        repo_name: Repository name (inferred from remote if not provided)
        force: If True, skip confirmation prompt

    Returns:
        List of branch names that were deleted (or would be deleted in dry-run mode)
    """
    # Get repo info if not provided
    if repo_owner is None or repo_name is None:
        repo_info = ghstack.github_utils.get_github_repo_info(
            github=github,
            sh=sh,
            repo_owner=repo_owner,
            repo_name=repo_name,
            github_url=github_url,
            remote_name=remote_name,
        )
        repo_owner = repo_info["name_with_owner"]["owner"]
        repo_name = repo_info["name_with_owner"]["name"]

    # Fetch latest state from remote
    logging.info(f"Fetching from {remote_name}...")
    sh.git("fetch", "--prune", remote_name)

    # List all ghstack branches on the remote
    refs_output = sh.git(
        "for-each-ref",
        f"refs/remotes/{remote_name}/gh/",
        "--format=%(refname)",
    )

    if not refs_output.strip():
        logging.info("No ghstack branches found.")
        return []

    refs = refs_output.strip().split("\n")

    # Group branches by (username, ghnum)
    branches_by_ghnum: Dict[Tuple[str, GhNumber], List[str]] = {}

    for ref in refs:
        parsed = parse_ghstack_branch(ref)
        if parsed is None:
            continue

        branch_username, ghnum, kind = parsed

        # Filter by username if specified
        if username is not None and branch_username != username:
            continue

        key = (branch_username, ghnum)
        if key not in branches_by_ghnum:
            branches_by_ghnum[key] = []

        # Extract just the branch name without refs/remotes/{remote}/
        branch_name = f"gh/{branch_username}/{ghnum}/{kind}"
        branches_by_ghnum[key].append(branch_name)

    if not branches_by_ghnum:
        if username:
            logging.info(f"No ghstack branches found for user '{username}'.")
        else:
            logging.info("No ghstack branches found.")
        return []

    logging.info(f"Found {len(branches_by_ghnum)} ghstack PR(s) to check...")

    # Check which PRs are closed
    # Cache pr_number -> is_closed to handle multiple ghnums mapping to same PR
    pr_closed_cache: Dict[int, bool] = {}
    orphan_branches: List[str] = []

    for (branch_username, ghnum), branches in branches_by_ghnum.items():
        # Get the PR number from the orig branch
        pr_number = get_pr_number_for_ghnum(
            sh, remote_name, branch_username, ghnum, github_url
        )

        if pr_number is None:
            # Can't determine PR number from orig branch (missing or corrupted)
            # Try to find PR by querying GitHub for the head ref
            head_ref = f"gh/{branch_username}/{ghnum}/head"
            logging.info(
                f"Missing orig branch for gh/{branch_username}/{ghnum}, "
                f"querying GitHub by head ref..."
            )
            pr_info = find_pr_by_head_ref(github, repo_owner, repo_name, head_ref)

            if pr_info is None:
                # No PR exists for this head ref - truly orphan
                logging.info(
                    f"No PR found for gh/{branch_username}/{ghnum}, treating as orphan"
                )
                orphan_branches.extend(branches)
                continue

            pr_number, is_closed = pr_info
            pr_closed_cache[pr_number] = is_closed
            if is_closed:
                logging.info(
                    f"PR #{pr_number} (gh/{branch_username}/{ghnum}) is closed"
                )
                orphan_branches.extend(branches)
            else:
                logging.debug(
                    f"PR #{pr_number} (gh/{branch_username}/{ghnum}) is still open"
                )
            continue

        # Check cache first (handles multiple ghnums mapping to same PR)
        if pr_number in pr_closed_cache:
            is_closed = pr_closed_cache[pr_number]
        else:
            # Query GitHub for PR status (raises on API error - fail loudly)
            is_closed = check_pr_closed(github, repo_owner, repo_name, pr_number)
            pr_closed_cache[pr_number] = is_closed

        if is_closed:
            logging.info(f"PR #{pr_number} (gh/{branch_username}/{ghnum}) is closed")
            orphan_branches.extend(branches)
        else:
            logging.debug(
                f"PR #{pr_number} (gh/{branch_username}/{ghnum}) is still open"
            )

    if not orphan_branches:
        logging.info("No orphan branches found.")
        return []

    # Sort branches for consistent output
    orphan_branches.sort()

    # Display branches to be deleted
    click.echo("\nOrphan branches that would be deleted:")
    for branch in orphan_branches:
        click.echo(f"  {branch}")
    click.echo(f"\nTotal: {len(orphan_branches)} branch(es)")

    if dry_run:
        click.echo("\nRun without --dry-run to delete these branches.")
        return orphan_branches

    # Confirm before deleting (unless --force is specified)
    if not force:
        click.echo("\n" + "=" * 60)
        click.echo("WARNING: THIS OPERATION IS IRREVERSIBLE!")
        click.echo("These branches will be permanently deleted from the remote.")
        click.echo("=" * 60)
        response = click.prompt(
            "\nType 'delete' to confirm deletion",
            default="",
            show_default=False,
        )
        if response.strip().lower() != "delete":
            click.echo("Aborted. No branches were deleted.")
            return []

    # Delete branches on remote
    click.echo(f"\nDeleting {len(orphan_branches)} orphan branch(es)...")

    # Delete in batches to avoid command line length limits
    batch_size = 50
    deleted_branches: List[str] = []

    for i in range(0, len(orphan_branches), batch_size):
        batch = orphan_branches[i : i + batch_size]
        try:
            sh.git("push", remote_name, "--delete", *batch)
            deleted_branches.extend(batch)
            for branch in batch:
                click.echo(f"  Deleted: {branch}")
        except RuntimeError as e:
            logging.warning(f"Failed to delete some branches: {e}")
            # Try deleting individually to identify which ones failed
            for branch in batch:
                try:
                    sh.git("push", remote_name, "--delete", branch)
                    deleted_branches.append(branch)
                    click.echo(f"  Deleted: {branch}")
                except RuntimeError:
                    logging.warning(f"  Failed to delete: {branch}")

    # Optionally prune local tracking branches
    if clean_local:
        logging.info("Pruning local tracking branches...")
        sh.git("fetch", "--prune", remote_name)

    click.echo(f"\nSuccessfully deleted {len(deleted_branches)} branch(es).")

    return deleted_branches
