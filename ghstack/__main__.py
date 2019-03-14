#!/usr/bin/env python3

import ghstack
import ghstack.main
import ghstack.github_real
import ghstack.config

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Submit stack of diffs to GitHub.')
    parser.add_argument(
        '--version', action='store_true',
        help='Print version')
    parser.add_argument(
        '--message', '-m',
        default='Update',
        help='Description of change you made')
    parser.add_argument(
        '--update-fields', '-u', action='store_true',
        help='Update GitHub pull request summary from the local commit')

    args = parser.parse_args()

    if args.version:
        print("ghstack {}".format(ghstack.__version__))
        return

    conf = ghstack.config.read_config()
    github = ghstack.github_real.RealGitHubEndpoint(
        oauth_token=conf.github_oauth,
        proxy=conf.proxy
    )

    ghstack.main.main(
        msg=args.message,
        username=conf.github_username,
        github=github,
        update_fields=args.update_fields
    )


if __name__ == "__main__":
    main()
