import subprocess
import os
import functools
import re
import datetime
import uuid
import shutil
import sys


DATETIME_FORMAT = '%Y-%m-%d_%Hh%Mm%Ss'


RE_LOG_DIRNAME = re.compile(
    r'(\d{4}-\d\d-\d\d_\d\dh\d\dm\d\ds)_'
    r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')


@functools.lru_cache()
def base_dir() -> str:
    # Don't use shell here as we are not allowed to log yet!
    git_dir = subprocess.run(
        ("git", "rev-parse", "--git-dir"), capture_output=True
    ).stdout.decode("utf-8").rstrip()
    base_dir = os.path.join(git_dir, "ghstack", "log")

    try:
        os.makedirs(base_dir)
    except FileExistsError:
        pass

    return base_dir


@functools.lru_cache()
def run_dir() -> str:
    # NB: respects timezone
    cur_dir = os.path.join(
        base_dir(),
        "{}_{}"
        .format(datetime.datetime.now().strftime(DATETIME_FORMAT),
                uuid.uuid1()))

    try:
        os.makedirs(cur_dir)
    except FileExistsError:
        pass

    return cur_dir


def record_exception(e: BaseException) -> None:
    with open(os.path.join(run_dir(), "exception"), 'w') as f:
        f.write(type(e).__name__)


@functools.lru_cache()
def record_argv() -> None:
    with open(os.path.join(run_dir(), "argv"), 'w') as f:
        f.write(subprocess.list2cmdline(sys.argv[1:]))


def rotate() -> None:
    log_base = base_dir()
    old_logs = os.listdir(log_base)
    old_logs.sort(reverse=True)
    for stale_log in old_logs[100:]:
        # Sanity check that it looks like a log
        assert RE_LOG_DIRNAME.fullmatch(stale_log)
        shutil.rmtree(os.path.join(log_base, stale_log))
