import unittest
import textwrap
import re
import doctest
import sys
import string
import os
import traceback

from hypothesis import given, event
from hypothesis.strategies import text, integers, composite

import gh


ACCEPT = os.getenv('GH_TEST_ACCEPT')


ACCEPT_HISTORY = {}


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


def adjust_lineno(state, fn, lineno):
    if fn not in state:
        return lineno
    for edit_loc, edit_diff in state[fn]:
        if lineno > edit_loc:
            lineno += edit_diff
    return lineno


def record_edit(state, fn, lineno, delta):
    state.setdefault(fn, []).append(lineno, delta)


RE_EXPECT = re.compile(r"^( *)([^\n]*?''')(.*?)(''')", re.DOTALL | re.MULTILINE)


def replace_string_literal(src, lineno, new_string):
    r"""
    Replace a triple single-quoted string literal with new contents.
    Only handles printable ASCII correctly at the moment.

    Returns a tuple of the replaced string, as well as a delta of
    number of lines added/removed.

    >>> replace_string_literal("'''arf'''", 1, "barf")
    ("'''barf'''", 0)
    >>> r = replace_string_literal("  moo = '''arf'''", 1, "'a'\n\\b\n")
    >>> print(r[0])
      moo = '''\
    'a'
    \\b
    '''
    >>> r[1]
    3
    >>> replace_string_literal("  moo = '''\\\narf'''", 1, "'a'\n\\b\n")[1]
    2
    >>> print(replace_string_literal("    f('''\"\"\"''')", 1, "a ''' b")[0])
        f('''a \'\'\' b''')
    """
    assert all(c in string.printable for c in new_string)
    i = nth_line(src, lineno)
    new_string = normalize_nl(new_string)

    delta = [new_string.count("\n")]
    if delta[0] > 0:
        delta[0] += 1  # handle the extra \\\n

    def escape(s):
        return escape_trailing_quote(s.replace('\\', '\\\\')).replace("'''", r"\'\'\'")

    def replace(m):
        inner = escape(new_string) + m.group(4)
        msg = "\\\n" + inner if "\n" in new_string else inner
        delta[0] -= m.group(3).count("\n")
        return ''.join([m.group(1), m.group(2), msg])

    return (src[:i] + RE_EXPECT.sub(replace, src[i:], count=1), delta[0])


@composite
def text_lineno(draw):
    t = draw(text("a\n"))
    lineno = draw(integers(min_value=1, max_value=t.count("\n")+1))
    return (t, lineno)


class TestFunctional(unittest.TestCase):
    longMessage = True

    @given(text_lineno())
    def test_nth_line_ref(self, t_lineno):
        t, lineno = t_lineno
        event("lineno = {}".format(lineno))

        def nth_line_ref(src, lineno):
            xs = src.split("\n")[:lineno]
            xs[-1] = ''
            return len("\n".join(xs))
        self.assertEqual(nth_line(t, lineno), nth_line_ref(t, lineno))

    @given(text(string.printable))
    def test_replace_string_literal_roundtrip(self, t):
        prog = """\
        r = '''placeholder'''
        r2 = '''placeholder2'''
        r3 = '''placeholder3'''
        """
        new_prog = replace_string_literal(textwrap.dedent(prog), 2, t)[0]
        exec(new_prog)
        msg = "program was:\n{}".format(new_prog)
        self.assertEqual(r, 'placeholder', msg=msg)  # noqa: F821
        self.assertEqual(r2, normalize_nl(t), msg=msg)  # noqa: F821
        self.assertEqual(r3, 'placeholder3', msg=msg)  # noqa: F821


class TestExpect(unittest.TestCase):
    longMessage = True

    def assertExpected(self, actual, expect, skip=0):
        if ACCEPT:
            # current frame and parent frame, plus any requested skip
            tb = traceback.extract_stack(limit=2+skip)
            fn, lineno, _, _ = tb[0]
            """
            with open(fn, 'r+') as f:
                old = f.read()

                # compute the change in lineno
                lineno = adjust_lineno(ACCEPT_HISTORY, fn, lineno)
                delta = 

                # Only write the backup file the first time we hit the
                # file
                if fn not in ACCEPT_HISTORY:
                    with open(fn + ".bak", 'w') as f_bak:
                        f_bak.write(old)
                f.seek(0)
                f.truncate(0)
                f.write(new)

            record_edit(ACCEPT_HISTORY, fn, lineno, delta)
            """
        else:
            help_text = "To accept the current output, re-run test with envvar GH_TEST_ACCEPT=1"
            if hasattr(self, "assertMultiLineEqual"):
                self.assertMultiLineEqual(actual, expect, msg=help_text)
            else:
                self.assertEqual(actual, expect, msg=help_text)

    def test_sample(self):
        self.assertExpected("foo", '''bar''')

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
