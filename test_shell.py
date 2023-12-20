#!/usr/bin/env python3

import logging
import sys
import unittest
from dataclasses import dataclass
from typing import Any, List

import expecttest

import ghstack.shell


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
        # TODO: probably should make this scoped smh
        logging.getLogger("asyncio").setLevel(logging.WARNING)

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
            s = s.replace("'python'", "python")
            return s

        return "\n".join(redact(r.getMessage()) for r in cm.records)

    def test_stdout(self) -> None:
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(out(r"arf\n"))
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py o 'arf\\n'
arf
""",
        )

    def test_stderr(self) -> None:
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(err(r"arf\n"))
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py e 'arf\\n'
# stderr:
arf
""",
        )

    def test_stdout_passthru(self) -> None:
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(out(r"arf\n"), stdout=None)
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py o 'arf\\n'
arf
""",
        )

    def test_stdout_with_stderr_prefix(self) -> None:
        # What most commands should look like
        with self.assertLogs(level=logging.DEBUG) as cm:
            self.emit(
                err(r"Step 1...\n"),
                err(r"Step 2...\n"),
                err(r"Step 3...\n"),
                out(r"out\n"),
                stdout=None,
            )
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py e 'Step 1...\\n' e 'Step 2...\\n' e 'Step 3...\\n' o 'out\\n'
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
            self.emit(out(r"A\n"), err(r"B\n"), out(r"C\n"), err(r"D\n"), stdout=None)
        self.assertExpectedInline(
            self.flog(cm),
            """\
$ python emitter.py o 'A\\n' e 'B\\n' o 'C\\n' e 'D\\n'
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
        self.emit(out(r"A\n"), stdout=sys.stdout)


if __name__ == "__main__":
    unittest.main()
