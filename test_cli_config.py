import configparser
from pathlib import Path

from click.testing import CliRunner

import ghstack.cli


def read_config(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(path)
    return parser


def test_config_command() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        config_path = Path.cwd() / ".ghstackrc"
        env = {"GHSTACKRC_PATH": str(config_path)}

        result = runner.invoke(
            ghstack.cli.main,
            ["config", "automsg"],
            env=env,
        )
        assert result.exit_code == 1
        assert "automsg is not set" in result.output

        result = runner.invoke(
            ghstack.cli.main,
            ["config", "automsg", "claude"],
            env=env,
        )
        assert result.exit_code == 0

        parser = read_config(config_path)
        assert parser.get("ghstack", "automsg") == "claude"

        result = runner.invoke(
            ghstack.cli.main,
            ["config", "automsg", "codex", "--model", "gpt-5.4"],
            env=env,
        )
        assert result.exit_code == 0

        parser = read_config(config_path)
        assert parser.get("ghstack", "automsg") == "codex --model gpt-5.4"

        result = runner.invoke(
            ghstack.cli.main,
            ["config", "--unset", "automsg"],
            env=env,
        )
        assert result.exit_code == 0

        parser = read_config(config_path)
        assert not parser.has_option("ghstack", "automsg")
