import ghstack
import ghstack.endpoint

import os
import argparse
import getpass

try:
    import configparser
except ImportError:
    import ConfigParser as configparser  # type: ignore


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

    config = configparser.ConfigParser()
    config.read(['.ghstackrc', os.path.expanduser('~/.ghstackrc')])

    write_back = False

    if not config.has_section('ghstack'):
        config.add_section('ghstack')

    if not config.has_option('ghstack', 'github_oauth'):
        github_oauth = config.set(
            'ghstack',
            'github_oauth',
            getpass.getpass(
                'GitHub OAuth token (make one at '
                'https://github.com/settings/tokens -- '
                'we need public_repo permissions): ').strip())
        write_back = True
    else:
        github_oauth = config.get('ghstack', 'github_oauth')

    if config.has_option('ghstack', 'proxy'):
        proxy = config.get('ghstack', 'proxy')
    else:
        proxy = None

    if write_back:
        config.write(open(os.path.expanduser('~/.ghstackrc'), 'w'))
        print("NB: saved to ~/.ghstackrc")

    args.github_v4.oauth_token = github_oauth
    args.github_v3.oauth_token = github_oauth
    args.github_v4.proxy = proxy
    args.github_v3.proxy = proxy

    ghstack.main(
        msg=args.msg,
        github=args.github_v4,
        github_rest=args.github_v3)


if __name__ == "__main__":
    main()
