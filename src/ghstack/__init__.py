#!/usr/bin/env python3

import sys

if sys.version_info >= (3, 8):
    import importlib.metadata as importlib_metadata
else:
    import importlib_metadata

if sys.version_info >= (3, 14):
    # Create new event loop as asyncio.get_event_loop() throws runtime error in 3.14
    import asyncio as _asyncio
    _asyncio.set_event_loop(_asyncio.new_event_loop())

__version__ = importlib_metadata.version("ghstack")  # type: ignore[no-untyped-call]
