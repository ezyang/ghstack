import textwrap
import doctest
import expecttest
import unittest
import subprocess
import warnings

import gh


class TestGh(unittest.TestCase):
    # Per test server?!  How luxurious: it costs 0.5s.  Not a great idea.
    def setUp(self):
        port = 49152
        self.proc = subprocess.Popen(['node', 'github-fake/src/index.js', str(port)], stdout=subprocess.PIPE)
        self.proc.stdout.readline()

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait()

    def test_basic(self):
        print("shufflin")

    def test_basic2(self):
        print("shufflin")


#   def load_tests(loader, tests, ignore):
#       tests.addTests(doctest.DocTestSuite(gh))
#       return tests


if __name__ == '__main__':
    unittest.main()
