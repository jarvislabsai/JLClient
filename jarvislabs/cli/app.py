"""Root Typer app — entry point for the `jl` CLI.

Global flags (--json, --yes, --verbose, --token) are handled here via callback.
Subcommands are registered from commands.py and instance.py.
"""

from __future__ import annotations

import typer

from jarvislabs.cli import state

app = typer.Typer(
    name="jl",
    help=(
        "[bold cyan]⚡ JarvisLabs[/bold cyan] GPU Cloud CLI\n\n"
        "Use command groups, then inspect their subcommands:\n"
        "  [bold]jl instance --help[/bold]\n"
        "  [bold]jl ssh-key --help[/bold]\n\n"
        "  [bold]jl scripts --help[/bold]\n\n"
        "Quick examples:\n"
        "  [bold]jl gpus[/bold]\n"
        "  [bold]jl instance list[/bold]\n"
        "  [bold]jl instance create --gpu H100 --num-gpus 1 --storage 100[/bold]\n"
        "  [bold]jl scripts list[/bold]"
    ),
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
)


@app.callback(invoke_without_command=True)
def _global_flags(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    token: str | None = typer.Option(None, "--token", envvar="JL_API_KEY", metavar="API_KEY", help="API key override."),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        from importlib.metadata import version as pkg_version

        typer.echo(f"jl {pkg_version('jarvislabs')}")
        raise typer.Exit()

    state.json_output = json_output
    state.yes = yes
    state.token = token

    # If no subcommand was given (and --version wasn't handled above), show help
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def get_client():
    """Create a Client using the resolved token. Called lazily by commands that need it."""
    from jarvislabs.cli.render import die
    from jarvislabs.client import Client
    from jarvislabs.exceptions import JarvislabsError

    try:
        return Client(api_key=state.token)
    except JarvislabsError as e:
        die(str(e))


def main() -> None:
    """Entry point for `jl` command (wired via pyproject.toml [project.scripts])."""
    from jarvislabs.cli import commands, instance  # noqa: F401
    from jarvislabs.exceptions import JarvislabsError

    try:
        app()
    except JarvislabsError as e:
        from jarvislabs.cli.render import die

        die(str(e))
