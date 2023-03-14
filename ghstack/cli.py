import asyncio
import contextlib
from typing import Generator, List, Tuple

import click

import ghstack
import ghstack.action
import ghstack.checkout
import ghstack.circleci_real
import ghstack.config
import ghstack.github_real
import ghstack.land
import ghstack.logs
import ghstack.rage
import ghstack.status
import ghstack.submit
import ghstack.unlink

EXIT_STACK = contextlib.ExitStack()

GhstackContext = Tuple[
    ghstack.shell.Shell,
    ghstack.config.Config,
    ghstack.github_real.RealGitHubEndpoint,
]


@contextlib.contextmanager
def cli_context(
    request_circle_token: bool = False,
) -> Generator[GhstackContext, None, None]:
    with EXIT_STACK:
        shell = ghstack.shell.Shell()
        config = ghstack.config.read_config(request_circle_token=request_circle_token)
        github = ghstack.github_real.RealGitHubEndpoint(
            oauth_token=config.github_oauth,
            proxy=config.proxy,
            github_url=config.github_url,
        )
        yield shell, config, github


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(ghstack.__version__, "--version", "-V")
@click.option("--debug", is_flag=True, help="Log debug information to stderr")
# hidden arguments that we'll pass along to submit if no other command given
@click.option("--message", "-m", default="Update", hidden=True)
@click.option("--update-fields", "-u", is_flag=True, hidden=True)
@click.option("--short", is_flag=True, hidden=True)
@click.option("--force", is_flag=True, hidden=True)
@click.option("--no-skip", is_flag=True, hidden=True)
@click.option("--draft", is_flag=True, hidden=True)
def main(
    ctx: click.Context,
    debug: bool,
    message: str,
    update_fields: bool,
    short: bool,
    force: bool,
    no_skip: bool,
    draft: bool,
) -> None:
    """
    Submit stacks of diffs to Github
    """
    EXIT_STACK.enter_context(ghstack.logs.manager(debug=debug))

    if not ctx.invoked_subcommand:
        return ctx.invoke(
            submit,
            message=message,
            update_fields=update_fields,
            short=short,
            force=force,
            no_skip=no_skip,
            draft=draft,
        )


@main.command("action")
@click.option("--close", is_flag=True, help="Close the specified pull request")
@click.argument("pull_request", metavar="PR")
def action(close: bool, pull_request: str) -> None:
    """
    Perform actions on a PR
    """
    with cli_context() as (shell, _, github):
        ghstack.action.main(
            pull_request=pull_request,
            github=github,
            sh=shell,
            close=close,
        )


@main.command("checkout")
@click.argument("pull_request", metavar="PR")
def checkout(pull_request: str) -> None:
    """
    Checkout a PR
    """
    with cli_context() as (shell, config, github):
        ghstack.checkout.main(
            pull_request=pull_request,
            github=github,
            sh=shell,
            remote_name=config.remote_name,
        )


@main.command("land")
@click.option("--force", is_flag=True, help="force land even if the PR is closed")
@click.argument("pull_request", metavar="PR")
def land(force: bool, pull_request: str) -> None:
    """
    Land a PR stack
    """
    with cli_context() as (shell, config, github):
        ghstack.land.main(
            pull_request=pull_request,
            github=github,
            sh=shell,
            github_url=config.github_url,
            remote_name=config.remote_name,
            force=force,
        )


@main.command("rage")
@click.option(
    "--latest",
    is_flag=True,
    help="Select the last command (not including rage commands) to report",
)
def rage(latest: bool) -> None:
    with cli_context():
        ghstack.rage.main(latest)


@main.command("status")
@click.argument("pull_request", metavar="PR")
def status(pull_request: str):
    """
    Check status of a PR
    """
    with cli_context(request_circle_token=True) as (shell, config, github):
        circleci = ghstack.circleci_real.RealCircleCIEndpoint(
            circle_token=config.circle_token
        )

        fut = ghstack.status.main(
            pull_request=pull_request,
            github=github,
            circleci=circleci,
        )
        loop = asyncio.get_event_loop()
        loop.run_until_complete(fut)
        loop.close()


@main.command("submit")
@click.option(
    "--message",
    "-m",
    default="Update",
    help="Description of change you made",
)
@click.option(
    "--update-fields",
    "-u",
    is_flag=True,
    help="Update GitHub pull request summary from the local commit",
)
@click.option(
    "--short", is_flag=True, help="Print only the URL of the latest opened PR to stdout"
)
@click.option(
    "--force",
    is_flag=True,
    help="force push the branch even if your local branch is stale",
)
@click.option(
    "--no-skip",
    is_flag=True,
    help="Never skip pushing commits, even if the contents didn't change "
    "(use this if you've only updated the commit message).",
)
@click.option(
    "--draft",
    is_flag=True,
    help="Create the pull request in draft mode (only if it has not already been created)",
)
def submit(
    message: str,
    update_fields: bool,
    short: bool,
    force: bool,
    no_skip: bool,
    draft: bool,
) -> None:
    """
    Submit or update a PR stack
    """
    with cli_context() as (shell, config, github):
        ghstack.submit.main(
            msg=message,
            username=config.github_username,
            sh=shell,
            github=github,
            update_fields=update_fields,
            short=short,
            force=force,
            no_skip=no_skip,
            draft=draft,
            github_url=config.github_url,
            remote_name=config.remote_name,
        )


@main.command("unlink")
@click.argument("commits", nargs=-1, metavar="COMMIT")
def unlink(commits: List[str]) -> None:
    """
    Unlink commits from PRs
    """
    with cli_context() as (shell, config, github):
        ghstack.unlink.main(
            commits=commits,
            github=github,
            sh=shell,
            github_url=config.github_url,
            remote_name=config.remote_name,
        )
