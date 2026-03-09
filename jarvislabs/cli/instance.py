"""Instance commands for lifecycle, SSH, exec, and file transfer."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import typer

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client
from jarvislabs.exceptions import SSHError, ValidationError
from jarvislabs.ssh import build_remote_shell_command, build_scp_command, harden_ssh_parts, split_ssh_command

if TYPE_CHECKING:
    from jarvislabs.models import Instance

instance_app = typer.Typer(name="instance", help="Manage GPU instances.")
app.add_typer(instance_app, rich_help_panel="Infrastructure")


def _resolve_ssh(machine_id: int) -> tuple[Instance, list[str]]:
    client = get_client()
    with render.spinner("Fetching instance..."):
        inst = client.instances.get(machine_id)

    if inst.status != "Running":
        if inst.status == "Paused":
            render.die(f"Instance {machine_id} is paused. Resume it first: jl instance resume {machine_id}")
        if inst.status in {"Creating", "Resuming"}:
            render.die(f"Instance {machine_id} is not ready yet (status: {inst.status}). Wait for it to reach Running.")
        render.die(f"Instance {machine_id} is not available for SSH (status: {inst.status}).")

    if not inst.ssh_command:
        render.die(f"Instance {machine_id} has no SSH command (status: {inst.status}).")

    try:
        return inst, harden_ssh_parts(split_ssh_command(inst.ssh_command))
    except SSHError:
        render.die(f"Cannot parse SSH command: {inst.ssh_command}")


def _default_upload_dest(source: Path) -> str:
    name = source.name or source.resolve().name
    return f"/home/{name}"


def _default_download_dest(source: str) -> str:
    cleaned = source.rstrip("/")
    name = PurePosixPath(cleaned).name
    if not name:
        raise ValueError(f"Cannot infer a local destination from remote path: {source}")
    return name


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
    fs_id: int | None = typer.Option(None, "--fs-id", help="Filesystem ID to attach."),
) -> None:
    """Create a new GPU instance."""
    details = [f"gpu={num_gpus}x {gpu}", f"template={template}", f"storage={storage}GB", f"name={name!r}"]
    if script_id:
        details.append(f"script_id={script_id}")
    if script_args:
        details.append(f"script_args={script_args!r}")
    if fs_id is not None:
        details.append(f"fs_id={fs_id}")
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
            fs_id=fs_id,
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
    client = get_client()
    with render.spinner("Checking instance..."):
        client.instances.get(machine_id)

    if not render.confirm(f"Pause instance {machine_id}?", skip=state.yes):
        raise typer.Exit()

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
    fs_id: int | None = typer.Option(None, "--fs-id", help="Filesystem ID to attach."),
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
    if fs_id is not None:
        changes.append(f"fs_id={fs_id}")

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
            fs_id=fs_id,
        )

    if inst.machine_id != machine_id:
        render.warning(f"Instance ID changed: {machine_id} → {inst.machine_id}")

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
    client = get_client()
    with render.spinner("Checking instance..."):
        client.instances.get(machine_id)

    if not render.confirm(
        f"Destroy instance {machine_id}? This cannot be undone.",
        skip=state.yes,
    ):
        raise typer.Exit()

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

    if inst.status != "Running":
        if inst.status == "Paused":
            render.die(f"Instance {machine_id} is paused. Resume it first: jl instance resume {machine_id}")
        if inst.status in {"Creating", "Resuming"}:
            render.die(f"Instance {machine_id} is not ready yet (status: {inst.status}). Wait for it to reach Running.")
        render.die(f"Instance {machine_id} is not available for SSH (status: {inst.status}).")

    try:
        parts = harden_ssh_parts(split_ssh_command(inst.ssh_command))
    except SSHError:
        render.die(f"Cannot parse SSH command: {inst.ssh_command}")

    render.info(f"Connecting to {machine_id}...")
    raise SystemExit(subprocess.call(parts))


@instance_app.command(
    "exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def instance_exec(
    ctx: typer.Context,
    machine_id: int = typer.Argument(..., help="Instance ID."),
) -> None:
    """Execute a command on a running instance."""
    if not ctx.args:
        render.die(f"No command specified. Use -- to separate: jl instance exec {machine_id} -- <command>")

    _, parts = _resolve_ssh(machine_id)
    try:
        remote_command = build_remote_shell_command(ctx.args)
    except (SSHError, ValidationError):
        render.die(f"Cannot prepare SSH command for instance {machine_id}.")

    command_label = shlex.join(ctx.args)
    parts.append(remote_command)

    if state.json_output:
        completed = subprocess.run(parts, capture_output=True, text=True, check=False)
        render.print_json(
            {
                "machine_id": machine_id,
                "command": command_label,
                "exit_code": completed.returncode,
                "stdout": getattr(completed, "stdout", ""),
                "stderr": getattr(completed, "stderr", ""),
            }
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        return

    render.info(f"Executing on {machine_id}: {command_label}")
    raise SystemExit(subprocess.call(parts))


@instance_app.command("upload")
def instance_upload(
    machine_id: int = typer.Argument(..., help="Instance ID."),
    source: Path = typer.Argument(
        ..., exists=True, readable=True, resolve_path=True, help="Local file or directory to upload."
    ),
    dest: str | None = typer.Argument(None, help="Remote destination path. Defaults to /home/<name>."),
) -> None:
    """Upload a local file or directory to a running instance."""
    inst, ssh_parts = _resolve_ssh(machine_id)
    remote_dest = dest or _default_upload_dest(source)
    recursive = source.is_dir()

    if dest is not None:
        if recursive:
            remote_prep = remote_dest.rstrip("/") or remote_dest
        else:
            remote_prep = PurePosixPath(remote_dest).parent.as_posix()
        prep_command = build_remote_shell_command(["mkdir", "-p", remote_prep])
        if subprocess.call([*ssh_parts, prep_command]) != 0:
            render.die(f"Failed to prepare remote destination {remote_prep}.")

    try:
        parts = build_scp_command(
            inst.ssh_command,
            source=str(source),
            dest=remote_dest,
            upload=True,
            recursive=recursive,
        )
    except SSHError:
        render.die(f"Cannot prepare upload command for instance {machine_id}.")

    if state.json_output:
        completed = subprocess.run(parts, capture_output=True, text=True, check=False)
        render.print_json(
            {
                "machine_id": machine_id,
                "direction": "upload",
                "source": str(source),
                "dest": remote_dest,
                "recursive": recursive,
                "exit_code": completed.returncode,
            }
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        return

    render.info(f"Uploading to {machine_id}: {source} -> {remote_dest}")
    raise SystemExit(subprocess.call(parts))


@instance_app.command("download")
def instance_download(
    machine_id: int = typer.Argument(..., help="Instance ID."),
    source: str = typer.Argument(..., help="Remote file or directory to download."),
    dest: Path | None = typer.Argument(None, resolve_path=True, help="Local destination path. Defaults to ./<name>."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Download directories recursively."),
) -> None:
    """Download a remote file or directory from a running instance."""
    inst, _ = _resolve_ssh(machine_id)

    try:
        local_dest = dest or Path(_default_download_dest(source))
    except ValueError as exc:
        render.die(str(exc))

    try:
        parts = build_scp_command(
            inst.ssh_command,
            source=source,
            dest=str(local_dest),
            upload=False,
            recursive=recursive,
        )
    except SSHError:
        render.die(f"Cannot prepare download command for instance {machine_id}.")

    if state.json_output:
        completed = subprocess.run(parts, capture_output=True, text=True, check=False)
        render.print_json(
            {
                "machine_id": machine_id,
                "direction": "download",
                "source": source,
                "dest": str(local_dest),
                "recursive": recursive,
                "exit_code": completed.returncode,
            }
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        return

    render.info(f"Downloading from {machine_id}: {source} -> {local_dest}")
    raise SystemExit(subprocess.call(parts))
