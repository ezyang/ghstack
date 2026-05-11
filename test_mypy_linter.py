import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tools.linter.adapters.mypy_linter import RESULTS_RE


def _check(paths: List[Path]) -> List[Dict[str, Any]]:
    repo_root = Path(__file__).parent
    proc = subprocess.run(
        [
            sys.executable,
            "tools/linter/adapters/mypy_linter.py",
            "--config=mypy.ini",
            "--",
            *[str(path) for path in paths],
        ],
        capture_output=True,
        check=True,
        cwd=repo_root,
        text=True,
    )
    return [json.loads(line) for line in proc.stdout.splitlines()]


def test_py_test_allows_top_level_await(tmp_path: Path) -> None:
    test_file = tmp_path / "ok.py.test"
    test_file.write_text(
        """\
async def commit(name: str) -> None:
    pass

await commit("A")
"""
    )

    assert _check([test_file]) == []


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="mypy does not report this top-level coroutine fixture on Windows",
)
def test_py_test_detects_unawaited_coroutine(tmp_path: Path) -> None:
    test_file = tmp_path / "missing_await.py.test"
    test_file.write_text(
        """\
async def commit(name: str) -> None:
    pass

commit("A")
"""
    )

    lint_messages = _check([test_file])
    assert [message["name"] for message in lint_messages] == ["[unused-coroutine]"]
    assert lint_messages[0]["path"] == str(test_file)
    assert lint_messages[0]["line"] == 4
    assert lint_messages[0]["description"] == (
        'Value of type "Coroutine[Any, Any, None]" must be used '
    )


def test_results_re_parses_windows_drive_paths() -> None:
    match = RESULTS_RE.match(
        r'C:\tmp\py_test_0.py:4:1: error: Value of type "Coroutine[Any, Any, None]" must be used  [unused-coroutine]'
    )
    assert match is not None
    assert match["file"] == r"C:\tmp\py_test_0.py"
    assert match["line"] == "4"
    assert match["column"] == "1"
    assert match["severity"] == "error"
    assert match["code"] == "[unused-coroutine]"
