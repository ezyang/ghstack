#!/bin/sh
set -ex
isort . --check --diff
black --check .
flake8
mypy --install-types --non-interactive .
pytest --verbose
echo "OK"
