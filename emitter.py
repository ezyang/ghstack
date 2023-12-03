#!/usr/bin/env python3

# Helper binary used by test_shell.py to print interleaved sequences
# of strings to stderr/stdout.

import itertools
import sys
from typing import Iterator, Sequence, Tuple, TypeVar

T = TypeVar("T")


def grouper(n: int, iterable: Sequence[T]) -> Iterator[Tuple[T, ...]]:
    "grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx"
    args = [iter(iterable)] * n
    return itertools.zip_longest(*args)


if __name__ == "__main__":
    for mode, payload in grouper(2, sys.argv[1:]):
        # NB: this is \n and not linesep because we are going to send
        # payload to a text mode stream (stderr/stdout) which will
        # automatically translate to native layout as needed
        payload = payload.replace(r"\n", "\n")
        if mode == "e":
            print(payload, end="", file=sys.stderr)
            sys.stderr.flush()
        elif mode == "o":
            print(payload, end="", file=sys.stdout)
            sys.stdout.flush()
        elif mode == "r":
            # Big enough payload to exceed default chunk limit
            print("." * (4096 * 128), file=sys.stdout)
            sys.stdout.flush()
        else:
            raise RuntimeError("Unrecognized mode")
