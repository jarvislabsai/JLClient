"""Instance lifecycle commands — list, create, rename, pause, resume, destroy, get, ssh."""

from __future__ import annotations

import subprocess

import typer

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client

instance_app = typer.Typer(name="instance", help="Manage GPU instances.")
app.add_typer(instance_app, rich_help_panel="Infrastructure")


@instance_app.command("list")
def instance_list() -> None:
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
    script_id: str | None = typer.Option(None, "--script-id", help="Startup script ID to run on launch."),
    script_args: str = typer.Option("", "--script-args", help="Arguments passed to startup script."),
) -> None:
    """Create a new GPU instance."""
    details = [f"gpu={num_gpus}x {gpu}", f"template={template}", f"storage={storage}GB", f"name={name!r}"]
    if script_id:
        details.append(f"script_id={script_id}")
    if script_args:
        details.append(f"script_args={script_args!r}")
    prompt = f"Create instance ({', '.join(details)})?"
    if not render.confirm(prompt, skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Creating instance — this may take a few seconds..."):
        inst = client.instances.create(
            gpu_type=gpu,
            num_gpus=num_gpus,
            template=template,
            storage=storage,
            name=name,
            script_id=script_id,
            script_args=script_args,
        )

    if state.json_output:
        render.print_json(inst)
        return

    render.success(f"Instance {inst.machine_id} is Running.")
    render.instance_detail(inst, client.account.currency())


@instance_app.command("rename")
def instance_rename(
    machine_id: int = typer.Argument(..., help="Instance ID to rename."),
    name: str = typer.Option(..., "--name", "-n", help="New instance name."),
) -> None:
    """Rename an instance."""
    if not render.confirm(f"Rename instance {machine_id} to {name!r}?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Renaming instance..."):
        client.instances.rename(machine_id, name)

    if state.json_output:
        render.print_json({"success": True, "machine_id": machine_id, "name": name})
        return

    render.success(f"Instance {machine_id} renamed to {name!r}.")


@instance_app.command("pause")
def instance_pause(
    machine_id: int = typer.Argument(..., help="Instance ID to pause."),
) -> None:
    """Pause a running instance."""
    if not render.confirm(f"Pause instance {machine_id}?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Pausing instance..."):
        client.instances.pause(machine_id)

    if state.json_output:
        render.print_json({"success": True, "machine_id": machine_id})
        return

    render.success(f"Instance {machine_id} paused.")


@instance_app.command("resume")
def instance_resume(
    machine_id: int = typer.Argument(..., help="Instance ID to resume."),
    gpu: str | None = typer.Option(None, "--gpu", "-g", help="Resume with a different GPU type."),
    num_gpus: int | None = typer.Option(None, "--num-gpus", help="Change number of GPUs."),
    storage: int | None = typer.Option(None, "--storage", "-s", help="Expand storage (GB). Can only increase."),
    name: str | None = typer.Option(None, "--name", "-n", help="Rename instance."),
    script_id: str | None = typer.Option(None, "--script-id", help="Startup script ID to use on resume."),
    script_args: str | None = typer.Option(None, "--script-args", help="Arguments passed to startup script."),
) -> None:
    """Resume a paused instance. Optionally swap GPU, expand storage, or rename."""
    changes: list[str] = []
    if gpu:
        changes.append(f"gpu={gpu}")
    if num_gpus is not None:
        changes.append(f"num_gpus={num_gpus}")
    if storage is not None:
        changes.append(f"storage={storage}GB")
    if name is not None:
        changes.append(f"name={name!r}")
    if script_id is not None:
        changes.append(f"script_id={script_id}")
    if script_args is not None:
        changes.append(f"script_args={script_args!r}")

    details = ", ".join(changes) if changes else "current configuration"
    if not render.confirm(f"Resume instance {machine_id} with {details}?", skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Resuming instance..."):
        inst = client.instances.resume(
            machine_id,
            gpu_type=gpu,
            num_gpus=num_gpus,
            storage=storage,
            name=name,
            script_id=script_id,
            script_args=script_args,
        )

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
        raise typer.Exit()

    client = get_client()
    with render.spinner("Destroying instance..."):
        client.instances.destroy(machine_id)

    if state.json_output:
        render.print_json({"success": True, "machine_id": machine_id})
        return

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

    if print_command:
        render.stdout_console.print(inst.ssh_command)
        return

    if state.json_output:
        render.print_json({"ssh_command": inst.ssh_command})
        return

    import shlex

    parts = shlex.split(inst.ssh_command)
    if not parts or parts[0] != "ssh":
        render.die(f"Cannot parse SSH command: {inst.ssh_command}")

    render.info(f"Connecting to {machine_id}...")
    raise SystemExit(subprocess.call(parts))
