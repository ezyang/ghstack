import os
import collections
import getpass

try:
    import configparser
except ImportError:
    import ConfigParser as configparser  # type: ignore


Config = collections.namedtuple('Config', [
    'proxy',
    'github_oauth',
    'fbsource_path',
    'github_path'
])


def read_config():
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
