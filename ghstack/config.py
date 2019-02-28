import os
import getpass
import configparser
from typing import NamedTuple, Optional


Config = NamedTuple('Config', [
    ('proxy', Optional[str]),
    ('github_oauth', str),
    ('fbsource_path', str),
    ('github_path', str)
])


def read_config() -> Config:
    config = configparser.ConfigParser()
    config.read(['.ghstackrc', os.path.expanduser('~/.ghstackrc')])

    write_back = False

    if not config.has_section('ghstack'):
        config.add_section('ghstack')

    if not config.has_option('ghstack', 'github_oauth'):
        github_oauth = getpass.getpass(
            'GitHub OAuth token (make one at '
            'https://github.com/settings/tokens -- '
            'we need public_repo permissions): ').strip()
        config.set(
            'ghstack',
            'github_oauth',
            github_oauth)
        write_back = True
    else:
        github_oauth = config.get('ghstack', 'github_oauth')

    proxy = None
    if config.has_option('ghstack', 'proxy'):
        proxy = config.get('ghstack', 'proxy')

    if write_back:
        config.write(open(os.path.expanduser('~/.ghstackrc'), 'w'))
        print("NB: saved to ~/.ghstackrc")

    if config.has_option('ghstack', 'fbsource_path'):
        fbsource_path = config.get('ghstack', 'fbsource_path')
    else:
        fbsource_path = os.path.expanduser('~/local/fbsource')

    if config.has_option('ghstack', 'github_path'):
        github_path = config.get('ghstack', 'github_path')
    else:
        github_path = os.path.expanduser('~/local/ghstack-pytorch')

    return Config(
        github_oauth=github_oauth,
        proxy=proxy,
        fbsource_path=fbsource_path,
        github_path=github_path)
