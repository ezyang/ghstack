import unittest
import inspect
import textwrap
import re
import doctest
import sys

from hypothesis import given, event
from hypothesis.strategies import text, integers, composite

import gh

def indent(text, prefix):
    return ''.join(prefix+line for line in text.splitlines(True))

# lineno is 1-indexed
def nth_line(src, lineno):
    """
    >>> nth_line("aaa\\nbb\\nc", 2)
    4
    """
    assert lineno >= 1
    pos = 0
    for _ in range(lineno - 1):
        pos = src.find('\n', pos) + 1
    return pos

RE_EXPECT = re.compile(r"^([^\n]*)(.*?''')(.*?)(''')", re.DOTALL | re.MULTILINE)

def replace_string_literal(src, lineno, new_string):
    r"""
    >>> print(replace_string_literal("'''arf'''", 1, "barf"))
    '''\
    barf'''
    """
    i = nth_line(src, lineno)
    def replace(m):
        return ''.join([m.group(1), m.group(2), "\\\n",
                        indent(new_string.encode("string_escape"), m.group(1)),
                        m.group(4)
                        ])
    return src[:i] + RE_EXPECT.sub(replace, src[i:], count=1)

@composite
def text_lineno(draw):
    t = draw(text("a\n"))
    lineno = draw(integers(min_value=1, max_value=t.count("\n")+1))
    return (t, lineno)

class TestFunctional(unittest.TestCase):
    @given(text_lineno())
    def test_nth_line_ref(self, t_l):
        t, l = t_l
        event("lineno = {}".format(l))
        def nth_line_ref(src, lineno):
            xs = src.split("\n")[:lineno]
            xs[-1] = ''
            return len("\n".join(xs))
        self.assertEqual(nth_line(t, l), nth_line_ref(t, l))

class TestExpect(unittest.TestCase):
    def test_replace(self):
        s = """\
        def f():
            foo(\"\"\"\\
            blah
            \"\"\", more)
        """
        textwrap.dedent(s)
        s2 = """\
        def f():
            foo(\"\"\"\\
            bloop
            bling
            \"\"\", more)
        """


class TestGh(unittest.TestCase):
    def test_basic(self):
        pass

def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(sys.modules[__name__]))
    return tests

if __name__ == '__main__':
    unittest.main()
