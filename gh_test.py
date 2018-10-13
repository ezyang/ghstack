import unittest
import inspect
import textwrap
import re
import doctest
import sys
import string

from hypothesis import given, event
from hypothesis.strategies import text, integers, composite

import gh

def indent(text, prefix):
    """
    Poly-fill for textwrap.indent on Python 2
    """
    return ''.join(prefix+line for line in text.splitlines(True))

def nth_line(src, lineno):
    """
    Compute the starting index of the n-th line (where n is 1-indexed)

    >>> nth_line("aaa\\nbb\\nc", 2)
    4
    """
    assert lineno >= 1
    pos = 0
    for _ in range(lineno - 1):
        pos = src.find('\n', pos) + 1
    return pos

def normalize_nl(t):
    return t.replace('\r\n', '\n').replace('\r', '\n')

def escape_trailing_quote(s):
    if s and s[-1] == "'":
        return s[:-1] + r"\'"
    else:
        return s

RE_EXPECT = re.compile(r"^( *)([^\n]*?''')(.*?)(''')", re.DOTALL | re.MULTILINE)

def replace_string_literal(src, lineno, new_string):
    r"""
    Replace a triple single-quoted string literal with new contents.
    Only handles printable ASCII correctly at the moment.

    >>> print(replace_string_literal("'''arf'''", 1, "barf"))
    '''barf'''
    >>> print(replace_string_literal("  moo = '''arf'''", 1, "'a'\n\\b\n"))
      moo = '''\
    'a'
    \\b
    '''
    >>> print(replace_string_literal("    f('''\"\"\"''')", 1, "a ''' b"))
        f('''a \'\'\' b''')
    """
    assert all(c in string.printable for c in new_string)
    i = nth_line(src, lineno)
    def escape(s):
        return escape_trailing_quote(normalize_nl(s).replace('\\', '\\\\')) \
                .replace("'''", r"\'\'\'")
    def replace(m):
        inner = escape(new_string) + m.group(4)
        msg = "\\\n" + inner if "\n" in new_string else inner
        return ''.join([m.group(1), m.group(2), msg])
    return src[:i] + RE_EXPECT.sub(replace, src[i:], count=1)

@composite
def text_lineno(draw):
    t = draw(text("a\n"))
    lineno = draw(integers(min_value=1, max_value=t.count("\n")+1))
    return (t, lineno)

class TestFunctional(unittest.TestCase):
    longMessage = True

    @given(text_lineno())
    def test_nth_line_ref(self, t_l):
        t, l = t_l
        event("lineno = {}".format(l))
        def nth_line_ref(src, lineno):
            xs = src.split("\n")[:lineno]
            xs[-1] = ''
            return len("\n".join(xs))
        self.assertEqual(nth_line(t, l), nth_line_ref(t, l))

    @given(text(string.printable))
    def test_replace_string_literal_roundtrip(self, t):
        prog = """\
        r = '''placeholder'''
        r2 = '''placeholder2'''
        r3 = '''placeholder3'''
        """
        new_prog = replace_string_literal(textwrap.dedent(prog), 2, t)
        exec(new_prog)
        msg = "program was:\n{}".format(new_prog)
        self.assertEqual(r, 'placeholder', msg=msg)
        self.assertEqual(r2, normalize_nl(t), msg=msg)
        self.assertEqual(r3, 'placeholder3', msg=msg)

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
