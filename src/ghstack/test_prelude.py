import argparse
import atexit
import contextlib
import inspect
import io
import os
import re
import shutil
import stat
import sys
import tempfile
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

from expecttest import assert_expected_inline

import ghstack.checkout
import ghstack.cherry_pick

import ghstack.github
import ghstack.github_fake
import ghstack.github_utils
import ghstack.land
import ghstack.log
import ghstack.pull
import ghstack.shell
import ghstack.submit
import ghstack.sync
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
    "gh_checkout",
    "gh_log",
    "gh_pull",
    "gh_sync",
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
    "get_pr_reviewers",
    "get_pr_labels",
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
        self.direct = direct

    async def initialize(self) -> None:
        assert isinstance(self.github, ghstack.github_fake.FakeGitHubEndpoint)
        await self.github.state.initialize()
        await self.sh.agit("clone", self.upstream_sh.cwd, ".")
        await self.sh.agit("fetch", "origin", "+refs/heads/*:refs/remotes/origin/*")

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

    async def check_global_github_invariants(self, direct: bool) -> None:
        r = await self.github.graphql(
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


async def init_test() -> Context:
    global CTX
    if CTX is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--direct", action="store_true")
        args = parser.parse_args()
        CTX = Context(args.direct)
        await CTX.initialize()
        atexit.register(CTX.cleanup)
    return CTX


@contextlib.asynccontextmanager
async def scoped_test(direct: bool) -> AsyncIterator[None]:
    global CTX
    assert CTX is None
    try:
        CTX = Context(direct)
        await CTX.initialize()
        yield
    finally:
        CTX.cleanup()
        CTX = None


# NB: returns earliest first
async def gh_submit(
    msg: Optional[str] = "Update",
    update_fields: bool = False,
    short: bool = False,
    no_skip: bool = False,
    base: Optional[str] = None,
    revs: Sequence[str] = (),
    stack: bool = True,
    reviewer: Optional[str] = None,
    label: Optional[str] = None,
    automsg: Optional[str] = None,
) -> List[ghstack.submit.DiffMeta]:
    self = CTX
    r = await ghstack.submit.main(
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
        reviewer=reviewer,
        label=label,
        automsg=automsg,
    )
    await self.check_global_github_invariants(self.direct)
    return r


async def gh_land(pull_request: str) -> None:
    self = CTX
    return await ghstack.land.main(
        remote_name="origin",
        pull_request=pull_request,
        github=self.github,
        sh=self.sh,
        github_url="github.com",
    )


async def gh_unlink() -> None:
    self = CTX
    await ghstack.unlink.main(
        github=self.github,
        sh=self.sh,
        repo_owner="pytorch",
        repo_name="pytorch",
        github_url="github.com",
        remote_name="origin",
    )


async def gh_cherry_pick(pull_request: str, stack: bool = False) -> None:
    self = CTX
    return await ghstack.cherry_pick.main(
        pull_request=pull_request,
        github=self.github,
        sh=self.sh,
        remote_name="origin",
        stack=stack,
    )


async def gh_checkout(pull_request: str, same_base: bool = False) -> None:
    self = CTX
    return await ghstack.checkout.main(
        pull_request=pull_request,
        github=self.github,
        sh=self.sh,
        remote_name="origin",
        same_base=same_base,
    )


async def gh_log(pull_request: Optional[str] = None, args: Sequence[str] = ()) -> None:
    self = CTX
    return await ghstack.log.main(
        github=self.github,
        sh=self.sh,
        remote_name="origin",
        github_url="github.com",
        args=list(args),
        pull_request=pull_request,
    )


async def gh_pull(pull_request: Optional[str] = None, continue_: bool = False) -> None:
    self = CTX
    return await ghstack.pull.main(
        github=self.github,
        sh=self.sh,
        remote_name="origin",
        github_url="github.com",
        pull_request=pull_request,
        continue_=continue_,
    )


async def gh_sync() -> GitCommitHash:
    self = CTX
    return await ghstack.sync.main(
        github=self.github,
        sh=self.sh,
        repo_owner="pytorch",
        repo_name="pytorch",
        github_url="github.com",
        remote_name="origin",
    )


async def write_file_and_add(filename: str, contents: str) -> None:
    self = CTX
    with self.sh.open(filename, "w") as f:
        f.write(contents)
    await self.sh.agit("add", filename)


async def commit(name: str, msg: Optional[str] = None) -> None:
    self = CTX
    await write_file_and_add(f"{name}.txt", "A")
    await self.sh.agit(
        "commit",
        "-m",
        f"Commit {name}\n\nThis is commit {name}" if msg is None else msg,
    )
    self.sh.test_tick()


async def amend(name: str) -> None:
    self = CTX
    await write_file_and_add(f"{name}.txt", "A")
    await self.sh.agit("commit", "--amend", "--no-edit", tick=True)


async def git(*args: Any, **kwargs: Any) -> Any:
    return await CTX.sh.agit(*args, **kwargs)


def ok() -> None:
    print("\033[92m" + "TEST PASSED" + "\033[0m")


async def checkout(commit: Union[GitCommitHash, ghstack.submit.DiffMeta]) -> None:
    self = CTX
    if isinstance(commit, ghstack.submit.DiffMeta):
        h = commit.orig
    else:
        h = commit
    await self.sh.agit("checkout", h)


async def cherry_pick(commit: Union[GitCommitHash, ghstack.submit.DiffMeta]) -> None:
    self = CTX
    if isinstance(commit, ghstack.submit.DiffMeta):
        h = commit.orig
    else:
        h = commit
    await self.sh.agit("cherry-pick", h, tick=True)


async def dump_github() -> str:
    self = CTX
    r = await self.github.graphql(
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
            pr["commits"] = await self.upstream_sh.agit(
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

    refs = await self.upstream_sh.agit(
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


async def assert_github_state(expect: str, *, skip: int = 0) -> None:
    assert_expected_inline(await dump_github(), expect, skip=skip + 1)


def is_direct() -> bool:
    return CTX.direct


def get_github() -> "ghstack.github_fake.FakeGitHubEndpoint":
    github = CTX.github
    assert isinstance(github, ghstack.github_fake.FakeGitHubEndpoint)
    return github


def get_pr_reviewers(pr_number: int) -> List[str]:
    """Get the reviewers for a PR number."""
    github = get_github()
    repo = github.state.repository("pytorch", "pytorch")
    pr = github.state.pull_request(repo, ghstack.github_fake.GitHubNumber(pr_number))
    return pr.reviewers


def get_pr_labels(pr_number: int) -> List[str]:
    """Get the labels for a PR number."""
    github = get_github()
    repo = github.state.repository("pytorch", "pytorch")
    pr = github.state.pull_request(repo, ghstack.github_fake.GitHubNumber(pr_number))
    return pr.labels


def assert_eq(a: Any, b: Any) -> None:
    assert a == b, f"{a} != {b}"


async def assert_raises(
    exc_type: Type[BaseException],
    callable: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    try:
        result = callable(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
    except exc_type:
        return
    assert False, "did not raise when expected to"


async def assert_expected_raises_inline(
    exc_type: Type[BaseException],
    callable: Callable[..., Any],
    expect: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    try:
        result = callable(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
    except exc_type as e:
        assert_expected_inline(str(e), expect, skip=1)
        return
    assert False, "did not raise when expected to"


def get_sh() -> ghstack.shell.Shell:
    return CTX.sh


def get_upstream_sh() -> ghstack.shell.Shell:
    return CTX.upstream_sh


def tick() -> None:
    CTX.sh.test_tick()
