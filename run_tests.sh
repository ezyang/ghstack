#!/bin/sh
set -e
flake8
mypy --strict ghstack
mypy test_ghstack.py
python3 test_expecttest.py
python3 test_ghstack.py
