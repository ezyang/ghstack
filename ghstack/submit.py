#!/usr/bin/env python3

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

import ghstack
import ghstack.git
import ghstack.github
import ghstack.github_utils
import ghstack.gpg_sign
import ghstack.logs
import ghstack.shell
from ghstack.types import GhNumber, GitCommitHash, GitHubNumber, GitHubRepositoryId

# Either "base", "head" or "orig"; which of the ghstack generated
# branches this diff corresponds to
BranchKind = str

# Metadata describing a diff we submitted to GitHub
# TODO: Make this directly contain a DiffWithGitHubMetadata?
DiffMeta = NamedTuple(
    "DiffMeta",
    [
        ("title", str),
        ("number", GitHubNumber),
        # The PR body to put on GitHub
        ("body", str),
        # The commit message to put on the orig commit
        ("commit_msg", str),
        ("username", str),
        ("ghnum", GhNumber),
        # What Git commit hash we should push to what branch
        ("push_branches", List[Tuple[GitCommitHash, BranchKind]]),
        # A human-readable string like 'Created' which describes what
        # happened to this pull request
        ("what", str),
        ("closed", bool),
        ("pr_url", str),
    ],
)


# Ya, sometimes we get carriage returns.  Crazy right?
RE_STACK = re.compile(r"Stack.*:\r?\n(\* [^\r\n]+\r?\n)+")


# NB: This regex is fuzzy because the D1234567 identifier is typically
# linkified.
RE_DIFF_REV = re.compile(r"^Differential Revision:.+?(D[0-9]+)", re.MULTILINE)


# Suppose that you have submitted a commit to GitHub, and that commit's
# tree was AAA.  The ghstack-source-id of your local commit after this
# submit is AAA.  When you submit a new change on top of this, we check
# that the source id associated with your orig commit agrees with what's
# recorded in GitHub: this lets us know that you are "up-to-date" with
# what was stored on GitHub.  Then, we update the commit message on your
# local commit to record a new ghstack-source-id and push it to orig.
#
# We must store this in the orig commit as we have no other mechanism of
# attaching information to a commit in question.  We don't store this in
# the pull request body as there isn't really any need to do so.
RE_GHSTACK_SOURCE_ID = re.compile(r"^ghstack-source-id: (.+)\n?", re.MULTILINE)


# repo layout:
#   - gh/username/23/head -- what we think GitHub's current tip for commit is
#   - gh/username/23/base -- what we think base commit for commit is
#   - gh/username/23/orig -- the "clean" commit history, i.e., what we're
#                      rebasing, what you'd like to cherry-pick (???)
#                      (Maybe this isn't necessary, because you can
#                      get the "whole" diff from GitHub?  What about
#                      commit description?)


def branch(username: str, ghnum: GhNumber, kind: BranchKind) -> GitCommitHash:
    return GitCommitHash("gh/{}/{}/{}".format(username, ghnum, kind))


def branch_base(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "base")


def branch_head(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "head")


def branch_orig(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "orig")


RE_MENTION = re.compile(r"(?<!\w)@([a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38})", re.I)


# Replace GitHub mentions with non mentions, to prevent spamming people
def strip_mentions(body: str) -> str:
    return RE_MENTION.sub(r"\1", body)


STACK_HEADER = (
    "Stack from [ghstack](https://github.com/ezyang/ghstack) (oldest at bottom)"
)


def starts_with_bullet(body: str) -> bool:
    """
    Returns True if the string in question begins with a Markdown
    bullet list
    """
    return bool(re.match(r"^[\s\t]*[*\-+][\s\t]+", body))


@dataclass
class DiffWithGitHubMetadata:
    diff: ghstack.diff.Diff
    number: GitHubNumber
    username: str
    # Really ought not to be optional, but for BC reasons it might be
    remote_source_id: Optional[str]
    title: str
    body: str
    closed: bool
    ghnum: GhNumber
    pull_request_resolved: ghstack.diff.PullRequestResolved


def main(
    *,
    msg: Optional[str],
    username: str,
    github: ghstack.github.GitHubEndpoint,
    update_fields: bool = False,
    sh: Optional[ghstack.shell.Shell] = None,
    stack_header: str = STACK_HEADER,
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None,
    short: bool = False,
    force: bool = False,
    no_skip: bool = False,
    draft: bool = False,
    github_url: str,
    remote_name: str,
    base: Optional[str] = None,
    revs: Optional[List[str]] = None,
) -> List[Optional[DiffMeta]]:

    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    repo = ghstack.github_utils.get_github_repo_info(
        github=github,
        sh=sh,
        repo_owner=repo_owner,
        repo_name=repo_name,
        github_url=github_url,
        remote_name=remote_name,
    )
    repo_owner_nonopt = repo["name_with_owner"]["owner"]
    repo_name_nonopt = repo["name_with_owner"]["name"]

    if repo["is_fork"]:
        raise RuntimeError(
            "Cowardly refusing to upload diffs to a repository that is a "
            "fork.  ghstack expects '{}' of your Git checkout to point "
            "to the upstream repository in question.  If your checkout does "
            "not comply, please either adjust your remotes (by editing "
            ".git/config) or change the 'remote_name' field in your .ghstackrc "
            "file to point to the correct remote.  If this message is in "
            "error, please register your complaint on GitHub issues (or edit "
            "this line to delete the check above).".format(remote_name)
        )
    repo_id = repo["id"]
    if base is not None:
        default_branch = base
    else:
        default_branch = repo["default_branch"]

    # TODO: Potentially we could narrow this refspec down to only OUR gh
    # branches.  However, this will interact poorly with cross-author
    # so it needs to be thought more carefully
    sh.git(
        "fetch", "--prune", remote_name, f"+refs/heads/*:refs/remotes/{remote_name}/*"
    )

    base_ref = f"{remote_name}/{default_branch}"

    # Default is stack, but this is configurable
    if not revs:
        revs = ["HEAD"]

    # In jf, we determine whether or not we should consider a diff by checking
    # if it is draft or not (only draft commits can be posted).  Git doesn't
    # have a directly analogous concept, so we need some other strategy.  A
    # simple approach is to inspect the base branch in the upstream
    # repository, and exclude all commits which are reachable from it.
    # We don't want to blast ALL remote branches into the list here though;
    # it's possible the draft commits were pushed to the remote repo for
    # unrelated reasons, and we don't want to treat them as non-draft if
    # this happens!
    commits_to_submit_and_boundary = ghstack.git.split_header(
        sh.git(
            "rev-list", "--header", "--topo-order", "--boundary", *revs, f"^{base_ref}"
        ),
    )

    commits_to_submit = [d for d in commits_to_submit_and_boundary if not d.boundary]

    # NB: A little bit of redundant parsing here, because we will re-parse
    # commits that we had already parsed in commits_to_submit, and we will
    # also parse prefix even if it's not being processed, but it's at most ~10
    # extra parses so whatever
    commits_to_rebase = ghstack.git.split_header(
        sh.git(
            "rev-list",
            "--header",
            "--topo-order",
            # Get all commits reachable from HEAD...
            "HEAD",
            # ...as well as all the commits we are going to submit...
            *[c.commit_id for c in commits_to_submit],
            # ...but we don't need any commits that aren't draft
            f"^{base_ref}",
        )
    )

    # NB: commits_to_rebase does not necessarily contain diffs_to_submit, as you
    # can specify REVS that are not connected to HEAD.  In principle, we
    # could also rebase them, if we identified all local branches for which
    # the REV was reachable from--this is left for future work.
    #
    # NB: commits_to_submit does not necessarily contain diffs_to_rebase.  If
    # you ask to submit only a prefix of your current stack, the suffix is
    # not to be submitted, but it needs to be rebased (to, e.g., update the
    # ghstack-source-id)

    commit_count = len(commits_to_submit)

    if commit_count == 0:
        raise RuntimeError(
            "There appears to be no commits to process, based on the revs you passed me.  "
            f"I determined this by running `git rev-list {' '.join(revs)} ^{base_ref}`."
        )
    elif commit_count > 8 and not force:
        raise RuntimeError(
            "Cowardly refusing to handle a stack with more than eight PRs.  "
            "You are likely to get rate limited by GitHub if you try to create or "
            "manipulate this many PRs.  You can bypass this throttle using --force"
        )

    # TODO: figure this out.  BC when revs is standard; maybe multiple calls
    # for non standard rev patterns
    # run_pre_ghstack_hook(sh, base, top.oid)

    submitter = Submitter(
        github=github,
        sh=sh,
        username=username,
        repo_owner=repo_owner_nonopt,
        repo_name=repo_name_nonopt,
        repo_id=repo_id,
        stack_header=stack_header,
        update_fields=update_fields,
        msg=msg,
        short=short,
        force=force,
        no_skip=no_skip,
        draft=draft,
        github_url=github_url,
        remote_name=remote_name,
        commit_index={d.commit_id: d for d in commits_to_submit_and_boundary},
    )

    diff_meta_index = submitter.prepare_submit(commits_to_submit)
    # Also has side effect of updating branch updates
    rebase_index = submitter.prepare_rebase(commits_to_rebase, diff_meta_index)
    logging.debug("rebase_index = %s", rebase_index)
    diffs_to_submit = [
        diff_meta_index[h.commit_id]
        for h in commits_to_submit
        if h.commit_id in diff_meta_index
    ]
    submitter.push_updates(diffs_to_submit)
    if new_head := rebase_index.get(GitCommitHash(sh.git("rev-parse", "HEAD"))):
        sh.git("reset", "--soft", new_head)

    # NB: earliest first
    return list(reversed(diffs_to_submit))


def all_branches(username: str, ghnum: GhNumber) -> Tuple[str, str, str]:
    return (
        branch_base(username, ghnum),
        branch_head(username, ghnum),
        branch_orig(username, ghnum),
    )


def push_spec(commit: GitCommitHash, branch: str) -> str:
    return "{}:refs/heads/{}".format(commit, branch)


class Submitter:
    """
    A class responsible for managing the environment associated
    with submitting PRs at GitHub.

    This used to contain mutable state but now it is immutable.
    """

    # Endpoint to access GitHub
    github: ghstack.github.GitHubEndpoint

    # Shell inside git checkout that we are submitting
    sh: ghstack.shell.Shell

    # GitHub username who is doing the submitting
    username: str

    # Owner of the repository we are submitting to.  Usually 'pytorch'
    repo_owner: str

    # Name of the repository we are submitting to.  Usually 'pytorch'
    repo_name: str

    # GraphQL ID of the repository
    repo_id: GitHubRepositoryId

    # Message describing the update to the stack that was done
    msg: Optional[str]

    # List of input diffs which we ignored (i.e., treated as if they
    # did not exist on the stack at all), because they were associated
    # with a patch that contains no changes.  GhNumber may be false
    # if the diff was never associated with a PR.
    # TODO: mutable state, move out
    ignored_diffs: List[Tuple[ghstack.diff.Diff, Optional[GitHubNumber]]]

    # Set of seen ghnums
    # TODO: mutable state, move out
    seen_ghnums: Set[GhNumber]

    # String used to describe the stack in question
    stack_header: str

    # Clobber existing PR description with local commit message
    update_fields: bool

    # Print only PR URL to stdout
    short: bool

    # Force an update to GitHub, even if we think that your local copy
    # is stale.
    force: bool

    # Do not skip unchanged diffs
    no_skip: bool

    # Create the PR in draft mode if it is going to be created (and not updated).
    draft: bool

    # Github url (normally github.com)
    github_url: str

    # Name of the upstream remote (normally origin)
    remote_name: str

    # Index for original commit hashes to the commit header we parsed out for
    # them
    commit_index: Dict[GitCommitHash, ghstack.git.CommitHeader]

    def __init__(
        self,
        github: ghstack.github.GitHubEndpoint,
        sh: ghstack.shell.Shell,
        username: str,
        repo_owner: str,
        repo_name: str,
        repo_id: GitHubRepositoryId,
        stack_header: str,
        update_fields: bool,
        msg: Optional[str],
        short: bool,
        force: bool,
        no_skip: bool,
        draft: bool,
        github_url: str,
        remote_name: str,
        commit_index: Dict[GitCommitHash, ghstack.git.CommitHeader],
    ):
        self.github = github
        self.sh = sh
        self.username = username
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_id = repo_id
        self.update_fields = update_fields
        self.stack_header = stack_header.format(github_url=github_url)
        self.msg = msg
        self.short = short
        self.force = force
        self.no_skip = no_skip
        self.draft = draft
        self.github_url = github_url
        self.remote_name = remote_name
        self.commit_index = commit_index

        # TODO: mutable state to remove
        self.ignored_diffs = []
        self.seen_ghnums = set()

    def _default_title_and_body(
        self, commit: ghstack.diff.Diff, old_pr_body: Optional[str]
    ) -> Tuple[str, str]:
        """
        Compute what the default title and body of a newly opened pull
        request would be, given the existing commit message.

        If you pass in the old PR body, we also preserve "Differential
        Revision" information in the PR body.  We only overwrite PR
        body if you explicitly ask for it with --update-fields, but
        it's good not to lose Phabricator diff assignment, so we special
        case this.
        """
        title = commit.title
        extra = ""
        if old_pr_body is not None:
            # Look for tags we should preserve, and keep them
            m = RE_DIFF_REV.search(old_pr_body)
            if m:
                extra = (
                    "\n\nDifferential Revision: "
                    "[{phabdiff}]"
                    "(https://our.internmc.facebook.com/intern/diff/{phabdiff})"
                ).format(phabdiff=m.group(1))
        commit_body = "".join(commit.summary.splitlines(True)[1:]).lstrip()
        # Don't store ghstack-source-id in the PR body; it will become
        # stale quickly
        commit_body = RE_GHSTACK_SOURCE_ID.sub("", commit_body)
        # Don't store Pull request resolved in the PR body; it's
        # unnecessary
        commit_body = ghstack.diff.re_pull_request_resolved_w_sp(self.github_url).sub(
            "", commit_body
        )
        if starts_with_bullet(commit_body):
            commit_body = f"----\n\n{commit_body}"
        pr_body = "{}:\n* (to be filled)\n\n{}{}".format(
            self.stack_header, commit_body, extra
        )
        return title, pr_body

    def _is_valid_ref(self, ref: str) -> bool:
        splits = ref.split("/")
        if len(splits) < 3:
            return False
        else:
            return splits[-2].isnumeric()

    def elaborate_diff(
        self, commit: ghstack.diff.Diff, *, is_ghexport: bool = False
    ) -> DiffWithGitHubMetadata:
        """
        Query GitHub API for the current title, body and closed? status
        of the pull request corresponding to a ghstack.diff.Diff.
        """

        assert commit.pull_request_resolved is not None
        assert commit.pull_request_resolved.owner == self.repo_owner
        assert commit.pull_request_resolved.repo == self.repo_name

        number = commit.pull_request_resolved.number
        # TODO: There is no reason to do a node query here; we can
        # just look up the repo the old fashioned way
        r = self.github.graphql(
            """
          query ($repo_id: ID!, $number: Int!) {
            node(id: $repo_id) {
              ... on Repository {
                pullRequest(number: $number) {
                  body
                  title
                  closed
                  headRefName
                }
              }
            }
          }
        """,
            repo_id=self.repo_id,
            number=number,
        )["data"]["node"]["pullRequest"]

        # Sorry, this is a big hack to support the ghexport case
        m = re.match(r"(refs/heads/)?export-D([0-9]+)$", r["headRefName"])
        if m is not None and is_ghexport:
            raise RuntimeError(
                """\
This commit appears to already be associated with a pull request,
but the pull request was previously submitted with an old version of
ghexport.  You can continue exporting using the old style using:

    ghexport --legacy

For future diffs, we recommend using the non-legacy version of ghexport
as it supports bidirectional syncing.  However, there is no way to
convert a pre-existing PR in the old style to the new format which
supports bidirectional syncing.  If you would like to blow away the old
PR and start anew, edit the Summary in the Phabricator diff to delete
the line 'Pull Request resolved' and then run ghexport again.
"""
            )

        # TODO: Hmm, I'm not sure why this matches
        m = re.match(r"gh/([^/]+)/([0-9]+)/head$", r["headRefName"])
        if m is None:
            if is_ghexport:
                raise RuntimeError(
                    """\
This commit appears to already be associated with a pull request,
but the pull request doesn't look like it was submitted by ghexport
Maybe you exported it using the "Export to Open Source" button on
the Phabricator diff page?  If so, please continue to use that button
to export your diff.

If you think this is in error, edit the Summary in the Phabricator diff
to delete the line 'Pull Request resolved' and then run ghexport again.
"""
                )
            else:
                raise RuntimeError(
                    """\
This commit appears to already be associated with a pull request,
but the pull request doesn't look like it was submitted by ghstack.
If you think this is in error, run:

    ghstack unlink {}

to disassociate the commit with the pull request, and then try again.
(This will create a new pull request!)
""".format(
                        commit.oid
                    )
                )
        username = m.group(1)
        gh_number = GhNumber(m.group(2))

        # NB: Technically, we don't need to pull this information at
        # all, but it's more convenient to unconditionally edit
        # title/body when we update the pull request info
        title = r["title"]
        pr_body = r["body"]
        if self.update_fields:
            title, pr_body = self._default_title_and_body(commit, pr_body)

        # TODO: remote summary should be done earlier so we can use
        # it to test if updates are necessary

        try:
            rev_list = self.sh.git(
                "rev-list",
                "--max-count=1",
                "--header",
                self.remote_name + "/" + branch_orig(username, gh_number),
            )
        except RuntimeError as e:
            if r["closed"]:
                raise RuntimeError(
                    f"Cannot ghstack a stack with closed PR #{number} whose branch was deleted.  "
                    "If you were just trying to update a later PR in the stack, `git rebase` and try again.  "
                    "Otherwise, you may have been trying to update a PR that was already closed. "
                    "To disassociate your update from the old PR and open a new PR, "
                    "run `ghstack unlink`, `git rebase` and then try again."
                ) from e
            raise
        remote_summary = ghstack.git.split_header(rev_list)[0]
        m_remote_source_id = RE_GHSTACK_SOURCE_ID.search(remote_summary.commit_msg)
        remote_source_id = m_remote_source_id.group(1) if m_remote_source_id else None

        return DiffWithGitHubMetadata(
            diff=commit,
            title=title,
            body=pr_body,
            closed=r["closed"],
            number=number,
            username=username,
            ghnum=gh_number,
            remote_source_id=remote_source_id,
            pull_request_resolved=commit.pull_request_resolved,
        )

    def git_push(self, branches: Sequence[str], force: bool = False) -> None:
        assert branches, "empty branches would push master, probably bad!"
        try:
            self.sh.git(
                "push",
                self.remote_name,
                *(["--force"] if force else []),
                *branches,
            )
        except RuntimeError as e:
            remote_url = self.sh.git("remote", "get-url", "--push", self.remote_name)
            if remote_url.startswith("https://"):
                raise RuntimeError(
                    "[E001] git push failed, probably because it asked for password "
                    "(scroll up to see original error).  "
                    "Change your git URL to use SSH instead of HTTPS to enable passwordless push.  "
                    "See https://github.com/ezyang/ghstack/wiki/E001 for more details."
                ) from e
            raise
        self.github.push_hook(branches)

    def process_new_commit(
        self, base: ghstack.git.CommitHeader, commit: ghstack.diff.Diff
    ) -> Optional[DiffMeta]:
        """
        Process a diff that has never been pushed to GitHub before.
        """

        logging.debug(
            "process_new_commit(base=%s, commit=%s)", base.commit_id, commit.oid
        )

        if "[ghstack-poisoned]" in commit.summary:
            raise RuntimeError(
                """\
This commit is poisoned: it is from a head or base branch--ghstack
cannot validly submit it.  The most common situation for this to
happen is if you checked out the head branch of a pull request that was
previously submitted with ghstack (e.g., by using hub checkout).
Making modifications on the head branch is not supported; instead,
you should fetch the original commits in question by running:

    ghstack checkout $PR_URL

Since we cannot proceed, ghstack will abort now.
"""
            )

        title, pr_body = self._default_title_and_body(commit, None)

        # Determine the next available GhNumber.  We do this by
        # iterating through known branches and keeping track
        # of the max.  The next available GhNumber is the next number.
        # This is technically subject to a race, but we assume
        # end user is not running this script concurrently on
        # multiple machines (you bad bad)
        refs = self.sh.git(
            "for-each-ref",
            # Use OUR username here, since there's none attached to the
            # diff
            "refs/remotes/{}/gh/{}".format(self.remote_name, self.username),
            "--format=%(refname)",
        ).split()
        refs = list(filter(lambda r: self._is_valid_ref(r), refs))
        max_ref_num = max(int(ref.split("/")[-2]) for ref in refs) if refs else 0
        ghnum = GhNumber(str(max_ref_num + 1))

        # Create the incremental pull request diff
        tree = commit.tree

        # Actually, if there's no change in the tree, stop processing
        if tree == base.tree:
            self.ignored_diffs.append((commit, None))
            logging.warning(
                "Skipping {} {}, as the commit has no changes".format(commit.oid, title)
            )
            return None

        assert ghnum not in self.seen_ghnums
        self.seen_ghnums.add(ghnum)

        new_base = GitCommitHash(
            self.sh.git(
                "commit-tree",
                *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                # No parents!  We could hypothetically give this a parent
                # back to the base branch, but it's not really necessary
                base.tree,
                input='Update base for {} on "{}"\n\n{}\n\n[ghstack-poisoned]'.format(
                    self.msg, commit.title, base.commit_msg
                ),
            )
        )

        new_pull = GitCommitHash(
            self.sh.git(
                "commit-tree",
                *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                "-p",
                new_base,
                tree,
                input=commit.summary + "\n\n[ghstack-poisoned]",
            )
        )

        # Push the branches, so that we can create a PR for them
        new_branches = (
            push_spec(new_pull, branch_head(self.username, ghnum)),
            push_spec(new_base, branch_base(self.username, ghnum)),
        )
        self.git_push(new_branches)

        # Time to open the PR
        # NB: GraphQL API does not support opening PRs
        r = self.github.post(
            "repos/{owner}/{repo}/pulls".format(
                owner=self.repo_owner, repo=self.repo_name
            ),
            title=title,
            head=branch_head(self.username, ghnum),
            base=branch_base(self.username, ghnum),
            body=pr_body,
            maintainer_can_modify=True,
            draft=self.draft,
        )
        number = r["number"]

        logging.info("Opened PR #{}".format(number))

        # Update the commit message of the local diff with metadata
        # so we can correlate these later
        pull_request_resolved = ghstack.diff.PullRequestResolved(
            owner=self.repo_owner, repo=self.repo_name, number=number
        )
        commit_msg = (
            "{commit_msg}\n\n"
            "ghstack-source-id: {sourceid}\n"
            "Pull Request resolved: "
            "https://{github_url}/{owner}/{repo}/pull/{number}".format(
                commit_msg=strip_mentions(commit.summary.rstrip()),
                owner=self.repo_owner,
                repo=self.repo_name,
                number=number,
                sourceid=commit.source_id,
                github_url=self.github_url,
            )
        )

        return DiffMeta(
            title=title,
            number=number,
            body=pr_body,
            commit_msg=commit_msg,
            ghnum=ghnum,
            username=self.username,
            push_branches=[],
            what="Created",
            closed=False,
            pr_url=pull_request_resolved.url(self.github_url),
        )

    def process_old_commit(
        self, base: ghstack.git.CommitHeader, elab_commit: DiffWithGitHubMetadata
    ) -> Optional[DiffMeta]:
        """
        Process a diff that has an existing upload to GitHub.
        """

        # Do not process closed commits
        if elab_commit.closed:
            return None

        commit = elab_commit.diff
        username = elab_commit.username
        ghnum = elab_commit.ghnum
        number = elab_commit.number

        if ghnum in self.seen_ghnums:
            raise RuntimeError(
                "Something very strange has happened: a commit for "
                "the pull request #{} occurs twice in your local "
                "commit stack.  This is usually because of a botched "
                "rebase.  Please take a look at your git log and seek "
                "help from your local Git expert.".format(number)
            )
        self.seen_ghnums.add(ghnum)

        logging.info("Pushing to #{}".format(number))

        # Compute the local and remote source IDs
        summary = commit.summary
        m_local_source_id = RE_GHSTACK_SOURCE_ID.search(summary)
        if m_local_source_id is None:
            # For BC, just slap on a source ID.  After BC is no longer
            # needed, we can just error in this case; however, this
            # situation is extremely likely to happen for preexisting
            # stacks.
            logging.warning(
                "Local commit has no ghstack-source-id; assuming that it is "
                "up-to-date with remote."
            )
            summary = "{}\nghstack-source-id: {}".format(summary, commit.source_id)
        else:
            local_source_id = m_local_source_id.group(1)
            if elab_commit.remote_source_id is None:
                # This should also be an error condition, but I suppose
                # it can happen in the wild if a user had an aborted
                # ghstack run, where they updated their head pointer to
                # a copy with source IDs, but then we failed to push to
                # orig.  We should just go ahead and push in that case.
                logging.warning(
                    "Remote commit has no ghstack-source-id; assuming that we are "
                    "up-to-date with remote."
                )
            else:
                if local_source_id != elab_commit.remote_source_id and not self.force:
                    logging.debug(
                        f"elab_commit.remote_source_id = {elab_commit.remote_source_id}"
                    )
                    raise RuntimeError(
                        "Cowardly refusing to push an update to GitHub, since it "
                        "looks another source has updated GitHub since you last "
                        "pushed.  If you want to push anyway, rerun this command "
                        "with --force.  Otherwise, diff your changes against "
                        "{} and reapply them on top of an up-to-date commit from "
                        "GitHub.".format(local_source_id)
                    )
                summary = RE_GHSTACK_SOURCE_ID.sub(
                    "ghstack-source-id: {}\n".format(commit.source_id), summary
                )

        # I vacillated between whether or not we should use the PR
        # body or the literal commit message here.  Right now we use
        # the PR body, because after initial commit the original
        # commit message is not supposed to "matter" anymore.  orig
        # still uses the original commit message, however, because
        # it's supposed to be the "original".
        non_orig_commit_msg = strip_mentions(RE_STACK.sub("", elab_commit.body))

        # We've got an update to do!  But what exactly should we
        # do?
        #
        # Here is the relevant state:
        #   - Local parent tree
        #   - Local commit tree
        #   - Remote base branch
        #   - Remote head branch
        #
        # Our job is to synchronize local with remote.  Here are a few
        # common situations:
        #
        #   - Neither this commit nor any of the earlier commits were
        #     modified; everything is in sync.  We want to do nothing in this
        #     case.
        #
        #   - User updated top commit on stack, but none of the earlier commits.
        #     Here, we expect local parent tree to match remote base tree (BA), but
        #     local commit tree to mismatch remote head branch (A).  We will push
        #     a new commit to head (A2), no merge necessary.
        #
        #       BA
        #        \
        #         A - A2
        #
        #   - User updated an earlier commit in the stack (it doesn't matter
        #     if the top commit is logically modified or not: it always counts as
        #     having been modified to resolve the merge.)  We don't expect
        #     local parent tree to match remote base tree, so we must push a
        #     new base commit (BA2), and a merge commit (A2) on it.
        #
        #       BA - BA2
        #        \    \
        #         A - A2
        #
        #     Notably, this must happen even if the local commit tree matches
        #     the remote head branch.  A common situation this could occur is
        #     if we squash commits I and J into IJ (keeping J as the tree).
        #     Then for J we see:
        #
        #        BJ - BJ2
        #         \    \
        #          J - BJ2
        #
        #    Where BJ contains I, but BJ2 does NOT contain I.  The net result
        #    is the changes of I are included inside the BJ2 merge commit.
        #
        # Note that, counterintuitively, the base of a diff has no
        # relationship to the head of an earlier diff on the stack.  This
        # makes it possible to selectively only update one diff in a stack
        # without updating any others.  This also makes our handling uniform
        # even if you rebase a commit backwards: you just see that the base
        # is updated to also remove changes.

        def resolve_remote(branch: str) -> Tuple[str, ghstack.diff.Diff, str]:
            remote_ref = self.remote_name + "/" + branch
            (remote_diff,) = ghstack.git.parse_header(
                self.sh.git("rev-list", "--header", "-1", remote_ref), self.github_url
            )
            remote_tree = remote_diff.tree

            return remote_ref, remote_diff, remote_tree

        # Edge case: check if the commit is empty; if so we don't
        # submit a PR for it
        if base.tree == elab_commit.diff.tree:
            self.ignored_diffs.append((commit, number))
            logging.warning(
                "Skipping PR #{} {}, as the commit now has no changes".format(
                    number, elab_commit.title
                )
            )
            return None

        remote_base_ref, remote_base, remote_base_tree = resolve_remote(
            branch_base(username, ghnum)
        )
        remote_head_ref, remote_head, remote_head_tree = resolve_remote(
            branch_head(username, ghnum)
        )

        push_branches = []

        # Check if bases match
        base_args: Tuple[str, ...] = ()
        if remote_base_tree != base.tree:
            # Base is not the same, perform base update
            new_base = GitCommitHash(
                self.sh.git(
                    "commit-tree",
                    *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                    "-p",
                    remote_base_ref,
                    base.tree,
                    input='Update base for {} on "{}"\n\n{}\n\n[ghstack-poisoned]'.format(
                        self.msg, elab_commit.title, non_orig_commit_msg
                    ),
                )
            )
            base_args = ("-p", new_base)
            push_branches.append((new_base, "base"))

        # Check if heads match, or base was updated
        new_head: Optional[GitCommitHash] = None
        if base_args or remote_head_tree != elab_commit.diff.tree:
            new_head = GitCommitHash(
                self.sh.git(
                    "commit-tree",
                    *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                    "-p",
                    remote_head_ref,
                    *base_args,
                    elab_commit.diff.tree,
                    input='{} on "{}"\n\n{}\n\n[ghstack-poisoned]'.format(
                        self.msg, elab_commit.title, non_orig_commit_msg
                    ),
                )
            )
            push_branches.append((new_head, "head"))

        if not push_branches:
            what = "Skipped"
        elif elab_commit.closed:
            # TODO: this does not seem synced with others
            what = "Skipped closed"
        else:
            what = "Updated"

        return DiffMeta(
            title=elab_commit.title,
            number=number,
            # NB: Ignore the commit message, and just reuse the old commit
            # message.  This is consistent with 'jf submit' default
            # behavior.  The idea is that people may have edited the
            # PR description on GitHub and you don't want to clobber
            # it.
            body=elab_commit.body,
            commit_msg=summary,
            ghnum=ghnum,
            username=username,
            push_branches=push_branches,
            what=what,
            closed=elab_commit.closed,
            pr_url=elab_commit.pull_request_resolved.url(self.github_url),
        )

    def prepare_submit(
        self, commits_to_submit: List[ghstack.git.CommitHeader]
    ) -> Dict[GitCommitHash, DiffMeta]:
        commit_index = self.commit_index

        # NB: this can be done in parallel, if you like.
        # When sequentially, reversed means we update early commits first (this
        # doesn't really matter, but it's more intuitive for users)
        diff_meta_index: Dict[GitCommitHash, DiffMeta] = {}
        for commit in reversed(commits_to_submit):
            parents = commit.parents
            if len(parents) != 1:
                raise RuntimeError(
                    "The commit {} has {} parents, which makes my head explode.  "
                    "`git rebase -i` your diffs into a stack, then try again.".format(
                        commit.commit_id, len(parents)
                    )
                )
            parent_commit = commit_index[parents[0]]
            diff = ghstack.git.convert_header(commit, self.github_url)
            if diff.pull_request_resolved is not None:
                diff_meta = self.process_old_commit(
                    parent_commit, self.elaborate_diff(diff)
                )
            else:
                diff_meta = self.process_new_commit(parent_commit, diff)
            if diff_meta is not None:
                diff_meta_index[commit.commit_id] = diff_meta

        return diff_meta_index

    def prepare_rebase(
        self,
        commits_to_rebase: List[ghstack.git.CommitHeader],
        diff_meta_index: Dict[GitCommitHash, DiffMeta],
    ) -> Dict[GitCommitHash, GitCommitHash]:
        # Now that we've prepared the head/base branches, now we prepare the orig
        # by performing a rebase in reverse topological order.
        # (Reverse here is important because we must have processed parents
        # first.)
        rebase_index: Dict[GitCommitHash, GitCommitHash] = {}
        for commit in reversed(commits_to_rebase):
            # Check if we actually need to rebase it, or can use it as is
            assert len(commit.parents) == 1
            if commit.parents[0] in rebase_index or commit.commit_id in diff_meta_index:
                # Yes, we need to rebase it

                if diff_meta := diff_meta_index.get(commit.commit_id):
                    # use the updated commit message, if it exists
                    commit_msg = diff_meta.commit_msg
                else:
                    commit_msg = commit.commit_msg

                if rebase_id := rebase_index.get(commit.parents[0]):
                    # use the updated base, if it exists
                    base_commit_id = rebase_id
                else:
                    base_commit_id = commit.parents[0]

                # Preserve authorship of original commit
                # (TODO: for some reason, we didn't do this for old commits,
                # maybe it doesn't matter)
                env = {}
                if commit.author_name is not None:
                    env["GIT_AUTHOR_NAME"] = commit.author_name
                if commit.author_email is not None:
                    env["GIT_AUTHOR_EMAIL"] = commit.author_email

                new_orig = GitCommitHash(
                    self.sh.git(
                        "commit-tree",
                        *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                        "-p",
                        base_commit_id,
                        commit.tree,
                        input=commit_msg,
                        env=env,
                    )
                )

                if diff_meta is not None:
                    # Add the new_orig to push
                    # This may not exist.  If so, that means this diff only exists
                    # to update HEAD.
                    diff_meta.push_branches.append((new_orig, "orig"))

                rebase_index[commit.commit_id] = new_orig

        return rebase_index

    # TODO: do the tree formatting minigame
    # Main things:
    # - need to express some tree structure
    # - want "as complete" a tree as possible; this may involve
    #   poking around the xrefs to find out all the other PRs
    #   involved in the stack
    def _format_stack(self, diffs_to_submit: List[DiffMeta], number: int) -> str:
        rows = []
        # NB: top is top of stack, opposite of update order
        for s in diffs_to_submit:
            if s.number == number:
                rows.append(f"* __->__ #{s.number}")
            else:
                rows.append(f"* #{s.number}")
        return self.stack_header + ":\n" + "\n".join(rows) + "\n"

    def push_updates(
        self, diffs_to_submit: List[DiffMeta], *, import_help: bool = True
    ) -> None:
        # update pull request information, update bases as necessary
        #   preferably do this in one network call
        # push your commits (be sure to do this AFTER you update bases)
        base_push_branches: List[str] = []
        push_branches: List[str] = []
        force_push_branches: List[str] = []

        for s in reversed(diffs_to_submit):
            # NB: GraphQL API does not support modifying PRs
            assert not s.closed
            logging.info(
                "# Updating https://{github_url}/{owner}/{repo}/pull/{number}".format(
                    github_url=self.github_url,
                    owner=self.repo_owner,
                    repo=self.repo_name,
                    number=s.number,
                )
            )
            # TODO: don't update this if it doesn't need updating
            self.github.patch(
                "repos/{owner}/{repo}/pulls/{number}".format(
                    owner=self.repo_owner, repo=self.repo_name, number=s.number
                ),
                body=RE_STACK.sub(
                    self._format_stack(diffs_to_submit, s.number),
                    s.body,
                ),
                title=s.title,
            )

            # It is VERY important that we do base updates BEFORE real
            # head updates, otherwise GitHub will spuriously think that
            # the user pushed a number of patches as part of the PR,
            # when actually they were just from the (new) upstream
            # branch

            for commit, b in s.push_branches:
                if b == "orig":
                    q = force_push_branches
                elif b == "base":
                    q = base_push_branches
                else:
                    q = push_branches
                q.append(push_spec(commit, branch(s.username, s.ghnum, b)))
        # Careful!  Don't push master.
        # TODO: These pushes need to be atomic (somehow)
        if base_push_branches:
            self.git_push(base_push_branches)
        if push_branches:
            self.git_push(push_branches)
        if force_push_branches:
            self.git_push(force_push_branches, force=True)

        # Report what happened
        def format_url(s: DiffMeta) -> str:
            return "https://{github_url}/{owner}/{repo}/pull/{number}".format(
                github_url=self.github_url,
                owner=self.repo_owner,
                repo=self.repo_name,
                number=s.number,
            )

        if self.short:
            # Guarantee that the FIRST PR URL is the top of the stack
            print("\n".join(format_url(s) for s in reversed(diffs_to_submit)))
            return

        print()
        print("# Summary of changes (ghstack {})".format(ghstack.__version__))
        print()
        if diffs_to_submit:
            for s in reversed(diffs_to_submit):
                url = format_url(s)
                print(" - {} {}".format(s.what, url))

            print()
            if import_help:
                top_of_stack = diffs_to_submit[0]

                print("Meta employees can import your changes by running ")
                print("(on a Meta machine):")
                print()
                print("    ghimport -s {}".format(format_url(top_of_stack)))
                print()
                print("If you want to work on this diff stack on another machine:")
                print()
                print("    ghstack checkout {}".format(format_url(top_of_stack)))
                print("")
        else:
            print(
                "No pull requests updated; all commits in your diff stack were empty!"
            )

        if self.ignored_diffs:
            print()
            print("FYI: I ignored the following commits, because they had no changes:")
            print()
            noop_pr = False
            for d, pr in reversed(self.ignored_diffs):
                if pr is None:
                    print(" - {} {}".format(d.oid[:8], d.title))
                else:
                    noop_pr = True
                    print(
                        " - {} {} (was previously submitted as PR #{})".format(
                            d.oid[:8], d.title, pr
                        )
                    )
            if noop_pr:
                print()
                print(
                    "I did NOT close or update PRs previously associated with these commits."
                )


def run_pre_ghstack_hook(
    sh: ghstack.shell.Shell, base_commit: str, top_commit: str
) -> None:
    """If a `pre-ghstack` git hook is configured, run it."""
    default_hooks_path = os.path.join(
        sh.git("rev-parse", "--show-toplevel"), ".git/hooks"
    )
    try:
        hooks_path = sh.git(
            "config", "--default", default_hooks_path, "--get", "core.hooksPath"
        )
        hook_file = os.path.join(hooks_path, "pre-ghstack")
    except Exception as e:
        logging.warning(f"Pre ghstack hook failed: {e}")
        return

    if not os.path.isfile(hook_file) or not os.access(hook_file, os.X_OK):
        return

    sh.sh(hook_file, base_commit, top_commit, stdout=None)
