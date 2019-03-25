#!/usr/bin/env python3

import subprocess
import os
import logging
from typing import Dict, Sequence, Optional, TypeVar, Union, Any, overload, IO


# Shell commands generally return str, but with exitcode=True
# they return a bool, and if stdout is piped straight to sys.stdout
# they return None.
_SHELL_RET = Union[bool, str, None]


_HANDLE = Union[None, int, IO[Any]]


def log_command(args: Sequence[str]) -> None:
    """
    Given a command, print it in a both machine and human readable way.

    Args:
        *args: the list of command line arguments you want to run
        env: the dictionary of environment variable settings for the command
    """
    cmd = subprocess.list2cmdline(args).replace("\n", "\\n")
    logging.info("$ " + cmd)


K = TypeVar('K')


V = TypeVar('V')


def merge_dicts(x: Dict[K, V], y: Dict[K, V]) -> Dict[K, V]:
    z = x.copy()
    z.update(y)
    return z


class Shell(object):
    """
    An object representing a shell (e.g., the bash prompt in your
    terminal), maintaining a concept of current working directory, and
    also the necessary accoutrements for testing.
    """

    # Current working directory of shell.
    cwd: str

    # Whether or not to suppress printing of command executed.
    quiet: bool

    # Whether or not shell is in testing mode; some commands are made
    # more deterministic in this case.
    testing: bool

    # The current Unix timestamp.  Only used during testing mode.
    testing_time: int

    def __init__(self,
                 quiet: bool = False,
                 cwd: Optional[str] = None,
                 testing: bool = False):
        """
        Args:
            cwd: Current working directory of the shell.  Pass None to
                initialize to the current cwd of the current process.
            quiet: If True, suppress printing out the command executed
                by the shell.  By default, we print out commands for ease
                of debugging.  Quiet is most useful for non-mutating
                shell commands.
            testing: If True, operate in testing mode.  Testing mode
                enables features which make the outputs of commands more
                deterministic; e.g., it sets a number of environment
                variables for Git.
        """
        self.cwd = cwd if cwd else os.getcwd()
        self.quiet = quiet
        self.testing = testing
        self.testing_time = 1112911993

    def sh(self, *args: str,
           env: Optional[Dict[str, str]] = None,
           stderr: _HANDLE = None,
           input: Optional[str] = None,
           stdin: _HANDLE = None,
           stdout: _HANDLE = subprocess.PIPE,
           exitcode: bool = False) -> _SHELL_RET:
        """
        Run a command specified by args, and return string representing
        the stdout of the run command, raising an error if exit code
        was nonzero (unless exitcode kwarg is specified; see below).

        Args:
            *args: the list of command line arguments to run
            env: any extra environment variables to set when running the
                command.  Environment variables set this way are ADDITIVE
                (unlike subprocess default)
            stderr: where to pipe stderr; by default, we pipe it straight
                to this process's stderr
            input: string value to pass stdin.  This is mutually exclusive
                with stdin
            stdin: where to pipe stdin from.  This is mutually exclusive
                with input
            stdout: where to pipe stdout; by default, we capture the stdout
                and return it
            exitcode: if True, return a bool rather than string, specifying
                whether or not the process successfully returned with exit
                code 0.  We never raise an exception when this is True.
        """
        assert not (stdin and input)
        if input:
            stdin = subprocess.PIPE
        if not self.quiet:
            log_command(args)
        if env is not None:
            env = merge_dicts(dict(os.environ), env)
        p = subprocess.Popen(
            args,
            stdout=stdout,
            stdin=stdin,
            stderr=stderr,
            cwd=self.cwd,
            env=env
        )
        input_bytes = None
        if input is not None:
            input_bytes = input.encode('utf-8')
        out, err = p.communicate(input_bytes)
        if err is not None:
            # NB: Not debug; we always want to show this to user.
            logging.info(err)
        if exitcode:
            logging.debug("Exit code: {}".format(p.returncode))
            return p.returncode == 0
        if p.returncode != 0:
            raise RuntimeError(
                "{} failed with exit code {}"
                .format(' '.join(args), p.returncode)
            )
        if out is not None:
            r = out.decode()
            assert isinstance(r, str)
            logging.debug(r.replace('\0', '\\0'))
            return r
        else:
            return None

    def _maybe_rstrip(self, s: _SHELL_RET) -> _SHELL_RET:
        if isinstance(s, str):
            return s.rstrip()
        else:
            return s

    @overload  # noqa: F811
    def git(self, *args: str) -> str:
        ...

    @overload  # noqa: F811
    def git(self, *args: str, input: str) -> str:

        ...

    @overload  # noqa: F811
    def git(self, *args: str, **kwargs: Any) -> _SHELL_RET:

        ...

    def git(self, *args: str, **kwargs: Any  # noqa: F811
            ) -> _SHELL_RET:
        """
        Run a git command.  The returned stdout has trailing newlines stripped.

        Args:
            *args: Arguments to git
            **kwargs: Any valid kwargs for sh()
        """
        env = kwargs.setdefault("env", {})
        # Some envvars to make things a little more script mode nice
        if self.testing:
            env.setdefault("EDITOR", ":")
            env.setdefault("GIT_MERGE_AUTOEDIT", "no")
            env.setdefault("LANG", "C")
            env.setdefault("LC_ALL", "C")
            env.setdefault("PAGER", "cat")
            env.setdefault("TZ", "UTC")
            env.setdefault("TERM", "dumb")
            # These are important so we get deterministic commit times
            env.setdefault("GIT_AUTHOR_EMAIL", "author@example.com")
            env.setdefault("GIT_AUTHOR_NAME", "A U Thor")
            env.setdefault("GIT_COMMITTER_EMAIL", "committer@example.com")
            env.setdefault("GIT_COMMITTER_NAME", "C O Mitter")
            env.setdefault("GIT_COMMITTER_DATE",
                           "{} -0700".format(self.testing_time))
            env.setdefault("GIT_AUTHOR_DATE",
                           "{} -0700".format(self.testing_time))
            if 'stderr' not in kwargs:
                kwargs['stderr'] = subprocess.PIPE

        return self._maybe_rstrip(self.sh(*(("git",) + args), **kwargs))

    @overload  # noqa: F811
    def hg(self, *args: str) -> str:
        ...

    @overload  # noqa: F811
    def hg(self, *args: str, input: str) -> str:
        ...

    @overload  # noqa: F811
    def hg(self, *args: str, **kwargs: Any) -> _SHELL_RET:
        ...

    def hg(self, *args: str, **kwargs: Any  # noqa: F811
           ) -> _SHELL_RET:
        """
        Run a hg command.  The returned stdout has trailing newlines stripped.

        Args:
            *args: Arguments to hg
            **kwargs: Any valid kwargs for sh()
        """

        return self._maybe_rstrip(self.sh(*(("hg",) + args), **kwargs))

    def jf(self, *args: str, **kwargs: Any) -> _SHELL_RET:
        """
        Run a jf command.  The returned stdout has trailing newlines stripped.

        Args:
            *args: Arguments to jf
            **kwargs: Any valid kwargs for sh()
        """

        kwargs.setdefault('stdout', None)

        return self._maybe_rstrip(self.sh(*(("jf",) + args), **kwargs))

    def test_tick(self) -> None:
        """
        Increase the current time.  Useful when testing is True.
        """
        self.testing_time += 60

    def open(self, fn: str, mode: str) -> IO[Any]:
        """
        Open a file, relative to the current working directory.

        Args:
            fn: filename to open
            mode: mode to open the file as
        """
        return open(os.path.join(self.cwd, fn), mode)

    def cd(self, d: str) -> None:
        """
        Change the current working directory.

        Args:
            d: directory to change to
        """
        self.cwd = os.path.join(self.cwd, d)
