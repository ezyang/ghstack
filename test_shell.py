#!/usr/bin/env python3

import logging
import os
import sys
import unittest
from dataclasses import dataclass
from typing import Any, List

import expecttest

import ghstack.shell


N = os.linesep


@dataclass
class ConsoleMsg:
    pass


@dataclass
class out(ConsoleMsg):
    msg: str


@dataclass
class err(ConsoleMsg):
    msg: str


@dataclass
class big_dump(ConsoleMsg):
    pass


class TestShell(expecttest.TestCase):
    def setUp(self) -> None:
        self.sh = ghstack.shell.Shell()

    def emit(self, *payload: ConsoleMsg, **kwargs: Any) -> ghstack.shell._SHELL_RET:
        args: List[str] = [sys.executable, "emitter.py"]
        for p in payload:
            if isinstance(p, out):
                args.extend(("o", p.msg))
            elif isinstance(p, err):
                args.extend(("e", p.msg))
            elif isinstance(p, big_dump):
                args.extend(("r", "-"))
        return self.sh.sh(*args, **kwargs)

    def flog(self, cm: "unittest._AssertLogsContext") -> str:  # type: ignore[name-defined]
        def redact(s: str) -> str:
            s = s.replace(sys.executable, "python")
            return s

        return N.join(redact(r.getMessage()) for r in cm.records)

    def test_stdout(self) -> None:
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(out("arf" + N))
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py o 'arf
'
arf
""",
        )

    def test_stderr(self) -> None:
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(err("arf" + N))
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py e 'arf
'
# stderr:
arf
""",
        )

    def test_stdout_passthru(self) -> None:
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(out("arf" + N), stdout=None)
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py o 'arf
'
arf
""",
        )

    def test_stdout_with_stderr_prefix(self) -> None:
        # What most commands should look like
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(
                err("Step 1..." + N),
                err("Step 2..." + N),
                err("Step 3..." + N),
                out("out" + N),
                stdout=None,
            )
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py e 'Step 1...
' e 'Step 2...
' e 'Step 3...
' o 'out
'
# stderr:
Step 1...
Step 2...
Step 3...

# stdout:
out
""",
        )

    def test_interleaved_stdout_stderr_passthru(self) -> None:
        # NB: stdout is flushed in each of these cases
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(
                out("A" + N), err("B" + N), out("C" + N), err("D" + N), stdout=None
            )
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py o 'A
' e 'B
' o 'C
' e 'D
'
# stderr:
B
D

# stdout:
A
C
""",
        )

    def test_deadlock(self) -> None:
        self.emit(big_dump())

    def test_uses_raw_fd(self) -> None:
        self.emit(out("A" + N), stdout=sys.stdout)


if __name__ == "__main__":
    unittest.main()
