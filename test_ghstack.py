from __future__ import print_function

import contextlib
import io
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    NewType,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import expecttest

import ghstack.github
import ghstack.github_fake
import ghstack.github_utils
import ghstack.land
import ghstack.shell
import ghstack.submit
import ghstack.unlink
from ghstack.types import GitCommitHash


DIRECT = False


# TODO: replicate github commit list


@contextlib.contextmanager
def use_direct() -> Iterator[None]:
    global DIRECT
    try:
        DIRECT = True
        yield
    finally:
        DIRECT = False
        pass


@contextlib.contextmanager
def captured_output() -> Iterator[Tuple[io.StringIO, io.StringIO]]:
    new_out, new_err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = new_out, new_err
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_out, old_err


# TODO: Figure out how to make all git stuff in memory, so it runs
# faster.  Need to work on OSX.


GH_KEEP_TMP = os.getenv("GH_KEEP_TMP")


SubstituteRev = NewType("SubstituteRev", str)


def strip_trailing_whitespace(text: str) -> str:
    return re.sub(r" +$", "", text, flags=re.MULTILINE)


def indent(text: str, prefix: str) -> str:
    return "".join(
        prefix + line if line.strip() else line for line in text.splitlines(True)
    )


class TestGh(expecttest.TestCase):
    github: ghstack.github.GitHubEndpoint
    rev_map: Dict[SubstituteRev, GitCommitHash]
    upstream_sh: ghstack.shell.Shell
    sh: ghstack.shell.Shell

    def setUp(self) -> None:
        # Set up a "parent" repository with an empty initial commit that we'll operate on
        upstream_dir = tempfile.mkdtemp()
        if GH_KEEP_TMP:
            self.addCleanup(
                lambda: print("upstream_dir preserved at: {}".format(upstream_dir))
            )
        else:
            self.addCleanup(
                lambda: shutil.rmtree(
                    upstream_dir,
                    onerror=self.handle_remove_read_only,
                )
            )
        self.upstream_sh = ghstack.shell.Shell(cwd=upstream_dir, testing=True)
        self.github = ghstack.github_fake.FakeGitHubEndpoint(self.upstream_sh)

        local_dir = tempfile.mkdtemp()
        if GH_KEEP_TMP:
            self.addCleanup(
                lambda: print("local_dir preserved at: {}".format(local_dir))
            )
        else:
            self.addCleanup(
                lambda: shutil.rmtree(
                    local_dir,
                    onerror=self.handle_remove_read_only,
                )
            )
        self.sh = ghstack.shell.Shell(cwd=local_dir, testing=True)
        self.sh.git("clone", upstream_dir, ".")

        self.rev_map = {}
        self.substituteRev(GitCommitHash("HEAD"), SubstituteRev("rINI0"))

    def handle_remove_read_only(
        self, func: Callable[..., Any], path: str, exc_info: Any
    ) -> None:
        """
        Error handler for ``shutil.rmtree``.

        If the error is due to an access error (read only file),
        it attempts to add write permission and then retries.

        If the error is for another reason, it re-raises the error.

        Usage : ``shutil.rmtree(path, onerror=onerror)``
        """

        if not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWUSR)
            func(path)
        else:
            raise

    def writeFileAndAdd(self, filename: str, contents: str) -> None:
        with self.sh.open(filename, "w") as f:
            f.write(contents)
        self.sh.git("add", filename)

    def lookupRev(self, substitute: str) -> GitCommitHash:
        return self.rev_map[SubstituteRev(substitute)]

    def substituteRev(self, rev: str, substitute: str) -> None:
        # short doesn't really have to be here if we do substituteRev
        h = GitCommitHash(self.sh.git("rev-parse", "--short", rev))
        self.rev_map[SubstituteRev(substitute)] = h
        print("substituteRev: {} = {}".format(substitute, h))
        # self.substituteExpected(h, substitute)

    # NB: returns earliest first
    def gh(
        self,
        msg: str = "Update",
        update_fields: bool = False,
        short: bool = False,
        no_skip: bool = False,
        base: Optional[str] = None,
        revs: Sequence[str] = (),
        stack: bool = True,
    ) -> List[ghstack.submit.DiffMeta]:
        direct = DIRECT
        r = ghstack.submit.main(
            msg=msg,
            username="ezyang",
            github=self.github,
            sh=self.sh,
            update_fields=update_fields,
            stack_header="Stack",
            repo_owner_opt="pytorch",
            repo_name_opt="pytorch",
            short=short,
            direct=direct,
            no_skip=no_skip,
            github_url="github.com",
            remote_name="origin",
            base_opt=base,
            revs=revs,
            stack=stack,
            check_invariants=True,
        )
        self.check_global_github_invariants(direct)
        return r

    def gh_land(self, pull_request: str) -> None:
        return ghstack.land.main(
            remote_name="origin",
            pull_request=pull_request,
            github=self.github,
            sh=self.sh,
            github_url="github.com",
        )

    def gh_unlink(self) -> None:
        ghstack.unlink.main(
            github=self.github,
            sh=self.sh,
            repo_owner="pytorch",
            repo_name="pytorch",
            github_url="github.com",
            remote_name="origin",
        )

    def check_global_github_invariants(self, direct: bool) -> None:
        r = self.github.graphql(
            """
          query {
            repository(name: "pytorch", owner: "pytorch") {
              pullRequests {
                nodes {
                  baseRefName
                  headRefName
                  closed
                }
              }
            }
          }
        """
        )
        # No refs may be reused for multiple open PRs
        seen_refs = set()
        for pr in r["data"]["repository"]["pullRequests"]["nodes"]:
            if pr["closed"]:
                continue
            # In direct mode, only head refs may not be reused;
            # base refs can be reused in octopus situations
            if not direct:
                assert pr["baseRefName"] not in seen_refs
                seen_refs.add(pr["baseRefName"])
            assert pr["headRefName"] not in seen_refs
            seen_refs.add(pr["headRefName"])

    def dump_github(self) -> str:
        r = self.github.graphql(
            """
          query {
            repository(name: "pytorch", owner: "pytorch") {
              pullRequests {
                nodes {
                  number
                  baseRefName
                  headRefName
                  title
                  body
                  closed
                }
              }
            }
          }
        """
        )
        prs = []
        for pr in r["data"]["repository"]["pullRequests"]["nodes"]:
            pr["body"] = indent(pr["body"].replace("\r", ""), "    ")
            # TODO: Use of git --graph here is a bit of a loaded
            # footgun, because git doesn't really give any guarantees
            # about what the graph should look like.  So there isn't
            # really any assurance that this will output the same thing
            # on multiple test runs.  We'll have to reimplement this
            # ourselves to do it right.
            #
            # UPDATE: Another good reason to rewrite this is because git
            # puts the first parent on the left, which leads to ugly
            # graphs.  Swapping the parents would give us nice pretty graphs.
            if not pr["closed"]:
                pr["commits"] = self.upstream_sh.git(
                    "log",
                    "--graph",
                    "--oneline",
                    "--pretty=format:%h%d%n%w(0,3,3)%s",
                    pr["headRefName"],
                )
                pr["commits"] = indent(strip_trailing_whitespace(pr["commits"]), "    ")
            else:
                pr["commits"] = "      (omitted)"
            pr["status"] = "[X]" if pr["closed"] else "[O]"
            prs.append(
                "{status} #{number} {title} ({headRefName} -> {baseRefName})\n\n"
                "{body}\n\n{commits}\n\n".format(**pr)
            )
        return "".join(prs)

    # ------------------------------------------------------------------------- #

    def test_get_repo_name_with_owner(self) -> None:
        self.sh.git("remote", "add", "normal", "git@github.com:ezyang/ghstack.git")
        self.assertEqual(
            ghstack.github_utils.get_github_repo_name_with_owner(
                sh=self.sh, github_url="github.com", remote_name="normal"
            ),
            {"owner": "ezyang", "name": "ghstack"},
        )
        self.sh.git(
            "remote", "add", "with-dot", "git@github.com:ezyang/ghstack.dotted.git"
        )
        self.assertEqual(
            ghstack.github_utils.get_github_repo_name_with_owner(
                sh=self.sh, github_url="github.com", remote_name="with-dot"
            ),
            {"owner": "ezyang", "name": "ghstack.dotted"},
        )
        self.sh.git("remote", "add", "https", "https://github.com/ezyang/ghstack")
        self.assertEqual(
            ghstack.github_utils.get_github_repo_name_with_owner(
                sh=self.sh, github_url="github.com", remote_name="https"
            ),
            {"owner": "ezyang", "name": "ghstack"},
        )
        self.sh.git(
            "remote",
            "add",
            "https-with-dotgit",
            "https://github.com/ezyang/ghstack.git",
        )
        self.assertEqual(
            ghstack.github_utils.get_github_repo_name_with_owner(
                sh=self.sh, github_url="github.com", remote_name="https-with-dotgit"
            ),
            {"owner": "ezyang", "name": "ghstack"},
        )
        self.sh.git(
            "remote",
            "add",
            "https-with-dot",
            "https://github.com/ezyang/ghstack.dotted",
        )
        self.assertEqual(
            ghstack.github_utils.get_github_repo_name_with_owner(
                sh=self.sh, github_url="github.com", remote_name="https-with-dot"
            ),
            {"owner": "ezyang", "name": "ghstack.dotted"},
        )
        self.sh.git(
            "remote",
            "add",
            "https-with-dot-with-dotgit",
            "https://github.com/ezyang/ghstack.dotted.git",
        )
        self.assertEqual(
            ghstack.github_utils.get_github_repo_name_with_owner(
                sh=self.sh,
                github_url="github.com",
                remote_name="https-with-dot-with-dotgit",
            ),
            {"owner": "ezyang", "name": "ghstack.dotted"},
        )

    def test_simple(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")

        # Just to test what happens if we use those branches
        self.sh.git("checkout", "gh/ezyang/1/orig")
        self.commit("B")
        self.gh("Initial 2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    * f4778ef (gh/ezyang/1/head)
    |    Initial 1 on "Commit A"
    * 6b23cb6 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    * f16bff9 (gh/ezyang/2/head)
    |    Initial 2 on "Commit B"
    * c7e3a0c (gh/ezyang/2/base)
         Update base for Initial 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_simple(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")

        # Just to test what happens if we use those branches
        self.sh.git("checkout", "gh/ezyang/1/orig")
        self.commit("B")
        self.gh("Initial 2")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * c3ca023 (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/1/head)



    * 09a6970 (gh/ezyang/2/next, gh/ezyang/2/head)
    |    Initial 2 on "Commit B"
    * c3ca023 (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_when_malform_gh_branch_exist(self) -> None:
        print("####################")
        print("### test_when_malform_gh_branch_exist")
        print("###")
        # Ensure that even if there are gh/{} branch that doesn't conform with
        # ghstack naming convension, it still works
        self.sh.git("checkout", "-b", "gh/ezyang/malform")
        self.sh.git("push", "origin", "gh/ezyang/malform")
        self.sh.git("checkout", "-b", "gh/ezyang/non_int/head")
        self.sh.git("push", "origin", "gh/ezyang/non_int/head")
        self.sh.git("checkout", "master")

        # It is doing same thing as test_simple from this point forward.
        print("### First commit")
        self.writeFileAndAdd("a", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    This is my first commit

    * 9a174dd (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )
        print("###")
        print("### Second commit")
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        self.gh("Initial 2")
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("origin/gh/ezyang/2/head", "rMRG2")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500

    This is my first commit

    * 9a174dd (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

[O] #501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500

    This is my second commit

    * 21f20fe (gh/ezyang/2/head)
    |    Initial 2 on "Commit 2"
    * 9c89bd6 (gh/ezyang/2/base)
         Update base for Initial 2 on "Commit 2"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_empty_commit(self) -> None:
        print("####################")
        print("### test_empty_commit")
        print("###")

        print("### Empty commit")
        self.sh.git(
            "commit", "--allow-empty", "-m", "Commit 1\n\nThis is my first commit"
        )
        self.writeFileAndAdd("bar", "baz")
        self.sh.git("commit", "-m", "Commit 2")

        self.sh.test_tick()
        self.gh("Initial")
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 2 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500



    * 20ca97f (gh/ezyang/1/head)
    |    Initial on "Commit 2"
    * 93739c0 (gh/ezyang/1/base)
         Update base for Initial on "Commit 2"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_strip_mentions(self) -> None:
        self.writeFileAndAdd("bar", "baz")
        self.sh.git(
            "commit",
            "-m",
            "Commit 1\n\nThis is my first commit, hello @foobar @Ivan\n\nSigned-off-by: foo@gmail.com",
        )

        self.sh.test_tick()
        self.gh("Initial")

        self.github.patch(
            "repos/pytorch/pytorch/pulls/500",
            body="""\
Stack:
* **#500 Commit 1**

cc @foobar @Ivan

Signed-off-by: foo@gmail.com""",
            title="This is my first commit",
        )

        self.sh.test_tick()
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "--amend", "--no-edit")
        self.gh("Update 1")

        # Ensure no mentions in the log
        self.assertExpectedInline(
            self.sh.git("log", "--format=%B", "-n1", "origin/gh/ezyang/1/head"),
            """\
Update 1 on "Commit 1"

[ghstack-poisoned]""",
        )
        self.assertExpectedInline(
            self.sh.git("log", "--format=%B", "-n1", "origin/gh/ezyang/1/orig"),
            """\
Commit 1

This is my first commit, hello foobar Ivan

Signed-off-by: foo@gmail.com

ghstack-source-id: 36c3df70a403234bbd5005985399205a8109950b
Pull Request resolved: https://github.com/pytorch/pytorch/pull/500""",
        )

    # ------------------------------------------------------------------------- #

    def test_commit_amended_to_empty(self) -> None:
        print("####################")
        print("### test_empty_commit")
        print("###")

        self.writeFileAndAdd("bar", "baz")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")

        self.sh.test_tick()
        self.gh("Initial")
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    This is my first commit

    * 3d5d65c (gh/ezyang/1/head)
    |    Initial on "Commit 1"
    * 6e62a66 (gh/ezyang/1/base)
         Update base for Initial on "Commit 1"

""",
        )

        self.sh.git("rm", "bar")
        self.sh.git("commit", "--amend", "--allow-empty", "--no-edit")
        self.sh.test_tick()
        self.gh("Update")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    This is my first commit

    * 3d5d65c (gh/ezyang/1/head)
    |    Initial on "Commit 1"
    * 6e62a66 (gh/ezyang/1/base)
         Update base for Initial on "Commit 1"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_amend(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")

        self.amend("A2")
        (A2,) = self.gh("Update A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500



    * e46afee (gh/ezyang/1/head)
    |    Update A on "Commit A"
    * f4778ef
    |    Initial 1 on "Commit A"
    * 6b23cb6 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit A"

""",
        )

    @use_direct()
    def test_direct_amend(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")

        self.amend("A2")
        (A2,) = self.gh("Update A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * e3902de (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Update A on "Commit A"
    * c3ca023
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_amend_message_only(self) -> None:
        print("####################")
        print("### test_amend")
        print("###")
        print("### First commit")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    A commit with an A

    * fd92fed (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )
        print("###")
        print("### Amend the commit")
        # Can't use -m here, it will clobber the metadata
        # TODO: This is really slow smh
        self.sh.git(
            "filter-branch",
            "-f",
            "--msg-filter",
            "cat && echo 'blargle'",
            "HEAD~..HEAD",
        )
        self.substituteRev("HEAD", "rCOM2")
        self.sh.test_tick()
        self.gh("Update A", no_skip=True)
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG2")
        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    A commit with an A

    * fd92fed (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_amend_out_of_date(self) -> None:
        print("####################")
        print("### test_amend_out_of_date")
        print("###")
        print("### First commit")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh("Initial 1")
        old_head = self.sh.git("rev-parse", "HEAD")

        print("###")
        print("### Amend the commit")
        self.writeFileAndAdd("file1.txt", "ABBA")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend", "--no-edit")
        self.sh.test_tick()
        self.gh("Update A")

        # Reset to the old version
        self.sh.git("reset", "--hard", old_head)
        self.writeFileAndAdd("file1.txt", "BAAB")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend", "--no-edit")
        self.sh.test_tick()
        self.assertRaises(RuntimeError, lambda: self.gh("Update B"))

    # ------------------------------------------------------------------------- #

    def test_multi(self) -> None:
        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial 1 and 2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    * 01a577e (gh/ezyang/1/head)
    |    Initial 1 and 2 on "Commit A"
    * 7557970 (gh/ezyang/1/base)
         Update base for Initial 1 and 2 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    * 4bc08ea (gh/ezyang/2/head)
    |    Initial 1 and 2 on "Commit B"
    * 0db1241 (gh/ezyang/2/base)
         Update base for Initial 1 and 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_multi(self) -> None:
        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial 1 and 2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * c5b379e (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 1 and 2 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/1/head)



    * fd9fc99 (gh/ezyang/2/next, gh/ezyang/2/head)
    |    Initial 1 and 2 on "Commit B"
    * c5b379e (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 1 and 2 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_amend_top(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")

        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.amend("B2")
        A3, B3 = self.gh("Update A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    * f4778ef (gh/ezyang/1/head)
    |    Initial 1 on "Commit A"
    * 6b23cb6 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    * d4be138 (gh/ezyang/2/head)
    |    Update A on "Commit B"
    * f16bff9
    |    Initial 2 on "Commit B"
    * c7e3a0c (gh/ezyang/2/base)
         Update base for Initial 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_amend_top(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")

        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.amend("B2")
        A3, B3 = self.gh("Update A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * c3ca023 (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/1/head)



    * 20bbb07 (gh/ezyang/2/next, gh/ezyang/2/head)
    |    Update A on "Commit B"
    * 09a6970
    |    Initial 2 on "Commit B"
    * c3ca023 (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_amend_bottom(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")
        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.checkout(A2)
        self.amend("A3")
        (A3,) = self.gh("Update A")

        self.cherry_pick(B2)
        A4, B4 = self.gh("Update B")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    * 79e3249 (gh/ezyang/1/head)
    |    Update A on "Commit A"
    * f4778ef
    |    Initial 1 on "Commit A"
    * 6b23cb6 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    *   dd9d87d (gh/ezyang/2/head)
    |\\     Update B on "Commit B"
    | * e24c5c2 (gh/ezyang/2/base)
    | |    Update base for Update B on "Commit B"
    * | f16bff9
    |/     Initial 2 on "Commit B"
    * c7e3a0c
         Update base for Initial 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_amend_bottom(self) -> None:
        self.commit("A")
        (A,) = self.gh("Initial 1")
        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.checkout(A2)
        self.amend("A3")
        (A3,) = self.gh("Update A")

        self.cherry_pick(B2)
        A4, B4 = self.gh("Update B")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * f22b24c (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Update A on "Commit A"
    * c3ca023
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/1/head)



    *   165ebd2 (gh/ezyang/2/next, gh/ezyang/2/head)
    |\\     Update B on "Commit B"
    | * f22b24c (gh/ezyang/1/next, gh/ezyang/1/head)
    | |    Update A on "Commit A"
    * | 09a6970
    |/     Initial 2 on "Commit B"
    * c3ca023
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_amend_all(self) -> None:
        self.commit("A")
        _ = self.gh("Initial 1")

        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.checkout(A2)
        self.amend("A3")
        self.cherry_pick(B2)
        self.amend("B3")
        A3, B3 = self.gh("Update A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    * 98de643 (gh/ezyang/1/head)
    |    Update A on "Commit A"
    * f4778ef
    |    Initial 1 on "Commit A"
    * 6b23cb6 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    *   870e05a (gh/ezyang/2/head)
    |\\     Update A on "Commit B"
    | * 293b569 (gh/ezyang/2/base)
    | |    Update base for Update A on "Commit B"
    * | f16bff9
    |/     Initial 2 on "Commit B"
    * c7e3a0c
         Update base for Initial 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_amend_all(self) -> None:
        self.commit("A")
        _ = self.gh("Initial 1")

        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.checkout(A2)
        self.amend("A3")
        self.cherry_pick(B2)
        self.amend("B3")
        A3, B3 = self.gh("Update A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * 9d56b39 (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Update A on "Commit A"
    * c3ca023
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/1/head)



    *   e3873c9 (gh/ezyang/2/next, gh/ezyang/2/head)
    |\\     Update A on "Commit B"
    | * 9d56b39 (gh/ezyang/1/next, gh/ezyang/1/head)
    | |    Update A on "Commit A"
    * | 09a6970
    |/     Initial 2 on "Commit B"
    * c3ca023
    |    Initial 1 on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_rebase(self) -> None:
        self.sh.git("checkout", "-b", "feature")

        self.commit("A")
        (A1,) = self.gh("Initial 1")
        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.sh.git("checkout", "master")
        self.commit("M")
        self.sh.git("push", "origin", "master")

        self.sh.git("checkout", "feature")
        self.sh.git("rebase", "origin/master")

        self.gh("Rebase")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    *   51b1590 (gh/ezyang/1/head)
    |\\     Rebase on "Commit A"
    | * 0c51c0c (gh/ezyang/1/base)
    | |    Update base for Rebase on "Commit A"
    * | f4778ef
    |/     Initial 1 on "Commit A"
    * 6b23cb6
         Update base for Initial 1 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    *   f33fe2b (gh/ezyang/2/head)
    |\\     Rebase on "Commit B"
    | * 96db6fb (gh/ezyang/2/base)
    | |    Update base for Rebase on "Commit B"
    * | f16bff9
    |/     Initial 2 on "Commit B"
    * c7e3a0c
         Update base for Initial 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_rebase(self) -> None:
        self.sh.git("checkout", "-b", "feature")

        self.commit("A")
        (A1,) = self.gh("Initial 1")
        self.commit("B")
        A2, B2 = self.gh("Initial 2")

        self.sh.git("checkout", "master")
        self.commit("M")
        self.sh.git("push", "origin", "master")

        self.sh.git("checkout", "feature")
        self.sh.git("rebase", "origin/master")

        self.gh("Rebase")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    *   ad37802 (gh/ezyang/1/next, gh/ezyang/1/head)
    |\\     Rebase on "Commit A"
    | * 686e5ea (HEAD -> master)
    | |    Commit M
    * | c3ca023
    |/     Initial 1 on "Commit A"
    * dc8bfe4
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/1/head)



    *   1d1ca2d (gh/ezyang/2/next, gh/ezyang/2/head)
    |\\     Rebase on "Commit B"
    | *   ad37802 (gh/ezyang/1/next, gh/ezyang/1/head)
    | |\\     Rebase on "Commit A"
    | | * 686e5ea (HEAD -> master)
    | | |    Commit M
    * | | 09a6970
    |/ /     Initial 2 on "Commit B"
    * / c3ca023
    |/     Initial 1 on "Commit A"
    * dc8bfe4
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_cherry_pick(self) -> None:
        self.sh.git("checkout", "-b", "feature")

        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial 2")

        self.sh.git("checkout", "master")
        self.commit("M")
        self.sh.git("push", "origin", "master")

        self.cherry_pick(B)
        self.gh("Cherry pick")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500



    * 48cad68 (gh/ezyang/1/head)
    |    Initial 2 on "Commit A"
    * adb13d7 (gh/ezyang/1/base)
         Update base for Initial 2 on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501



    *   c1946ee (gh/ezyang/2/head)
    |\\     Cherry pick on "Commit B"
    | * cd14633 (gh/ezyang/2/base)
    | |    Update base for Cherry pick on "Commit B"
    * | f16bff9
    |/     Initial 2 on "Commit B"
    * c7e3a0c
         Update base for Initial 2 on "Commit B"

""",
        )

    @use_direct()
    def test_direct_cherry_pick(self) -> None:
        self.sh.git("checkout", "-b", "feature")

        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial 2")

        self.sh.git("checkout", "master")
        self.commit("M")
        self.sh.git("push", "origin", "master")

        self.cherry_pick(B)
        self.gh("Cherry pick")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> master)



    * 2949b6b (gh/ezyang/1/next, gh/ezyang/1/head)
    |    Initial 2 on "Commit A"
    * dc8bfe4
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> master)



    *   fd891f3 (gh/ezyang/2/next, gh/ezyang/2/head)
    |\\     Cherry pick on "Commit B"
    | * 686e5ea (HEAD -> master)
    | |    Commit M
    * | d8884f2
    | |    Initial 2 on "Commit B"
    * | 2949b6b (gh/ezyang/1/next, gh/ezyang/1/head)
    |/     Initial 2 on "Commit A"
    * dc8bfe4
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_reorder(self) -> None:
        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial")

        self.checkout(GitCommitHash("HEAD~~"))
        self.cherry_pick(B)
        self.cherry_pick(A)
        B2, A2 = self.gh("Reorder")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500
    * #501



    *   5a11d6e (gh/ezyang/1/head)
    |\\     Reorder on "Commit A"
    | * 48df0b3 (gh/ezyang/1/base)
    | |    Update base for Reorder on "Commit A"
    * | 30f6c01
    |/     Initial on "Commit A"
    * 7e61353
         Update base for Initial on "Commit A"

[O] #501 Commit B (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * #500
    * __->__ #501



    *   28e7ae2 (gh/ezyang/2/head)
    |\\     Reorder on "Commit B"
    | * 7be762b (gh/ezyang/2/base)
    | |    Update base for Reorder on "Commit B"
    * | 4d6d2a4
    |/     Initial on "Commit B"
    * c9e5b0d
         Update base for Initial on "Commit B"

""",
        )

    @use_direct()
    def test_direct_reorder(self) -> None:
        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial")

        self.checkout(GitCommitHash("HEAD~~"))
        self.cherry_pick(B)
        self.cherry_pick(A)
        B2, A2 = self.gh("Reorder")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit A (gh/ezyang/1/head -> gh/ezyang/2/head)



    *   3a17667 (gh/ezyang/1/next, gh/ezyang/1/head)
    |\\     Reorder on "Commit A"
    | * 5f812b3 (gh/ezyang/2/next, gh/ezyang/2/head)
    | |    Reorder on "Commit B"
    | * 60b80d9
    |/     Initial on "Commit B"
    * 8bf3ca1
    |    Initial on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

[O] #501 Commit B (gh/ezyang/2/head -> master)



    * 5f812b3 (gh/ezyang/2/next, gh/ezyang/2/head)
    |    Reorder on "Commit B"
    * 60b80d9
    |    Initial on "Commit B"
    * 8bf3ca1
    |    Initial on "Commit A"
    * dc8bfe4 (HEAD -> master)
         Initial commit

""",
        )

    # ------------------------------------------------------------------------- #

    def test_no_clobber(self) -> None:
        # Check that we don't clobber changes to PR description or title

        print("####################")
        print("### test_no_clobber")
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nOriginal message")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        print("###")
        print("### Amend the PR")
        self.github.patch(
            "repos/pytorch/pytorch/pulls/500",
            body="""\
Stack:
* **#500 Commit 1**

Directly updated message body""",
            title="Directly updated title",
        )

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Directly updated title (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * **#500 Commit 1**

    Directly updated message body

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        print("###")
        print("### Submit an update")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "--amend", "--no-edit")
        self.sh.test_tick()
        self.gh("Update 1")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Directly updated title (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Directly updated message body

    * 5c110bc (gh/ezyang/1/head)
    |    Update 1 on "Commit 1"
    * e0c08a4
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_no_clobber_carriage_returns(self) -> None:
        # In some situations, GitHub will replace your newlines with
        # \r\n.  Check we handle this correctly.

        print("####################")
        print("### test_no_clobber_carriage_returns")
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nOriginal message")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        print("###")
        print("### Amend the PR")
        self.github.patch(
            "repos/pytorch/pytorch/pulls/500",
            body="""\
Stack:
* **#500 Commit 1**

Directly updated message body""".replace(
                "\n", "\r\n"
            ),
            title="Directly updated title",
        )

        print("###")
        print("### Submit a new commit")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 2")
        self.sh.test_tick()
        self.gh("Initial 2")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("origin/gh/ezyang/2/head", "rMRG2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Directly updated title (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500

    Directly updated message body

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

[O] #501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500



    * 9357368 (gh/ezyang/2/head)
    |    Initial 2 on "Commit 2"
    * f1dde2f (gh/ezyang/2/base)
         Update base for Initial 2 on "Commit 2"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_reject_head_stack(self) -> None:
        self.writeFileAndAdd("a", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.gh("Initial 1")

        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.sh.git("checkout", "gh/ezyang/1/head")

        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()

        self.assertRaises(RuntimeError, lambda: self.gh("Initial 2"))

    # ------------------------------------------------------------------------- #

    def test_update_fields(self) -> None:
        # Check that we do clobber fields when explicitly asked

        print("####################")
        print("### test_update_fields")
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nOriginal message")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        print("###")
        print("### Amend the PR")
        self.github.patch(
            "repos/pytorch/pytorch/pulls/500",
            body="Directly updated message body",
            title="Directly updated title",
        )

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Directly updated title (gh/ezyang/1/head -> gh/ezyang/1/base)

    Directly updated message body

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        print("###")
        print("### Force update fields")
        self.gh("Update 1", update_fields=True)
        self.sh.test_tick()

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_update_fields_preserves_commit_message(self) -> None:
        # Check that we do clobber fields when explicitly asked

        print("####################")
        print("### test_update_fields")
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nOriginal message")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        print("###")
        print("### Amend the commit")
        self.sh.git(
            "filter-branch", "--msg-filter", "echo Amended && cat", "HEAD~..HEAD"
        )
        self.gh("Update 1", update_fields=True)
        self.sh.test_tick()

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Amended (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Commit 1

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        self.assertRegex(
            self.sh.git("log", "--format=%B", "-n", "1", "HEAD"), "Amended"
        )

    # ------------------------------------------------------------------------- #

    def test_update_fields_preserve_differential_revision(self) -> None:
        # Check that Differential Revision is preserved

        logging.info("### test_update_fields_preserve_differential_revision")
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nOriginal message")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.sh.test_tick()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        logging.info("### Amend the PR")
        body = """\n
Directly updated message body

Differential Revision: [D14778507](https://our.internmc.facebook.com/intern/diff/D14778507)
"""
        self.github.patch(
            "repos/pytorch/pytorch/pulls/500", body=body, title="Directly updated title"
        )

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Directly updated title (gh/ezyang/1/head -> gh/ezyang/1/base)



    Directly updated message body

    Differential Revision: [D14778507](https://our.internmc.facebook.com/intern/diff/D14778507)


    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        logging.info("### Force update fields")
        self.gh("Update 1", update_fields=True)
        self.sh.test_tick()

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    Original message

    Differential Revision: [D14778507](https://our.internmc.facebook.com/intern/diff/D14778507)

    * e0c08a4 (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_remove_bottom_commit(self) -> None:
        # This is to test a bug where we decided not to update base,
        # but this was wrong

        self.sh.git("checkout", "-b", "feature")

        print("###")
        print("### First commit")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        print("###")
        print("### Second commit")
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()
        self.gh("Initial 2")
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("origin/gh/ezyang/2/head", "rMRG2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500

    A commit with an A

    * fd92fed (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

[O] #501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500

    A commit with a B

    * b93d7fa (gh/ezyang/2/head)
    |    Initial 2 on "Commit 2"
    * 59cae92 (gh/ezyang/2/base)
         Update base for Initial 2 on "Commit 2"

""",
        )

        print("###")
        print("### Delete first commit")
        self.sh.git("checkout", "master")

        print("###")
        print("### Cherry-pick the second commit")
        self.sh.git("cherry-pick", "feature")

        self.substituteRev("HEAD", "rCOM2A")

        self.gh("Cherry pick")
        self.substituteRev("origin/gh/ezyang/2/base", "rINI2A")
        self.substituteRev("origin/gh/ezyang/2/head", "rMRG2A")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500

    A commit with an A

    * fd92fed (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

[O] #501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501

    A commit with a B

    *   10d2ea6 (gh/ezyang/2/head)
    |\\     Cherry pick on "Commit 2"
    | * 86b83f3 (gh/ezyang/2/base)
    | |    Update base for Cherry pick on "Commit 2"
    * | b93d7fa
    |/     Initial 2 on "Commit 2"
    * 59cae92
         Update base for Initial 2 on "Commit 2"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_short(self) -> None:
        self.writeFileAndAdd("b", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        with captured_output() as (out, err):
            self.gh("Initial", short=True)
        self.assertEqual(
            out.getvalue(), "https://github.com/pytorch/pytorch/pull/500\n"
        )

    # ------------------------------------------------------------------------- #

    def test_land_ff(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        (diff,) = self.gh("Initial")
        assert diff is not None
        pr_url = diff.pr_url
        # Because this is fast forward, commit will be landed exactly as is
        self.substituteRev("HEAD", "rCOM1")

        self.gh_land(pr_url)
        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """\
c7c1805 Commit 1
dc8bfe4 Initial commit""",
        )

    # ------------------------------------------------------------------------- #
    #
    def test_land_ff_stack(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        (
            diff1,
            diff2,
        ) = self.gh("Initial")
        assert diff1 is not None
        assert diff2 is not None
        pr_url = diff2.pr_url
        # Because this is fast forward, commit will be landed exactly as is
        self.substituteRev("HEAD~", "rCOM1")
        self.substituteRev("HEAD", "rCOM2")

        self.gh_land(pr_url)
        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """\
3600902 Commit 2
a32aa2b Commit 1
dc8bfe4 Initial commit""",
        )

    # ------------------------------------------------------------------------- #
    #
    def test_land_ff_stack_two_phase(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        (
            diff1,
            diff2,
        ) = self.gh("Initial")
        assert diff1 is not None
        assert diff2 is not None
        pr_url1 = diff1.pr_url
        pr_url2 = diff2.pr_url

        self.substituteRev("HEAD~", "rCOM1")
        self.substituteRev("HEAD", "rCOM2")

        self.gh_land(pr_url1)
        self.gh_land(pr_url2)
        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """\
3600902 Commit 2
a32aa2b Commit 1
dc8bfe4 Initial commit""",
        )

    # ------------------------------------------------------------------------- #
    #
    def test_land_non_ff_stack_two_phase(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        (
            diff1,
            diff2,
        ) = self.gh("Initial")
        assert diff1 is not None
        assert diff2 is not None
        pr_url1 = diff1.pr_url
        pr_url2 = diff2.pr_url

        self.sh.git("checkout", "origin/master")
        self.writeFileAndAdd("file3.txt", "C")
        self.sh.git("commit", "-m", "Commit 3\n\nThis makes it not ff")
        self.sh.git("push", "origin", "HEAD:master")
        self.substituteRev("HEAD", "rCOM3")

        self.gh_land(pr_url1)
        self.substituteRev("origin/master", "rCOM1")
        self.gh_land(pr_url2)
        self.substituteRev("origin/master", "rCOM2")
        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """\
16c0410 Commit 2
cf230e2 Commit 1
367e236 Commit 3
dc8bfe4 Initial commit""",
        )

    # ------------------------------------------------------------------------- #
    #
    def test_land_with_early_mod(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        (
            diff1,
            diff2,
        ) = self.gh("Initial")
        assert diff1 is not None
        assert diff2 is not None
        pr_url = diff2.pr_url

        # edit earlier commit
        self.sh.git("checkout", "HEAD~")
        self.writeFileAndAdd("file1.txt", "ABBA")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend", "--no-edit")
        self.substituteRev("HEAD", "rCOM1A")
        self.gh("Update")

        self.gh_land(pr_url)
        self.assertExpectedInline(
            self.upstream_sh.git("show", "master:file1.txt"), """ABBA"""
        )
        self.assertExpectedInline(
            self.upstream_sh.git("show", "master:file2.txt"), """B"""
        )

    # ------------------------------------------------------------------------- #

    def test_land_non_ff(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        (diff,) = self.gh("Initial")
        assert diff is not None
        pr_url = diff.pr_url
        self.substituteRev("HEAD", "rCOM1")

        self.sh.git("reset", "--hard", "origin/master")
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Upstream commit")
        self.substituteRev("HEAD", "rUP1")
        self.sh.git("push")

        self.sh.git("checkout", "gh/ezyang/1/orig")
        self.gh_land(pr_url)

        self.substituteRev("origin/master", "rUP2")

        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """\
31fb74c Commit 1
b29d563 Upstream commit
dc8bfe4 Initial commit""",
        )

    # ------------------------------------------------------------------------- #

    def test_unlink(self) -> None:
        print("###")
        print("### First commit")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an B")
        self.sh.test_tick()
        self.gh("Initial 1")
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        # Unlink
        self.gh_unlink()

        self.gh("Initial 2")
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("origin/gh/ezyang/2/head", "rMRG2")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500

    A commit with an A

    * c8c7a8c (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bad4a1e (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

[O] #501 Commit 1 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501
    * #500

    A commit with an B

    * e5adf76 (gh/ezyang/2/head)
    |    Initial 1 on "Commit 1"
    * 1e34833 (gh/ezyang/2/base)
         Update base for Initial 1 on "Commit 1"

[O] #502 Commit 1 (gh/ezyang/3/head -> gh/ezyang/3/base)

    Stack:
    * #503
    * __->__ #502

    A commit with an A

    * 977ffd4 (gh/ezyang/3/head)
    |    Initial 2 on "Commit 1"
    * b4653bb (gh/ezyang/3/base)
         Update base for Initial 2 on "Commit 1"

[O] #503 Commit 1 (gh/ezyang/4/head -> gh/ezyang/4/base)

    Stack:
    * __->__ #503
    * #502

    A commit with an B

    * fd2f563 (gh/ezyang/4/head)
    |    Initial 2 on "Commit 1"
    * 2a80464 (gh/ezyang/4/base)
         Update base for Initial 2 on "Commit 1"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_default_branch_change(self) -> None:
        # make commit
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        # ghstack
        (diff1,) = self.gh("Initial 1")
        assert diff1 is not None
        self.substituteRev("origin/gh/ezyang/1/head", "rMRG1")

        # make main branch
        self.sh.git("branch", "main", "master")
        self.sh.git("push", "origin", "main")
        # change default branch to main
        self.github.patch(
            "repos/pytorch/pytorch",
            name="pytorch",
            default_branch="main",
        )

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    This is my first commit

    * fd92fed (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

""",
        )

        # land
        self.gh_land(diff1.pr_url)
        self.substituteRev("origin/main", "rUP1")

        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """dc8bfe4 Initial commit""",
        )
        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "main"),
            """\
c7c1805 Commit 1
dc8bfe4 Initial commit""",
        )

        # make another commit
        self.writeFileAndAdd("file2.txt", "B")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        # ghstack
        (diff2,) = self.gh("Initial 2")
        assert diff2 is not None
        self.substituteRev("origin/gh/ezyang/2/head", "rMRG2")

        # change default branch back to master
        self.github.patch(
            "repos/pytorch/pytorch",
            name="pytorch",
            default_branch="master",
        )

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    This is my first commit

    * fd92fed (gh/ezyang/1/head)
    |    Initial 1 on "Commit 1"
    * bf7ce67 (gh/ezyang/1/base)
         Update base for Initial 1 on "Commit 1"

[O] #501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501

    This is my second commit

    * b93d7fa (gh/ezyang/2/head)
    |    Initial 2 on "Commit 2"
    * 59cae92 (gh/ezyang/2/base)
         Update base for Initial 2 on "Commit 2"

""",
        )

        # land again
        self.gh_land(diff2.pr_url)
        self.substituteRev("origin/master", "rUP3")
        self.substituteRev("origin/master~", "rUP2")

        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "master"),
            """\
9523c9d Commit 2
75108b9 Commit 1
dc8bfe4 Initial commit""",
        )
        self.assertExpectedInline(
            self.upstream_sh.git("log", "--oneline", "main"),
            """\
c7c1805 Commit 1
dc8bfe4 Initial commit""",
        )

    # ------------------------------------------------------------------------- #

    def test_update_after_land(self) -> None:
        # make stack of two
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.writeFileAndAdd("file2.txt", "A")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        (diff1, diff2) = self.gh("Initial 1")
        assert diff1 is not None
        assert diff2 is not None
        self.substituteRev("origin/gh/ezyang/1/head", "rSELF1")
        self.substituteRev("origin/gh/ezyang/2/head", "rSELF2A")

        # setup an upstream commit, so the land isn't just trivial
        self.sh.git("reset", "--hard", "origin/master")
        self.writeFileAndAdd("file0.txt", "B")
        self.sh.git("commit", "-m", "Upstream commit")
        self.substituteRev("HEAD", "rUP1")
        self.sh.git("push")

        # land first pr
        self.gh_land(diff1.pr_url)
        self.substituteRev("origin/master", "rSELF1")

        # go back to stack
        self.sh.git("checkout", "gh/ezyang/2/orig")

        # update second pr
        self.writeFileAndAdd("file3.txt", "A")
        self.sh.git("commit", "--amend")
        self.sh.test_tick()

        # try to push
        self.assertRaisesRegex(RuntimeError, "git rebase", lambda: self.gh("Run 2"))

        # show the remediation works
        self.sh.git("rebase", "origin/master")
        self.gh("Run 3")
        self.substituteRev("origin/gh/ezyang/2/base", "rBASE2B")
        self.substituteRev("origin/gh/ezyang/2/head", "rSELF2B")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[X] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * #501
    * __->__ #500

    This is my first commit

      (omitted)

[O] #501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Stack:
    * __->__ #501

    This is my second commit

    *   40b34ea (gh/ezyang/2/head)
    |\\     Run 3 on "Commit 2"
    | * bd30532 (gh/ezyang/2/base)
    | |    Update base for Run 3 on "Commit 2"
    * | ffcf6e3
    |/     Initial 1 on "Commit 2"
    * e9c9e53
         Update base for Initial 1 on "Commit 2"

""",
        )

    # ------------------------------------------------------------------------- #

    def test_reuse_branch_refuse_land(self) -> None:
        # make a stack
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        (diff1,) = self.gh("Initial 1")
        assert diff1 is not None

        # land first pr
        self.gh_land(diff1.pr_url)

        # make another stack
        self.writeFileAndAdd("file2.txt", "A")
        self.sh.git("commit", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        (diff2,) = self.gh("Second 2")
        assert diff2 is not None

        # check the head number was reused
        self.assertEqual(diff1.ghnum, diff2.ghnum)

        # refuse to reland first pr
        self.assertRaisesRegex(
            RuntimeError, r"already closed", lambda: self.gh_land(diff1.pr_url)
        )

    # ------------------------------------------------------------------------- #

    def test_minimal_fetch(self) -> None:
        # Narrow down the fetch on origin
        self.sh.git(
            "config",
            "remote.origin.fetch",
            "+refs/heads/master:refs/remotes/origin/master",
        )

        self.writeFileAndAdd("a", "asdf")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.gh("Initial 1")

        self.writeFileAndAdd("a", "asdfb")
        self.sh.git("commit", "--amend")
        self.sh.test_tick()
        self.gh("Update 2")

    # ------------------------------------------------------------------------- #

    def test_preserve_authorship(self) -> None:
        # make a commit with non-standard author
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git(
            "commit",
            "-m",
            "Commit 1\n\nThis is my first commit",
            env={
                "GIT_AUTHOR_NAME": "Ben Bitdiddle",
                "GIT_AUTHOR_EMAIL": "benbitdiddle@example.com",
            },
        )
        self.sh.test_tick()
        # ghstack
        (diff1,) = self.gh("Initial 1")
        assert diff1 is not None
        self.assertExpectedInline(
            self.sh.git(
                "log",
                "--format=Author: %an <%ae>\nCommitter: %cn <%ce>",
                "-n1",
                "origin/gh/ezyang/1/orig",
            ),
            """\
Author: Ben Bitdiddle <benbitdiddle@example.com>
Committer: C O Mitter <committer@example.com>""",
        )

    # ------------------------------------------------------------------------- #

    def test_throttle(self) -> None:
        for i in range(10):
            self.writeFileAndAdd(f"file{i}.txt", "A")
            self.sh.git("commit", "-m", f"Commit {i}")

        self.assertRaisesRegex(RuntimeError, "throttle", lambda: self.gh("Initial"))

    # ------------------------------------------------------------------------- #

    def test_land_and_invalid_resubmit(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        (diff,) = self.gh("Initial")
        assert diff is not None
        pr_url = diff.pr_url

        self.gh_land(pr_url)

        self.writeFileAndAdd("file2.txt", "A")
        self.sh.git("commit", "--amend")
        self.assertRaisesRegex(RuntimeError, "closed", lambda: self.gh("Update"))

        # Do the remediation
        self.gh_unlink()
        self.sh.git("rebase", "origin/master")
        self.gh("New PR")
        self.substituteRev("origin/gh/ezyang/1/base", "rBASE")
        self.substituteRev("origin/gh/ezyang/1/head", "rHEAD")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[X] #500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    This is my first commit

      (omitted)

[O] #501 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #501

    This is my first commit

    * 7355574 (gh/ezyang/1/head)
    |    New PR on "Commit 1"
    * 0b27755 (gh/ezyang/1/base)
         Update base for New PR on "Commit 1"

""",
        )

        # only the amend shows up now
        self.assertExpectedInline(
            self.sh.git("show", "--pretty=", "--name-only", "origin/gh/ezyang/1/orig"),
            """file2.txt""",
        )

    # ------------------------------------------------------------------------- #

    def test_non_standard_base(self) -> None:
        # make release branch
        self.sh.git("branch", "release", "master")

        # diverge release and regular branch
        self.sh.git("checkout", "master")
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Master commit")
        self.sh.test_tick()
        self.sh.git("push", "origin", "master")

        self.sh.git("checkout", "release")
        self.writeFileAndAdd("file2.txt", "A")
        self.sh.git("commit", "-m", "Release commit")
        self.sh.test_tick()
        self.sh.git("push", "origin", "release")

        # make commit on release branch
        self.writeFileAndAdd("file3.txt", "A")
        self.sh.git("commit", "-m", "PR on release")
        self.sh.test_tick()

        # use non-standard base
        self.gh("Initial 1", base="release")
        self.substituteRev("origin/gh/ezyang/1/base", "rBASE")
        self.substituteRev("origin/gh/ezyang/1/head", "rHEAD")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 PR on release (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500



    * 9be6059 (gh/ezyang/1/head)
    |    Initial 1 on "PR on release"
    * 3055698 (gh/ezyang/1/base)
         Update base for Initial 1 on "PR on release"

""",
        )

    def test_bullet_divider(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git(
            "commit",
            "-m",
            """This is my commit

* It starts with a fabulous
* Bullet list""",
        )
        self.sh.test_tick()
        self.gh("Initial")
        self.substituteRev("origin/gh/ezyang/1/head", "rHEAD")

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 This is my commit (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500

    ----

    * It starts with a fabulous
    * Bullet list

    * 840eb38 (gh/ezyang/1/head)
    |    Initial on "This is my commit"
    * b059a32 (gh/ezyang/1/base)
         Update base for Initial on "This is my commit"

""",
        )

    def test_fail_same_source_id(self) -> None:
        self.writeFileAndAdd("file1.txt", "A")
        self.sh.git("commit", "-m", "Commit 1")
        self.sh.test_tick()
        self.gh("Initial")

        # botch it up
        self.writeFileAndAdd("file2.txt", "A")
        self.sh.git("commit", "-C", "HEAD")
        self.sh.test_tick()
        self.assertRaisesRegex(
            RuntimeError, "occurs twice", lambda: self.gh("Should fail")
        )

    def commit(self, name: str) -> None:
        self.writeFileAndAdd(f"{name}.txt", "A")
        self.sh.git("commit", "-m", f"Commit {name}")
        self.sh.test_tick()

    def amend(self, name: str) -> None:
        self.writeFileAndAdd(f"{name}.txt", "A")
        self.sh.git("commit", "--amend", "--no-edit", tick=True)

    def checkout(self, commit: Union[GitCommitHash, ghstack.submit.DiffMeta]) -> None:
        if isinstance(commit, ghstack.submit.DiffMeta):
            h = commit.orig
        else:
            h = commit
        self.sh.git("checkout", h)

    def cherry_pick(
        self, commit: Union[GitCommitHash, ghstack.submit.DiffMeta]
    ) -> None:
        if isinstance(commit, ghstack.submit.DiffMeta):
            h = commit.orig
        else:
            h = commit
        self.sh.git("cherry-pick", h, tick=True)

    def test_submit_prefix_only_no_stack(self) -> None:
        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial")

        self.checkout(A)
        self.amend("A2")
        self.cherry_pick(B)
        (A2,) = self.gh("Update base only", revs=["HEAD~"], stack=False)

        self.assertEqual(A.number, A2.number)

    def test_submit_suffix_only_no_stack(self) -> None:
        self.commit("A")
        self.commit("B")
        A, B = self.gh("Initial")

        self.checkout(A)
        self.amend("A2")
        self.cherry_pick(B)
        (B2,) = self.gh("Update head only", revs=["HEAD"], stack=False)

        self.assertEqual(B.number, B2.number)

    def test_submit_prefix_only_stack(self) -> None:
        self.commit("A")
        self.commit("B")
        self.commit("C")
        A, B, C = self.gh("Initial")

        self.checkout(A)
        self.amend("A2")
        self.cherry_pick(B)
        self.cherry_pick(C)
        A2, B2 = self.gh("Don't update C", revs=["HEAD~"], stack=True)

        self.assertEqual(A.number, A2.number)
        self.assertEqual(B.number, B2.number)

    def test_submit_range_only_stack(self) -> None:
        self.commit("A")
        self.commit("B")
        self.commit("C")
        self.commit("D")
        A, B, C, D = self.gh("Initial")

        self.checkout(A)
        self.amend("A2")
        self.cherry_pick(B)
        self.cherry_pick(C)
        self.cherry_pick(D)
        B2, C2 = self.gh("Update B and C only", revs=["HEAD~~~..HEAD~"], stack=True)

        self.assertEqual(B.number, B2.number)
        self.assertEqual(C.number, C2.number)

    def test_do_not_revert_local_commit_msg_on_skip(self) -> None:
        self.commit("TO_REPLACE")
        (A,) = self.gh("Initial")
        self.sh.git(
            "commit", "--amend", "-m", A.commit_msg.replace("TO_REPLACE", "ARGLE")
        )
        (A2,) = self.gh("Skip")
        self.assertExpectedInline(
            self.sh.git("show", "-s", "--pretty=%B", "HEAD"),
            """\
Commit ARGLE

ghstack-source-id: ac00f28640afe01e4299441bb5041cdf06d0b6b4
Pull Request resolved: https://github.com/pytorch/pytorch/pull/500""",
        )

        self.assertExpectedInline(
            self.dump_github(),
            """\
[O] #500 Commit TO_REPLACE (gh/ezyang/1/head -> gh/ezyang/1/base)

    Stack:
    * __->__ #500



    * 37fd652 (gh/ezyang/1/head)
    |    Initial on "Commit TO_REPLACE"
    * b6bd9bb (gh/ezyang/1/base)
         Update base for Initial on "Commit TO_REPLACE"

""",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    unittest.main()
