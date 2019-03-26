# ghstack

Conveniently submit stacks of diffs to GitHub as separate pull requests.

```
pip3 install ghstack
```

Python 3.6 and greater only.

## How to use

Prepare a series of commits on top of master, then run `ghstack`.  This
tool will push and create pull requests for each commit on the stack.

**How do I stack another PR on top of an existing one?** Assuming
you've checked out the latest commit from the existing PR, just
`git commit` a new commit on top, and then run `ghstack`.

**How do I modify a PR?**  Just edit the commit in question, and then
run `ghstack` again.  If the commit is at the top of your stack,
you can edit it with `git commit --amend`; otherwise, you'll have
to use `git rebase -i` to edit the commit directly.

**How do I rebase?**  The obvious way: `git rebase origin/master`.
Don't do a `git merge`; `ghstack` will throw a hissy fit if you
do that.  (There's also a more fundamental reason why this
won't work: since each commit is a separate PR, you have to
resolve conflicts in *each* PR, not just for the entire stack.)

**How do I start a new feature?**  Just checkout master on a new
branch, and start working on a fresh branch.

**WARNING.**  You will NOT be able to merge these commits using the
normal GitHub UI, as their branch bases won't be master.  For the
PyTorch repository, we have a special mechanism for landing diffs;
if you need a way to land these commits on a regular GitHub
repository, give a holler on issues and we'll add this functionality.

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
  to get a "clean" commit all by itself, for example, because you
  want to work on the commits from another machine, this is the best way
  to get it.

## Developer notes

We have tests, using a mock GitHub GraphQL server!  How cool is that?
Run these tests using `python test_ghstack.py`

## Design constraints

There are some weird aspects about GitHub's design which lead to unusual
design decisions on this tool.

1. When you create a PR on GitHub, it is ALWAYS created on the
   repository that the base branch exists on.  Thus, we MUST
   push branches to the upstream repository that you want
   PRs to be created on.  This can result in a lot of stale
   branches hanging around; you'll need to setup some other
   mechanism for pruning these branches.

2. Branch name does not correspond to pull request number. While this
   would be excellent, we have no way of reserving a pull request
   number, so we have no idea what it's going to be until we open
   the pull request, but we can't open the pull request without a
   branch.

## Ripley Cupboard

Channeling Conor McBridge, this section documents mistakes worth
mentioning.

**Non-stack mode.**  ghstack processes your entire stack when it
uploads updates, but it doesn't have to be that way; you could
imagine that you could ask ghstack to only process the topmost
commit and leave the rest alone.  An easy and attractive
looking way of doing this is to edit the stack selection algorithm
to look a single commit, rather than all the commits from
merge-base to head.

This sounds OK but you try it and you realize two things:

1. This is wrong, if you exclude the commits before your commit
   you'll end up with a base commit based on the "literal"
   commit in your Git repository.  But this has no relationship
   with the base commit that was previously uploaded, which
   was synthetically constructed.

2. You also have do extra work to pull out an up to date stack
   to write into the pull request body.

So, this is not impossible to do, but it will need some work.
You have to work out what the real base commit is, whether
or not you need to advance it, and also rewrite the stack rendering
code.
