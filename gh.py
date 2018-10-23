import argparse
import requests
import subprocess
import re
import uuid
import json
import itertools
from pprint import pprint

class Shell(object):
    def __init__(self, cwd=None):
        self.cwd = cwd

    def sh(self, *args, **kwargs):
        stdin = None
        if 'input' in kwargs:
            stdin = subprocess.PIPE
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stdin=stdin, cwd=self.cwd)
        out, _ = p.communicate(kwargs.get('input'))
        if p.returncode != 0:
            raise RuntimeError("{} failed with exit code {}".format(' '.join(args), p.returncode))
        return out.decode()

    def git(self, *args, **kwargs):
        return self.sh(*(("git",) + args), **kwargs).rstrip("\n")

class Endpoint(object):
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def graphql(self, query, **kwargs):
        resp = requests.post(self.endpoint, json={"query": query, "variables": kwargs})
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(json.dumps(resp.json(), indent=1))
        return resp.json()

def split_header(s):
    return s.split("\0")[:-1]

# repo layout:
#   - gh/username/base-2345 -- what we think GitHub's current tip for commit is
#   - gh/username/head-2345 -- what we think base commit for commit is
#   - gh/username/orig-2345 -- the "clean" commit history, i.e., what we're
#                      rebasing, what you'd like to cherry-pick (???)
#                      (Maybe this isn't necessary, because you can
#                      get the "whole" diff from GitHub?  What about
#                      commit description?)

def branch_base(username, diffid):
    return "gh/{}/{}/{}".format(username, "base", diffid)

def branch_head(username, diffid):
    return "gh/{}/{}/{}".format(username, "head", diffid)

def branch_orig(username, diffid):
    return "gh/{}/{}/{}".format(username, "orig", diffid)

def main(github=None, sh=None, repo_owner="pytorch", repo_name="pytorch", username="ezyang"):
    if github is None:
        github = Endpoint('http://localhost:4000')

    if sh is None:
        sh = Shell()

    # TODO: Cache this guy
    repo_id = github.graphql("""
        query ($owner: String!, $name: String!) {
            repository(name: $name, owner: $owner) {
                id
            }
        }""", owner=repo_owner, name=repo_name)["data"]["repository"]["id"]

    base = sh.git("merge-base", "origin/master", "HEAD")

    # compute the stack of commits to process (reverse chronological order),
    # INCLUDING the base commit
    stack = split_header(sh.git("rev-list", "--header", "^" + base + "^@", "HEAD"))

    # fetch from origin
    # TODO

    submitter = Submitter(github, sh, username, repo_owner, repo_name, repo_id, base)

    # start with the earliest commit
    g = reversed(stack)
    submitter.process_base(next(g))
    for s in g:
        submitter.process_commit(s)
    submitter.post_process()

RE_RAW_COMMIT_ID = re.compile(r'^(?P<commit>[a-f0-9]+)$', re.MULTILINE)
RE_RAW_AUTHOR = re.compile(r'^author (?P<name>[^<]+?) <(?P<email>[^>]+)>', re.MULTILINE)
RE_RAW_PARENT = re.compile(r'^parent (?P<commit>[a-f0-9]+)$', re.MULTILINE)
RE_RAW_TREE = re.compile(r'^tree (?P<tree>.+)$', re.MULTILINE)
RE_RAW_COMMIT_MSG_LINE = re.compile(r'^    (?P<line>.*)$', re.MULTILINE)
RE_RAW_METADATA = re.compile(r'^    Pull Request resolved: https://github.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>[0-9]+) \(gh/(?P<username>[a-zA-Z0-9-]+)/head/(?P<diffid>[0-9]+)\)$', re.MULTILINE)

def all_branches(username, diffid):
    return (branch_base(username, diffid),
            branch_head(username, diffid),
            branch_orig(username, diffid))

class Submitter(object):
    def __init__(self, github, sh, username, repo_owner, repo_name, repo_id, base_commit):
        self.github = github
        self.sh = sh
        self.username = username
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_id = repo_id
        self.base_commit = base_commit
        self.base_tree = None
        self.stack_meta = []

    def process_base(self, commit):
        self.base_tree = RE_RAW_TREE.search(commit).group("tree")

    def process_commit(self, commit):
        title = RE_RAW_COMMIT_MSG_LINE.search(commit).group("line")
        commit_id = RE_RAW_COMMIT_ID.search(commit).group("commit")
        tree = RE_RAW_TREE.search(commit).group("tree")

        print("### Processing {} {}".format(commit_id[:9], title))

        # check if we authored the commit.  We don't touch shit we didn't
        # create. (OPTIONAL)
        m = RE_RAW_AUTHOR.search(commit)
        if m is None:
            raise RuntimeError("malformed commit object:\n\n{}".format(commit))
        if m.group("email") != 'ezyang@fb.com':
            return

        commit_msg = '\n'.join(map(lambda m: m.group("line"), RE_RAW_COMMIT_MSG_LINE.finditer(commit)))

        # check if the commit message says what pull request it's associated with
        #   If NONE:
        #       - If possible, allocate ourselves a pull request number and then
        #         fix the branch afterwards.
        #       - Otherwise, generate a unique branch name, and attach it to
        #         the commit message

        # fetch up to date pull request information
        # TODO

        # synchronize local pull/base state with external state
        # TODO

        m_metadata = RE_RAW_METADATA.search(commit)
        if m_metadata is None:
            # Determine the next available UUID.  We do this by
            # iterating through known branches and keeping track
            # of the max.  The next available UUID is the next number.
            # This is technically subject to a race, but we assume
            # end user is not running this script concurrently on
            # multiple machines (you bad bad)
            refs = self.sh.git("for-each-ref", "refs/remotes/origin/gh/{}/head".format(self.username), "--format=%(refname)").split()
            max_ref_num = max(int(ref.split('/')[-1]) for ref in refs) if refs else 0
            diffid = str(max_ref_num + 1)

            new_base = self.base_commit
            self.sh.git("branch", "-f", branch_base(self.username, diffid), new_base)

            new_pull = self.sh.git("commit-tree", tree,
                                   "-p", new_base,
                                   input=commit_msg)
            self.sh.git("branch", "-f", branch_head(self.username, diffid), new_pull)

            self.sh.git("branch", "-f", branch_orig(self.username, diffid), commit_id)

            self.sh.git("push", "origin", *all_branches(self.username, diffid))

            # Time to open the PR
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
                    "body": commit_msg,
                    "ownerId": self.repo_id,
                })
            prid = r["data"]["createPullRequest"]["pullRequest"]["id"]
            number = r["data"]["createPullRequest"]["pullRequest"]["number"]
            print("Opened PR #{}".format(number))

            # Time to fuck around with the commit message
            commit_msg = ("{commit_msg}\n\n"
                         "Pull Request resolved: "
                         "https://github.com/{owner}/{repo}/pull/{number} (gh/{username}/head/{diffid})\n"
                         .format(commit_msg=commit_msg.rstrip(),
                                 owner=self.repo_owner,
                                 repo=self.repo_name,
                                 number=number,
                                 username=self.username,
                                 diffid=diffid))
            parents = itertools.chain.from_iterable(('-p', m.group("commit")) for m in RE_RAW_PARENT.finditer(commit))
            self.sh.git("commit-tree", tree, *parents, input=commit_msg)

            self.stack_meta.append({
                'id': prid,
                'title': title,
                'number': number,
                'body': commit_msg,
                'base': branch_base(self.username, diffid),
                'push_branches': (),
                })

        else:
            if m.match("username") != self.username:
                # This is someone else's diff
                raise RuntimeError("cannot handle stack from diffs of other people yet")

            diffid = m.match("diffid")
            number = int(m.match("number"))

            # With the REST API, this is totally unnecessary. Might
            # be better to store these IDs in the commit message itself.
            r = self.github.graphql("""
              query ($repo_id: ID!, $number: Int!) {
                node(id: $repo_id) {
                  ... on Repository {
                    pullRequest(number: $number) {
                      id
                    }
                  }
                }
              }
            """)
            prid = r["data"]["node"]["pullRequest"]["id"]

            # Check if updating is needed
            clean_commit_id = self.sh.git("rev-parse", branch_orig(self.username, diffid))
            if clean_commit_id == commit_id:
                print("Nothing to do")
                push_branches = []
            else:
                print("Updating #{}".format(number))

                # ok, so now we want to ENTER a new entry into our log
                #   - Directly blast the tree of HEAD~ as the newest entry in base,
                #     synthetically merged with merge-base of HEAD and origin/master.
                #     (This will make sure merge with master still works.)
                #       - MAYBE, if we correspond to a known gh/pull branch, we can
                #         also insert a merge here as well.  This will help merges
                #         with feature branches keep working too.
                #         (if you're doing weird shit with cherry-picking, this
                #         won't work so good)

                new_base = self.sh.git("commit-tree", self.base_tree,
                                       "-p", branch_base(self.username, diffid),
                                       "-p", self.base_commit,
                                       input="Update")
                self.sh.git("branch", "-f", branch_base(self.username, diffid), new_base)

                #   - Directly blast our current tree as the newest entry of pull,
                #     merging against the previous pull entry, and the newest base.

                tree = RE_RAW_TREE.search(commit).group("tree")
                new_pull = self.sh.git("commit-tree", tree,
                                       "-p", branch_head(self.username, diffid),
                                       "-p", new_base,
                                       input="Update")
                self.sh.git("branch", "-f", branch_head(self.username, diffid), new_base)

                self.sh.git("branch", "-f", branch_orig(self.username, diffid), commit_id)

                push_branches = all_branches(self.username, diffid)

            self.stack_meta.append({
                'id': prid,
                'title': title,
                'number': number,
                'body': commit_msg,
                'base': branch_base(self.username, diffid),
                'push_branches': push_branches
                })

        self.base_commit = new_base
        self.base_tree = tree


    def post_process(self):
        # update pull request information, update bases as necessary
        #   preferably do this in one network call
        # push your commits (be sure to do this AFTER you update bases)
        for i, s in enumerate(self.stack_meta):
            print("### Updating #{}".format(s["number"]))
            self.github.graphql("""
                mutation ($input : UpdatePullRequestInput!) {
                    updatePullRequest(input: $input) {
                        clientMutationId
                    }
                }
            """, input={
                    'pullRequestId': s['id'],
                    # "Stack:\n" + format_stack(stack_meta, i) + "\n\n" + 
                    'body': s['body'],
                    'title': s['title'],
                    'baseRefName': s['base']
                })
            # It is VERY important that we do this push AFTER fixing the base,
            # otherwise GitHub will spuriously think that the user pushed a number
            # of patches as part of the PR, when actually they were just from
            # the (new) upstream branch
            if s['push_branches']:
                sh.git("push", "origin", *s['push_branches'])


# How to update commit messages?  Probably should reimplement git rebase
# -i by hand.  Prefer NOT to actually affect working copy when making
# changes.

if __name__ == "__main__":
    main()
