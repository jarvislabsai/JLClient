from __future__ import annotations

import os

import typer

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client
from jarvislabs.config import load_config, save_config


@app.command(rich_help_panel="Account")
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
            raise typer.Exit()

    if not token:
        render.info("Generate your API key at: [magenta]https://jarvislabs.ai/settings/api-keys[/magenta]")
        token = render.console.input("[yellow]?[/yellow] API token: ", password=True).strip()
    if not token:
        render.die("No token provided.")

    try:
        from jarvislabs.client import Client
        from jarvislabs.exceptions import JarvislabsError

        with render.spinner("Authenticating..."):
            client = Client(api_key=token)
            info = client.account.user_info()
    except JarvislabsError as e:
        render.die(f"Invalid token: {e}")

    first_login = not existing
    config.setdefault("auth", {})["token"] = token
    save_config(config)
    render.success(f"Logged in as {info.name} ({info.user_id})")
    if first_login:
        render.info("Tip: Run `jl --install-completion` to enable tab completion")


@app.command(rich_help_panel="Account")
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


@app.command(rich_help_panel="Account")
def status() -> None:
    """Show account info, balance, and resource summary."""
    client = get_client()
    with render.spinner("Fetching account info..."):
        info = client.account.user_info()
        bal = client.account.balance()
        metrics = client.account.resource_metrics()
        currency = client.account.currency()
        sym = "₹" if currency == "INR" else "$"

    if state.json_output:
        render.print_json(
            {
                "user": info.model_dump(),
                "balance": bal.model_dump(),
                "resources": metrics.model_dump(),
                "currency": currency,
            }
        )
        return

    render.account_status(info, bal, metrics, sym)


@app.command(rich_help_panel="Resources")
def gpus() -> None:
    """Show GPU availability and pricing."""
    client = get_client()
    with render.spinner("Fetching GPU availability..."):
        availability = client.account.gpu_availability()
        currency = client.account.currency()

    if state.json_output:
        render.print_json(availability)
        return

    render.gpu_table(availability, currency)


@app.command(rich_help_panel="Resources")
def templates() -> None:
    """List available instance templates."""
    client = get_client()
    with render.spinner("Fetching templates..."):
        tpls = client.account.templates()

    if state.json_output:
        render.print_json(tpls)
        return

    render.templates_table(tpls)


ssh_key_app = typer.Typer(name="ssh-key", help="Manage SSH keys.")
app.add_typer(ssh_key_app, rich_help_panel="Infrastructure")

scripts_app = typer.Typer(name="scripts", help="Manage startup scripts.")
app.add_typer(scripts_app, rich_help_panel="Infrastructure")

filesystem_app = typer.Typer(name="filesystem", help="Manage persistent filesystems.")
app.add_typer(filesystem_app, rich_help_panel="Infrastructure")


@ssh_key_app.command("list")
def ssh_key_list() -> None:
    """List SSH keys."""
    client = get_client()
    with render.spinner("Fetching SSH keys..."):
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
        raise typer.Exit()

    client = get_client()
    client.ssh_keys.remove(key_id)
    render.success(f"SSH key {key_id} removed.")


@scripts_app.command("list")
def scripts_list() -> None:
    """List startup scripts."""
    client = get_client()
    with render.spinner("Fetching startup scripts..."):
        scripts = client.scripts.list()

    if state.json_output:
        render.print_json(scripts)
        return

    render.scripts_table(scripts)


@scripts_app.command("add")
def scripts_add(
    script_file: typer.FileBinaryRead = typer.Argument(..., help="Path to script file."),
    name: str | None = typer.Option(None, "--name", "-n", help="Script name (defaults to filename stem)."),
) -> None:
    """Add a startup script."""
    content = script_file.read()
    if not content.strip():
        render.die("Script file is empty.")

    default_name = os.path.splitext(os.path.basename(script_file.name))[0]
    script_name = name.strip() if name else default_name
    client = get_client()
    with render.spinner("Adding startup script..."):
        client.scripts.add(content, script_name)

    if state.json_output:
        render.print_json({"success": True, "name": script_name})
        return

    render.success(f"Script '{script_name}' added.")


@scripts_app.command("update")
def scripts_update(
    script_id: int = typer.Argument(..., help="Script ID to update."),
    script_file: typer.FileBinaryRead = typer.Argument(..., help="Path to script file."),
) -> None:
    """Update startup script contents."""
    content = script_file.read()
    if not content.strip():
        render.die("Script file is empty.")

    client = get_client()
    with render.spinner("Updating startup script..."):
        client.scripts.update(script_id, content)

    if state.json_output:
        render.print_json({"success": True, "script_id": script_id})
        return

    render.success(f"Script {script_id} updated.")


@scripts_app.command("remove")
def scripts_remove(
    script_id: int = typer.Argument(..., help="Script ID to remove."),
) -> None:
    """Remove a startup script."""
    if not render.confirm(f"Remove startup script {script_id}?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Removing startup script..."):
        client.scripts.remove(script_id)

    if state.json_output:
        render.print_json({"success": True, "script_id": script_id})
        return

    render.success(f"Script {script_id} removed.")


@filesystem_app.command("list")
def filesystem_list() -> None:
    """List filesystems."""
    client = get_client()
    with render.spinner("Fetching filesystems..."):
        filesystems = client.filesystems.list()

    if state.json_output:
        render.print_json(filesystems)
        return

    render.filesystems_table(filesystems)


@filesystem_app.command("create")
def filesystem_create(
    name: str = typer.Option(..., "--name", "-n", help="Filesystem name."),
    storage: int = typer.Option(..., "--storage", "-s", help="Storage in GB (50-2048)."),
) -> None:
    """Create a filesystem."""
    if not render.confirm(f"Create filesystem (name={name!r}, storage={storage}GB)?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Creating filesystem..."):
        fs_id = client.filesystems.create(fs_name=name, storage=storage)

    if state.json_output:
        render.print_json({"success": True, "fs_id": fs_id, "fs_name": name, "storage": storage})
        return

    render.success(f"Filesystem {fs_id} created.")


@filesystem_app.command("edit")
def filesystem_edit(
    fs_id: int = typer.Argument(..., help="Filesystem ID to edit."),
    storage: int = typer.Option(..., "--storage", "-s", help="New storage size in GB (increase only)."),
) -> None:
    """Expand filesystem storage."""
    if not render.confirm(f"Expand filesystem {fs_id} to {storage}GB?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Updating filesystem..."):
        new_fs_id = client.filesystems.edit(fs_id=fs_id, storage=storage)

    if state.json_output:
        render.print_json({"success": True, "fs_id": new_fs_id, "previous_fs_id": fs_id, "storage": storage})
        return

    render.success(f"Filesystem updated. New filesystem ID: {new_fs_id}.")


@filesystem_app.command("remove")
def filesystem_remove(
    fs_id: int = typer.Argument(..., help="Filesystem ID to remove."),
) -> None:
    """Delete a filesystem."""
    if not render.confirm(f"Remove filesystem {fs_id}?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Removing filesystem..."):
        client.filesystems.remove(fs_id)

    if state.json_output:
        render.print_json({"success": True, "fs_id": fs_id})
        return

    render.success(f"Filesystem {fs_id} removed.")
