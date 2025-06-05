#!/usr/bin/env python3

import dataclasses
import itertools
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import ghstack
import ghstack.git
import ghstack.github
import ghstack.github_utils
import ghstack.gpg_sign
import ghstack.logs
import ghstack.shell
import ghstack.trailers
from ghstack.types import GhNumber, GitCommitHash, GitHubNumber, GitHubRepositoryId

# Either "base", "head" or "orig"; which of the ghstack generated
# branches this diff corresponds to
# For direct ghstack, either "next", "head" or "orig"
BranchKind = str


@dataclass(frozen=True)
class GhCommit:
    commit_id: GitCommitHash
    tree: str


# Commit can be None if this is a completely fresh PR
@dataclass
class GhBranch:
    commit: Optional[GhCommit] = None
    updated: bool = False

    def update(self, val: GhCommit) -> None:
        self.commit = val
        self.updated = True


@dataclass
class GhBranches:
    # What Git commit hash we should push to what branch.
    # The orig branch is populated later
    orig: GhBranch = dataclasses.field(default_factory=GhBranch)
    head: GhBranch = dataclasses.field(default_factory=GhBranch)
    base: GhBranch = dataclasses.field(default_factory=GhBranch)
    next: GhBranch = dataclasses.field(default_factory=GhBranch)

    def to_list(self) -> List[Tuple[GitCommitHash, BranchKind]]:
        r = []
        if self.orig.updated:
            assert self.orig.commit is not None
            r.append((self.orig.commit.commit_id, "orig"))
        if self.next.updated:
            assert self.next.commit is not None
            r.append((self.next.commit.commit_id, "next"))
        if self.base.updated:
            assert self.base.commit is not None
            r.append((self.base.commit.commit_id, "base"))
        if self.head.updated:
            assert self.head.commit is not None
            r.append((self.head.commit.commit_id, "head"))
        return r

    def __iter__(self) -> Iterator[Tuple[GitCommitHash, BranchKind]]:
        return iter(self.to_list())

    def __bool__(self) -> bool:
        return bool(self.to_list())

    def clear(self) -> None:
        self.orig.updated = False
        self.head.updated = False
        self.base.updated = False
        self.next.updated = False


@dataclass(frozen=True)
class PreBranchState:
    # NB: these do not necessarily coincide with head/base branches.
    # In particular, in direct mode, the base commit will typically be
    # another head branch, or the upstream main branch itself.
    base_commit_id: GitCommitHash
    head_commit_id: GitCommitHash


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


# When we make a GitHub PR using --direct, we submit an extra comment which
# contains the links to the rest of the PRs in the stack.  We don't put this
# inside the pull request body, because if you squash merge the PR, that body
# gets put into the commit message, but the stack information is just line
# noise and shouldn't go there.
#
# We can technically find the ghstack commit by querying GitHub API for all
# comments, but this is a more efficient way of getting it.
RE_GHSTACK_COMMENT_ID = re.compile(r"^ghstack-comment-id: (.+)\n?", re.MULTILINE)


# repo layout:
#   - gh/username/23/head -- what we think GitHub's current tip for commit is
#   - gh/username/23/base -- what we think base commit for commit is
#   - gh/username/23/orig -- the "clean" commit history, i.e., what we're
#                      rebasing, what you'd like to cherry-pick (???)
#                      (Maybe this isn't necessary, because you can
#                      get the "whole" diff from GitHub?  What about
#                      commit description?)
#
#
# In direct mode, there is no base branch, instead:
#
#   - gh/username/23/next -- staging ground for commits that must exist
#     for later PRs in the stack to merge against, but should not be shown
#     for the PR itself (because that PR was not submitted)


def branch(username: str, ghnum: GhNumber, kind: BranchKind) -> GitCommitHash:
    return GitCommitHash("gh/{}/{}/{}".format(username, ghnum, kind))


def branch_base(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "base")


def branch_head(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "head")


def branch_orig(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "orig")


def branch_next(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "next")


RE_MENTION = re.compile(r"(?<!\w)@([a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38})", re.I)


# Replace GitHub mentions with non mentions, to prevent spamming people
def strip_mentions(body: str) -> str:
    return RE_MENTION.sub(r"\1", body)


STACK_HEADER = f"Stack from [ghstack](https://github.com/ezyang/ghstack/tree/{ghstack.__version__}) (oldest at bottom)"


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
    # Guaranteed to be set for --direct PRs
    comment_id: Optional[int]
    title: str
    body: str
    closed: bool
    ghnum: GhNumber
    pull_request_resolved: ghstack.diff.PullRequestResolved
    head_ref: str
    base_ref: str


# Metadata describing a diff we submitted to GitHub
@dataclass
class DiffMeta:
    elab_diff: DiffWithGitHubMetadata
    # The commit message to put on the orig commit
    commit_msg: str

    push_branches: GhBranches
    # A human-readable string like 'Created' which describes what
    # happened to this pull request
    what: str

    # The name of the branch that should be targeted
    base: str

    @property
    def pr_url(self) -> str:
        return self.elab_diff.pull_request_resolved.url()

    @property
    def title(self) -> str:
        return self.elab_diff.title

    @property
    def number(self) -> GitHubNumber:
        return self.elab_diff.number

    @property
    def body(self) -> str:
        return self.elab_diff.body

    @property
    def username(self) -> str:
        return self.elab_diff.username

    @property
    def ghnum(self) -> GhNumber:
        return self.elab_diff.ghnum

    @property
    def closed(self) -> bool:
        return self.elab_diff.closed

    @property
    def orig(self) -> GitCommitHash:
        assert self.push_branches.orig.commit is not None
        return self.push_branches.orig.commit.commit_id

    @property
    def head(self) -> GitCommitHash:
        assert self.push_branches.head.commit is not None
        return self.push_branches.head.commit.commit_id

    @property
    def next(self) -> GitCommitHash:
        assert self.push_branches.next.commit is not None
        return self.push_branches.next.commit.commit_id


def main(**kwargs: Any) -> List[DiffMeta]:
    submitter = Submitter(**kwargs)
    return submitter.run()


def all_branches(username: str, ghnum: GhNumber) -> Tuple[str, str, str]:
    return (
        branch_base(username, ghnum),
        branch_head(username, ghnum),
        branch_orig(username, ghnum),
    )


def push_spec(commit: GitCommitHash, branch: str) -> str:
    return "{}:refs/heads/{}".format(commit, branch)


@dataclass(frozen=True)
class Submitter:
    """
    A class responsible for managing the environment associated
    with submitting PRs at GitHub.
    """

    # ---------------------------
    # Direct arguments to submit

    # Message describing the update to the stack that was done
    msg: Optional[str]

    # GitHub username who is doing the submitting
    username: str

    # Endpoint to access GitHub
    github: ghstack.github.GitHubEndpoint

    # Clobber existing PR description with local commit message
    update_fields: bool = False

    # Shell inside git checkout that we are submitting
    sh: ghstack.shell.Shell = dataclasses.field(default_factory=ghstack.shell.Shell)

    # String used to describe the stack in question
    stack_header: str = STACK_HEADER

    # Owner of the repository we are submitting to.  Usually 'pytorch'
    # Presents as repo_owner kwarg in main
    repo_owner_opt: Optional[str] = None

    # Name of the repository we are submitting to.  Usually 'pytorch'
    # Presents as repo_name kwarg in main
    repo_name_opt: Optional[str] = None

    # Print only PR URL to stdout
    short: bool = False

    # Force an update to GitHub, even if we think that your local copy
    # is stale.
    force: bool = False

    # Do not skip unchanged diffs
    no_skip: bool = False

    # Create the PR in draft mode if it is going to be created (and not updated).
    draft: bool = False

    # Github url (normally github.com)
    github_url: str = "github.com"

    # Name of the upstream remote (normally origin)
    remote_name: str = "origin"

    base_opt: Optional[str] = None

    revs: Sequence[str] = ()

    # Controls rev parse behavior, whether or not to submit a stack
    # of commits or only one commit individually
    stack: bool = True

    # Check that invariants are upheld during execution
    check_invariants: bool = False

    # Instead of merging into base branch, merge directly into the appropriate
    # main or head branch.  Change merge targets appropriately as PRs get
    # merged.  If None, infer whether or not the PR should be direct or not.
    direct_opt: Optional[bool] = None

    # ~~~~~~~~~~~~~~~~~~~~~~~~
    # Computed in post init

    # GraphQL ID of the repository
    repo_id: GitHubRepositoryId = dataclasses.field(init=False)

    repo_owner: str = dataclasses.field(init=False)

    repo_name: str = dataclasses.field(init=False)

    base: str = dataclasses.field(init=False)

    direct: bool = dataclasses.field(init=False)

    # ~~~~~~~~~~~~~~~~~~~~~~~~
    # Mutable state; TODO: remove me

    # List of input diffs which we ignored (i.e., treated as if they
    # did not exist on the stack at all), because they were associated
    # with a patch that contains no changes.  GhNumber may be false
    # if the diff was never associated with a PR.
    ignored_diffs: List[Tuple[ghstack.diff.Diff, Optional[DiffWithGitHubMetadata]]] = (
        dataclasses.field(default_factory=list)
    )

    # Set of seen ghnums
    seen_ghnums: Set[Tuple[str, GhNumber]] = dataclasses.field(default_factory=set)

    # ~~~~~~~~~~~~~~~~~~~~~~~~
    # Post initialization

    def __post_init__(self) -> None:
        # Network call in the constructor, help me father, for I have sinned
        repo = ghstack.github_utils.get_github_repo_info(
            github=self.github,
            sh=self.sh,
            repo_owner=self.repo_owner_opt,
            repo_name=self.repo_name_opt,
            github_url=self.github_url,
            remote_name=self.remote_name,
        )
        object.__setattr__(self, "repo_owner", repo["name_with_owner"]["owner"])
        object.__setattr__(self, "repo_name", repo["name_with_owner"]["name"])

        if repo["is_fork"]:
            raise RuntimeError(
                "Cowardly refusing to upload diffs to a repository that is a "
                "fork.  ghstack expects '{}' of your Git checkout to point "
                "to the upstream repository in question.  If your checkout does "
                "not comply, please either adjust your remotes (by editing "
                ".git/config) or change the 'remote_name' field in your .ghstackrc "
                "file to point to the correct remote.  If this message is in "
                "error, please register your complaint on GitHub issues (or edit "
                "this line to delete the check above).".format(self.remote_name)
            )
        object.__setattr__(self, "repo_id", repo["id"])
        if self.base_opt is not None:
            default_branch = self.base_opt
        else:
            default_branch = repo["default_branch"]

        object.__setattr__(self, "base", default_branch)

        # Check if direct should be used, if the user didn't explicitly
        # specify an option
        direct = self.direct_opt
        if direct is None:
            direct_r = self.sh.git(
                "cat-file", "-e", "HEAD:.github/ghstack_direct", exitcode=True
            )
            assert isinstance(direct_r, bool)
            direct = direct_r

        object.__setattr__(self, "direct", direct)

    # ~~~~~~~~~~~~~~~~~~~~~~~~
    # The main algorithm

    def run(self) -> List[DiffMeta]:
        self.fetch()

        commits_to_submit_and_boundary = self.parse_revs()

        commits_to_submit = [
            d for d in commits_to_submit_and_boundary if not d.boundary
        ]

        # NB: A little bit of redundant parsing here, because we will re-parse
        # commits that we had already parsed in commits_to_submit, and we will
        # also parse prefix even if it's not being processed, but it's at most ~10
        # extra parses so whatever
        commits_to_rebase_and_boundary = ghstack.git.split_header(
            self.sh.git(
                "rev-list",
                "--boundary",
                "--header",
                "--topo-order",
                # Get all commits reachable from HEAD...
                "HEAD",
                # ...as well as all the commits we are going to submit...
                *[c.commit_id for c in commits_to_submit],
                # ...but we don't need any commits that aren't draft
                f"^{self.remote_name}/{self.base}",
            )
        )

        commits_to_rebase = [
            d for d in commits_to_rebase_and_boundary if not d.boundary
        ]

        # NB: commits_to_rebase always contains all diffs to submit (because
        # we always have to generate orig commits for submitted diffs.)
        # However, commits_to_submit does not necessarily contain
        # diffs_to_rebase.  If you ask to submit only a prefix of your current
        # stack, the suffix is not to be submitted, but it needs to be rebased
        # (to, e.g., update the ghstack-source-id)

        commit_count = len(commits_to_submit)

        if commit_count == 0:
            raise RuntimeError(
                "There appears to be no commits to process, based on the revs you passed me."
            )

        # This is not really accurate if you're doing a fancy pattern;
        # if this is a problem file us a bug.
        run_pre_ghstack_hook(
            self.sh, f"{self.remote_name}/{self.base}", commits_to_submit[0].commit_id
        )

        # NB: This is duplicative with prepare_submit to keep the
        # check_invariants code small, as it counts as TCB
        pre_branch_state_index: Dict[GitCommitHash, PreBranchState] = {}
        if self.check_invariants:
            for h in commits_to_submit:
                d = ghstack.git.convert_header(h, self.github_url)
                if d.pull_request_resolved is not None:
                    ed = self.elaborate_diff(d)
                    pre_branch_state_index[h.commit_id] = PreBranchState(
                        head_commit_id=GitCommitHash(
                            self.sh.git(
                                "rev-parse", f"{self.remote_name}/{ed.head_ref}"
                            )
                        ),
                        base_commit_id=GitCommitHash(
                            self.sh.git(
                                "rev-parse", f"{self.remote_name}/{ed.base_ref}"
                            )
                        ),
                    )

        # NB: deduplicates
        commit_index = {
            h.commit_id: h
            for h in itertools.chain(
                commits_to_submit_and_boundary, commits_to_rebase_and_boundary
            )
        }
        diff_meta_index, rebase_index = self.prepare_updates(
            commit_index, commits_to_submit, commits_to_rebase
        )
        logging.debug("rebase_index = %s", rebase_index)
        diffs_to_submit = [
            diff_meta_index[h.commit_id]
            for h in commits_to_submit
            if h.commit_id in diff_meta_index
        ]
        self.push_updates(diffs_to_submit)
        if new_head := rebase_index.get(
            old_head := GitCommitHash(self.sh.git("rev-parse", "HEAD"))
        ):
            self.sh.git("reset", "--soft", new_head)
        # TODO: print out commit hashes for things we rebased but not accessible
        # from HEAD

        if self.check_invariants:
            self.fetch()
            for h in commits_to_submit:
                # TODO: Do a separate check for this
                if h.commit_id not in diff_meta_index:
                    continue
                new_orig = diff_meta_index[h.commit_id].orig
                self.check_invariants_for_diff(
                    h.commit_id,
                    new_orig,
                    pre_branch_state_index.get(h.commit_id),
                )
                # Test that orig commits are accessible from HEAD, if the old
                # commits were accessible.  And if the commit was not
                # accessible, it better not be accessible now!
                if self.sh.git(
                    "merge-base", "--is-ancestor", h.commit_id, old_head, exitcode=True
                ):
                    assert new_head is not None
                    assert self.sh.git(
                        "merge-base", "--is-ancestor", new_orig, new_head, exitcode=True
                    )
                else:
                    if new_head is not None:
                        assert not self.sh.git(
                            "merge-base",
                            "--is-ancestor",
                            new_orig,
                            new_head,
                            exitcode=True,
                        )

        # NB: earliest first, which is the intuitive order for unit testing
        return list(reversed(diffs_to_submit))

    # ~~~~~~~~~~~~~~~~~~~~~~~~
    # The main pieces

    def fetch(self) -> None:
        # TODO: Potentially we could narrow this refspec down to only OUR gh
        # branches.  However, this will interact poorly with cross-author
        # so it needs to be thought more carefully
        self.sh.git(
            "fetch",
            "--prune",
            self.remote_name,
            f"+refs/heads/*:refs/remotes/{self.remote_name}/*",
        )

    def parse_revs(self) -> List[ghstack.git.CommitHeader]:
        # There are two distinct usage patterns:
        #
        #   1. You may want to submit only HEAD, but not everything below it,
        #      because you only did minor changes to the commits below and
        #      you want to let the CI finish without those changes.
        #      See https://github.com/ezyang/ghstack/issues/165
        #
        #   2. I want to submit a prefix of the stack, because I'm still working
        #      on the top of the stack and don't want to spam people with
        #      useless changes.  See https://github.com/ezyang/ghstack/issues/101
        #
        # If we use standard git log/rev-list style parsing, you get (2) by
        # default because a single commit implies a reachability constraint.
        # Specifying (1) is a bit inconvenient; you have to say something
        # like `ghstack submit HEAD~..`.  In particular, both (1) and (2) would like
        # the meaning of `ghstack submit HEAD` to do different things (1 wants a single
        # commit, whereas 2 wants everything reachable from the commit.)
        #
        # To resolve the ambiguity, we introduce a new command line argument
        # --no-stack (analogous to the --stack argument on jf) which disables
        # "stacky" behavior.  With --no-stack, we only submit HEAD by default
        # and you can also specify a specific commit to submit if you like
        # (if this commit is not reachable from HEAD, we will tell you how
        # to checkout the updated commit.)  If you specify multiple commits,
        # we will process each of them in turn.  Ranges are not supported; use
        # git rev-list to preprocess them into single commits first (in principle
        # we could support this, but it would require determining if a REV was
        # a range versus a commit, as different handling would be necessary
        # in each case.)
        #
        # Without --no-stack, we use standard git rev-list semantics.  Some of the
        # more advanced spellings can be counterintuitive, but `ghstack submit X`
        # is equivalent to checking out X and then performing ghstack (and then
        # restacking HEAD on top, if necessary), and you can say `X..Y`
        # (exclusive-inclusive) to specify a specific range of commits (oddly,
        # `X..` will do what you expect, but `..Y` will almost always be empty.)
        # But I expect this to be fairly niche.
        #
        # In both cases, we support submitting multiple commits, because the set
        # of commits you specify affects what rebasing we do, which is sometimes
        # not conveniently done by calling ghstack multiple times.

        # Interestingly, the default is the same whether it is --stack or
        # --no-stack
        revs = ("HEAD",) if not self.revs else self.revs

        # In jf, we determine whether or not we should consider a diff by checking
        # if it is draft or not (only draft commits can be posted).  Git doesn't
        # have a directly analogous concept, so we need some other strategy.  A
        # simple approach is to inspect the base branch in the upstream
        # repository, and exclude all commits which are reachable from it.
        # We don't want to blast ALL remote branches into the list here though;
        # it's possible the draft commits were pushed to the remote repo for
        # unrelated reasons, and we don't want to treat them as non-draft if
        # this happens!

        commits_to_submit_and_boundary = []
        if self.stack:
            # Easy case, make rev-list do the hard work
            commits_to_submit_and_boundary.extend(
                ghstack.git.split_header(
                    self.sh.git(
                        "rev-list",
                        "--header",
                        "--topo-order",
                        "--boundary",
                        *revs,
                        f"^{self.remote_name}/{self.base}",
                    ),
                )
            )
        else:
            # Hard case, need to query rev-list repeatedly
            for rev in revs:
                # We still do rev-list as it gets us the parent commits
                r = ghstack.git.split_header(
                    self.sh.git(
                        "rev-list",
                        "--header",
                        "--topo-order",
                        "--boundary",
                        f"{rev}~..{rev}",
                        f"^{self.remote_name}/{self.base}",
                    ),
                )
                if not r:
                    raise RuntimeError(
                        f"{r} doesn't seem to be a commit that can be submitted!"
                    )
                # NB: There may be duplicate commits that are
                # boundary/not-boundary, but once we generate commits_to_submit
                # there should not be any dupes if rev was not duped
                # TODO: check no dupe revs, though actually it's harmless
                commits_to_submit_and_boundary.extend(r)

        return commits_to_submit_and_boundary

    def prepare_updates(
        self,
        commit_index: Dict[GitCommitHash, ghstack.git.CommitHeader],
        commits_to_submit: List[ghstack.git.CommitHeader],
        commits_to_rebase: List[ghstack.git.CommitHeader],
    ) -> Tuple[Dict[GitCommitHash, DiffMeta], Dict[GitCommitHash, GitCommitHash]]:
        # Prepare diffs in reverse topological order.
        # (Reverse here is important because we must have processed parents
        # first.)
        # NB: some parts of the algo (namely commit creation) could
        # be done in parallel
        submit_set = set(h.commit_id for h in commits_to_submit)
        diff_meta_index: Dict[GitCommitHash, DiffMeta] = {}
        rebase_index: Dict[GitCommitHash, GitCommitHash] = {}
        for commit in reversed(commits_to_rebase):
            submit = commit.commit_id in submit_set
            parents = commit.parents
            if len(parents) != 1:
                raise RuntimeError(
                    "The commit {} has {} parents, which makes my head explode.  "
                    "`git rebase -i` your diffs into a stack, then try again.".format(
                        commit.commit_id, len(parents)
                    )
                )
            parent = parents[0]
            diff_meta = None
            parent_commit = commit_index[parent]
            parent_diff_meta = diff_meta_index.get(parent)
            diff = ghstack.git.convert_header(commit, self.github_url)
            diff_meta = self.process_commit(
                parent_commit,
                parent_diff_meta,
                diff,
                (
                    self.elaborate_diff(diff)
                    if diff.pull_request_resolved is not None
                    else None
                ),
                submit,
            )
            if diff_meta is not None:
                diff_meta_index[commit.commit_id] = diff_meta

            # Check if we actually need to rebase it, or can use it as is
            # NB: This is not in process_commit, because we may need
            # to rebase a commit even if we didn't submit it
            if parent in rebase_index or diff_meta is not None:
                # Yes, we need to rebase it

                if diff_meta is not None:
                    # use the updated commit message, if it exists
                    commit_msg = diff_meta.commit_msg
                else:
                    commit_msg = commit.commit_msg

                if rebase_id := rebase_index.get(commit.parents[0]):
                    # use the updated base, if it exists
                    base_commit_id = rebase_id
                else:
                    base_commit_id = parent

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
                    diff_meta.push_branches.orig.update(GhCommit(new_orig, commit.tree))

                rebase_index[commit.commit_id] = new_orig

        return diff_meta_index, rebase_index

    def elaborate_diff(
        self, diff: ghstack.diff.Diff, *, is_ghexport: bool = False
    ) -> DiffWithGitHubMetadata:
        """
        Query GitHub API for the current title, body and closed? status
        of the pull request corresponding to a ghstack.diff.Diff.
        """

        assert diff.pull_request_resolved is not None
        assert diff.pull_request_resolved.owner == self.repo_owner
        assert diff.pull_request_resolved.repo == self.repo_name

        number = diff.pull_request_resolved.number
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
                  baseRefName
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
the line 'Pull-Request' and then run ghexport again.
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
to delete the line 'Pull-Request' and then run ghexport again.
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
                        diff.oid
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
            title, pr_body = self._default_title_and_body(diff, pr_body)

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
        m_comment_id = RE_GHSTACK_COMMENT_ID.search(remote_summary.commit_msg)
        comment_id = int(m_comment_id.group(1)) if m_comment_id else None

        return DiffWithGitHubMetadata(
            diff=diff,
            title=title,
            body=pr_body,
            closed=r["closed"],
            number=number,
            username=username,
            ghnum=gh_number,
            remote_source_id=remote_source_id,
            comment_id=comment_id,
            pull_request_resolved=diff.pull_request_resolved,
            head_ref=r["headRefName"],
            base_ref=r["baseRefName"],
        )

    def process_commit(
        self,
        base: ghstack.git.CommitHeader,
        base_diff_meta: Optional[DiffMeta],
        diff: ghstack.diff.Diff,
        elab_diff: Optional[DiffWithGitHubMetadata],
        submit: bool,
    ) -> Optional[DiffMeta]:
        # Do not process poisoned commits
        if "[ghstack-poisoned]" in diff.summary:
            self._raise_poisoned()

        # Do not process closed commits
        if elab_diff is not None and elab_diff.closed:
            if self.direct:
                self._raise_needs_rebase()
            return None

        # Edge case: check if the commit is empty; if so skip submitting
        if base.tree == diff.tree:
            self._warn_empty(diff, elab_diff)
            # Maybe it can just fall through here and make an empty PR fine
            assert not self.direct, "empty commits with direct NYI"
            return None

        username = elab_diff.username if elab_diff is not None else self.username
        ghnum = elab_diff.ghnum if elab_diff is not None else self._allocate_ghnum()
        self._sanity_check_ghnum(username, ghnum)

        # Create base/head commits if needed
        push_branches, base_branch = self._create_non_orig_branches(
            base, base_diff_meta, diff, elab_diff, username, ghnum, submit
        )

        # Create pull request, if needed
        if elab_diff is None:
            # Need to push branches now rather than later, so we can create PR
            self._git_push(
                [push_spec(p[0], branch(username, ghnum, p[1])) for p in push_branches]
            )
            push_branches.clear()
            elab_diff = self._create_pull_request(diff, base_diff_meta, ghnum)
            what = "Created"
            new_pr = True
        else:
            if not push_branches:
                what = "Skipped"
            elif push_branches.head is None:
                what = "Skipped (next updated)"
            else:
                what = "Updated"
            new_pr = False

        pull_request_resolved = elab_diff.pull_request_resolved

        if not new_pr:
            # Underlying diff can be assumed to have the correct metadata, we
            # only need to update it
            commit_msg = self._update_source_id(diff.summary, elab_diff)
        else:
            # Need to insert metadata for the first time
            # Using our Python implementation of interpret-trailers
            trailers_to_add = [f"ghstack-source-id: {diff.source_id}"]

            if self.direct:
                trailers_to_add.append(f"ghstack-comment-id: {elab_diff.comment_id}")

            trailers_to_add.append(f"Pull-Request: {pull_request_resolved.url()}")

            commit_msg = ghstack.trailers.interpret_trailers(
                strip_mentions(diff.summary.rstrip()), trailers_to_add
            )

        return DiffMeta(
            elab_diff=elab_diff,
            commit_msg=commit_msg,
            push_branches=push_branches,
            what=what,
            base=base_branch,
        )

    def _raise_poisoned(self) -> None:
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

    def _raise_needs_rebase(self) -> None:
        raise RuntimeError(
            """\
ghstack --next requires all PRs in the stack to be open.  One of your PRs
is closed (likely due to being merged).  Please rebase to upstream and try again.
"""
        )

    def _warn_empty(
        self, diff: ghstack.diff.Diff, elab_diff: Optional[DiffWithGitHubMetadata]
    ) -> None:
        self.ignored_diffs.append((diff, elab_diff))
        logging.warning(
            "Skipping '{}', as the commit now has no changes".format(diff.title)
        )

    def _allocate_ghnum(self) -> GhNumber:
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

        def _is_valid_ref(ref: str) -> bool:
            splits = ref.split("/")
            if len(splits) < 3:
                return False
            else:
                return splits[-2].isnumeric()

        refs = list(filter(_is_valid_ref, refs))
        max_ref_num = max(int(ref.split("/")[-2]) for ref in refs) if refs else 0
        return GhNumber(str(max_ref_num + 1))

    def _sanity_check_ghnum(self, username: str, ghnum: GhNumber) -> None:
        if (username, ghnum) in self.seen_ghnums:
            raise RuntimeError(
                "Something very strange has happened: a commit for "
                f"the gh/{username}/{ghnum} occurs twice in your local "
                "commit stack.  This is usually because of a botched "
                "rebase.  Please take a look at your git log and seek "
                "help from your local Git expert."
            )
        self.seen_ghnums.add((username, ghnum))

    def _update_source_id(self, summary: str, elab_diff: DiffWithGitHubMetadata) -> str:
        m_local_source_id = RE_GHSTACK_SOURCE_ID.search(summary)
        if m_local_source_id is None:
            # This is for an already submitted PR, so there should
            # already be a source id on it.  But there isn't.
            # For BC, just slap on a source ID.  After BC is no longer
            # needed, we can just error in this case; however, this
            # situation is extremely likely to happen for preexisting
            # stacks.
            logging.warning(
                "Local commit has no ghstack-source-id; assuming that it is "
                "up-to-date with remote."
            )
            summary = "{}\nghstack-source-id: {}".format(
                summary, elab_diff.diff.source_id
            )
        else:
            local_source_id = m_local_source_id.group(1)
            if elab_diff.remote_source_id is None:
                # This should also be an error condition, but I suppose
                # it can happen in the wild if a user had an aborted
                # ghstack run, where they updated their head pointer to
                # a copy with source IDs, but then we failed to push to
                # orig.  We should just go ahead and push in that case.
                logging.warning(
                    "Remote commit has no ghstack-source-id; assuming that we are "
                    "up-to-date with remote."
                )
            elif local_source_id != elab_diff.remote_source_id and not self.force:
                logging.debug(
                    f"elab_diff.remote_source_id = {elab_diff.remote_source_id}"
                )
                # TODO: have a 'ghstack pull' remediation for this case
                raise RuntimeError(
                    "Cowardly refusing to push an update to GitHub, since it "
                    "looks another source has updated GitHub since you last "
                    "pushed.  If you want to push anyway, rerun this command "
                    "with --force.  Otherwise, diff your changes against "
                    "{} and reapply them on top of an up-to-date commit from "
                    "GitHub.".format(local_source_id)
                )
            summary = RE_GHSTACK_SOURCE_ID.sub(
                "ghstack-source-id: {}\n".format(elab_diff.diff.source_id), summary
            )
        return summary

    # NB: mutates GhBranch
    def _resolve_gh_branch(
        self, kind: str, gh_branch: GhBranch, username: str, ghnum: GhNumber
    ) -> None:
        remote_ref = self.remote_name + "/" + branch(username, ghnum, kind)
        (remote_commit,) = ghstack.git.split_header(
            self.sh.git("rev-list", "--header", "-1", remote_ref)
        )
        gh_branch.commit = GhCommit(remote_commit.commit_id, remote_commit.tree)

    # Precondition: these branches exist
    def _resolve_gh_branches(self, username: str, ghnum: GhNumber) -> GhBranches:
        push_branches = GhBranches()
        self._resolve_gh_branch("orig", push_branches.orig, username, ghnum)
        self._resolve_gh_branch("head", push_branches.head, username, ghnum)
        if self.direct:
            self._resolve_gh_branch("next", push_branches.next, username, ghnum)
        else:
            self._resolve_gh_branch("base", push_branches.base, username, ghnum)
        return push_branches

    def _create_non_orig_branches(
        self,
        base: ghstack.git.CommitHeader,
        base_diff_meta: Optional[DiffMeta],
        diff: ghstack.diff.Diff,
        elab_diff: Optional[DiffWithGitHubMetadata],
        username: str,
        ghnum: GhNumber,
        submit: bool,
    ) -> Tuple[GhBranches, str]:
        # How exactly do we submit a commit to GitHub?
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
        # First time submission proceeds similarly, except that we no longer
        # need to create a parent pointer to the previous base/head.
        #
        # Note that, counterintuitively, the base of a diff has no
        # relationship to the head of an earlier diff on the stack.  This
        # makes it possible to selectively only update one diff in a stack
        # without updating any others.  This also makes our handling uniform
        # even if you rebase a commit backwards: you just see that the base
        # is updated to also remove changes.

        if elab_diff is not None:
            push_branches = self._resolve_gh_branches(username, ghnum)
        else:
            push_branches = GhBranches()

        # Initialize head arguments (as original head parent must come first
        # in parents list)
        head_args: List[str] = []
        if push_branches.head.commit is not None:
            head_args.extend(("-p", push_branches.head.commit.commit_id))

        # Create base commit if necessary
        updated_base = False
        if not self.direct:
            base_branch = branch_base(username, ghnum)
            if (
                push_branches.base.commit is None
                or push_branches.base.commit.tree != base.tree
            ):
                # Base is not the same, perform base update
                updated_base = True
                base_args: List[str] = []
                if push_branches.base.commit is not None:
                    base_args.extend(("-p", push_branches.base.commit.commit_id))
                # We don't technically need to do this, but often tooling
                # relies on pull requests being able to compute merge-base
                # with the main branch.  While the result you get here can be
                # misleading (in particular, the merge-base will not
                # incorporate changes on base, and if a ghstack has been
                # rebased backwards in time, the merge-base will be stuck
                # on the more recent commit), it is useful so we put it in.
                extra_base = self.sh.git(
                    "merge-base", base.commit_id, f"{self.remote_name}/{self.base}"
                )
                if push_branches.base.commit is None or not self.sh.git(
                    "merge-base",
                    "--is-ancestor",
                    extra_base,
                    push_branches.base.commit.commit_id,
                    exitcode=True,
                ):
                    base_args.extend(("-p", extra_base))
                new_base = GitCommitHash(
                    self.sh.git(
                        "commit-tree",
                        *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                        *base_args,
                        base.tree,
                        input="{} (base update)\n\n[ghstack-poisoned]".format(self.msg),
                    )
                )
                head_args.extend(("-p", new_base))
                push_branches.base.update(GhCommit(new_base, base.tree))
        else:
            # So, there is some complication here.  We're computing what base
            # to use based on the local situation on the user diff stack, but
            # the remote merge structure may disagree with our local
            # situation.  For example, suppose I have a commit stack A - B,
            # and then I insert a new commit A - M - B between them;
            # previously B would have been based on A, but now it is based
            # on M.  What should happen here?
            #
            # Here are the high level correctness conditions:
            # - No force pushes (history must be preserved)
            # - GitHub displays a diff which is equivalent to the original
            #   user diff
            #
            # It turns out the logic here is fine, and the only thing it
            # chokes on is rebasing back in time on master branch (you can't
            # go back in time on PR branches, so this is a moot point there.)
            # The problem is suppose you have:
            #
            #   A - B - C
            #    \   \
            #     M2  M1  # M1 was cherry-picked onto A, becoming M2
            #
            # In branch form, this becomes:
            #
            #   A - B - C
            #    \   \
            #     \   M1 - M2
            #      \       /
            #       \-----/
            #
            # However, the merge base for C and M2 will always be computed to
            # be B, because B is an ancestor of both C and M2, and it always
            # beets out A (which is an ancestor of B).  This means that you
            # will diff M2 against B, which will typically result in "remove
            # changes from B" spuriously showing up on the PR.
            #
            # When heads are always monotonically moving forward in time,
            # there is not any problem with progressively more complicated
            # merge histories, because we always specify the "correct" base
            # branch.  For example, consider:
            #
            #   A - B
            #        \
            #         \- X - Y1
            #          \
            #           \- Y2
            #
            # Where Y1 is cherry-picked off of X onto B directly.  In branch
            # form, this becomes:
            #
            #   A - B
            #        \
            #         \- X - Y1 - Y2
            #
            # But we update the base branch to be B, so we correctly diff Y2
            # against B (where here, the tree for Y2 no longer incorporates
            # the changes for X).
            #
            # What does NOT work in this situation is if you manually (outside
            # of ghstack) retarget Y2 back at X; we will spuriously report
            # that the diff X and Y2 removes the changes from X.  If you use
            # ghstack, however, we will do this:
            #
            #   A - B
            #        \
            #         \- X - Y1 - Y2 - Y3
            #
            # Where here Y3 has restored the changes from X, so the diff from
            # X to Y3 checks out.
            #
            # It turns out there are a subset of manipulations, for which it
            # is always safe to change the target base commit from GitHub UI
            # without pushing a new commit.  Intuitively, the idea is that
            # once you add a commit as a merge base, you can't take it back:
            # we always consider that branch to have been "merged in".  So
            # you can effectively only ever insert new commits between
            # pre-existing commits, but once a commit depends on another
            # commit, that dependency must always exist.  I'm still
            # considering whether or not we should force push by default in
            # this sort of situation.
            #
            # By the way, what happens if you reorder commits?  You get this
            # funny looking graph:
            #
            # A - B
            #      \
            #       X - Y - Y2
            #        \        \
            #         \------- X2

            # We never have to create a base commit, we read it out from
            # the base
            if base_diff_meta is not None:
                # The base was submitted the normal way (merge base is either
                # next or head)
                #
                # We can always use next, because if head is OK, head will have
                # been advanced to next anyway
                #
                # TODO: I do not feel this can be None
                if base_diff_meta.head is not None:
                    # TODO: This assert is sus, next may be ahed of head
                    assert base_diff_meta.next == base_diff_meta.head
                new_base = base_diff_meta.next

                if base_diff_meta.next == base_diff_meta.head:
                    # use head
                    base_branch = branch_head(
                        base_diff_meta.username, base_diff_meta.ghnum
                    )
                else:
                    # use next
                    base_branch = branch_next(
                        base_diff_meta.username, base_diff_meta.ghnum
                    )
            else:
                # TODO: test that there isn't a more recent ancestor
                # such that this doesn't actually work
                new_base = base.commit_id

                base_branch = GitCommitHash(self.base)

            # Check if the base is already an ancestor, don't need to add it
            # if so
            if push_branches.next.commit is not None and self.sh.git(
                "merge-base",
                "--is-ancestor",
                new_base,
                push_branches.next.commit.commit_id,
                exitcode=True,
            ):
                new_base = None

            if new_base is not None:
                updated_base = True
                head_args.extend(("-p", new_base))

        # Check head commit if necessary
        if (
            push_branches.head.commit is None
            or updated_base
            or push_branches.head.commit.tree != diff.tree
        ):
            new_head = GitCommitHash(
                self.sh.git(
                    "commit-tree",
                    *ghstack.gpg_sign.gpg_args_if_necessary(self.sh),
                    *head_args,
                    diff.tree,
                    input="{}\n\n[ghstack-poisoned]".format(self.msg),
                )
            )
            if self.direct:
                # only update head branch if we're actually submitting
                if submit:
                    push_branches.head.update(GhCommit(new_head, diff.tree))
                push_branches.next.update(GhCommit(new_head, diff.tree))
            else:
                push_branches.head.update(GhCommit(new_head, diff.tree))

        return push_branches, base_branch

    def _create_pull_request(
        self,
        diff: ghstack.diff.Diff,
        base_diff_meta: Optional[DiffMeta],
        ghnum: GhNumber,
    ) -> DiffWithGitHubMetadata:
        title, body = self._default_title_and_body(diff, None)
        head_ref = branch_head(self.username, ghnum)

        if self.direct:
            if base_diff_meta is None:
                base_ref = self.base
            else:
                base_ref = branch_head(base_diff_meta.username, base_diff_meta.ghnum)
        else:
            base_ref = branch_base(self.username, ghnum)

        # Time to open the PR
        # NB: GraphQL API does not support opening PRs
        r = self.github.post(
            "repos/{owner}/{repo}/pulls".format(
                owner=self.repo_owner, repo=self.repo_name
            ),
            title=title,
            head=head_ref,
            base=base_ref,
            body=body,
            maintainer_can_modify=True,
            draft=self.draft,
        )
        number = r["number"]

        comment_id = None
        if self.direct:
            rc = self.github.post(
                f"repos/{self.repo_owner}/{self.repo_name}/issues/{number}/comments",
                body=f"{self.stack_header}:\n* (to be filled)",
            )
            comment_id = rc["id"]

        logging.info("Opened PR #{}".format(number))

        pull_request_resolved = ghstack.diff.PullRequestResolved(
            owner=self.repo_owner,
            repo=self.repo_name,
            number=number,
            github_url=self.github_url,
        )

        return DiffWithGitHubMetadata(
            diff=diff,
            number=number,
            username=self.username,
            remote_source_id=diff.source_id,  # in sync
            comment_id=comment_id,
            title=title,
            body=body,
            closed=False,
            ghnum=ghnum,
            pull_request_resolved=pull_request_resolved,
            head_ref=head_ref,
            base_ref=base_ref,
        )

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
            # It is VERY important that we do base updates BEFORE real
            # head updates, otherwise GitHub will spuriously think that
            # the user pushed a number of patches as part of the PR,
            # when actually they were just from the (new) upstream
            # branch

            for diff, b in s.push_branches:
                if b == "orig":
                    q = force_push_branches
                elif b == "base":
                    q = base_push_branches
                else:
                    q = push_branches
                q.append(push_spec(diff, branch(s.username, s.ghnum, b)))
        # Careful!  Don't push master.
        # TODO: These pushes need to be atomic (somehow)
        if base_push_branches:
            self._git_push(base_push_branches)
        if push_branches:
            self._git_push(push_branches, force=self.force)
        if force_push_branches:
            self._git_push(force_push_branches, force=True)

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
            base_kwargs = {}
            if self.direct:
                base_kwargs["base"] = s.base
            else:
                assert s.base == s.elab_diff.base_ref
            stack_desc = self._format_stack(diffs_to_submit, s.number)
            self.github.patch(
                "repos/{owner}/{repo}/pulls/{number}".format(
                    owner=self.repo_owner, repo=self.repo_name, number=s.number
                ),
                # NB: this substitution does nothing on direct PRs
                body=RE_STACK.sub(
                    stack_desc,
                    s.body,
                ),
                title=s.title,
                **base_kwargs,
            )

            if s.elab_diff.comment_id is not None:
                self.github.patch(
                    f"repos/{self.repo_owner}/{self.repo_name}/issues/comments/{s.elab_diff.comment_id}",
                    body=stack_desc,
                )

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
            for d, elab_diff in reversed(self.ignored_diffs):
                if elab_diff is None:
                    print(" - {} {}".format(d.oid[:8], d.title))
                else:
                    noop_pr = True
                    print(
                        " - {} {} (was previously submitted as PR #{})".format(
                            d.oid[:8], d.title, elab_diff.number
                        )
                    )
            if noop_pr:
                print()
                print(
                    "I did NOT close or update PRs previously associated with these commits."
                )

    def check_invariants_for_diff(
        self,
        # the user diff is what the user actual sent us
        user_commit_id: GitCommitHash,
        orig_commit_id: GitCommitHash,
        pre_branch_state: Optional[PreBranchState],
    ) -> None:
        def is_git_commit_hash(h: str) -> bool:
            return re.match(r"[a-f0-9]{40}", h) is not None

        def assert_eq(a: Any, b: Any) -> None:
            assert a == b, f"{a} != {b}"

        assert is_git_commit_hash(user_commit_id)
        assert is_git_commit_hash(orig_commit_id)
        if pre_branch_state:
            assert is_git_commit_hash(pre_branch_state.head_commit_id)
            assert is_git_commit_hash(pre_branch_state.base_commit_id)

        # Fetch information about user/orig commits, do some basic sanity
        # checks
        user_commit, user_parent_commit = ghstack.git.split_header(
            self.sh.git("rev-list", "--header", "--boundary", "-1", user_commit_id)
        )
        assert_eq(user_commit.commit_id, user_commit_id)
        assert not user_commit.boundary
        assert user_parent_commit.boundary
        orig_commit, orig_parent_commit = ghstack.git.split_header(
            self.sh.git("rev-list", "--header", "--boundary", "-1", orig_commit_id)
        )
        assert_eq(orig_commit.commit_id, orig_commit_id)
        assert not orig_commit.boundary
        assert orig_parent_commit.boundary

        user_diff = ghstack.git.convert_header(user_commit, self.github_url)
        orig_diff = ghstack.git.convert_header(orig_commit, self.github_url)

        # 1. Used same PR if it exists
        if (pr := user_diff.pull_request_resolved) is not None:
            assert_eq(pr, orig_diff.pull_request_resolved)

        # 2. Must have a PR after running
        assert orig_diff.pull_request_resolved is not None

        # 3. We didn't corrupt the diff
        assert_eq(user_commit.tree, orig_commit.tree)
        assert_eq(user_parent_commit.tree, orig_parent_commit.tree)

        # 4. Orig diff has correct metadata
        m = RE_GHSTACK_SOURCE_ID.search(orig_commit.commit_msg)
        assert m is not None
        assert_eq(m.group(1), orig_commit.tree)

        elaborated_orig_diff = self.elaborate_diff(orig_diff)

        # 5. GitHub branches are correct
        head_ref = elaborated_orig_diff.head_ref
        assert_eq(head_ref, branch_head(self.username, elaborated_orig_diff.ghnum))
        (head_commit,) = ghstack.git.split_header(
            self.sh.git("rev-list", "--header", "-1", f"{self.remote_name}/{head_ref}")
        )
        assert_eq(head_commit.tree, user_commit.tree)

        base_ref = elaborated_orig_diff.base_ref

        if not self.direct:
            assert_eq(base_ref, branch_base(self.username, elaborated_orig_diff.ghnum))
        else:
            # TODO: assert the base is the head of the next branch, or main
            pass

        (base_commit,) = ghstack.git.split_header(
            self.sh.git("rev-list", "--header", "-1", f"{self.remote_name}/{base_ref}")
        )
        # TODO: tree equality may not hold for self.direct, figure out a
        # related invariant
        if not self.direct:
            assert_eq(base_commit.tree, user_parent_commit.tree)

        # 6.  Orig commit was correctly pushed
        assert_eq(
            orig_commit.commit_id,
            GitCommitHash(
                self.sh.git(
                    "rev-parse",
                    self.remote_name
                    + "/"
                    + branch_orig(self.username, elaborated_orig_diff.ghnum),
                )
            ),
        )

        # 7. Branches are either unchanged, or parent (no force pushes)
        # NB: head is always merged in as first parent
        # NB: you could relax this into an ancestor check
        if pre_branch_state:
            assert pre_branch_state.head_commit_id in [
                head_commit.commit_id,
                head_commit.parents[0],
            ]
            # The base branch can change if we changed base in direct mode
            if not self.direct:
                assert pre_branch_state.base_commit_id in [
                    base_commit.commit_id,
                    *([base_commit.parents[0]] if base_commit.parents else []),
                ]
        else:
            # Direct commit parent typically have base, as it will be the
            # main branch
            if not self.direct:
                pass
                # This is now set to the orig base
                # assert not base_commit.parents

        # 8. Head branch is not malformed
        assert self.sh.git(
            "merge-base",
            "--is-ancestor",
            base_commit.commit_id,
            head_commit.commit_id,
            exitcode=True,
        )

        # 9. Head and base branches are correctly poisoned
        assert "[ghstack-poisoned]" in head_commit.commit_msg

        # TODO: direct PR based on main are not poisoned base commit
        if not self.direct:
            assert "[ghstack-poisoned]" in base_commit.commit_msg

    # ~~~~~~~~~~~~~~~~~~~~~~~~
    # Small helpers

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

    def _default_title_and_body(
        self, diff: ghstack.diff.Diff, old_pr_body: Optional[str]
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
        title = diff.title
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
        commit_body = "".join(diff.summary.splitlines(True)[1:]).lstrip()
        # Don't store ghstack-source-id in the PR body; it will become
        # stale quickly
        commit_body = RE_GHSTACK_SOURCE_ID.sub("", commit_body)
        # Comment ID is not necessary; source of truth is orig commit
        commit_body = RE_GHSTACK_COMMENT_ID.sub("", commit_body)
        # Don't store Pull request in the PR body; it's
        # unnecessary
        commit_body = ghstack.diff.re_pull_request_resolved_w_sp(self.github_url).sub(
            "", commit_body
        )
        if self.direct:
            pr_body = f"{commit_body}{extra}"
        else:
            if starts_with_bullet(commit_body):
                commit_body = f"----\n\n{commit_body}"
            pr_body = "{}:\n* (to be filled)\n\n{}{}".format(
                self.stack_header, commit_body, extra
            )
        return title, pr_body

    def _git_push(self, branches: Sequence[str], force: bool = False) -> None:
        assert branches, "empty branches would push master, probably bad!"
        try:
            self.sh.git(
                "push",
                self.remote_name,
                "--no-verify",
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
