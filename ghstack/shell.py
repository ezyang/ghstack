from __future__ import print_function

import subprocess
import os
import sys


def format_env(env):
    r = []
    for k, v in env.items():
        r.append("{}={}".format(k, subprocess.list2cmdline([v])))
    return ' '.join(r)


def log_command(args, env=None):
    cmd = subprocess.list2cmdline(args).replace("\n", "\\n")
    # if env is not None:
    #     cmd = "{} {}".format(format_env(env), cmd)
    print("$ " + cmd)


def merge_dicts(x, y):
    z = x.copy()
    z.update(y)
    return z


class Shell(object):
    def __init__(self, quiet=False, cwd=None, testing=False):
        self.cwd = cwd
        self.quiet = quiet
        self.testing = testing
        self.testing_time = 1112911993

    def sh(self, *args, **kwargs):
        stdin = None
        if 'input' in kwargs:
            stdin = subprocess.PIPE
        env = kwargs.get("env")
        if not self.quiet:
            log_command(args, env=env)
        if env is not None:
            env = merge_dicts(os.environ, env)
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
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
        return out.decode()

    def git(self, *args, **kwargs):
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
        if kwargs.get('exitcode'):
            return r
        else:
            return r.rstrip("\n")

    def test_tick(self):
        self.testing_time += 60

    def open(self, fn, mode):
        return open(os.path.join(self.cwd, fn), mode)


