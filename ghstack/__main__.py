#!/usr/bin/env python3

import ghstack

import ghstack.submit
import ghstack.unlink
import ghstack.rage

import ghstack.logging
import ghstack.github_real
import ghstack.config

import argparse


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

    with ghstack.logging.manager():

        sh = ghstack.shell.Shell()
        conf = ghstack.config.read_config()
        ghstack.logging.formatter.redact(conf.github_oauth, '<GITHUB_OAUTH>')
        github = ghstack.github_real.RealGitHubEndpoint(
            oauth_token=conf.github_oauth,
            proxy=conf.proxy
        )

        if args.cmd == 'rage':
            ghstack.rage.main(latest=args.latest)
        elif args.cmd == 'submit':
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


if __name__ == "__main__":
    main()
