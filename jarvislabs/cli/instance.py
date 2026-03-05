"""Instance lifecycle commands — ls, create, pause, resume, destroy, get, ssh."""

from __future__ import annotations

import subprocess

import typer

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client

instance_app = typer.Typer(name="instance", help="Manage GPU instances.")
app.add_typer(instance_app, rich_help_panel="Infrastructure")


@instance_app.command("ls")
def instance_ls() -> None:
    """List all instances."""
    client = get_client()
    with render.spinner("Fetching instances..."):
        instances = client.instances.list()
        currency = client.account.currency()

    if state.json_output:
        render.print_json(instances)
        return

    render.instances_table(instances, currency)


@instance_app.command("get")
def instance_get(
    machine_id: int = typer.Argument(..., help="Instance ID."),
) -> None:
    """Show details of a specific instance."""
    client = get_client()
    with render.spinner("Fetching instance..."):
        inst = client.instances.get(machine_id)
        currency = client.account.currency()

    if state.json_output:
        render.print_json(inst)
        return

    render.instance_detail(inst, currency)


@instance_app.command("create")
def instance_create(
    gpu: str = typer.Option(..., "--gpu", "-g", help="GPU type (e.g. H100, A100, RTX5000)."),
    template: str = typer.Option("pytorch", "--template", "-t", help="Framework template."),
    storage: int = typer.Option(40, "--storage", "-s", help="Storage in GB."),
    name: str = typer.Option("Name me", "--name", "-n", help="Instance name."),
    num_gpus: int = typer.Option(1, "--num-gpus", help="Number of GPUs."),
    region: str | None = typer.Option(None, "--region", help="Region (auto-detected if omitted)."),
) -> None:
    """Create a new GPU instance."""
    if not render.confirm(f"Create {num_gpus}x {gpu} instance?", skip=state.yes):
        raise typer.Abort()

    client = get_client()
    with render.spinner("Creating instance — this may take a few minutes..."):
        inst = client.instances.create(
            gpu_type=gpu,
            num_gpus=num_gpus,
            template=template,
            storage=storage,
            name=name,
            region=region,
        )

    if state.json_output:
        render.print_json(inst)
        return

    render.success(f"Instance {inst.machine_id} is Running.")
    render.instance_detail(inst, client.account.currency())


@instance_app.command("pause")
def instance_pause(
    machine_id: int = typer.Argument(..., help="Instance ID to pause."),
) -> None:
    """Pause a running instance."""
    if not render.confirm(f"Pause instance {machine_id}?", skip=state.yes):
        raise typer.Abort()

    client = get_client()
    with render.spinner("Pausing instance..."):
        client.instances.pause(machine_id)
    render.success(f"Instance {machine_id} paused.")


@instance_app.command("resume")
def instance_resume(
    machine_id: int = typer.Argument(..., help="Instance ID to resume."),
) -> None:
    """Resume a paused instance."""
    if not render.confirm(f"Resume instance {machine_id}?", skip=state.yes):
        raise typer.Abort()

    client = get_client()
    with render.spinner("Resuming instance..."):
        inst = client.instances.resume(machine_id)

    if state.json_output:
        render.print_json(inst)
        return

    render.success(f"Instance {inst.machine_id} is Running.")
    render.instance_detail(inst, client.account.currency())


@instance_app.command("destroy")
def instance_destroy(
    machine_id: int = typer.Argument(..., help="Instance ID to destroy."),
) -> None:
    """Permanently destroy an instance."""
    if not render.confirm(
        f"Destroy instance {machine_id}? This cannot be undone.",
        skip=state.yes,
    ):
        raise typer.Abort()

    client = get_client()
    with render.spinner("Destroying instance..."):
        client.instances.destroy(machine_id)
    render.success(f"Instance {machine_id} destroyed.")


@instance_app.command("ssh")
def instance_ssh(
    machine_id: int = typer.Argument(..., help="Instance ID."),
    print_command: bool = typer.Option(False, "--print-command", "-p", help="Print SSH command instead of connecting."),
) -> None:
    """SSH into a running instance."""
    client = get_client()
    with render.spinner("Fetching instance..."):
        inst = client.instances.get(machine_id)

    if not inst.ssh_command:
        render.die(f"Instance {machine_id} has no SSH command (status: {inst.status}).")

    if print_command or state.json_output:
        render.stdout_console.print(inst.ssh_command)
        return

    parts = inst.ssh_command.split()
    render.info(f"Connecting to {machine_id}...")
    raise SystemExit(subprocess.call(parts))
