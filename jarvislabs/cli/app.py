"""Root Typer app — entry point for the `jl` CLI.

Global flags (--json, --yes, --verbose, --token) are handled here via callback.
Subcommands are registered from commands.py and instance.py.
"""

from __future__ import annotations

import typer

from jarvislabs.cli import state

app = typer.Typer(
    name="jl",
    help="CLI for JarvisLabs.ai GPU cloud.",
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
)


@app.callback(invoke_without_command=True)
def _global_flags(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
    verbose: bool = typer.Option(False, "--verbose", help="Show HTTP request details."),
    token: str | None = typer.Option(None, "--token", envvar="JL_API_KEY", help="API key override."),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        from importlib.metadata import version as pkg_version

        typer.echo(f"jl {pkg_version('jarvislabs')}")
        raise typer.Exit()

    state.json_output = json_output
    state.yes = yes
    state.verbose = verbose
    state.token = token

    # If no subcommand was given (and --version wasn't handled above), show help
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def get_client():
    """Create a Client using the resolved token. Called lazily by commands that need it."""
    from jarvislabs.cli.render import die
    from jarvislabs.client import Client

    try:
        return Client(api_key=state.token)
    except Exception as e:
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
