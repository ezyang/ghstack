import textwrap
import doctest
import expecttest
import unittest
import subprocess
import warnings
import os

import gh


def indent(text, prefix):
    """
    Poly-fill for textwrap.indent on Python 2
    """
    return ''.join(prefix+line for line in text.splitlines(True))


def dump_github_state(github):
    r = github.graphql("""
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
        prs.append("#{number} {title} ({headRefName} -> {baseRefName})\n"
                   "{body}\n".format(**pr))
    return "\n".join(prs)


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
            resetGitHub(input: {})
          }
        """)

    def test_basic(self):
        create_pr(self.github)
        self.assertExpected(dump_github_state(self.github), '''''')


#   def load_tests(loader, tests, ignore):
#       tests.addTests(doctest.DocTestSuite(gh))
#       return tests


if __name__ == '__main__':
    unittest.main()
