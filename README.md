# ghstack

Conveniently submit stacks of diffs to GitHub as separate pull requests.

```
pip3 install ghstack
```

Python 3.6 and greater only.

## How to setup

Go to github.com `Settings→Developer Settings→Personal Access Tokens` and
generate a token with `public_repo` access only.
Create a `~/.ghstackrc` as shown below:
```
λ cat ~/.ghstackrc
[ghstack]
github_url = github.com
github_oauth = [your_own_token]
github_username = [your_username]
remote_name = upstream [if remote is called upstream and not origin]
```

## How to use

Make sure you have write permission to the repo you're opening PR with.

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
normal GitHub UI, as their branch bases won't be master.  Use
`ghstack land $PR_URL` to land a ghstack'ed pull request.

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

This project uses [Poetry](https://python-poetry.org/docs/#installation), so
after you've installed Poetry itself, run this command in your clone of this
repo to install all the dependencies you need for working on `ghstack`:
```
poetry install
```
Note that this installs the dependencies (and `ghstack` itself) in an isolated
Python virtual environment rather than globally. If your cwd is in your clone of
this repo then you can run your locally-built `ghstack` using `poetry run
ghstack $ARGS`, but if you want to run it from somewhere else, you probably want
[`poetry shell`](https://python-poetry.org/docs/cli/#shell) instead:
```
poetry shell
cd $SOMEWHERE
ghstack $ARGS
```

### Testing

We have tests, using a mock GitHub GraphQL server!  How cool is that?
```
poetry run python test_ghstack.py
```
That runs most of the tests; you can run all tests (including lints) like this:
```
poetry run ./run_tests.sh
```

### Publishing

You can also [use Poetry to
publish](https://python-poetry.org/docs/cli/#publish) to a package repository.
For instance, if you've configured your [Poetry
repositories](https://python-poetry.org/docs/repositories/) like this:
```
poetry config repositories.testpypi https://test.pypi.org/legacy/
```
Then you can publish to TestPyPI like this:
```
poetry publish --build --repository testpypi
```
To publish to PyPI itself, just omit the `--repository` argument.

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

Channeling Conor McBride, this section documents mistakes worth
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
