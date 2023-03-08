#!/bin/sh
set -ex
isort --check --diff ghstack
black --check ghstack
flake8 ghstack
mypy --install-types --non-interactive -m ghstack
pytest --verbose
echo "OK"
