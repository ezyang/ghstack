#!/usr/bin/env python3

import ghstack
import ghstack.logging
import os
import datetime
import tempfile


def get_argv(log_dir: str) -> str:
    argv = "Unknown"
    argv_fn = os.path.join(log_dir, 'argv')
    if os.path.exists(argv_fn):
        with open(argv_fn, 'r') as f:
            argv = f.read().rstrip()
    return argv


def main(latest: bool = False) -> None:

    log_base = ghstack.logging.base_dir()
    logs = os.listdir(log_base)
    logs.sort(reverse=True)

    index = 0
    if not latest:
        print("Which ghstack invocation would you like to report?")
        print()
        for (i, fn) in enumerate(logs[:10]):
            m = ghstack.logging.RE_LOG_DIRNAME.fullmatch(fn)
            if m:
                date = datetime.datetime.strptime(
                    m.group(1), ghstack.logging.DATETIME_FORMAT
                ).astimezone(tz=None).strftime("%a %b %d %H:%M:%S %Z")
            else:
                date = "Unknown"
            log_dir = os.path.join(log_base, fn)
            argv = get_argv(log_dir)
            exception = "Succeeded"
            exception_fn = os.path.join(log_base, fn, 'exception')
            if os.path.exists(exception_fn):
                with open(exception_fn, 'r') as f:
                    exception = "Failed with: " + f.read().rstrip()

            print("{:<5}  {}  ghstack [{}]  {}"
                  .format("[{}].".format(i), date, argv, exception))
        print()
        index = int(input('(input individual number, for example 1 or 2)\n'))

    log_dir = os.path.join(log_base, logs[index])

    print()
    print("Writing report, please wait...")
    with tempfile.NamedTemporaryFile(mode='w', suffix=".log",
                                     prefix="ghstack", delete=False) as g:
        g.write("version: {}\n".format(ghstack.__version__))
        g.write("command: ghstack {}\n".format(get_argv(log_dir)))
        g.write("\n")
        log_fn = os.path.join(log_dir, "ghstack.log")
        if os.path.exists(log_fn):
            with open(log_fn) as log:
                g.write(log.read())

    print("=> Report written to {}".format(f.name))
    print("Please include this log with your bug report!")
