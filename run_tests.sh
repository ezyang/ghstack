#!/bin/sh
set -ex
isort . --check --diff
flake8
mypy .
pytest --verbose
echo "OK"
