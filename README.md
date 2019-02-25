# ghstack

Conveniently submit stacks of diffs to GitHub as separate pull requests.

## How to use

Prepare a series of commits on top of master, then run `ghstack`.  This
tool will push and create individuals for each PR on the stack.

**WARNING.**  You will NOT be able to merge these commits using the
normal UI method, as their branch bases won't be master.

## Structure of submitted pull requests

Every commit in your local commit stack gets submitted into a separate
pull request and pushes commits onto three branches:

* `gh/username/1/base` - think of this like "master": it's the base
  branch that your commit was based upon.  It is never force pushed;
  whenever you rebase your local stack, we add merge commits on top of
  base from the true upstream master.

* `gh/username/1/head` - this branch is your change, on top of the base
  branch.  Like base, it is never force pushed.  We open a pull request
  on this branch, requesting to merge into base.

* `gh/username/1/orig` - this is the actual commit as per your local
  copy.  GitHub pull requests never sees this commit, but if you want
  to get a "clean" commit all by itself, this is an easy way to get it.

## Developer notes

We have tests, using a mock GitHub GraphQL server!  How cool is that?
Run these tests using `python test_ghstack.py`

## Design constraints

There are some weird aspects about GitHub's design which lead to unusual
design decisions on this tool.

1. When you create a PR on GitHub, you cannot subsequently change which
   repository it merges into (you can change which branch merges into).
   This means you have a hard choice when designing a tool like this:
   if you want to target the topmost PR to master, you must push
   branches onto the origin repo; if you give up on this, you can push
   branches into a fork.  We've decided to give up targets to master so
   that we can push the branches to your fork.

   (Actually, this is a lie; we still push to origin lol.)

2. Branch name does not correspond to pull request number. While this
   would be excellent, we have no way of reserving a pull request
   number, so we have no idea what it's going to be until we open
   the pull request, but we can't open the pull request without a
   branch.
