#!/usr/bin/env python3

import ghstack.main
import ghstack.endpoint
import ghstack.config

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Submit stack of diffs to GitHub.')
    parser.add_argument(
        'msg', metavar='MSG', default='Update', nargs='?', type=str,
        help='Description of change you made')
    args = parser.parse_args()

    conf = ghstack.config.read_config()
    github = ghstack.endpoint.GitHubEndpoint(
        oauth_token=conf.github_oauth,
        proxy=conf.proxy
    )

    ghstack.main.main(
        msg=args.msg,
        username=conf.github_username,
        github=github)


if __name__ == "__main__":
    main()
