from __future__ import print_function

import re
import ghstack.shell
import ghstack.endpoint
from typing import List, NewType, Union, Optional, NamedTuple, Tuple, cast, Pattern, Match
from typing_extensions import Literal


PhabDiffNumber = NewType('PhabDiffNumber', str)  # aka "D1234567"
GitHubNumber = NewType('GitHubNumber', int)  # aka 1234 (as in #1234)
GraphQLId = NewType('GraphQLId', str)  # aka MDExOlB1bGxSZXF1ZXN0MjU2NDM3MjQw (GraphQL ID)
StackDiffId = NewType('StackDiffId', str)  # aka 12 (as in gh/ezyang/12/base)
BranchKind = Union[Literal['base'], Literal['head'], Literal['orig']]

# Actually, sometimes we smuggle revs in here.  It doesn't seem to
# matter at the moment, but it might be good to make a better
# distinction here.
GitCommitHash = NewType('GitCommitHash', str)  # aka commit 3f72e04eeabcc7e77f127d3e7baf2f5ccdb148ee

GitTreeHash = NewType('GitTreeHash', str)  # aka tree 3f72e04eeabcc7e77f127d3e7baf2f5ccdb148ee


DiffMeta = NamedTuple('DiffMeta', [
    ('id', GraphQLId),
    ('title', str),
    ('number', GitHubNumber),
    ('body', str),
    ('base', str),
    ('diffid', StackDiffId),
    ('push_branches', Tuple[BranchKind, ...]),
])

# repo layout:
#   - gh/username/base-2345 -- what we think GitHub's current tip for commit is
#   - gh/username/head-2345 -- what we think base commit for commit is
#   - gh/username/orig-2345 -- the "clean" commit history, i.e., what we're
#                      rebasing, what you'd like to cherry-pick (???)
#                      (Maybe this isn't necessary, because you can
#                      get the "whole" diff from GitHub?  What about
#                      commit description?)


def branch(username: str, diffid: StackDiffId, kind: BranchKind) -> GitCommitHash:
    return GitCommitHash("gh/{}/{}/{}".format(username, diffid, kind))


def branch_base(username: str, diffid: StackDiffId) -> GitCommitHash:
    return branch(username, diffid, "base")


def branch_head(username: str, diffid: StackDiffId) -> GitCommitHash:
    return branch(username, diffid, "head")


def branch_orig(username: str, diffid: StackDiffId) -> GitCommitHash:
    return branch(username, diffid, "orig")


def main(msg: Optional[str],
         github: ghstack.endpoint.GraphQLEndpoint,
         github_rest: Optional[ghstack.endpoint.RESTEndpoint],  # hmmm
         sh: Optional[ghstack.shell.Shell] = None,
         repo_owner: Optional[str] = None,
         repo_name: Optional[str] = None,
         username: str = "ezyang") -> List[DiffMeta]:  # TODO: fix hardcoded username

    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    if repo_owner is None or repo_name is None:
        # Grovel in remotes to figure it out
        origin_url = sh.git("remote", "get-url", "origin")
        while True:
            m = re.match(r'^git@github.com:([^/]+)/([^.]+)\.git$', origin_url)
            if m:
                repo_owner = m.group(1)
                repo_name = m.group(2)
                break
            m = re.match(r'https://github.com/([^/]+)/([^.]+).git', origin_url)
            if m:
                repo_owner = m.group(1)
                repo_name = m.group(2)
                break
            raise RuntimeError(
                    "Couldn't determine repo owner and name from url: {}"
                    .format(origin_url))

    # TODO: Cache this guy
    repo_id = github.graphql("""
        query ($owner: String!, $name: String!) {
            repository(name: $name, owner: $owner) {
                id
            }
        }""", owner=repo_owner, name=repo_name)["data"]["repository"]["id"]

    sh.git("fetch", "origin")
    base = GitCommitHash(sh.git("merge-base", "origin/master", "HEAD"))

    # compute the stack of commits to process (reverse chronological order),
    # INCLUDING the base commit
    print(sh.git("rev-list", "^" + base + "^@", "HEAD"))
    stack = split_header(
        sh.git("rev-list", "--header", "^" + base + "^@", "HEAD"))

    # start with the earliest commit
    g = reversed(stack)
    base_obj = next(g)

    submitter = Submitter(github=github,
                          github_rest=github_rest,
                          sh=sh,
                          username=username,
                          repo_owner=repo_owner,
                          repo_name=repo_name,
                          repo_id=repo_id,
                          base_commit=base,
                          base_tree=base_obj.tree(),
                          msg=msg)

    for s in g:
        submitter.process_commit(s)
    submitter.post_process()

    # NB: earliest first
    return submitter.stack_meta


RE_RAW_COMMIT_ID = re.compile(r'^(?P<commit>[a-f0-9]+)$', re.MULTILINE)
RE_RAW_AUTHOR = re.compile(r'^author (?P<name>[^<]+?) <(?P<email>[^>]+)>',
                           re.MULTILINE)
RE_RAW_PARENT = re.compile(r'^parent (?P<commit>[a-f0-9]+)$', re.MULTILINE)
RE_RAW_TREE = re.compile(r'^tree (?P<tree>.+)$', re.MULTILINE)
RE_RAW_COMMIT_MSG_LINE = re.compile(r'^    (?P<line>.*)$', re.MULTILINE)
RE_RAW_METADATA = re.compile(
    r'^    gh-metadata: (?P<owner>[^/]+) (?P<repo>[^/]+) (?P<number>[0-9]+) '
    r'gh/(?P<username>[a-zA-Z0-9-]+)/(?P<diffid>[0-9]+)/head$', re.MULTILINE)


RE_STACK = re.compile(r'Stack:\n(\* [^\n]+\n)+')


class CommitHeader(object):
    """
    Represents the information extracted from `git rev-list --header`
    """
    # The unparsed output from git rev-list --header
    raw_header: str

    def __init__(self, raw_header: str) -> None:
        self.raw_header = raw_header

    def _search_group(self, regex: Pattern, group: str) -> str:
        m = regex.search(self.raw_header)
        assert m
        return m.group(group)

    def tree(self) -> GitTreeHash:
        return GitTreeHash(self._search_group(RE_RAW_TREE, "tree"))

    def title(self) -> str:
        return self._search_group(RE_RAW_COMMIT_MSG_LINE, "line")

    def commit_id(self) -> GitCommitHash:
        return GitCommitHash(self._search_group(RE_RAW_COMMIT_ID, "commit"))

    def parents(self) -> List[GitCommitHash]:
        return [GitCommitHash(m.group("commit")) for m in RE_RAW_PARENT.finditer(self.raw_header)]

    def author(self) -> str:
        return self._search_group(RE_RAW_AUTHOR, "author")

    def commit_msg(self) -> str:
        return '\n'.join(m.group("line") for m in RE_RAW_COMMIT_MSG_LINE.finditer(self.raw_header))

    def match_metadata(self) -> Optional[Match]:
        return RE_RAW_METADATA.search(self.raw_header)


def split_header(s: str) -> List[CommitHeader]:
    return list(map(CommitHeader, s.split("\0")[:-1]))


def all_branches(username: str, diffid: StackDiffId) -> Tuple[str, str, str]:
    return (branch_base(username, diffid),
            branch_head(username, diffid),
            branch_orig(username, diffid))


class Submitter(object):
    # GraphQL endpoint to access GitHub
    github: ghstack.endpoint.GraphQLEndpoint

    # REST endpoint to access GitHub (None during testing)
    github_rest: Optional[ghstack.endpoint.RESTEndpoint]

    # Shell inside git checkout that we are submitting
    sh: ghstack.shell.Shell

    # GitHub username who is doing the submitting
    username: str

    # Owner of the repository we are submitting to.  Usually 'pytorch'
    repo_owner: str

    # Name of the repository we are submitting to.  Usually 'pytorch'
    repo_name: str

    # GraphQL ID of the repository
    repo_id: GraphQLId

    # The base commit of the next diff we are submitting
    base_commit: GitCommitHash

    # The base tree of the next diff we are submitting
    base_tree: GitTreeHash

    # Message describing the update to the stack that was done
    msg: Optional[str]

    # Description of all the diffs we submitted; to be populated
    # by Submitter.
    stack_meta: List[DiffMeta]

    def __init__(
            self,
            github: ghstack.endpoint.GraphQLEndpoint,
            github_rest: Optional[ghstack.endpoint.RESTEndpoint],
            sh: ghstack.shell.Shell,
            username: str,
            repo_owner: str,
            repo_name: str,
            repo_id: GraphQLId,
            base_commit: GitCommitHash,
            base_tree: GitTreeHash,
            msg: Optional[str]) -> None:
        self.github = github
        self.github_rest = github_rest
        self.sh = sh
        self.username = username
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_id = repo_id
        self.base_commit = base_commit
        self.base_orig = base_commit
        self.base_tree = base_tree
        self.stack_meta = []
        self.msg = msg

    def process_commit(self, commit: CommitHeader) -> None:
        title = commit.title()
        commit_id = commit.commit_id()
        tree = commit.tree()
        parents = commit.parents()
        new_orig = commit_id

        print("# Processing {} {}".format(commit_id[:9], title))
        print("Base is {}".format(self.base_commit))

        if len(parents) != 1:
            print("{} parents makes my head explode.  "
                  "`git rebase -i` your diffs into a stack, then try again.")
        parent = parents[0]

        # TODO: check if we authored the commit.  We don't touch shit we didn't
        # create.

        commit_msg = commit.commit_msg()

        # check if the commit message says what pull request it's associated
        # with
        #   If NONE:
        #       - If possible, allocate ourselves a pull request number and
        #         then fix the branch afterwards.
        #       - Otherwise, generate a unique branch name, and attach it to
        #         the commit message

        # fetch up to date pull request information
        # TODO

        m_metadata = commit.match_metadata()
        if m_metadata is None:
            # Determine the next available UUID.  We do this by
            # iterating through known branches and keeping track
            # of the max.  The next available UUID is the next number.
            # This is technically subject to a race, but we assume
            # end user is not running this script concurrently on
            # multiple machines (you bad bad)
            refs = self.sh.git(
                "for-each-ref",
                "refs/remotes/origin/gh/{}".format(self.username),
                "--format=%(refname)").split()
            max_ref_num = max(int(ref.split('/')[-2]) for ref in refs) \
                if refs else 0
            diffid = StackDiffId(str(max_ref_num + 1))

            # Record the base branch per the previous commit on the
            # stack
            self.sh.git(
                "branch",
                "-f", branch_base(self.username, diffid),
                self.base_commit)

            # Create the incremental pull request diff
            new_pull = GitCommitHash(self.sh.git("commit-tree", tree,
                                   "-p", self.base_commit,
                                   input=commit_msg))
            self.sh.git(
                "branch",
                "-f", branch_head(self.username, diffid),
                new_pull)

            # Push the branches, so that we can create a PR for them
            self.sh.git(
                "push",
                "origin",
                branch_head(self.username, diffid),
                branch_base(self.username, diffid)
            )

            pr_body = \
                "Stack:\n* (to be filled)\n\n" + \
                ''.join(commit_msg.splitlines(True)[1:]).lstrip()

            # Time to open the PR
            if self.github.future or not self.github_rest:
                r = self.github.graphql("""
                    mutation ($input : CreatePullRequestInput!) {
                        createPullRequest(input: $input) {
                            pullRequest {
                                id
                                number
                                title
                            }
                        }
                    }
                """, input={
                        "baseRefName": branch_base(self.username, diffid),
                        "headRefName": branch_head(self.username, diffid),
                        "title": title,
                        "body": pr_body,
                        "ownerId": self.repo_id,
                    })
                pullRequest = r["data"]["createPullRequest"]["pullRequest"]
                prid = GraphQLId(pullRequest["id"])
                number = pullRequest["number"]
            else:
                r = self.github_rest.post(
                    "repos/{owner}/{repo}/pulls"
                    .format(owner=self.repo_owner, repo=self.repo_name),
                    title=title,
                    head=branch_head(self.username, diffid),
                    base=branch_base(self.username, diffid),
                    body=pr_body,
                    maintainer_can_modify=True,
                    )
                prid = GraphQLId(r['node_id'])  # not used, but let's type it
                number = r['number']

            print("Opened PR #{}".format(number))

            # Update the commit message of the local diff with metadata
            # so we can correlate these later
            commit_msg = ("{commit_msg}\n\n"
                          "gh-metadata: "
                          "{owner} {repo} {number} {branch_head}"
                          .format(commit_msg=commit_msg.rstrip(),
                                  owner=self.repo_owner,
                                  repo=self.repo_name,
                                  number=number,
                                  branch_head=branch_head(self.username,
                                                          diffid)))

            # TODO: Try harder to preserve the old author/commit
            # information (is it really necessary? Check what
            # --amend does...)
            new_orig = GitCommitHash(self.sh.git(
                "commit-tree",
                tree,
                "-p", self.base_orig,
                input=commit_msg))

            # Update the orig pointer
            self.sh.git(
                "branch",
                "-f", branch_orig(self.username, diffid),
                new_orig)

            self.stack_meta.append(DiffMeta(
                id=prid,
                title=title,
                number=number,
                body=pr_body,
                base=branch_base(self.username, diffid),
                diffid=diffid,
                push_branches=('orig', ),
            ))

        else:
            if m_metadata.group("username") != self.username:
                # This is someone else's diff
                raise RuntimeError(
                    "cannot handle stack from diffs of other people yet")

            diffid = m_metadata.group("diffid")
            number = int(m_metadata.group("number"))

            # synchronize local pull/base state with external state
            for b in all_branches(self.username, diffid):
                self.sh.git("branch", "-f", b, "origin/" + b)

            r = self.github.graphql("""
              query ($repo_id: ID!, $number: Int!) {
                node(id: $repo_id) {
                  ... on Repository {
                    pullRequest(number: $number) {
                      id
                      body
                      title
                    }
                  }
                }
              }
            """, repo_id=self.repo_id, number=number)
            prid = GraphQLId(r["data"]["node"]["pullRequest"]["id"])
            pr_body = r["data"]["node"]["pullRequest"]["body"]
            # NB: Technically, we don't need to pull this information at
            # all, but it's more convenient to unconditionally edit
            # title in the code below
            # NB: This overrides setting of title previously, from the
            # commit message.
            title = r["data"]["node"]["pullRequest"]["title"]

            # Check if updating is needed
            clean_commit_id = GitCommitHash(self.sh.git(
                "rev-parse",
                branch_orig(self.username, diffid)
            ))
            push_branches: Tuple[BranchKind, ...]
            if clean_commit_id == commit_id:
                print("Nothing to do")
                # NB: NOT commit_id, that's the orig commit!
                new_pull = branch_head(self.username, diffid)
                push_branches = ()
            else:
                print("Pushing to #{}".format(number))

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
                #      is the same or later.  (You cherry-picked a
                #      commit out of an old stack and want to make it
                #      independent.)
                #
                # In cases 1-3, we can maintain a clean merge history
                # if we do a little extra book-keeping, so we do
                # precisely this.
                #
                #   - In cases 1 and 2, we'd like to use the newly
                #     created gh/ezyang/$PARENT/head which is recorded
                #     in self.base_commit, because it's exactly the
                #     correct base commit to base our diff off of.
                #

                # First, check if gh/ezyang/1/head is equal
                # to gh/ezyang/2/base.
                # We don't need to update base, nor do we need an extra
                # merge base.  (--is-ancestor check here is acceptable,
                # because the base_commit in our stack could not have
                # gone backwards)
                base_args: Tuple[str, ...]
                if self.sh.git(
                        "merge-base",
                        "--is-ancestor", self.base_commit,
                        branch_base(self.username, diffid), exitcode=True):

                    new_base = self.base_commit
                    base_args = ()

                else:
                    # Second, check if gh/ezyang/2/base is an ancestor
                    # of gh/ezyang/1/head.  If it is, we'll do a merge,
                    # but we don't need to create a synthetic base
                    # commit.
                    if self.sh.git(
                          "merge-base",
                          "--is-ancestor", branch_base(self.username, diffid),
                          self.base_commit, exitcode=True):

                        new_base = self.base_commit

                    else:
                        # Our base changed in a strange way, and we are
                        # now obligated to create a synthetic base
                        # commit.
                        new_base = GitCommitHash(self.sh.git(
                            "commit-tree", self.base_tree,
                            "-p", branch_base(self.username, diffid),
                            "-p", self.base_commit,
                            input='Update base for {} on "{}"'
                                  .format(self.msg, title)))
                    base_args = ("-p", new_base)

                self.sh.git(
                    "branch",
                    "-f", branch_base(self.username, diffid),
                    new_base)

                #   - Directly blast our current tree as the newest entry of
                #   pull, merging against the previous pull entry, and the
                #   newest base.

                tree = commit.tree()
                new_pull = GitCommitHash(self.sh.git(
                    "commit-tree", tree,
                    "-p", branch_head(self.username, diffid),
                    *base_args,
                    input='{} on "{}"'.format(self.msg, title)))
                print("new_pull = {}".format(new_pull))
                self.sh.git(
                    "branch",
                    "-f", branch_head(self.username, diffid),
                    new_pull)

                # History reedit!  Commit message changes only
                if parent != self.base_orig:
                    print("Restacking commit on {}".format(self.base_orig))
                    new_orig = GitCommitHash(self.sh.git(
                        "commit-tree", tree,
                        "-p", self.base_orig, input=commit_msg))

                self.sh.git(
                    "branch",
                    "-f", branch_orig(self.username, diffid),
                    new_orig)

                push_branches = ("base", "head", "orig")

            self.stack_meta.append(DiffMeta(
                id=prid,
                title=title,
                number=number,
                # NB: Ignore the commit message, and just reuse the old commit
                # message.  This is consistent with 'jf submit' default
                # behavior.  The idea is that people may have edited the
                # PR description on GitHub and you don't want to clobber
                # it.
                body=pr_body,
                base=branch_base(self.username, diffid),
                diffid=diffid,
                push_branches=push_branches
            ))

        # The current pull request head commit, is the new base commit
        self.base_commit = new_pull
        self.base_orig = new_orig
        self.base_tree = tree
        print("base_commit = {}".format(self.base_commit))
        print("base_orig = {}".format(self.base_orig))
        print("base_tree = {}".format(self.base_tree))

    def _format_stack(self, index: int) -> str:
        rows = []
        for i, s in enumerate(self.stack_meta):
            if index == i:
                rows.append('* **#{} {}**'.format(s.number, s.title))
            else:
                rows.append('* #{} {}'.format(s.number, s.title))
        return 'Stack:\n' + '\n'.join(rows) + '\n'

    def post_process(self) -> None:
        # fix the HEAD pointer
        self.sh.git("reset", "--soft", self.base_orig)

        # update pull request information, update bases as necessary
        #   preferably do this in one network call
        # push your commits (be sure to do this AFTER you update bases)
        push_branches = []
        force_push_branches = []
        for i, s in enumerate(self.stack_meta):
            print("# Updating https://github.com/{owner}/{repo}/pull/{number}"
                  .format(owner=self.repo_owner,
                          repo=self.repo_name,
                          number=s.number))
            if self.github.future or not self.github_rest:
                self.github.graphql("""
                    mutation ($input : UpdatePullRequestInput!) {
                        updatePullRequest(input: $input) {
                            clientMutationId
                        }
                    }
                """, input={
                        'pullRequestId': s.id,
                        'body': RE_STACK.sub(self._format_stack(i), s.body),
                        'title': s.title,
                        'baseRefName': s.base
                    })
            else:
                self.github_rest.patch(
                    "repos/{owner}/{repo}/pulls/{number}"
                    .format(owner=self.repo_owner, repo=self.repo_name,
                            number=s.number),
                    body=s.body,
                    title=s.title,
                    base=s.base)
            # It is VERY important that we do this push AFTER fixing the base,
            # otherwise GitHub will spuriously think that the user pushed a
            # number of patches as part of the PR, when actually they were just
            # from the (new) upstream branch
            for b in s.push_branches:
                if b == 'orig':
                    force_push_branches.append(
                        branch(self.username, s.diffid, b))
                else:
                    push_branches.append(branch(self.username, s.diffid, b))
        # Careful!  Don't push master.
        if push_branches:
            self.sh.git("push", "origin", *push_branches)
        if force_push_branches:
            self.sh.git("push", "origin", "--force", *force_push_branches)
