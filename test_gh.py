import textwrap
import doctest
import expecttest
import unittest
import subprocess
import warnings
import os
import shutil
import tempfile
import re

import gh


def indent(text, prefix):
    return ''.join(prefix+line if line.strip() else line for line in text.splitlines(True))


def create_pr(github):
    github.graphql("""
      mutation {
        createPullRequest(input: {
            baseRefName: "master",
            headRefName: "blah",
            title: "New PR",
            body: "What a nice PR this is",
            ownerId: 1000,
          }) {
          pullRequest {
            number
          }
        }
      }
    """)


class TestGh(expecttest.TestCase):
    # Starting up node takes 0.7s.  Don't do it every time.
    @classmethod
    def setUpClass(cls):
        port = 49152
        # Find an open port to run our tests on
        while True:
            cls.proc = subprocess.Popen(['node', 'github-fake/src/index.js', str(port)], stdout=subprocess.PIPE)
            r = cls.proc.stdout.readline()
            if not r.strip():
                cls.proc.terminate()
                cls.proc.wait()
                port +=1
                print("Retrying with port {}".format(port))
                continue
            break
        cls.github = gh.Endpoint("http://localhost:{}".format(port))

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def setUp(self):
        self.github.graphql("""
          mutation {
            resetGitHub(input: {}) {
              clientMutationId
            }
          }
        """)
        tmp_dir = tempfile.mkdtemp()

        # Set up a "parent" repository with an empty initial commit that we'll operate on
        upstream_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(upstream_dir))
        self.upstream_sh = gh.Shell(cwd=upstream_dir, testing=True)
        self.upstream_sh.git("init", "--bare")
        tree = self.upstream_sh.git("write-tree")
        commit = self.upstream_sh.git("commit-tree", tree, input="Initial commit")
        self.upstream_sh.git("branch", "-f", "master", commit)

        local_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(local_dir))
        self.sh = gh.Shell(cwd=local_dir, testing=True)
        self.sh.git("clone", upstream_dir, ".")

        self.substituteRev("HEAD", "rINI0")

    def gh(self):
        gh.main(github=self.github, sh=self.sh)

    def dump_github(self):
        r = self.github.graphql("""
          query {
            repository(name: "pytorch", owner: "pytorch") {
              pullRequests {
                nodes {
                  number
                  baseRefName
                  headRefName
                  title
                  body
                }
              }
            }
          }
        """)
        prs = []
        for pr in r['data']['repository']['pullRequests']['nodes']:
            pr['body'] = indent(pr['body'], '    ')
            pr['commits'] = self.upstream_sh.git("log", "--reverse", "--pretty=format:%h %s", pr["baseRefName"] + ".." + pr["headRefName"])
            pr['commits'] = indent(pr['commits'], '     * ')
            prs.append("#{number} {title} ({headRefName} -> {baseRefName})\n\n"
                       "{body}\n\n{commits}\n\n".format(**pr))
            refs = self.upstream_sh.git("for-each-ref", "--format=%(refname:lstrip=2) %(objectname:short) %(contents:subject)", "refs/heads/gh/")
        return "".join(prs) + refs + "\n"

    def substituteRev(self, rev, substitute):
        # short doesn't really have to be here if we do substituteRev
        self.substituteExpected(self.sh.git("rev-parse", "--short", rev), substitute)

    def test_simple(self):
        print("####################")
        print("### test_simple")
        print("###")

        print("### First commit")
        self.sh.git("commit", "--allow-empty", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    This is my first commit

     * rMRG1 Commit 1

gh/ezyang/1/base rINI0 Initial commit
gh/ezyang/1/head rMRG1 Commit 1
gh/ezyang/1/orig rCOM1 Commit 1
''')
        print("###")
        print("### Second commit")
        self.sh.git("commit", "--allow-empty", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    This is my first commit

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    This is my second commit

     * rMRG2 Commit 2

gh/ezyang/1/base rINI0 Initial commit
gh/ezyang/1/head rMRG1 Commit 1
gh/ezyang/1/orig rCOM1 Commit 1
gh/ezyang/2/base rCOM1 Commit 1
gh/ezyang/2/head rMRG2 Commit 2
gh/ezyang/2/orig rCOM2 Commit 2
''')

    def test_amend(self):
        print("####################")
        print("### test_amend")
        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    A commit with an A

     * rMRG1 Commit 1

gh/ezyang/1/base rINI0 Initial commit
gh/ezyang/1/head rMRG1 Commit 1
gh/ezyang/1/orig rCOM1 Commit 1
''')
        print("###")
        print("### Amend the commit")
        self.sh.open("file1.txt", "w").write("ABBA")
        self.sh.git("add", "file1.txt")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend")
        self.substituteRev("HEAD", "rCOM2")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("gh/ezyang/1/head", "rMRG2")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1
     * rMRG2 Update

gh/ezyang/1/base rINI0 Initial commit
gh/ezyang/1/head rMRG2 Update
gh/ezyang/1/orig rCOM2 Commit 1
''')


#   def load_tests(loader, tests, ignore):
#       tests.addTests(doctest.DocTestSuite(gh))
#       return tests


if __name__ == '__main__':
    unittest.main()
