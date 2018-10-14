import unittest
import textwrap
import re
import doctest
import sys
import string
import os
import traceback

import hypothesis
from hypothesis import given, event, assume
from hypothesis.strategies import text, integers, composite, sampled_from, booleans

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


def escape_trailing_quote(s, quote):
    if s and s[-1] == quote:
        return s[:-1] + '\\' + quote
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
    state.setdefault(fn, []).append((lineno, delta))


def ok_for_raw_triple_quoted_string(s, quote):
    """
    Is this string representable inside a raw triple-quoted string?
    Due to the fact that backslashes are always treated literally,
    some strings are not representable.

    >>> ok_for_raw_triple_quoted_string("blah", quote="'")
    True
    >>> ok_for_raw_triple_quoted_string("'", quote="'")
    False
    >>> ok_for_raw_triple_quoted_string("a ''' b", quote="'")
    False
    """
    return quote * 3 not in s and (not s or s[-1] not in [quote, '\\'])


RE_EXPECT = re.compile(r"^(?P<prefix>[^\n]*?)"
                       r"(?P<raw>r?)"
                       r"(?P<quote>'''|" r'""")'
                       r"(?P<body>.*?)"
                       r"(?P=quote)", re.DOTALL)


def replace_string_literal(src, lineno, new_string):
    r"""
    Replace a triple quoted string literal with new contents.
    Only handles printable ASCII correctly at the moment.  This
    will preserve the quote style (and raw-ness) of the original
    string.

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

    def replace(m):
        s = new_string
        raw = m.group('raw') == 'r'
        if raw:
            assert ok_for_raw_triple_quoted_string(s, quote=m.group('quote')[0])
        else:
            s = s.replace('\\', '\\\\')
            if m.group('quote') == "'''":
                s = escape_trailing_quote(s, "'").replace("'''", r"\'\'\'")
            else:
                s = escape_trailing_quote(s, '"').replace('"""', r'\"\"\"')

        new_body = "\\\n" + s if "\n" in s and not raw else s
        delta[0] -= m.group('body').count("\n")

        return ''.join([m.group('prefix'),
                        m.group('raw'),
                        m.group('quote'),
                        new_body,
                        m.group('quote')])

    return (src[:i] + RE_EXPECT.sub(replace, src[i:], count=1), delta[0])


@composite
def text_lineno(draw):
    t = draw(text("a\n"))
    lineno = draw(integers(min_value=1, max_value=t.count("\n")+1))
    return (t, lineno)


class TestCase(unittest.TestCase):
    longMessage = True

    def assertExpected(self, actual, expect, skip=0):
        if ACCEPT:
            # current frame and parent frame, plus any requested skip
            tb = traceback.extract_stack(limit=2+skip)
            fn, lineno, _, _ = tb[0]
            with open(fn, 'r+') as f:
                old = f.read()

                # compute the change in lineno
                lineno = adjust_lineno(ACCEPT_HISTORY, fn, lineno)
                new, delta = replace_string_literal(old, lineno, actual)

                # Only write the backup file the first time we hit the
                # file
                if fn not in ACCEPT_HISTORY:
                    with open(fn + ".bak", 'w') as f_bak:
                        f_bak.write(old)
                f.seek(0)
                f.truncate(0)

                f.write(new)

            record_edit(ACCEPT_HISTORY, fn, lineno, delta)
        else:
            help_text = "To accept the current output, re-run test with envvar GH_TEST_ACCEPT=1"
            if hasattr(self, "assertMultiLineEqual"):
                self.assertMultiLineEqual(expect, actual, msg=help_text)
            else:
                self.assertEqual(expect, actual, msg=help_text)



class TestFunctional(TestCase):
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

    @given(text(string.printable), booleans(), sampled_from(['"', "'"]))
    def test_replace_string_literal_roundtrip(self, t, raw, quote):
        if raw:
            assume(ok_for_raw_triple_quoted_string(t, quote=quote))
        prog = """\
        r = {r}{quote}placeholder{quote}
        r2 = {r}{quote}placeholder2{quote}
        r3 = {r}{quote}placeholder3{quote}
        """.format(r='r' if raw else '', quote=quote*3)
        new_prog = replace_string_literal(textwrap.dedent(prog), 2, t)[0]
        exec(new_prog)
        msg = "program was:\n{}".format(new_prog)
        self.assertEqual(r, 'placeholder', msg=msg)  # noqa: F821
        self.assertEqual(r2, normalize_nl(t), msg=msg)  # noqa: F821
        self.assertEqual(r3, 'placeholder3', msg=msg)  # noqa: F821

    #ef test_hard_replace_string_literal(self):
    #   prog = 'x = """\\\n' "\\'\\'\\'\n" '"""'
    #   self.assertExpected(replace_string_literal(prog, 1, "'''")[0], '''''')


class TestExpect(TestCase):
    def test_sample(self):
        prog = r"""single_single('''0''')
single_multi('''1''')
multi_single('''\
2
''')
multi_multi_less('''\
3
4
''')
multi_multi_same('''\
5
''')
multi_multi_more('''\
6
''')
"""
        edits = [(1, "a"),
                 (2, "b\n"),
                 (3, "c"),
                 (6, "d\n"),
                 (10, "e\n"),
                 (13, "f\ng\n")]
        history = {}
        fn = 'test.py'
        for lineno, actual in edits:
            lineno = adjust_lineno(history, fn, lineno)
            prog, delta = replace_string_literal(prog, lineno, actual)
            record_edit(history, fn, lineno, delta)
        self.assertExpected(prog, """\
single_single(\'\'\'a\'\'\')
single_multi(\'\'\'\\
b
\'\'\')
multi_single(\'\'\'c\'\'\')
multi_multi_less(\'\'\'\\
d
\'\'\')
multi_multi_same(\'\'\'\\
e
\'\'\')
multi_multi_more(\'\'\'\\
f
g
a
\'\'\')
""")

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
