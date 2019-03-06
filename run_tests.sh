#!/bin/sh
set -e
flake8-3 ghstack
mypy ghstack test_ghstack.py
python3 test_expecttest.py
python3 test_ghstack.py
