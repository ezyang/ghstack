import argparse
import atexit
import contextlib
import io
import os
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple, Union

from expecttest import assert_expected_inline

import ghstack.cherry_pick

import ghstack.github
import ghstack.github_fake
import ghstack.github_utils
import ghstack.land
import ghstack.shell
import ghstack.submit
import ghstack.unlink
from ghstack.types import GitCommitHash

__all__ = [
    "ghstack",
    "init_test",
    "commit",
    "git",
    "gh_submit",
    "gh_land",
    "gh_unlink",
    "gh_cherry_pick",
    "GitCommitHash",
    "checkout",
    "amend",
    "commit",
    "cherry_pick",
    "dump_github",
    "ok",
    "is_direct",
    "write_file_and_add",
    "assert_expected_inline",
    "assert_raises",
    "assert_expected_raises_inline",
    "assert_github_state",
    "assert_eq",
    "get_sh",
    "get_upstream_sh",
    "get_github",
    "tick",
    "captured_output",
]

GH_KEEP_TMP = os.getenv("GH_KEEP_TMP")


@contextlib.contextmanager
def captured_output() -> Iterator[Tuple[io.StringIO, io.StringIO]]:
    new_out, new_err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = new_out, new_err
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def strip_trailing_whitespace(text: str) -> str:
    return re.sub(r" +$", "", text, flags=re.MULTILINE)


def indent(text: str, prefix: str) -> str:
    return "".join(
        prefix + line if line.strip() else line for line in text.splitlines(True)
    )


def handle_remove_read_only(func: Callable[..., Any], path: str, exc_info: Any) -> None:
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


class Context:
    github: ghstack.github.GitHubEndpoint
    upstream_sh: ghstack.shell.Shell
    sh: ghstack.shell.Shell
    direct: bool

    def __init__(self, direct: bool) -> None:
        # Set up a "parent" repository with an empty initial commit that we'll operate on
        upstream_dir = tempfile.mkdtemp()
        self.upstream_sh = ghstack.shell.Shell(cwd=upstream_dir, testing=True)
        self.github = ghstack.github_fake.FakeGitHubEndpoint(self.upstream_sh)

        local_dir = tempfile.mkdtemp()
        self.sh = ghstack.shell.Shell(cwd=local_dir, testing=True)
        self.sh.git("clone", upstream_dir, ".")
        self.direct = direct

    def cleanup(self) -> None:
        if GH_KEEP_TMP:
            print("upstream_dir preserved at: {}".format(self.upstream_sh.cwd))
            print("local_dir preserved at: {}".format(self.sh.cwd))
        else:
            shutil.rmtree(
                self.upstream_sh.cwd,
                onerror=handle_remove_read_only,
            )
            shutil.rmtree(
                self.sh.cwd,
                onerror=handle_remove_read_only,
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


CTX: Context = None  # type: ignore


def init_test() -> Context:
    global CTX
    if CTX is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--direct", action="store_true")
        args = parser.parse_args()
        CTX = Context(args.direct)
        atexit.register(CTX.cleanup)
    return CTX


@contextlib.contextmanager
def scoped_test(direct: bool) -> Iterator[None]:
    global CTX
    assert CTX is None
    try:
        CTX = Context(direct)
        yield
    finally:
        CTX.cleanup()
        CTX = None


# NB: returns earliest first
def gh_submit(
    msg: str = "Update",
    update_fields: bool = False,
    short: bool = False,
    no_skip: bool = False,
    base: Optional[str] = None,
    revs: Sequence[str] = (),
    stack: bool = True,
) -> List[ghstack.submit.DiffMeta]:
    self = CTX
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
        direct_opt=self.direct,
        no_skip=no_skip,
        github_url="github.com",
        remote_name="origin",
        base_opt=base,
        revs=revs,
        stack=stack,
        check_invariants=True,
    )
    self.check_global_github_invariants(self.direct)
    return r


def gh_land(pull_request: str) -> None:
    self = CTX
    return ghstack.land.main(
        remote_name="origin",
        pull_request=pull_request,
        github=self.github,
        sh=self.sh,
        github_url="github.com",
    )


def gh_unlink() -> None:
    self = CTX
    ghstack.unlink.main(
        github=self.github,
        sh=self.sh,
        repo_owner="pytorch",
        repo_name="pytorch",
        github_url="github.com",
        remote_name="origin",
    )


def gh_cherry_pick(pull_request: str, stack: bool = False) -> None:
    self = CTX
    return ghstack.cherry_pick.main(
        pull_request=pull_request,
        github=self.github,
        sh=self.sh,
        remote_name="origin",
        stack=stack,
    )


def write_file_and_add(filename: str, contents: str) -> None:
    self = CTX
    with self.sh.open(filename, "w") as f:
        f.write(contents)
    self.sh.git("add", filename)


def commit(name: str, msg: Optional[str] = None) -> None:
    self = CTX
    write_file_and_add(f"{name}.txt", "A")
    self.sh.git(
        "commit",
        "-m",
        f"Commit {name}\n\nThis is commit {name}" if msg is None else msg,
    )
    self.sh.test_tick()


def amend(name: str) -> None:
    self = CTX
    write_file_and_add(f"{name}.txt", "A")
    self.sh.git("commit", "--amend", "--no-edit", tick=True)


def git(*args: Any, **kwargs: Any) -> Any:
    return CTX.sh.git(*args, **kwargs)


def ok() -> None:
    print("\033[92m" + "TEST PASSED" + "\033[0m")


def checkout(commit: Union[GitCommitHash, ghstack.submit.DiffMeta]) -> None:
    self = CTX
    if isinstance(commit, ghstack.submit.DiffMeta):
        h = commit.orig
    else:
        h = commit
    self.sh.git("checkout", h)


def cherry_pick(commit: Union[GitCommitHash, ghstack.submit.DiffMeta]) -> None:
    self = CTX
    if isinstance(commit, ghstack.submit.DiffMeta):
        h = commit.orig
    else:
        h = commit
    self.sh.git("cherry-pick", h, tick=True)


def dump_github() -> str:
    self = CTX
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
                "--pretty=format:%h %s",
                f'{pr["baseRefName"]}..{pr["headRefName"]}',
            )
            pr["commits"] = indent(strip_trailing_whitespace(pr["commits"]), "    ")
        else:
            pr["commits"] = "      (omitted)"
        pr["status"] = "[X]" if pr["closed"] else "[O]"
        prs.append(
            "{status} #{number} {title} ({headRefName} -> {baseRefName})\n\n"
            "{body}\n\n{commits}\n\n".format(**pr)
        )

    refs = self.upstream_sh.git(
        "log",
        "--graph",
        "--oneline",
        "--branches=gh/*/*/next",
        "--branches=gh/*/*/head",
        "--pretty=format:%h%d%n%w(0,3,3)%s",
    )
    prs.append(
        "Repository state:\n\n" + indent(strip_trailing_whitespace(refs), "    ") + "\n"
    )
    return indent("".join(prs), " " * 8) + " " * 8


def assert_github_state(expect: str, *, skip: int = 0) -> None:
    assert_expected_inline(dump_github(), expect, skip=skip + 1)


def is_direct() -> bool:
    return CTX.direct


def assert_eq(a: Any, b: Any) -> None:
    assert a == b, f"{a} != {b}"


def assert_raises(
    exc_type: any, callable: Callable[..., any], *args: any, **kwargs: any
):
    try:
        callable(*args, **kwargs)
    except exc_type:
        return
    assert False, "did not raise when expected to"


def assert_expected_raises_inline(
    exc_type: any, callable: Callable[..., any], expect: str, *args: any, **kwargs: any
):
    try:
        callable(*args, **kwargs)
    except exc_type as e:
        assert_expected_inline(str(e), expect, skip=1)
        return
    assert False, "did not raise when expected to"


def get_sh() -> ghstack.shell.Shell:
    return CTX.sh


def get_upstream_sh() -> ghstack.shell.Shell:
    return CTX.upstream_sh


def get_github() -> ghstack.github.GitHubEndpoint:
    return CTX.github


def tick() -> None:
    CTX.sh.test_tick()
