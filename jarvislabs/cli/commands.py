from __future__ import annotations

import typer

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client
from jarvislabs.config import load_config, save_config


@app.command()
def login(
    token: str = typer.Option(None, "--token", "-t", help="API token (prompted if not given)."),
) -> None:
    """Save API token to config file."""
    config = load_config()
    existing = config.get("auth", {}).get("token")

    if existing and not token:
        try:
            from jarvislabs.client import Client

            current = Client(api_key=existing).account.user_info()
            msg = f"Already logged in as {current.name}. Re-authenticate?"
        except Exception:
            msg = "Already logged in. Re-authenticate?"
        if not render.confirm(msg, skip=state.yes):
            raise typer.Abort()

    if not token:
        render.info("Generate your API key at: https://jarvislabs.ai/settings/api-keys")
        token = render.console.input("[yellow]?[/yellow] API token: ", password=True).strip()
    if not token:
        render.die("No token provided.")

    try:
        from jarvislabs.client import Client

        client = Client(api_key=token)
        info = client.account.user_info()
    except Exception as e:
        render.die(f"Invalid token: {e}")

    config.setdefault("auth", {})["token"] = token
    save_config(config)
    render.success(f"Logged in as {info.name} ({info.user_id})")


@app.command()
def logout() -> None:
    """Remove saved API token from config file."""
    config = load_config()
    if "auth" in config and "token" in config["auth"]:
        del config["auth"]["token"]
        if not config["auth"]:
            del config["auth"]
        save_config(config)
        render.success("Logged out — token removed from config.")
    else:
        render.info("No saved token found.")


@app.command()
def status() -> None:
    """Show account info, balance, and resource summary."""
    client = get_client()
    info = client.account.user_info()
    bal = client.account.balance()
    metrics = client.account.resource_metrics()
    sym = "₹" if client.account.currency() == "INR" else "$"

    if state.json_output:
        render.print_json(
            {
                "user": info.model_dump(),
                "balance": bal.model_dump(),
                "resources": metrics.model_dump(),
            }
        )
        return

    render.success(f"{info.name} ({info.user_id})")
    render.info(f"Balance: {sym}{bal.balance:.2f}  |  Grants: {sym}{bal.grants:.2f}")
    render.info(f"Running: {metrics.running_instances}  |  Paused: {metrics.paused_instances}")


@app.command()
def gpus() -> None:
    """Show GPU availability and pricing across regions."""
    client = get_client()
    availability = client.account.gpu_availability()

    if state.json_output:
        render.print_json(availability)
        return

    render.gpu_table(availability, client.account.currency())


@app.command()
def templates() -> None:
    """List available instance templates."""
    client = get_client()
    tpls = client.account.templates()

    if state.json_output:
        render.print_json(tpls)
        return

    from rich.table import Table

    table = Table(title="Templates")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Category", style="dim")
    for t in tpls:
        table.add_row(t.id, t.title, t.category or "—")
    render.stdout_console.print(table)


ssh_key_app = typer.Typer(name="ssh-key", help="Manage SSH keys.")
app.add_typer(ssh_key_app)


@ssh_key_app.command("list")
def ssh_key_list() -> None:
    """List SSH keys."""
    client = get_client()
    keys = client.ssh_keys.list()

    if state.json_output:
        render.print_json(keys)
        return

    render.ssh_keys_table(keys)


@ssh_key_app.command("add")
def ssh_key_add(
    pubkey_file: typer.FileText = typer.Argument(..., help="Path to public key file."),
    name: str = typer.Option(..., "--name", "-n", help="Name for this key."),
) -> None:
    """Add an SSH public key."""
    key_content = pubkey_file.read().strip()
    if not key_content:
        render.die("Public key file is empty.")

    client = get_client()
    client.ssh_keys.add(ssh_key=key_content, key_name=name)
    render.success(f"SSH key '{name}' added.")


@ssh_key_app.command("remove")
def ssh_key_remove(
    key_id: str = typer.Argument(..., help="Key ID to remove."),
) -> None:
    """Remove an SSH key."""
    if not render.confirm(f"Remove SSH key {key_id}?", skip=state.yes):
        raise typer.Abort()

    client = get_client()
    client.ssh_keys.remove(key_id)
    render.success(f"SSH key {key_id} removed.")
