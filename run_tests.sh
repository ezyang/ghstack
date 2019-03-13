#!/bin/sh
set -e
# NB: must not apply detailed-mypy.ini to flake8-3, because
# flake8 runs mypy on each file individually which means types
# from imported things won't get processed correctly
flake8-3 ghstack
mypy --config=detailed-mypy.ini ghstack test_ghstack.py
python3 test_expecttest.py
python3 test_ghstack.py
