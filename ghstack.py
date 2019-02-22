from __future__ import print_function

import argparse
import requests
import subprocess
import re
import uuid
import json
import itertools
import os
import sys
import getpass
from pprint import pprint

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

def format_env(env):
    r = []
    for k, v in env.items():
        r.append("{}={}".format(k, subprocess.list2cmdline([v])))
    return ' '.join(r)

def log_command(args, env=None):
    cmd = subprocess.list2cmdline(args).replace("\n", "\\n")
    #if env is not None:
    #    cmd = "{} {}".format(format_env(env), cmd)
    print("$ " + cmd)

def merge_dicts(x, y):
    z = x.copy()
    z.update(y)
    return z

class Shell(object):
    def __init__(self, quiet=False, cwd=None, testing=False):
        self.cwd = cwd
        self.quiet = quiet
        self.testing = testing
        self.testing_time = 1112911993

    def sh(self, *args, **kwargs):
        stdin = None
        if 'input' in kwargs:
            stdin = subprocess.PIPE
        env = kwargs.get("env")
        if not self.quiet:
            log_command(args, env=env)
        if env is not None:
            env = merge_dicts(os.environ, env)
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stdin=stdin, stderr=kwargs.get("stderr"), cwd=self.cwd, env=env)
        input = kwargs.get('input')
        if input is not None:
            input = input.encode('utf-8')
        out, err = p.communicate(input)
        if err is not None:
            print(err, file=sys.stderr, end='')
        if kwargs.get('exitcode'):
            return p.returncode == 0
        if p.returncode != 0:
            raise RuntimeError("{} failed with exit code {}".format(' '.join(args), p.returncode))
        return out.decode()

    def git(self, *args, **kwargs):
        env = kwargs.setdefault("env", {})
        # Some envvars to make things a little more script mode nice
        if self.testing:
            env.setdefault("EDITOR", ":")
            env.setdefault("GIT_MERGE_AUTOEDIT", "no")
            env.setdefault("LANG", "C")
            env.setdefault("LC_ALL", "C")
            env.setdefault("PAGER", "cat")
            env.setdefault("TZ", "UTC")
            env.setdefault("TERM", "dumb")
            # These are important so we get deterministic commit times
            env.setdefault("GIT_AUTHOR_EMAIL", "author@example.com")
            env.setdefault("GIT_AUTHOR_NAME", "A U Thor")
            env.setdefault("GIT_COMMITTER_EMAIL", "committer@example.com")
            env.setdefault("GIT_COMMITTER_NAME", "C O Mitter")
            env.setdefault("GIT_COMMITTER_DATE", "{} -0700".format(self.testing_time))
            env.setdefault("GIT_AUTHOR_DATE", "{} -0700".format(self.testing_time))
            if 'stderr' not in kwargs:
                kwargs['stderr'] = subprocess.PIPE

        r = self.sh(*(("git",) + args), **kwargs)
        if kwargs.get('exitcode'):
            return r
        else:
            return r.rstrip("\n")

    def test_tick(self):
        self.testing_time += 60

    def open(self, fn, mode):
        return open(os.path.join(self.cwd, fn), mode)

class GraphQLEndpoint(object):
    def __init__(self, endpoint, future=False):
        self.endpoint = endpoint
        self.oauth_token = None
        # Whether or not this API lives "in the future".  Features in
        # the future don't exist on the real GitHub API.
        self.future = future

    def graphql(self, query, **kwargs):
        headers = {}
        if self.oauth_token:
            headers['Authorization'] = 'bearer {}'.format(self.oauth_token)
        resp = requests.post(self.endpoint, json={"query": query, "variables": kwargs}, headers=headers)
        # Actually, this code is dead on the GitHub GraphQL API, because
        # they seem to always return 200, even in error case (as of
        # 11/5/2018)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(json.dumps(resp.json(), indent=1))
        r = resp.json()
        if 'errors' in r:
            raise RuntimeError(json.dumps(r, indent=1))
        return r

class RESTEndpoint(object):
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.oauth_token = None

    def headers(self):
        return {
          'Authorization': 'token ' + self.oauth_token,
          'Content-Type': 'application/json',
          'User-Agent': 'gh',
          'Accept': 'application/vnd.github.v3+json',
          }

    def get(self, path, **kwargs):
        return self.rest('get', path, **kwargs)

    def post(self, path, **kwargs):
        return self.rest('post', path, **kwargs)

    def patch(self, path, **kwargs):
        return self.rest('patch', path, **kwargs)

    def rest(self, method, path, **kwargs):
        r = getattr(requests, method)(self.endpoint + '/' + path, json=kwargs, headers=self.headers())
        r.raise_for_status()
        return r.json()

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

def branch(username, diffid, kind):
    return "gh/{}/{}/{}".format(username, diffid, kind)

def branch_base(username, diffid):
    return branch(username, diffid, "base")

def branch_head(username, diffid):
    return branch(username, diffid, "head")

def branch_orig(username, diffid):
    return branch(username, diffid, "orig")

def main(msg=None, github=None, github_rest=None, sh=None, repo_owner=None, repo_name=None, username="ezyang"):
    if sh is None:
        # Use CWD
        sh = Shell()

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
            raise RuntimeError("Couldn't determine repo owner and name from url: {}".format(origin_url))

    # TODO: Cache this guy
    repo_id = github.graphql("""
        query ($owner: String!, $name: String!) {
            repository(name: $name, owner: $owner) {
                id
            }
        }""", owner=repo_owner, name=repo_name)["data"]["repository"]["id"]

    sh.git("fetch", "origin")
    base = sh.git("merge-base", "origin/master", "HEAD")

    # compute the stack of commits to process (reverse chronological order),
    # INCLUDING the base commit
    print(sh.git("rev-list", "^" + base + "^@", "HEAD"))
    stack = split_header(sh.git("rev-list", "--header", "^" + base + "^@", "HEAD"))

    submitter = Submitter(github=github,
                          github_rest=github_rest,
                          sh=sh,
                          username=username,
                          repo_owner=repo_owner,
                          repo_name=repo_name,
                          repo_id=repo_id,
                          base_commit=base,
                          msg=msg)

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
RE_RAW_METADATA = re.compile(r'^    gh-metadata: (?P<owner>[^/]+) (?P<repo>[^/]+) (?P<number>[0-9]+) gh/(?P<username>[a-zA-Z0-9-]+)/(?P<diffid>[0-9]+)/head$', re.MULTILINE)

def all_branches(username, diffid):
    return (branch_base(username, diffid),
            branch_head(username, diffid),
            branch_orig(username, diffid))

class Submitter(object):
    def __init__(self, github, github_rest, sh, username, repo_owner, repo_name, repo_id, base_commit, msg):
        self.github = github
        self.github_rest = github_rest
        self.sh = sh
        self.username = username
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_id = repo_id
        self.base_commit = base_commit
        self.base_orig = base_commit
        self.base_tree = None
        self.stack_meta = []
        self.msg = msg

    def process_base(self, commit):
        self.base_tree = RE_RAW_TREE.search(commit).group("tree")

    def process_commit(self, commit):
        title = RE_RAW_COMMIT_MSG_LINE.search(commit).group("line")
        commit_id = RE_RAW_COMMIT_ID.search(commit).group("commit")
        tree = RE_RAW_TREE.search(commit).group("tree")
        parents = [m.group("commit") for m in RE_RAW_PARENT.finditer(commit)]
        new_orig = commit_id

        print("# Processing {} {}".format(commit_id[:9], title))
        print("Base is {}".format(self.base_commit))

        if len(parents) != 1:
            print("{} parents makes my head explode.  `git rebase -i` your diffs into a stack, then try again.")
        parent = parents[0]

        # check if we authored the commit.  We don't touch shit we didn't
        # create. (OPTIONAL)
        m = RE_RAW_AUTHOR.search(commit)
        if m is None:
            raise RuntimeError("malformed commit object:\n\n{}".format(commit))
        # TODO: Actually do this check
        # Maybe this doesn't matter: assume that commits that are not
        # ours are "appropriately formatted"
        #if m.group("email") != 'ezyang@fb.com':
        #    return

        commit_msg = '\n'.join(map(lambda m: m.group("line"), RE_RAW_COMMIT_MSG_LINE.finditer(commit)))

        # check if the commit message says what pull request it's associated with
        #   If NONE:
        #       - If possible, allocate ourselves a pull request number and then
        #         fix the branch afterwards.
        #       - Otherwise, generate a unique branch name, and attach it to
        #         the commit message

        # fetch up to date pull request information
        # TODO

        m_metadata = RE_RAW_METADATA.search(commit)
        if m_metadata is None:
            # Determine the next available UUID.  We do this by
            # iterating through known branches and keeping track
            # of the max.  The next available UUID is the next number.
            # This is technically subject to a race, but we assume
            # end user is not running this script concurrently on
            # multiple machines (you bad bad)
            refs = self.sh.git("for-each-ref", "refs/remotes/origin/gh/{}".format(self.username), "--format=%(refname)").split()
            max_ref_num = max(int(ref.split('/')[-2]) for ref in refs) if refs else 0
            diffid = str(max_ref_num + 1)

            # Record the base branch per the previous commit on the
            # stack
            self.sh.git("branch", "-f", branch_base(self.username, diffid), self.base_commit)

            # Create the incremental pull request diff
            new_pull = self.sh.git("commit-tree", tree,
                                   "-p", self.base_commit,
                                   input=commit_msg)
            self.sh.git("branch", "-f", branch_head(self.username, diffid), new_pull)

            # Push the branches, so that we can create a PR for them
            self.sh.git("push", "origin", branch_head(self.username, diffid), branch_base(self.username, diffid))

            pr_body = ''.join(commit_msg.splitlines(True)[1:]).lstrip()

            # Time to open the PR
            if self.github.future:
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
                prid = r["data"]["createPullRequest"]["pullRequest"]["id"]
                number = r["data"]["createPullRequest"]["pullRequest"]["number"]
            else:
                r = self.github_rest.post("repos/{owner}/{repo}/pulls".format(owner=self.repo_owner, repo=self.repo_name),
                    title=title,
                    head=branch_head(self.username, diffid),
                    base=branch_base(self.username, diffid),
                    body=pr_body,
                    maintainer_can_modify=True,
                    )
                prid = None
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
                                 branch_head=branch_head(self.username, diffid)))

            # TODO: Try harder to preserve the old author/commit
            # information (is it really necessary? Check what
            # --amend does...)
            new_orig = self.sh.git("commit-tree", tree, "-p", self.base_orig, input=commit_msg)

            # Update the orig pointer
            self.sh.git("branch", "-f", branch_orig(self.username, diffid), new_orig)

            self.stack_meta.append({
                'id': prid,
                'title': title,
                'number': number,
                'body': pr_body,
                'base': branch_base(self.username, diffid),
                'diffid': diffid,
                'push_branches': ('orig', ),
                })

        else:
            if m_metadata.group("username") != self.username:
                # This is someone else's diff
                raise RuntimeError("cannot handle stack from diffs of other people yet")

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
                    }
                  }
                }
              }
            """, repo_id=self.repo_id, number=number)
            prid = r["data"]["node"]["pullRequest"]["id"]
            pr_body = r["data"]["node"]["pullRequest"]["body"]

            # Check if updating is needed
            clean_commit_id = self.sh.git("rev-parse", branch_orig(self.username, diffid))
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

                # First, check if gh/ezyang/1/head is equal to gh/ezyang/2/base.
                # We don't need to update base, nor do we need an extra
                # merge base.  (--is-ancestor check here is acceptable,
                # because the base_commit in our stack could not have
                # gone backwards)
                if self.sh.git("merge-base", "--is-ancestor", self.base_commit, branch_base(self.username, diffid), exitcode=True):
                    new_base = self.base_commit
                    base_args = ()
                else:
                    # Second, check if gh/ezyang/2/base is an ancestor
                    # of gh/ezyang/1/head.  If it is, we'll do a merge,
                    # but we don't need to create a synthetic base
                    # commit.
                    if self.sh.git("merge-base", "--is-ancestor", branch_base(self.username, diffid), self.base_commit, exitcode=True):
                        new_base = self.base_commit
                    else:
                        # Our base changed in a strange way, and we are
                        # now obligated to create a synthetic base
                        # commit.
                        new_base = self.sh.git("commit-tree", self.base_tree,
                                               "-p", branch_base(self.username, diffid),
                                               "-p", self.base_commit,
                                               input='Update base for {} on "{}"'.format(self.msg, title))
                    base_args = ("-p", new_base)
                self.sh.git("branch", "-f", branch_base(self.username, diffid), new_base)

                #   - Directly blast our current tree as the newest entry of pull,
                #     merging against the previous pull entry, and the newest base.

                tree = RE_RAW_TREE.search(commit).group("tree")
                new_pull = self.sh.git("commit-tree", tree,
                                       "-p", branch_head(self.username, diffid),
                                       *base_args,
                                       input='{} on "{}"'.format(self.msg, title))
                print("new_pull = {}".format(new_pull))
                self.sh.git("branch", "-f", branch_head(self.username, diffid), new_pull)

                # History reedit!  Commit message changes only
                if parent != self.base_orig:
                    print("Restacking commit on {}".format(self.base_orig))
                    new_orig = self.sh.git("commit-tree", tree, "-p", self.base_orig, input=commit_msg)

                self.sh.git("branch", "-f", branch_orig(self.username, diffid), new_orig)

                push_branches = ("base", "head", "orig")

            self.stack_meta.append({
                'id': prid,
                'title': title,
                'number': number,
                # NB: Ignore the commit message, and just reuse the old commit
                # message
                'body': pr_body,
                'base': branch_base(self.username, diffid),
                'diffid': diffid,
                'push_branches': push_branches
                })

        # The current pull request head commit, is the new base commit
        self.base_commit = new_pull
        self.base_orig = new_orig
        self.base_tree = tree
        print("base_commit = {}".format(self.base_commit))
        print("base_orig = {}".format(self.base_orig))
        print("base_tree = {}".format(self.base_tree))


    def post_process(self):
        # fix the HEAD pointer
        self.sh.git("reset", "--soft", self.base_orig)

        # update pull request information, update bases as necessary
        #   preferably do this in one network call
        # push your commits (be sure to do this AFTER you update bases)
        push_branches = []
        force_push_branches = []
        for i, s in enumerate(self.stack_meta):
            print("# Updating https://github.com/{owner}/{repo}/pull/{number}".format(owner=self.repo_owner, repo=self.repo_name, number=s["number"]))
            if self.github.future:
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
            else:
                self.github_rest.patch("repos/{owner}/{repo}/pulls/{number}".format(owner=self.repo_owner, repo=self.repo_name, number=s['number']),
                                       body=s['body'],
                                       title=s['title'],
                                       base=s['base'])
            # It is VERY important that we do this push AFTER fixing the base,
            # otherwise GitHub will spuriously think that the user pushed a number
            # of patches as part of the PR, when actually they were just from
            # the (new) upstream branch
            for b in s['push_branches']:
                if b == 'orig':
                    force_push_branches.append(branch(self.username, s['diffid'], b))
                else:
                    push_branches.append(branch(self.username, s['diffid'], b))
        # Careful!  Don't push master.
        if push_branches:
            self.sh.git("push", "origin", *push_branches)
        if force_push_branches:
            self.sh.git("push", "origin", "--force", *force_push_branches)


# How to update commit messages?  Probably should reimplement git rebase
# -i by hand.  Prefer NOT to actually affect working copy when making
# changes.

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Submit stack of diffs to GitHub.')
    parser.add_argument('msg', metavar='MSG', default='Update', nargs='?', type=str, help='Description of change you made')
    parser.add_argument('--github-v4', default=GraphQLEndpoint('https://api.github.com/graphql'), help='GitHub GraphQL API endpoint (V4) to use')
    parser.add_argument('--github-v3', default=RESTEndpoint('https://api.github.com'), help='GitHub REST API endpoint (V3) to use')
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(['.ghrc', os.path.expanduser('~/.ghrc')])

    write_back = False
    if not config.has_section('gh'):
        config.add_section('gh')
    if not config.has_option('gh', 'github_oauth'):
        github_oauth = config.set('gh', 'github_oauth', getpass.getpass('GitHub OAuth token (make one at https://github.com/settings/tokens -- we need public_repo permissions): ').strip())
        write_back = True
    else:
        github_oauth = config.get('gh', 'github_oauth')

    if write_back:
        config.write(open(os.path.expanduser('~/.ghrc'), 'w'))
        print("NB: saved to ~/.ghrc")

    args.github_v4.oauth_token = github_oauth
    args.github_v3.oauth_token = github_oauth

    main(msg=args.msg, github=args.github_v4, github_rest=args.github_v3)
