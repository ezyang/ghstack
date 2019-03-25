#!/usr/bin/env python3

import ghstack

import ghstack.submit
import ghstack.unlink
import ghstack.rage

import ghstack.logging
import ghstack.github_real
import ghstack.config

import argparse
import logging
import os
import shutil
import re
import sys


class PlainFormatter(logging.Formatter):
    def formatMessage(self, record):
        if record.levelno == logging.INFO or record.levelno == logging.DEBUG:
            # Log INFO/DEBUG without any adornment
            return record.getMessage()
        else:
            return super().formatMessage(record)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Submit stack of diffs to GitHub.')
    parser.add_argument(
        '--version', action='store_true',
        help='Print version')

    subparsers = parser.add_subparsers(dest='cmd')

    submit = subparsers.add_parser('submit')
    for subparser in (submit, parser):
        subparser.add_argument(
            '--message', '-m',
            default='Update',
            help='Description of change you made')
        subparser.add_argument(
            '--update-fields', '-u', action='store_true',
            help='Update GitHub pull request summary from the local commit')

    unlink = subparsers.add_parser('unlink')
    unlink.add_argument('COMMITS', nargs='*')

    rage = subparsers.add_parser('rage')
    rage.add_argument('--latest', action='store_true',
        help='Select the last command (not including rage commands) to report')

    args = parser.parse_args()

    if args.version:
        print("ghstack {}".format(ghstack.__version__))
        return

    if args.cmd is None:
        args.cmd = 'submit'

    # TCB code to setup logging.  If a failure starts here we won't
    # be able to save the user ina  reasonable way.

    # Logging structure: there is one logger (the root logger)
    # and in processes all events.  There are two handlers:
    # stderr (INFO) and file handler (DEBUG).
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    plain_formatter = PlainFormatter(
        fmt="%(levelname)s: %(message)s", datefmt="")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(plain_formatter)
    root_logger.addHandler(console_handler)

    if args.cmd != 'rage':
        log_file = os.path.join(ghstack.logging.run_dir(), "ghstack.log")

        file_handler = logging.FileHandler(log_file)
        # TODO: Hypothetically, it is better if we log the timestamp.
        # But I personally feel the timestamps gunk up the log info
        # for not much benefit (since we're not really going to be
        # in the business of debugging performance bugs, for which
        # timestamps would really be helpful.)  Perhaps reconsider
        # at some point based on how useful this information actually is.
        file_handler.setFormatter(plain_formatter)
        # file_handler.setFormatter(logging.Formatter(
        #    fmt="[%(asctime)s] [%(levelname)8s] %(message)s"))
        root_logger.addHandler(file_handler)

        ghstack.logging.record_argv()

    # Rage is special; don't log for it
    if args.cmd == 'rage':
        ghstack.rage.main(latest=args.latest)
        return

    try:
        # Do log rotation (keep 100)
        ghstack.logging.rotate()

        sh = ghstack.shell.Shell()
        conf = ghstack.config.read_config()
        github = ghstack.github_real.RealGitHubEndpoint(
            oauth_token=conf.github_oauth,
            proxy=conf.proxy
        )

        if args.cmd == 'submit':
            ghstack.submit.main(
                msg=args.message,
                username=conf.github_username,
                sh=sh,
                github=github,
                update_fields=args.update_fields
            )
        elif args.cmd == 'unlink':
            ghstack.unlink.main(
                commits=args.COMMITS,
                sh=sh,
            )
        else:
            raise RuntimeError("Unrecognized command {}".format(args.cmd))

    except Exception as e:
        logging.exception("Fatal exception")
        ghstack.logging.record_argv()
        ghstack.logging.record_exception(e)
        sys.exit(1)

    except BaseException as e:
        # You could logging.debug here to suppress the backtrace
        # entirely, but there is no reason to hide it from technically
        # savvy users.
        logging.info("", exc_info=True)
        ghstack.logging.record_argv()
        ghstack.logging.record_exception(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
