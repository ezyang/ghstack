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
    parser.add_argument(
        '--github-v4',
        default='https://api.github.com/graphql',
        help='GitHub GraphQL API endpoint (V4) to use')
    parser.add_argument(
        '--github-v3',
        default='https://api.github.com',
        help='GitHub REST API endpoint (V3) to use')
    args = parser.parse_args()

    conf = ghstack.config.read_config()
    github_v4 = ghstack.endpoint.GraphQLEndpoint(
        endpoint=args.github_v4,
        oauth_token=conf.github_oauth,
        proxy=conf.proxy
    )
    github_v3 = ghstack.endpoint.RESTEndpoint(
        endpoint=args.github_v3,
        oauth_token=conf.github_oauth,
        proxy=conf.proxy
    )

    ghstack.main.main(
        msg=args.msg,
        username=args.github_username,
        github=github_v4,
        github_rest=github_v3)


if __name__ == "__main__":
    main()
