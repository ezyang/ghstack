#!/usr/bin/env python3

import ghstack

import ghstack.submit
import ghstack.unlink
import ghstack.rage
import ghstack.land
import ghstack.action
import ghstack.status
import ghstack.checkout

import ghstack.logging
import ghstack.github_real
import ghstack.circleci_real
import ghstack.config

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Submit stack of diffs to GitHub.')
    parser.add_argument(
        '--version', action='store_true',
        help='Print version')
    parser.add_argument(
        '--debug', action='store_true',
        help='Log debug information to stderr')

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
        subparser.add_argument(
            '--short', action='store_true',
            help='Print only the URL of the latest opened PR to stdout')

    unlink = subparsers.add_parser('unlink')
    unlink.add_argument('COMMITS', nargs='*')

    rage = subparsers.add_parser('rage')
    rage.add_argument('--latest', action='store_true',
        help='Select the last command (not including rage commands) to report')

    land = subparsers.add_parser('land')
    land.add_argument('COMMITS', nargs='*')

    checkout = subparsers.add_parser('checkout')
    checkout.add_argument('pull_request', metavar='PR',
        help='GitHub pull request URL to checkout')

    action = subparsers.add_parser('action')
    # TODO: support number as well
    action.add_argument('pull_request', metavar='PR',
        help='GitHub pull request URL to perform action on')
    action.add_argument('--close', action='store_true',
        help='Close the specified pull request')

    status = subparsers.add_parser('status')
    # TODO: support number as well
    status.add_argument('pull_request', metavar='PR',
        help='GitHub pull request URL to perform action on')

    args = parser.parse_args()

    if args.version:
        print("ghstack {}".format(ghstack.__version__))
        return

    if args.cmd is None:
        args.cmd = 'submit'

    with ghstack.logging.manager(debug=args.debug):

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
                update_fields=args.update_fields,
                short=args.short
            )
        elif args.cmd == 'unlink':
            ghstack.unlink.main(
                commits=args.COMMITS,
                sh=sh,
            )
        elif args.cmd == 'land':
            ghstack.land.main(
                sh=sh,
            )
        elif args.cmd == 'action':
            ghstack.action.main(
                pull_request=args.pull_request,
                github=github,
                sh=sh,
                close=args.close,
            )
        elif args.cmd == 'status':
            # Re-read conf and request circle token if not available
            # TODO: Restructure this so that we just request
            # configurations "on-demand" rather than all upfront
            conf = ghstack.config.read_config(request_circle_token=True)
            if conf.circle_token:
                ghstack.logging.formatter.redact(conf.circle_token, '<CIRCLE_TOKEN>')
            circleci = ghstack.circleci_real.RealCircleCIEndpoint(
                circle_token=conf.circle_token
            )
            # Blegh
            loop = asyncio.get_event_loop()
            loop.run_until_complete(ghstack.status.main(
                pull_request=args.pull_request,
                github=github,
                circleci=circleci
            ))
            loop.close()
        elif args.cmd == 'checkout':
            ghstack.checkout.main(pull_request=args.pull_request, github=github, sh=sh)
        else:
            raise RuntimeError("Unrecognized command {}".format(args.cmd))


if __name__ == "__main__":
    main()
