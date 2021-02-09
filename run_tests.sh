#!/bin/sh
set -ex
# NB: must not apply detailed-mypy.ini to flake8, because
# flake8 runs mypy on each file individually which means types
# from imported things won't get processed correctly
flake8 ghstack
mypy --config=detailed-mypy.ini ghstack test_ghstack.py
pytest --verbose
echo "OK"
