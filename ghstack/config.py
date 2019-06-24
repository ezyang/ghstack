#!/usr/bin/env python3

import os
import getpass
import configparser
import logging
from typing import NamedTuple, Optional


Config = NamedTuple('Config', [
    # Proxy to use when making connections to GitHub
    ('proxy', Optional[str]),
    # OAuth token to authenticate to GitHub with
    ('github_oauth', str),
    # GitHub username; used to namespace branches we create
    ('github_username', str),
    # Token to authenticate to CircleCI with
    ('circle_token', Optional[str]),

    # These config parameters are not used by ghstack, but other
    # tools that reuse this module

    # Path to working fbsource checkout
    ('fbsource_path', str),
    # Path to working git checkout (ghstack infers your git checkout
    # based on CWD)
    ('github_path', str),
    # Path to project directory inside fbsource, to default when
    # autodetection fails
    ('default_project_dir', str),
])


def read_config(*, request_circle_token: bool = False) -> Config:  # noqa: C901
    config = configparser.ConfigParser()
    config.read(['.ghstackrc', os.path.expanduser('~/.ghstackrc')])

    write_back = False

    if not config.has_section('ghstack'):
        config.add_section('ghstack')

    # Environment variable overrides config file
    # This envvar is legacy from ghexport days
    github_oauth = os.getenv("OAUTH_TOKEN")
    if github_oauth is None and config.has_option('ghstack', 'github_oauth'):
        github_oauth = config.get('ghstack', 'github_oauth')
    if github_oauth is None:
        github_oauth = getpass.getpass(
            'GitHub OAuth token (make one at '
            'https://github.com/settings/tokens -- '
            'we need public_repo permissions): ').strip()
        config.set(
            'ghstack',
            'github_oauth',
            github_oauth)
        write_back = True

    circle_token = None
    if circle_token is None and config.has_option('ghstack', 'circle_token'):
        circle_token = config.get('ghstack', 'circle_token')
    if circle_token is None and request_circle_token:
        circle_token = getpass.getpass(
            'CircleCI Personal API token (make one at '
            'https://circleci.com/account/api ): ').strip()
        config.set(
            'ghstack',
            'circle_token',
            circle_token)
        write_back = True

    github_username = None
    if config.has_option('ghstack', 'github_username'):
        github_username = config.get('ghstack', 'github_username')
    if github_username is None:
        github_username = input('GitHub username: ')
        config.set(
            'ghstack',
            'github_username',
            github_username)
        write_back = True

    proxy = None
    if config.has_option('ghstack', 'proxy'):
        proxy = config.get('ghstack', 'proxy')

    if config.has_option('ghstack', 'fbsource_path'):
        fbsource_path = config.get('ghstack', 'fbsource_path')
    else:
        fbsource_path = os.path.expanduser('~/local/fbsource')

    if config.has_option('ghstack', 'github_path'):
        github_path = config.get('ghstack', 'github_path')
    else:
        github_path = os.path.expanduser('~/local/ghstack-pytorch')

    if config.has_option('ghstack', 'default_project'):
        default_project_dir = config.get('ghstack', 'default_project_dir')
    else:
        default_project_dir = 'fbcode/caffe2'

    if write_back:
        config.write(open(os.path.expanduser('~/.ghstackrc'), 'w'))
        logging.info("NB: configuration saved to ~/.ghstackrc")

    return Config(
        github_oauth=github_oauth,
        circle_token=circle_token,
        github_username=github_username,
        proxy=proxy,
        fbsource_path=fbsource_path,
        github_path=github_path,
        default_project_dir=default_project_dir)
