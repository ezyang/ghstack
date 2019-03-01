#!/bin/sh
set -e
flake8
mypy --strict ghstack
python3 test_expecttest.py
python3 test_ghstack.py
