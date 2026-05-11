import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Pattern, Tuple


IS_WINDOWS: bool = os.name == "nt"


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, flush=True, **kwargs)


class LintSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    ADVICE = "advice"
    DISABLED = "disabled"


class LintMessage(NamedTuple):
    path: Optional[str]
    line: Optional[int]
    char: Optional[int]
    code: str
    severity: LintSeverity
    name: str
    original: Optional[str]
    replacement: Optional[str]
    description: Optional[str]


def as_posix(name: str) -> str:
    return name.replace("\\", "/") if IS_WINDOWS else name


def _path_key(name: str) -> str:
    return as_posix(os.path.abspath(name))


# tools/linter/flake8_linter.py:15:13: error: Incompatibl...int")  [assignment]
RESULTS_RE: Pattern[str] = re.compile(
    r"""(?mx)
    ^
    (?P<file>(?:[A-Za-z]:)?.*?):
    (?P<line>\d+):
    (?:(?P<column>-?\d+):)?
    \s(?P<severity>\S+?):?
    \s(?P<message>.*)
    \s(?P<code>\[.*\])
    $
    """
)

# torch/_dynamo/variables/tensor.py:363: error: INTERNAL ERROR
INTERNAL_ERROR_RE: Pattern[str] = re.compile(
    r"""(?mx)
    ^
    (?P<file>(?:[A-Za-z]:)?.*?):
    (?P<line>\d+):
    \s(?P<severity>\S+?):?
    \s(?P<message>INTERNAL\sERROR.*)
    $
    """
)


def run_command(
    args: List[str],
    *,
    extra_env: Optional[Dict[str, str]],
    retries: int,
) -> "subprocess.CompletedProcess[bytes]":
    logging.debug("$ %s", " ".join(args))
    start_time = time.monotonic()
    try:
        return subprocess.run(
            args,
            capture_output=True,
        )
    finally:
        end_time = time.monotonic()
        logging.debug("took %dms", (end_time - start_time) * 1000)


# Severity is either "error" or "note":
# https://github.com/python/mypy/blob/8b47a032e1317fb8e3f9a818005a6b63e9bf0311/mypy/errors.py#L46-L47
severities = {
    "error": LintSeverity.ERROR,
    "note": LintSeverity.ADVICE,
}


def check_mypy_installed(code: str) -> List[LintMessage]:
    cmd = [sys.executable, "-mmypy", "-V"]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return []
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode(errors="replace")
        return [
            LintMessage(
                path=None,
                line=None,
                char=None,
                code=code,
                severity=LintSeverity.ERROR,
                name="command-failed",
                original=None,
                replacement=None,
                description=f"Could not run '{' '.join(cmd)}': {msg}",
            )
        ]


def check_files(
    filenames: List[str],
    config: str,
    retries: int,
    code: str,
    extra_mypy_args: Optional[List[str]] = None,
    path_map: Optional[Dict[str, str]] = None,
) -> List[LintMessage]:
    try:
        proc = run_command(
            [sys.executable, "-mmypy", f"--config={config}"]
            + (extra_mypy_args or [])
            + filenames,
            extra_env={},
            retries=retries,
        )
    except OSError as err:
        return [
            LintMessage(
                path=None,
                line=None,
                char=None,
                code=code,
                severity=LintSeverity.ERROR,
                name="command-failed",
                original=None,
                replacement=None,
                description=(f"Failed due to {err.__class__.__name__}:\n{err}"),
            )
        ]
    stdout = str(proc.stdout, "utf-8").strip()
    stderr = str(proc.stderr, "utf-8").strip()

    def report_path(path: str) -> str:
        if path_map is None:
            return path
        return path_map.get(_path_key(path), path)

    rc = [
        LintMessage(
            path=report_path(match["file"]),
            name=match["code"],
            description=match["message"],
            line=int(match["line"]),
            char=(
                int(match["column"])
                if match["column"] is not None and not match["column"].startswith("-")
                else None
            ),
            code=code,
            severity=severities.get(match["severity"], LintSeverity.ERROR),
            original=None,
            replacement=None,
        )
        for match in RESULTS_RE.finditer(stdout)
    ] + [
        LintMessage(
            path=report_path(match["file"]),
            name="INTERNAL ERROR",
            description=match["message"],
            line=int(match["line"]),
            char=None,
            code=code,
            severity=severities.get(match["severity"], LintSeverity.ERROR),
            original=None,
            replacement=None,
        )
        for match in INTERNAL_ERROR_RE.finditer(stderr)
    ]
    return rc


def make_py_test_mypy_inputs(
    filenames: List[str],
    tmpdir: str,
) -> Tuple[List[str], Dict[str, str]]:
    mypy_filenames: List[str] = []
    path_map: Dict[str, str] = {}
    for i, filename in enumerate(filenames):
        mypy_filename = os.path.join(tmpdir, f"py_test_{i}.py")
        Path(mypy_filename).write_text(Path(filename).read_text())
        mypy_filenames.append(mypy_filename)
        path_map[_path_key(mypy_filename)] = filename
    return mypy_filenames, path_map


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mypy wrapper linter.",
        fromfile_prefix_chars="@",
    )
    parser.add_argument(
        "--retries",
        default=3,
        type=int,
        help="times to retry timed out mypy",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="path to an mypy .ini config file",
    )
    parser.add_argument(
        "--code",
        default="MYPY",
        help="the code this lint should report as",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="verbose logging",
    )
    parser.add_argument(
        "filenames",
        nargs="+",
        help="paths to lint",
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="<%(threadName)s:%(levelname)s> %(message)s",
        level=(
            logging.NOTSET
            if args.verbose
            else logging.DEBUG if len(args.filenames) < 1000 else logging.INFO
        ),
        stream=sys.stderr,
    )

    # Use a dictionary here to preserve order. mypy cares about order,
    # tragically, e.g. https://github.com/python/mypy/issues/2015
    filenames: Dict[str, bool] = {}

    # If a stub file exists, have mypy check it instead of the original file, in
    # accordance with PEP-484 (see https://www.python.org/dev/peps/pep-0484/#stub-files)
    for filename in args.filenames:
        if filename.endswith(".pyi"):
            filenames[filename] = True
            continue

        stub_filename = filename.replace(".py", ".pyi")
        if Path(stub_filename).exists():
            filenames[stub_filename] = True
        else:
            filenames[filename] = True

    py_filenames = [
        filename for filename in filenames if not filename.endswith(".py.test")
    ]
    py_test_filenames = [
        filename for filename in filenames if filename.endswith(".py.test")
    ]

    lint_messages = check_mypy_installed(args.code)
    if py_filenames:
        lint_messages += check_files(py_filenames, args.config, args.retries, args.code)
    if py_test_filenames:
        with tempfile.TemporaryDirectory() as tmpdir:
            mypy_filenames, path_map = make_py_test_mypy_inputs(
                py_test_filenames, tmpdir
            )
            lint_messages += check_files(
                mypy_filenames,
                args.config,
                args.retries,
                args.code,
                extra_mypy_args=[
                    "--no-incremental",
                    "--disable-error-code=top-level-await",
                ],
                path_map=path_map,
            )
    for lint_message in lint_messages:
        print(json.dumps(lint_message._asdict()), flush=True)


if __name__ == "__main__":
    main()
