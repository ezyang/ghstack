#!/usr/bin/env python3

import ghstack.shell
#from ghstack.git import GitCommitHash

from typing import Optional


def main(sh: Optional[ghstack.shell.Shell] = None) -> None:
    # We land the entire stack.  Explicitly specifying a commit
    # is not yet supported.
    #
    # Need to do a consistency check with PR
    #
    # Hard-coded push to master.

    raise NotImplementedError

    """
    if sh is None:
        # Use CWD
        sh = ghstack.shell.Shell()

    # Get up-to-date
    sh.git("fetch", "origin")

    base = GitCommitHash(sh.git("merge-base", "origin/master", "HEAD"))

    # compute the stack of commits in chronological order (does not
    # include base)
    stack = ghstack.git.split_header(
        sh.git("rev-list", "--reverse", "--header", "^" + base, "HEAD"))

    # Switch working copy
    try:
        prev_ref = sh.git("symbolic-ref", "--short", "HEAD")
    except RuntimeError:
        prev_ref = sh.git("rev-parse", "HEAD")

    # This will fail first
    sh.git("checkout", "origin/master")
    try:
        # do consistency check
        for s in stack:
            commit_id = s.commit_id()
            title = s.title()
            metadata = s.match_metadata()
            if metadata is None:
                raise RuntimeError('''\
Commit {} "{}"
does not appear to have been submitted to GitHub with ghstack;
we cannot land it (a.k.a. it is missing the gh-metadata line)
'''.format(commit_id[:9], title))

            username = metadata.group("username")
            ghnum = metadata.group("ghnum")
            remote_ref = "origin/gh/{}/{}/orig".format(username, ghnum)
            remote_id = GitCommitHash(sh.git("rev-parse", remote_ref))
            if commit_id != remote_id:
                raise RuntimeError('''\
Cowardly refusing to land:
local commit {} "{}"
does not match remote commit on pull request {}.
Run 'ghstack' first to ensure that the PR is up-to-date
with your local code before attempting a land.
'''.format(commit_id[:9], title, remote_id[:9]))

        # OK, actually do the land now
        for s in stack:
            try:
                sh.git("cherry-pick", s.commit_id())
            except BaseException:
                sh.git("cherry-pick", "--abort")
                raise
            m = s.match_metadata()
            assert m is not None
            sh.git(
                "commit", "--amend", "--message",
                "{}\n\nPull Request resolved: https://github.com/{}/{}/pull/{}"
                .format(s.commit_msg(), m.group("owner"), m.group("repo"), m.group("number"))
            )

        # All good! Push!
        sh.git("push", "origin", "HEAD:refs/heads/master")

    finally:
        sh.git("checkout", prev_ref)
    """
