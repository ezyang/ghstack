#!/bin/sh
set -ex
# NB: must not apply detailed-mypy.ini to flake8, because
# flake8 runs mypy on each file individually which means types
# from imported things won't get processed correctly
isort . --check --diff
flake8
mypy --config=detailed-mypy.ini .
pytest --verbose
echo "OK"
