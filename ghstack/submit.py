#!/usr/bin/env python3

import re
import ghstack
import ghstack.git
import ghstack.shell
import ghstack.github
import ghstack.logging
from ghstack.typing import GitHubNumber, GitHubRepositoryId, GhNumber, GitCommitHash, GitTreeHash
from typing import List, Optional, NamedTuple, Tuple, Set
import logging

# Either "base", "head" or "orig"; which of the ghstack generated
# branches this diff corresponds to
BranchKind = str

# Metadata describing a diff we submitted to GitHub
DiffMeta = NamedTuple('DiffMeta', [
    ('title', str),
    ('number', GitHubNumber),
    ('body', str),
    ('ghnum', GhNumber),
    # What Git commit hash we should push to what branch
    ('push_branches', Tuple[Tuple[GitCommitHash, BranchKind], ...]),
    # A human-readable string like 'Created' which describes what
    # happened to this pull request
    ('what', str),
    ('closed', bool),
])


RE_STACK = re.compile(r'Stack.*:\n(\* [^\n]+\n)+')


# NB: This regex is fuzzy because the D1234567 identifier is typically
# linkified.
RE_DIFF_REV = re.compile(r'^Differential Revision:.+?(D[0-9]+)', re.MULTILINE)


# repo layout:
#   - gh/username/23/base -- what we think GitHub's current tip for commit is
#   - gh/username/23/head -- what we think base commit for commit is
#   - gh/username/23/orig -- the "clean" commit history, i.e., what we're
#                      rebasing, what you'd like to cherry-pick (???)
#                      (Maybe this isn't necessary, because you can
#                      get the "whole" diff from GitHub?  What about
#                      commit description?)


def branch(username: str, ghnum: GhNumber, kind: BranchKind
           ) -> GitCommitHash:
    return GitCommitHash("gh/{}/{}/{}".format(username, ghnum, kind))


def branch_base(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "base")


def branch_head(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "head")


def branch_orig(username: str, ghnum: GhNumber) -> GitCommitHash:
    return branch(username, ghnum, "orig")


STACK_HEADER = "Stack from [ghstack](https://github.com/ezyang/ghstack)"


def main(msg: Optional[str],
         username: str,
         github: ghstack.github.GitHubEndpoint,
         update_fields: bool = False,
         sh: Optional[ghstack.shell.Shell] = None,
         stack_header: str = STACK_HEADER,
         repo_owner: Optional[str] = None,
         repo_name: Optional[str] = None,
         short: bool = False,
         ) -> List[DiffMeta]:

    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    if repo_owner is None or repo_name is None:
        # Grovel in remotes to figure it out
        origin_url = sh.git("remote", "get-url", "origin")
        while True:
            m = re.match(r'^git@github.com:([^/]+)/([^.]+)(?:\.git)?$', origin_url)
            if m:
                repo_owner_nonopt = m.group(1)
                repo_name_nonopt = m.group(2)
                break
            m = re.search(r'github.com/([^/]+)/([^.]+)', origin_url)
            if m:
                repo_owner_nonopt = m.group(1)
                repo_name_nonopt = m.group(2)
                break
            raise RuntimeError(
                "Couldn't determine repo owner and name from url: {}"
                .format(origin_url))
    else:
        repo_owner_nonopt = repo_owner
        repo_name_nonopt = repo_name

    # TODO: Cache this guy
    repo = github.graphql(
        """
        query ($owner: String!, $name: String!) {
            repository(name: $name, owner: $owner) {
                id
                isFork
            }
        }""",
        owner=repo_owner_nonopt,
        name=repo_name_nonopt)["data"]["repository"]

    if repo["isFork"]:
        raise RuntimeError(
            "Cowardly refusing to upload diffs to a repository that is a "
            "fork.  ghstack expects 'origin' of your Git checkout to point "
            "to the upstream repository in question.  If your checkout does "
            "not comply, please adjust your remotes (by editing .git/config) "
            "to make it so.  If this message is in error, please register "
            "your complaint on GitHub issues (or edit this line to delete "
            "the check above.")
    repo_id = repo["id"]

    sh.git("fetch", "origin")
    base = GitCommitHash(sh.git("merge-base", "origin/master", "HEAD"))

    # compute the stack of commits to process (reverse chronological order),
    stack = ghstack.git.parse_header(
        sh.git("rev-list", "--header", "^" + base, "HEAD"))

    # compute the base commit
    base_obj = ghstack.git.split_header(sh.git("rev-list", "--header", "^" + base + "^@", base))[0]

    assert len(stack) > 0

    ghstack.logging.record_status(
        "{} \"{}\"".format(stack[0].oid[:9], stack[0].title))

    submitter = Submitter(github=github,
                          sh=sh,
                          username=username,
                          repo_owner=repo_owner_nonopt,
                          repo_name=repo_name_nonopt,
                          repo_id=repo_id,
                          base_commit=base,
                          base_tree=base_obj.tree(),
                          stack_header=stack_header,
                          update_fields=update_fields,
                          msg=msg,
                          short=short)

    # start with the earliest commit
    skip = True
    for s in reversed(stack):
        current_oid = None
        if s.gh_metadata is not None:
            current_oid = GitCommitHash(sh.git(
                "rev-parse", "origin/" + branch_orig(username, s.gh_metadata.ghnum)
            ))
        if skip and s.oid == current_oid:
            submitter.skip_commit(s)
        else:
            skip = False
            submitter.process_commit(s)
    submitter.post_process()

    # NB: earliest first
    return submitter.stack_meta


def all_branches(username: str, ghnum: GhNumber) -> Tuple[str, str, str]:
    return (branch_base(username, ghnum),
            branch_head(username, ghnum),
            branch_orig(username, ghnum))


def push_spec(commit: GitCommitHash, branch: str) -> str:
    return "{}:refs/heads/{}".format(commit, branch)


class Submitter(object):
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

    # The base commit of the prev diff we submitted.  This
    # corresponds to the 'head' branch in GH.
    # INVARIANT: This is REALLY a hash, and not some random ref!
    base_commit: GitCommitHash

    # The base tree of the prev diff we submitted.
    # INVARIANT: This is base_commit^{tree}!  Cached here so we
    # don't have to keep asking about it.
    base_tree: GitTreeHash

    # The orig commit of the prev diff we submitted.  This
    # corresponds to the 'orig' branch in GH.
    base_orig: GitCommitHash

    # Message describing the update to the stack that was done
    msg: Optional[str]

    # Description of all the diffs we submitted; to be populated
    # by Submitter.
    stack_meta: List[DiffMeta]

    # Set of seen ghnums
    seen_ghnums: Set[GhNumber]

    # String used to describe the stack in question
    stack_header: str

    # Clobber existing PR description with local commit message
    update_fields: bool

    # Print only PR URL to stdout
    short: bool

    def __init__(
            self,
            github: ghstack.github.GitHubEndpoint,
            sh: ghstack.shell.Shell,
            username: str,
            repo_owner: str,
            repo_name: str,
            repo_id: GitHubRepositoryId,
            base_commit: GitCommitHash,
            base_tree: GitTreeHash,
            stack_header: str,
            update_fields: bool,
            msg: Optional[str],
            short: bool):
        self.github = github
        self.sh = sh
        self.username = username
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_id = repo_id
        self.base_commit = base_commit
        self.base_orig = base_commit
        self.base_tree = base_tree
        self.update_fields = update_fields
        self.stack_header = stack_header
        self.stack_meta = []
        self.seen_ghnums = set()
        self.msg = msg
        self.short = short

    def _default_title_and_body(self, commit: ghstack.diff.Diff,
                                old_pr_body: Optional[str]
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
        extra = ''
        if old_pr_body is not None:
            # Look for tags we should preserve, and keep them
            m = RE_DIFF_REV.search(old_pr_body)
            if m:
                extra = (
                    "\n\nDifferential Revision: "
                    "[{phabdiff}]"
                    "(https://our.internmc.facebook.com/intern/diff/{phabdiff})"
                ).format(phabdiff=m.group(1))
        pr_body = (
            "{}:\n* (to be filled)\n\n{}{}"
            .format(self.stack_header,
                    ''.join(commit.summary.splitlines(True)[1:]).lstrip(),
                    extra)
        )
        return title, pr_body

    def _query_pr(self, commit: ghstack.diff.Diff) -> Tuple[str, str, bool]:
        """
        Query GitHub API for the current title, body and closed? status
        of the pull request corresponding to a ghstack.diff.Diff.

        Precondition: Diff has valid gh-metadata
        """

        assert commit.gh_metadata is not None
        number = commit.gh_metadata.number
        # TODO: There is no reason to do a node query here; we can
        # just look up the repo the old fashioned way
        r = self.github.graphql("""
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
        """, repo_id=self.repo_id, number=number)["data"]["node"]["pullRequest"]

        assert r['headRefName'] == branch_head(self.username, commit.gh_metadata.ghnum)

        # NB: Technically, we don't need to pull this information at
        # all, but it's more convenient to unconditionally edit
        # title/body when we update the pull request info
        title = r['title']
        pr_body = r['body']
        if self.update_fields:
            title, pr_body = self._default_title_and_body(commit, pr_body)

        return title, pr_body, r['closed']

    def skip_commit(self, commit: ghstack.diff.Diff) -> None:
        """
        Skip a diff, because we happen to know that there were no local
        changes.  We have to update the internal metadata of the Submitter,
        so you're still obligated to call this even if you think there's
        nothing to do.
        """

        # You can't skip a commit unless we've already uploaded it!
        assert commit.gh_metadata is not None

        number = commit.gh_metadata.number
        ghnum = commit.gh_metadata.ghnum

        # Read out information about the PR from GitHub anyway;
        # we'll need it to write out the stack in the descriptions
        title, pr_body, closed = self._query_pr(commit)

        self.stack_meta.append(DiffMeta(
            title=title,
            number=number,
            body=pr_body,
            ghnum=ghnum,
            push_branches=(),
            what='Skipped',
            closed=closed,
        ))

        self.base_commit = GitCommitHash(self.sh.git(
            "rev-parse", "origin/" + branch_head(self.username, ghnum)))
        self.base_orig = GitCommitHash(self.sh.git(
            "rev-parse", "origin/" + branch_orig(self.username, ghnum)
        ))
        self.base_tree = GitTreeHash(self.sh.git(
            "rev-parse", self.base_orig + "^{tree}"
        ))

    def _process_new_commit(self, commit: ghstack.diff.Diff) -> None:
        """
        Process a diff that has never been pushed to GitHub before.
        """

        assert commit.gh_metadata is None

        title, pr_body = self._default_title_and_body(commit, None)

        # Determine the next available GhNumber.  We do this by
        # iterating through known branches and keeping track
        # of the max.  The next available GhNumber is the next number.
        # This is technically subject to a race, but we assume
        # end user is not running this script concurrently on
        # multiple machines (you bad bad)
        refs = self.sh.git(
            "for-each-ref",
            "refs/remotes/origin/gh/{}".format(self.username),
            "--format=%(refname)").split()
        max_ref_num = max(int(ref.split('/')[-2]) for ref in refs) \
            if refs else 0
        ghnum = GhNumber(str(max_ref_num + 1))
        assert ghnum not in self.seen_ghnums
        self.seen_ghnums.add(ghnum)

        # Create the incremental pull request diff
        tree = commit.patch.apply(self.sh, self.base_tree)
        new_pull = GitCommitHash(
            self.sh.git("commit-tree", tree,
                        "-p", self.base_commit,
                        input=commit.summary))

        # Push the branches, so that we can create a PR for them
        new_branches = (
            push_spec(new_pull, branch_head(self.username, ghnum)),
            push_spec(self.base_commit, branch_base(self.username, ghnum))
        )
        self.sh.git(
            "push",
            "origin",
            *new_branches,
        )
        self.github.push_hook(new_branches)

        # Time to open the PR
        # NB: GraphQL API does not support opening PRs
        r = self.github.post(
            "repos/{owner}/{repo}/pulls"
            .format(owner=self.repo_owner, repo=self.repo_name),
            title=title,
            head=branch_head(self.username, ghnum),
            base=branch_base(self.username, ghnum),
            body=pr_body,
            maintainer_can_modify=True,
        )
        number = r['number']

        logging.info("Opened PR #{}".format(number))

        # Update the commit message of the local diff with metadata
        # so we can correlate these later
        commit_msg = ("{commit_msg}\n\n"
                      "gh-metadata: "
                      "{owner} {repo} {number} {branch_head}"
                      .format(commit_msg=commit.summary.rstrip(),
                              owner=self.repo_owner,
                              repo=self.repo_name,
                              number=number,
                              branch_head=branch_head(self.username,
                                                      ghnum)))

        # TODO: Try harder to preserve the old author/commit
        # information (is it really necessary? Check what
        # --amend does...)
        new_orig = GitCommitHash(self.sh.git(
            "commit-tree",
            tree,
            "-p", self.base_orig,
            input=commit_msg))

        self.stack_meta.append(DiffMeta(
            title=title,
            number=number,
            body=pr_body,
            ghnum=ghnum,
            push_branches=((new_orig, 'orig'), ),
            what='Created',
            closed=False,
        ))

        self.base_commit = new_pull
        self.base_orig = new_orig
        self.base_tree = tree

    def _process_old_commit(self, commit: ghstack.diff.Diff) -> None:
        """
        Process a diff that has an existing upload to GitHub.
        """

        assert commit.gh_metadata is not None
        if commit.gh_metadata.username != self.username:
            # This is someone else's diff
            raise RuntimeError(
                "cannot handle stack from diffs of other people yet")

        ghnum = commit.gh_metadata.ghnum
        number = commit.gh_metadata.number

        if ghnum in self.seen_ghnums:
            raise RuntimeError(
                "Something very strange has happened: a commit for "
                "the pull request #{} occurs twice in your local "
                "commit stack.  This is usually because of a botched "
                "rebase.  Please take a look at your git log and seek "
                "help from your local Git expert.".format(number))
        self.seen_ghnums.add(ghnum)

        title, pr_body, closed = self._query_pr(commit)

        logging.info("Pushing to #{}".format(number))

        # We've got an update to do!  But what exactly should we
        # do?
        #
        # Here are a number of situations which may have
        # occurred.
        #
        #   1. None of the parent commits changed, and this is
        #      the first change we need to push an update to.
        #
        #   2. A parent commit changed, so we need to restack
        #      this commit too.  (You can't easily tell distinguish
        #      between rebase versus rebase+amend)
        #
        #   3. The parent is now master (any prior parent
        #      commits were absorbed into master.)
        #
        #   4. The parent is totally disconnected, the history
        #      is bogus but at least the merge-base on master
        #      is the same or later.  (This can occur if you
        #      cherry-picked a commit out of an old stack and
        #      want to make it independent.)
        #
        # In cases 1-3, we can maintain a clean merge history
        # if we do a little extra book-keeping, which is what
        # we do now.
        #
        # TODO: What we have here actually works pretty hard to
        # maintain a consistent merge history between all PRs;
        # so, e.g., you could merge with master and things
        # wouldn't break.  But we don't necessarily have to do
        # this; all we need is the delta between base and head
        # to make sense.  The benefit to doing this is you could
        # more easily update single revs only, without doing
        # the rest of the stack.  The downside is that you
        # get less accurate merge structure for your changes
        # (because each "diff" is completely disconnected.)
        #

        # First, check if the parent commit hasn't changed.
        # We do this by checking if our base_commit is the same
        # as the gh/ezyang/X/base commit.
        #
        # In this case, we don't need to include the base as a
        # parent at all; just construct our new diff as a plain,
        # non-merge commit.
        base_args: Tuple[str, ...]
        orig_base_hash = self.sh.git(
            "rev-parse", "origin/" + branch_base(self.username, ghnum))
        if orig_base_hash == self.base_commit:

            new_base = self.base_commit
            base_args = ()

        else:
            # Second, check if our local base (self.base_commit)
            # added some new commits, but is still rooted on the
            # old base.
            #
            # If so, all we need to do is include the local base
            # as a parent when we do the merge.
            is_ancestor = self.sh.git(
                "merge-base",
                "--is-ancestor",
                "origin/" + branch_base(self.username, ghnum),
                self.base_commit, exitcode=True)

            if is_ancestor:
                new_base = self.base_commit

            else:
                # If we've gotten here, it means that the new
                # base and the old base are completely
                # unrelated.  We'll make a fake commit that
                # "resets" the tree back to something that makes
                # sense and merge with that.  This doesn't fix
                # the fact that we still incorrectly report
                # the old base as an ancestor of our commit, but
                # it's better than nothing.
                new_base = GitCommitHash(self.sh.git(
                    "commit-tree", self.base_tree,
                    "-p", "origin/" + branch_base(self.username, ghnum),
                    "-p", self.base_commit,
                    input='Update base for {} on "{}"\n\n{}'
                          .format(self.msg, title, commit.summary)))

            base_args = ("-p", new_base)

        # Blast our current tree as the newest commit, merging
        # against the previous pull entry, and the newest base.

        tree = commit.patch.apply(self.sh, self.base_tree)
        new_pull = GitCommitHash(self.sh.git(
            "commit-tree", tree,
            "-p", "origin/" + branch_head(self.username, ghnum),
            *base_args,
            input='{} on "{}"\n\n{}'.format(self.msg, title, commit.summary)))

        # Perform what is effectively an interactive rebase
        # on the orig branch.
        #
        # Hypothetically, there could be a case when this isn't
        # necessary, but it's INCREDIBLY unlikely (because we'd
        # have to look EXACTLY like the original orig, and since
        # we're in the branch that says "hey we changed
        # something" that's probably not what happened.

        logging.info("Restacking commit on {}".format(self.base_orig))
        new_orig = GitCommitHash(self.sh.git(
            "commit-tree", tree,
            "-p", self.base_orig, input=commit.summary))

        push_branches = (
            (new_base, "base"),
            (new_pull, "head"),
            (new_orig, "orig"),
        )

        if closed:
            what = 'Skipped closed'
        else:
            what = 'Updated'

        self.stack_meta.append(DiffMeta(
            title=title,
            number=number,
            # NB: Ignore the commit message, and just reuse the old commit
            # message.  This is consistent with 'jf submit' default
            # behavior.  The idea is that people may have edited the
            # PR description on GitHub and you don't want to clobber
            # it.
            body=pr_body,
            ghnum=ghnum,
            push_branches=push_branches,
            what=what,
            closed=closed,
        ))

        self.base_commit = new_pull
        self.base_orig = new_orig
        self.base_tree = tree

    def process_commit(self, commit: ghstack.diff.Diff) -> None:
        logging.info("# Processing {} {}".format(commit.oid[:9], commit.title))
        logging.info("Base is {}".format(self.base_commit))

        # TODO: check if we authored the commit.  We ought not touch PRs we didn't
        # create.

        # check if the commit message says what pull request it's associated
        # with
        #   If NONE:
        #       - If possible, allocate ourselves a GhNumber and
        #         then fix the branch afterwards.
        #       - Otherwise, generate a unique branch name, and attach it to
        #         the commit message

        if commit.gh_metadata is None:
            self._process_new_commit(commit)
        else:
            self._process_old_commit(commit)

    def _format_stack(self, index: int) -> str:
        rows = []
        for i, s in reversed(list(enumerate(self.stack_meta))):
            if index == i:
                rows.append('* **#{} {}**'.format(s.number, s.title.strip()))
            else:
                rows.append('* #{} {}'.format(s.number, s.title.strip()))
        return self.stack_header + ':\n' + '\n'.join(rows) + '\n'

    def post_process(self, *, import_help: bool = True) -> None:
        """
        Do some post-processing that we can only do after we've finished
        processing all of the diffs
        """

        # fix the HEAD pointer
        self.sh.git("reset", "--soft", self.base_orig)

        # update pull request information, update bases as necessary
        #   preferably do this in one network call
        # push your commits (be sure to do this AFTER you update bases)
        base_push_branches: List[str] = []
        push_branches: List[str] = []
        force_push_branches: List[str] = []
        for i, s in enumerate(self.stack_meta):
            # NB: GraphQL API does not support modifying PRs
            if not s.closed:
                logging.info(
                    "# Updating https://github.com/{owner}/{repo}/pull/{number}"
                    .format(owner=self.repo_owner,
                            repo=self.repo_name,
                            number=s.number))
                self.github.patch(
                    "repos/{owner}/{repo}/pulls/{number}"
                    .format(owner=self.repo_owner, repo=self.repo_name,
                            number=s.number),
                    body=RE_STACK.sub(self._format_stack(i), s.body),
                    title=s.title)
            else:
                logging.info(
                    "# Skipping closed https://github.com/{owner}/{repo}/pull/{number}"
                    .format(owner=self.repo_owner,
                            repo=self.repo_name,
                            number=s.number))

            # It is VERY important that we do base updates BEFORE real
            # head updates, otherwise GitHub will spuriously think that
            # the user pushed a number of patches as part of the PR,
            # when actually they were just from the (new) upstream
            # branch

            for commit, b in s.push_branches:
                if b == 'orig':
                    q = force_push_branches
                elif b == 'base':
                    q = base_push_branches
                else:
                    q = push_branches
                q.append(push_spec(commit, branch(self.username, s.ghnum, b)))
        # Careful!  Don't push master.
        # TODO: These pushes need to be atomic (somehow)
        if base_push_branches:
            self.sh.git("push", "origin", *base_push_branches)
            self.github.push_hook(base_push_branches)
        if push_branches:
            self.sh.git("push", "origin", *push_branches)
            self.github.push_hook(push_branches)
        if force_push_branches:
            self.sh.git("push", "origin", "--force", *force_push_branches)
            self.github.push_hook(force_push_branches)

        # Report what happened
        def format_url(s: DiffMeta) -> str:
            return ("https://github.com/{owner}/{repo}/pull/{number}"
                    .format(owner=self.repo_owner,
                            repo=self.repo_name,
                            number=s.number))

        if self.short:
            # Guarantee that the FIRST PR URL is the top of the stack
            print('\n'.join(format_url(s) for s in reversed(self.stack_meta)))
            return

        print()
        print('# Summary of changes (ghstack {})'.format(ghstack.__version__))
        print()
        for s in reversed(self.stack_meta):
            url = format_url(s)
            print(" - {} {}".format(s.what, url))
        top_of_stack = self.stack_meta[-1]

        if import_help:
            print()
            print("Facebook employees can import your changes by running ")
            print("(on a Facebook machine):")
            print()
            print("    ghimport -s {}".format(format_url(top_of_stack)))
            print()
            print("If you want to work on this diff stack on another machine,")
            print("run these commands inside a valid Git checkout:")
            print()
            print("     git fetch origin")
            print("     git checkout {}"
                  .format(branch_orig(self.username, top_of_stack.ghnum)))
            print("")
