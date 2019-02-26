from __future__ import print_function

import subprocess
import os
import sys


def format_env(env):
    """
    Formats the explicitly specified environment in a human readable way.

    This isn't really actually used.

    Args:
        env: The environment dictionary you wish to format
    """
    r = []
    for k, v in env.items():
        r.append("{}={}".format(k, subprocess.list2cmdline([v])))
    return ' '.join(r)


def log_command(args, env=None):
    """
    Given a command, print it in a both machine and human readable way.

    Args:
        *args: the list of command line arguments you want to run
        env: the dictionary of environment variable settings for the command
    """
    cmd = subprocess.list2cmdline(args).replace("\n", "\\n")
    # if env is not None:
    #     cmd = "{} {}".format(format_env(env), cmd)
    print("$ " + cmd)


def merge_dicts(x, y):
    z = x.copy()
    z.update(y)
    return z


class Shell(object):
    """
    An object representing a shell (e.g., the bash prompt in your
    terminal), maintaining a concept of current working directory, and
    also the necessary accoutrements for testing.
    """

    def __init__(self, quiet=False, cwd=None, testing=False):
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

    def sh(self, *args, **kwargs):
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
        stdin = None
        if 'stdin' in kwargs:
            stdin = kwargs['stdin']
            assert 'input' not in kwargs
        elif 'input' in kwargs:
            stdin = subprocess.PIPE
        stdout = subprocess.PIPE
        if 'stdout' in kwargs:
            stdout = kwargs['stdout']
        env = kwargs.get("env")
        if not self.quiet:
            log_command(args, env=env)
        if env is not None:
            env = merge_dicts(os.environ, env)
        p = subprocess.Popen(
            args,
            stdout=stdout,
            stdin=stdin,
            stderr=kwargs.get("stderr"),
            cwd=self.cwd,
            env=env
        )
        input = kwargs.get('input')
        if input is not None:
            input = input.encode('utf-8')
        out, err = p.communicate(input)
        if err is not None:
            print(err, file=sys.stderr, end='')
        if kwargs.get('exitcode'):
            return p.returncode == 0
        if p.returncode != 0:
            raise RuntimeError(
                "{} failed with exit code {}"
                .format(' '.join(args), p.returncode)
            )
        if out is not None:
            return out.decode()
        else:
            return None

    def git(self, *args, **kwargs):
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

        r = self.sh(*(("git",) + args), **kwargs)
        if kwargs.get('exitcode') or not r:
            return r
        else:
            return r.rstrip("\n")

    def hg(self, *args, **kwargs):
        """
        Run a hg command.  The returned stdout has trailing newlines stripped.

        Args:
            *args: Arguments to hg
            **kwargs: Any valid kwargs for sh()
        """

        r = self.sh(*(("hg",) + args), **kwargs)
        if kwargs.get('exitcode') or not r:
            return r
        else:
            return r.rstrip("\n")

    def jf(self, *args, **kwargs):
        """
        Run a jf command.  The returned stdout has trailing newlines stripped.

        Args:
            *args: Arguments to jf
            **kwargs: Any valid kwargs for sh()
        """

        kwargs.setdefault('stdout', None)

        r = self.sh(*(("jf",) + args), **kwargs)
        if kwargs.get('exitcode') or not r:
            return r
        else:
            return r.rstrip("\n")

    def test_tick(self):
        """
        Increase the current time.  Useful when testing is True.
        """
        self.testing_time += 60

    def open(self, fn, mode):
        """
        Open a file, relative to the current working directory.

        Args:
            fn: filename to open
            mode: mode to open the file as
        """
        return open(os.path.join(self.cwd, fn), mode)

    def cd(self, d):
        """
        Change the current working directory.

        Args:
            d: directory to change to
        """
        self.cwd = os.path.join(self.cwd, d)
