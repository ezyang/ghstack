import ghstack
import ghstack.endpoint
import ghstack.config

import argparse

def main():
    parser = argparse.ArgumentParser(
        description='Submit stack of diffs to GitHub.')
    parser.add_argument(
        'msg', metavar='MSG', default='Update', nargs='?', type=str,
        help='Description of change you made')
    parser.add_argument(
        '--github-v4',
        default=ghstack.endpoint.GraphQLEndpoint('https://api.github.com/graphql'),
        help='GitHub GraphQL API endpoint (V4) to use')
    parser.add_argument(
        '--github-v3',
        default=ghstack.endpoint.RESTEndpoint('https://api.github.com'),
        help='GitHub REST API endpoint (V3) to use')
    args = parser.parse_args()

    conf = ghstack.config.read_config()

    args.github_v4.oauth_token = conf.github_oauth
    args.github_v3.oauth_token = conf.github_oauth
    args.github_v4.proxy = conf.proxy
    args.github_v3.proxy = conf.proxy

    ghstack.main(
        msg=args.msg,
        github=args.github_v4,
        github_rest=args.github_v3)


if __name__ == "__main__":
    main()
