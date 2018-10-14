import textwrap
import doctest
import expecttest
import unittest

import gh


class TestGh(unittest.TestCase):
    def test_basic(self):
        pass


#   def load_tests(loader, tests, ignore):
#       tests.addTests(doctest.DocTestSuite(gh))
#       return tests


if __name__ == '__main__':
    unittest.main()
