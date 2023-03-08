#!/bin/sh
set -ex
isort --check --diff ghstack
black --check ghstack
flake8 ghstack
mypy -m ghstack
pytest --verbose
echo "OK"
