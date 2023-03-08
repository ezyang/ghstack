#!/bin/sh
set -ex
export LIBCST_PARSER_TYPE=native
ufmt check ghstack
flake8 ghstack
mypy --install-types --non-interactive -m ghstack
pytest --verbose
echo "OK"
