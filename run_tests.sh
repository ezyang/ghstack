#!/bin/sh
set -ex
isort . --check --diff
black --check .
flake8
mypy .
pytest --verbose
echo "OK"
