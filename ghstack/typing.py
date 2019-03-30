#!/usr/bin/env python3

from typing import NewType

# A bunch of commonly used type definitions.

PhabricatorDiffNumberWithD = \
    NewType('PhabricatorDiffNumberWithD', str)  # aka "D1234567"

GitHubNumber = NewType('GitHubNumber', int)  # aka 1234 (as in #1234)

# GraphQL ID that identifies Repository from GitHubb schema;
# aka MDExOlB1bGxSZXF1ZXN0MjU2NDM3MjQw
GitHubRepositoryId = NewType('GitHubRepositoryId', str)

# aka 12 (as in gh/ezyang/12/base)
GhNumber = NewType('GhNumber', str)
