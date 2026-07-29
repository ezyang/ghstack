#!/usr/bin/env python3

import argparse
import ast
import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


async def run_path(
    path: str,
    *,
    argv: Optional[Sequence[str]] = None,
    globals_: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    script_path = str(Path(path))
    script_argv = [script_path, *(argv or ())]
    old_argv = sys.argv
    sys.argv = script_argv
    try:
        source = Path(script_path).read_text()
        namespace: Dict[str, Any] = {
            "__file__": script_path,
            "__name__": "__main__",
            "__package__": None,
            "__builtins__": __builtins__,
        }
        if globals_ is not None:
            namespace.update(globals_)
        code = compile(
            source,
            script_path,
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        result = eval(code, namespace)
        if inspect.isawaitable(result):
            await result
        return namespace
    finally:
        sys.argv = old_argv


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run a Python script with top-level await support."
    )
    parser.add_argument("script")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)
    asyncio.run(run_path(ns.script, argv=ns.args))


if __name__ == "__main__":
    main()
