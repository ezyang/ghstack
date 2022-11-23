#!/usr/bin/env python3

import sys

if sys.version_info >= (3, 8):
    import importlib.metadata as importlib_metadata
else:
    import importlib_metadata

__version__ = importlib_metadata.version("ghstack")  # type: ignore[no-untyped-call]
