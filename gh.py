import argparse
import requests
import subprocess

def sh(args):
    p = subprocess.Popen(args, stdout=subprocess.PIPE)
    out, _ = p.communicate()
    if p.returncode != 0:
        raise RuntimeError("{} failed with exit code {}".format(' '.join(args), p.returncode))
    return out.decode()

def git(*args):
    return sh(["git"] + args)

# repo layout:
#   - gh/pull/2345  -- what we think GitHub's current tip for commit is
#   - gh/base/2345  -- what we think base commit for commit is
#   - and the true external state:
#       - origin/gh/pull/2345
#       - origin/gh/base/2345

# git merge-base origin/master HEAD
# git log (ish) gives us the stack of commits to process
# fetch from origin

# start with the earliest commit

# check if we authored the commit.  We don't touch shit we didn't
# create. (OPTIONAL)
#
# check if the commit message says what pull request it's associated with
#   If NONE:
#       - If possible, allocate ourselves a pull request number and then
#         fix the branch afterwards.
#       - Otherwise, generate a unique branch name, and attach it to
#         the commit message
#
# fetch up to date pull request information
# synchronize local pull/base state with external state
#
# ok, so now we want to ENTER a new entry into our log
#   - Directly blast the tree of HEAD~ as the newest entry in base,
#     synthetically merged with merge-base of HEAD and origin/master.
#     (This will make sure merge with master still works.)
#       - MAYBE, if we correspond to a known gh/pull branch, we can
#         also insert a merge here as well.  This will help merges
#         with feature branches keep working too.
#         (if you're doing weird shit with cherry-picking, this
#         won't work so good)
#   - Directly blast our current tree as the newest entry of pull,
#     merging against the previous pull entry, and the newest base.
#
# update pull request information, update bases as necessary
#   preferably do this in one network call
# push your commits (be sure to do this AFTER you update bases)
#
#
#
# How to update commit messages?  Probably should reimplement git rebase
# -i by hand.  Prefer NOT to actually affect working copy when making
# changes.
