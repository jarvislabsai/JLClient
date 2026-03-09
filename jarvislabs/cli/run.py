"""Managed run commands built on top of instance SSH primitives."""

from __future__ import annotations

import json
import secrets
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import typer
from typer.core import TyperGroup

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client
from jarvislabs.cli.instance import _default_upload_dest, _resolve_ssh
from jarvislabs.exceptions import JarvislabsError, SSHError
from jarvislabs.ssh import build_rsync_upload_command, build_scp_command, harden_ssh_parts, split_ssh_command

if TYPE_CHECKING:
    from jarvislabs.models import Instance

_REMOTE_RUNS_ROOT = PurePosixPath("/home/.jl/runs")
_LOCAL_RUNS_ROOT = Path.home() / ".jl" / "runs"


class RunCommandGroup(TyperGroup):
    def resolve_command(self, ctx, args):  # type: ignore[override]
        default_command = "start"
        if not args or args[0] not in self.commands:
            cmd = self.get_command(ctx, default_command)
            return default_command, cmd, args
        return super().resolve_command(ctx, args)


run_app = typer.Typer(
    name="run",
    cls=RunCommandGroup,
    help="Start and inspect managed runs.",
    rich_markup_mode="rich",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
app.add_typer(run_app, rich_help_panel="Workloads")


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_id: str
    remote_run_dir: str
    remote_log: str
    remote_pid: str
    remote_exit_code: str
    remote_meta: str
    remote_wrapper: str


@dataclass(frozen=True, slots=True)
class RunSpec:
    target_kind: str
    local_target: Path | None
    remote_target: str | None
    working_dir: str | None
    launch_command: str


@dataclass(frozen=True, slots=True)
class LocalRunRecord:
    run_id: str
    machine_id: int
    target_kind: str
    local_target: str | None
    remote_target: str | None
    working_dir: str | None
    remote_run_dir: str
    remote_log: str
    remote_pid: str
    remote_exit_code: str
    remote_meta: str
    remote_wrapper: str
    launch_command: str
    started_at: str
    instance_origin: str = "existing"
    lifecycle_policy: str = "none"


@dataclass(frozen=True, slots=True)
class RunStatusSnapshot:
    run_id: str
    machine_id: int
    target_kind: str
    started_at: str
    state: str
    instance_status: str | None
    exit_code: int | None
    remote_log: str
    lifecycle_policy: str


def _make_run_id() -> str:
    return f"r_{secrets.token_hex(4)}"


def _build_run_paths(run_id: str) -> RunPaths:
    remote_run_dir = _REMOTE_RUNS_ROOT / run_id
    return RunPaths(
        run_id=run_id,
        remote_run_dir=remote_run_dir.as_posix(),
        remote_log=(remote_run_dir / "output.log").as_posix(),
        remote_pid=(remote_run_dir / "pid").as_posix(),
        remote_exit_code=(remote_run_dir / "exit_code").as_posix(),
        remote_meta=(remote_run_dir / "meta.json").as_posix(),
        remote_wrapper=(remote_run_dir / "run.sh").as_posix(),
    )


def _local_run_file(run_id: str) -> Path:
    return _LOCAL_RUNS_ROOT / f"{run_id}.json"


def _save_local_run(record: LocalRunRecord) -> None:
    _LOCAL_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    _local_run_file(record.run_id).write_text(json.dumps(asdict(record), indent=2) + "\n")


def _load_local_run(run_id: str) -> LocalRunRecord:
    try:
        payload = json.loads(_local_run_file(run_id).read_text())
    except FileNotFoundError:
        render.die(f"Run {run_id} was not found locally. Use jl run list to see saved runs.")
    except json.JSONDecodeError:
        render.die(f"Run {run_id} has a corrupted local record.")

    try:
        return LocalRunRecord(**payload)
    except TypeError:
        render.die(f"Run {run_id} has an incompatible local record.")


def _iter_local_runs() -> list[LocalRunRecord]:
    if not _LOCAL_RUNS_ROOT.exists():
        return []

    records: list[LocalRunRecord] = []
    for path in _LOCAL_RUNS_ROOT.glob("*.json"):
        try:
            payload = json.loads(path.read_text())
            records.append(LocalRunRecord(**payload))
        except (json.JSONDecodeError, TypeError):
            render.warning(f"Skipping invalid run record: {path.name}")

    records.sort(key=lambda record: record.started_at, reverse=True)
    return records


def _build_remote_command(script: str) -> str:
    return f"sh -lc {shlex.quote(script)}"


def _run_remote(ssh_parts: list[str], script: str) -> int:
    return subprocess.call([*ssh_parts, _build_remote_command(script)])


def _run_remote_capture(ssh_parts: list[str], script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ssh_parts, _build_remote_command(script)],
        capture_output=True,
        text=True,
        check=False,
    )


def _upload_to_remote(ssh_command: str, source: Path, dest: str, *, recursive: bool) -> int:
    parts = build_scp_command(
        ssh_command,
        source=str(source),
        dest=dest,
        upload=True,
        recursive=recursive,
    )
    return subprocess.call(parts)


def _sync_directory_to_remote(ssh_command: str, source: Path, dest: str) -> int:
    parts = build_rsync_upload_command(ssh_command, source=str(source), dest=dest)
    return subprocess.call(parts)


def _write_remote_text(ssh_parts: list[str], destination: str, content: str) -> int:
    remote_parent = PurePosixPath(destination).parent.as_posix()
    script = f"mkdir -p {shlex.quote(remote_parent)} && cat > {shlex.quote(destination)}"
    parts = [*ssh_parts, _build_remote_command(script)]
    completed = subprocess.run(parts, input=content, text=True, check=False)
    return completed.returncode


def _get_instance(machine_id: int) -> Instance | None:
    client = get_client()
    try:
        return client.instances.get(machine_id)
    except JarvislabsError:
        return None


def _ssh_parts_from_instance(inst: Instance) -> list[str] | None:
    if not inst.ssh_command:
        return None
    try:
        return harden_ssh_parts(split_ssh_command(inst.ssh_command))
    except SSHError:
        return None


def _fetch_exit_code_path(ssh_parts: list[str], remote_exit_code: str) -> int | None:
    completed = _run_remote_capture(ssh_parts, f"cat {shlex.quote(remote_exit_code)}")
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def _snapshot(
    record: LocalRunRecord,
    *,
    state: str,
    instance_status: str | None = None,
    exit_code: int | None = None,
) -> RunStatusSnapshot:
    return RunStatusSnapshot(
        run_id=record.run_id,
        machine_id=record.machine_id,
        target_kind=record.target_kind,
        started_at=record.started_at,
        state=state,
        instance_status=instance_status,
        exit_code=exit_code,
        remote_log=record.remote_log,
        lifecycle_policy=record.lifecycle_policy,
    )


def _get_run_snapshot(record: LocalRunRecord) -> RunStatusSnapshot:
    inst = _get_instance(record.machine_id)
    if inst is None:
        return _snapshot(record, state="instance-missing")

    if inst.status == "Paused":
        return _snapshot(record, state="instance-paused", instance_status=inst.status)

    if inst.status != "Running":
        return _snapshot(record, state=f"instance-{inst.status.lower()}", instance_status=inst.status)

    ssh_parts = _ssh_parts_from_instance(inst)
    if ssh_parts is None:
        return _snapshot(record, state="unknown", instance_status=inst.status)

    exit_code = _fetch_exit_code_path(ssh_parts, record.remote_exit_code)
    if exit_code is None:
        state_name = "running"
    elif exit_code == 0:
        state_name = "succeeded"
    else:
        state_name = "failed"

    return _snapshot(record, state=state_name, instance_status=inst.status, exit_code=exit_code)


def _resolve_run_ssh(run_id: str) -> tuple[LocalRunRecord, list[str]]:
    record = _load_local_run(run_id)
    inst = _get_instance(record.machine_id)
    if inst is None:
        render.die(f"Run {run_id} belongs to instance {record.machine_id}, but that instance no longer exists.")

    if inst.status == "Paused":
        render.die(
            f"Run {run_id} belongs to paused instance {record.machine_id}. Resume it first: jl instance resume {record.machine_id}"
        )

    if inst.status != "Running":
        render.die(f"Run {run_id} is on instance {record.machine_id}, which is currently {inst.status}.")

    ssh_parts = _ssh_parts_from_instance(inst)
    if ssh_parts is None:
        render.die(f"Run {run_id} is on instance {record.machine_id}, but SSH is not available.")
    return record, ssh_parts


def _build_run_spec(target: str | None, extra_args: list[str], *, script_path: str | None = None) -> RunSpec:
    if target is None:
        if script_path is not None:
            render.die("--script can only be used with a directory target.")
        if not extra_args:
            render.die(
                "No target or remote command provided. Use jl run <file|folder> --on <id> or jl run --on <id> -- <command>."
            )
        return RunSpec(
            target_kind="command",
            local_target=None,
            remote_target=None,
            working_dir=None,
            launch_command=shlex.join(extra_args),
        )

    local_target = Path(target).expanduser()
    if not local_target.exists():
        looks_like_path = "/" in target or target.startswith(".") or target.endswith(".py")
        if looks_like_path:
            render.die(f"Target does not exist: {target}")
        if script_path is not None:
            render.die("--script can only be used with a directory target.")
        return RunSpec(
            target_kind="command",
            local_target=None,
            remote_target=None,
            working_dir=None,
            launch_command=shlex.join([target, *extra_args]),
        )

    if local_target.is_dir():
        remote_target = _default_upload_dest(local_target)
        if script_path:
            launch_command = shlex.join(["python", script_path, *extra_args])
        elif extra_args:
            launch_command = shlex.join(extra_args)
        else:
            render.die(
                "Directory targets require --script <path> or a command after --. "
                "Example: jl run . --script train.py --gpu RTX5000"
            )
        return RunSpec(
            target_kind="directory",
            local_target=local_target,
            remote_target=remote_target,
            working_dir=remote_target,
            launch_command=launch_command,
        )

    if script_path is not None:
        render.die("--script can only be used with a directory target.")

    if local_target.suffix != ".py":
        render.die(
            "Only Python file targets are supported directly right now. "
            "Use a directory target or the instance upload/exec primitives for other files."
        )

    remote_target = _default_upload_dest(local_target)
    return RunSpec(
        target_kind="file",
        local_target=local_target,
        remote_target=remote_target,
        working_dir=PurePosixPath(remote_target).parent.as_posix(),
        launch_command=shlex.join(["python", remote_target, *extra_args]),
    )


def _parse_run_inputs(raw_args: list[str]) -> tuple[str | None, list[str]]:
    if not raw_args:
        return None, []

    candidate = raw_args[0]
    looks_like_path = "/" in candidate or candidate.startswith(".") or candidate.endswith(".py")
    if looks_like_path or Path(candidate).expanduser().exists():
        return candidate, raw_args[1:]
    return None, raw_args


def _write_remote_metadata(ssh_parts: list[str], inst, paths: RunPaths, spec: RunSpec) -> None:
    payload = {
        "run_id": paths.run_id,
        "machine_id": inst.machine_id,
        "target_kind": spec.target_kind,
        "remote_target": spec.remote_target,
        "working_dir": spec.working_dir,
        "launch_command": spec.launch_command,
        "started_at": datetime.now(UTC).isoformat(),
    }
    status = _write_remote_text(ssh_parts, paths.remote_meta, json.dumps(payload, indent=2) + "\n")
    if status != 0:
        render.die(f"Failed to upload metadata for run {paths.run_id}.")


def _write_remote_wrapper(ssh_parts: list[str], paths: RunPaths, spec: RunSpec) -> None:
    wrapper = f"""#!/bin/sh
set -u

RUN_DIR={shlex.quote(paths.remote_run_dir)}
LOG_FILE={shlex.quote(paths.remote_log)}
PID_FILE={shlex.quote(paths.remote_pid)}
EXIT_FILE={shlex.quote(paths.remote_exit_code)}
COMMAND={shlex.quote(spec.launch_command)}
WORKDIR={shlex.quote(spec.working_dir or "/home")}

rm -f "$EXIT_FILE"
touch "$LOG_FILE"
export PYTHONUNBUFFERED=1

child_pid=""
cleanup() {{
  if [ -n "$child_pid" ]; then
    kill "$child_pid" 2>/dev/null || true
  fi
}}

trap cleanup INT TERM

sh -lc "cd $WORKDIR && exec $COMMAND" >>"$LOG_FILE" 2>&1 &
child_pid=$!
printf '%s\\n' "$child_pid" >"$PID_FILE"
wait "$child_pid"
status=$?
printf '%s\\n' "$status" >"$EXIT_FILE"
exit 0
"""
    status = _write_remote_text(ssh_parts, paths.remote_wrapper, wrapper)
    if status != 0:
        render.die(f"Failed to upload wrapper script for run {paths.run_id}.")


def _ensure_remote_rsync(machine_id: int, ssh_parts: list[str]) -> None:
    if shutil.which("rsync") is None:
        render.die("rsync is required locally for directory runs. Install rsync and try again.")

    script = (
        "command -v rsync >/dev/null 2>&1 || "
        "(apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y rsync)"
    )
    with render.spinner(f"Ensuring rsync is installed on instance {machine_id}..."):
        completed = _run_remote_capture(ssh_parts, script)

    if completed.returncode != 0:
        render.die(f"Could not install rsync on instance {machine_id}.")


def _prepare_remote_target(inst, ssh_parts: list[str], spec: RunSpec) -> None:
    if spec.local_target is None or spec.remote_target is None:
        return

    if spec.target_kind == "directory":
        _ensure_remote_rsync(inst.machine_id, ssh_parts)
        if _run_remote(ssh_parts, f"mkdir -p {shlex.quote(spec.remote_target)}") != 0:
            render.die(f"Failed to prepare remote directory {spec.remote_target}.")

        render.info(f"Syncing project to {spec.remote_target}")
        if _sync_directory_to_remote(inst.ssh_command, spec.local_target, spec.remote_target) != 0:
            render.die(f"Failed to sync {spec.local_target} to instance {inst.machine_id}.")
        return

    render.info(f"Uploading file to {spec.remote_target}")
    if _upload_to_remote(inst.ssh_command, spec.local_target, spec.remote_target, recursive=False) != 0:
        render.die(f"Failed to upload {spec.local_target} to instance {inst.machine_id}.")


def _start_remote_run(ssh_parts: list[str], paths: RunPaths) -> int:
    script = (
        f"mkdir -p {shlex.quote(paths.remote_run_dir)} && "
        f"chmod +x {shlex.quote(paths.remote_wrapper)} && "
        f"nohup sh {shlex.quote(paths.remote_wrapper)} </dev/null >/dev/null 2>&1 & "
        "echo $!"
    )
    completed = _run_remote_capture(ssh_parts, script)
    if completed.returncode != 0:
        render.die(f"Failed to start run {paths.run_id}.")

    output = completed.stdout.strip()
    if not output.isdigit():
        render.die(f"Could not determine launcher pid for run {paths.run_id}.")
    return int(output)


def _follow_run_logs(ssh_parts: list[str], paths: RunPaths) -> bool:
    script = (
        f"touch {shlex.quote(paths.remote_log)}; "
        f"tail -n +1 -F {shlex.quote(paths.remote_log)} & "
        "tail_pid=$!; "
        f"while [ ! -f {shlex.quote(paths.remote_exit_code)} ]; do sleep 1; done; "
        "sleep 1; "
        "kill $tail_pid >/dev/null 2>&1 || true; "
        "wait $tail_pid 2>/dev/null || true"
    )
    parts = [*ssh_parts, _build_remote_command(script)]
    try:
        return subprocess.call(parts) == 0
    except KeyboardInterrupt:
        return False


def _tail_remote_log(
    ssh_parts: list[str],
    remote_log: str,
    *,
    follow: bool,
    tail: int | None,
) -> subprocess.CompletedProcess[str] | int:
    if follow:
        if tail is None:
            script = f"touch {shlex.quote(remote_log)} && tail -n +1 -F {shlex.quote(remote_log)}"
        else:
            script = f"touch {shlex.quote(remote_log)} && tail -n {tail} -F {shlex.quote(remote_log)}"
        return _run_remote(ssh_parts, script)

    if not state.json_output:
        if tail is not None:
            return _run_remote(ssh_parts, f"tail -n {tail} {shlex.quote(remote_log)}")
        return _run_remote(ssh_parts, f"cat {shlex.quote(remote_log)}")

    if tail is not None:
        return _run_remote_capture(ssh_parts, f"tail -n {tail} {shlex.quote(remote_log)}")
    return _run_remote_capture(ssh_parts, f"cat {shlex.quote(remote_log)}")


def _print_run_followups(run_id: str, *, include_list: bool = False) -> None:
    render.info(f"Run ID: {run_id}")
    render.console.print(f"  [dim]Logs[/dim]    [bold cyan]jl run logs {run_id} --follow[/bold cyan]")
    render.console.print(f"  [dim]Status[/dim]  [bold magenta]jl run status {run_id}[/bold magenta]")
    render.console.print(f"  [dim]Stop[/dim]    [bold red]jl run stop {run_id}[/bold red]")
    if include_list:
        render.console.print("  [dim]All runs[/dim] [bold green]jl run list[/bold green]")


def _stop_remote_run(ssh_parts: list[str], pid_file: str) -> str:
    script = (
        f"if [ ! -f {shlex.quote(pid_file)} ]; then echo missing-pid; exit 3; fi; "
        f"pid=$(cat {shlex.quote(pid_file)}); "
        'if [ -z "$pid" ]; then echo missing-pid; exit 3; fi; '
        'if kill -0 "$pid" 2>/dev/null; then kill "$pid" && echo stopped && exit 0; fi; '
        "echo not-running; exit 4"
    )
    completed = _run_remote_capture(ssh_parts, script)
    output = completed.stdout.strip()
    if output in {"stopped", "not-running", "missing-pid"}:
        return output
    return "error"


def _print_detach_instructions(machine_id: int, paths: RunPaths) -> None:
    render.warning(f"Detached from logs. Run {paths.run_id} is still running on instance {machine_id}.")
    _print_run_followups(paths.run_id, include_list=True)


def _start_managed_run(
    target: str | None,
    machine_id: int,
    extra_args: list[str],
    *,
    follow: bool,
    instance_origin: str,
    lifecycle_policy: str,
    script_path: str | None = None,
) -> tuple[str, int | None]:
    inst, ssh_parts = _resolve_ssh(machine_id)
    spec = _build_run_spec(target, extra_args, script_path=script_path)
    paths = _build_run_paths(_make_run_id())

    render.info(f"Preparing run {paths.run_id} on instance {machine_id}")
    if _run_remote(ssh_parts, f"mkdir -p {shlex.quote(paths.remote_run_dir)}") != 0:
        render.die(f"Failed to create remote run directory {paths.remote_run_dir}.")

    _prepare_remote_target(inst, ssh_parts, spec)
    _write_remote_wrapper(ssh_parts, paths, spec)
    _write_remote_metadata(ssh_parts, inst, paths, spec)

    launcher_pid = _start_remote_run(ssh_parts, paths)
    record = LocalRunRecord(
        run_id=paths.run_id,
        machine_id=machine_id,
        target_kind=spec.target_kind,
        local_target=str(spec.local_target) if spec.local_target else None,
        remote_target=spec.remote_target,
        working_dir=spec.working_dir,
        remote_run_dir=paths.remote_run_dir,
        remote_log=paths.remote_log,
        remote_pid=paths.remote_pid,
        remote_exit_code=paths.remote_exit_code,
        remote_meta=paths.remote_meta,
        remote_wrapper=paths.remote_wrapper,
        launch_command=spec.launch_command,
        started_at=datetime.now(UTC).isoformat(),
        instance_origin=instance_origin,
        lifecycle_policy=lifecycle_policy,
    )
    _save_local_run(record)

    summary = {
        "run_id": paths.run_id,
        "machine_id": machine_id,
        "launcher_pid": launcher_pid,
        "remote_log": paths.remote_log,
        "remote_exit_code": paths.remote_exit_code,
        "target_kind": spec.target_kind,
        "remote_target": spec.remote_target,
        "command": spec.launch_command,
        "instance_origin": instance_origin,
        "lifecycle_policy": lifecycle_policy,
    }

    if state.json_output:
        render.print_json(summary)
        return paths.run_id, None

    render.success(f"Started run {paths.run_id} on instance {machine_id} (launcher pid {launcher_pid}).")
    _print_run_followups(paths.run_id)

    if not follow:
        render.info("Run started in the background. It will keep going if you disconnect.")
        return paths.run_id, None

    render.info("Streaming logs. Press Ctrl+C to detach; the run will keep going.")

    followed_to_completion = _follow_run_logs(ssh_parts, paths)
    if not followed_to_completion:
        _print_detach_instructions(machine_id, paths)
        return paths.run_id, None

    exit_code = _fetch_exit_code_path(ssh_parts, paths.remote_exit_code)
    if exit_code is None:
        render.warning(f"Run {paths.run_id} finished log streaming, but exit status could not be read.")
        _print_run_followups(paths.run_id)
        return paths.run_id, None

    if exit_code == 0:
        render.success(f"Run {paths.run_id} completed successfully.")
    else:
        render.warning(f"Run {paths.run_id} finished with exit code {exit_code}.")

    _print_run_followups(paths.run_id)
    return paths.run_id, exit_code


def _pick_lifecycle_policy(*, pause: bool, destroy: bool, keep: bool) -> str | None:
    selected = [name for name, enabled in (("pause", pause), ("destroy", destroy), ("keep", keep)) if enabled]
    if len(selected) > 1:
        render.die("Choose only one lifecycle option: --pause, --destroy, or --keep.")
    return selected[0] if selected else None


def _apply_lifecycle(machine_id: int, policy: str) -> None:
    if policy == "keep":
        render.info(f"Leaving instance {machine_id} running.")
        return

    client = get_client()
    action = "Pausing" if policy == "pause" else "Destroying"
    with render.spinner(f"{action} instance {machine_id}..."):
        if policy == "pause":
            client.instances.pause(machine_id)
        else:
            client.instances.destroy(machine_id)

    if policy == "pause":
        render.success(f"Instance {machine_id} paused after the run.")
    else:
        render.success(f"Instance {machine_id} destroyed after the run.")


@run_app.command(
    "start",
    hidden=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_start(
    ctx: typer.Context,
    on: int | None = typer.Option(None, "--on", help="Run on an existing instance."),
    gpu: str | None = typer.Option(None, "--gpu", "-g", help="Create a fresh instance with this GPU."),
    script: str | None = typer.Option(
        None,
        "--script",
        help="Python script path inside a directory target. Example: jl run . --script train.py --gpu RTX5000",
    ),
    template: str = typer.Option("pytorch", "--template", "-t", help="Framework template for fresh instances."),
    storage: int = typer.Option(40, "--storage", "-s", help="Storage in GB for fresh instances."),
    name: str = typer.Option("jl-run", "--name", "-n", help="Instance name for fresh runs."),
    num_gpus: int = typer.Option(1, "--num-gpus", help="Number of GPUs for fresh runs."),
    pause: bool = typer.Option(False, "--pause", help="Pause a fresh instance after the run completes."),
    destroy: bool = typer.Option(False, "--destroy", help="Destroy a fresh instance after the run completes."),
    keep: bool = typer.Option(False, "--keep", help="Leave a fresh instance running after the run completes."),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream logs after starting the run."),
) -> None:
    """Start a managed run."""
    target, extra_args = _parse_run_inputs(list(ctx.args))

    lifecycle = _pick_lifecycle_policy(pause=pause, destroy=destroy, keep=keep)

    if on is not None and gpu is not None:
        render.die("Use either --on <instance_id> or --gpu <type>, not both.")

    if on is not None:
        if lifecycle is not None:
            render.die("Lifecycle flags are only for fresh instances. Do not use --pause/--destroy/--keep with --on.")
        _, exit_code = _start_managed_run(
            target,
            on,
            extra_args,
            follow=follow,
            instance_origin="existing",
            lifecycle_policy="none",
            script_path=script,
        )
        if exit_code not in (None, 0):
            raise SystemExit(exit_code)
        return

    if gpu is None:
        render.die("Use --on <instance_id> to run on an existing instance, or --gpu <type> to create a fresh one.")

    if lifecycle is None:
        if not follow:
            render.die("--no-follow requires an explicit lifecycle flag. Use --keep, --pause, or --destroy.")
        lifecycle = "pause"
        render.info("No lifecycle flag provided for the fresh run. Defaulting to --pause.")

    if not follow and lifecycle != "keep":
        render.die("--no-follow is only supported with --keep for fresh runs right now.")

    details = f"Create {num_gpus}x {gpu} instance for jl run (template={template}, storage={storage}GB, name={name!r})?"
    if not render.confirm(details, skip=state.yes):
        raise typer.Exit()

    client = get_client()
    with render.spinner("Creating instance for jl run..."):
        inst = client.instances.create(
            gpu_type=gpu,
            num_gpus=num_gpus,
            template=template,
            storage=storage,
            name=name,
        )

    render.success(f"Fresh instance {inst.machine_id} is ready.")

    try:
        run_id, exit_code = _start_managed_run(
            target,
            inst.machine_id,
            extra_args,
            follow=follow,
            instance_origin="fresh",
            lifecycle_policy=lifecycle,
            script_path=script,
        )
    except SystemExit:
        render.warning(
            f"Run setup failed after creating instance {inst.machine_id}. Manage it manually with jl instance ssh/pause/destroy."
        )
        raise

    if exit_code is None:
        render.warning(
            f"Run {run_id} is detached. Automatic {lifecycle} will not happen unless the CLI is still connected when the run ends."
        )
        render.info(f"Instance {inst.machine_id} is still running.")
        return

    _apply_lifecycle(inst.machine_id, lifecycle)
    if exit_code != 0:
        raise SystemExit(exit_code)


@run_app.command("list")
def run_list(
    refresh: bool = typer.Option(False, "--refresh", help="Refresh live status for each run. Can be slow."),
) -> None:
    """List locally tracked managed runs."""
    records = _iter_local_runs()
    if state.json_output:
        payload = []
        for record in records:
            item = asdict(record)
            item["state"] = _get_run_snapshot(record).state if refresh else "saved"
            payload.append(item)
        render.print_json(payload)
        return

    if not records:
        render.info("No saved runs found.")
        return

    if not refresh:
        render.info("Showing saved runs from this machine. Use --refresh for live status checks.")

    table = render._table("Managed Runs")
    table.add_column("Run ID", style="cyan")
    table.add_column("Machine", style="bold")
    table.add_column("Kind")
    table.add_column("State")
    table.add_column("Lifecycle")
    table.add_column("Started")
    for record in records:
        table.add_row(
            record.run_id,
            str(record.machine_id),
            record.target_kind,
            _get_run_snapshot(record).state if refresh else "saved",
            record.lifecycle_policy,
            record.started_at[:19].replace("T", " "),
        )
    render.stdout_console.print(table)


@run_app.command("status")
def run_status(
    run_id: str = typer.Argument(..., help="Run ID."),
) -> None:
    """Show the current status of a managed run."""
    record = _load_local_run(run_id)
    snapshot = _get_run_snapshot(record)

    if state.json_output:
        render.print_json(asdict(snapshot) | {"launch_command": record.launch_command})
        return

    render.info(f"Run ID: {snapshot.run_id}")
    render.info(f"Machine: {snapshot.machine_id}")
    render.info(f"State: {snapshot.state}")
    render.info(f"Lifecycle: {snapshot.lifecycle_policy}")
    render.info(f"Started: {snapshot.started_at}")
    render.info(f"Command: {record.launch_command}")
    render.info(f"Log file: {snapshot.remote_log}")
    if snapshot.exit_code is not None:
        render.info(f"Exit code: {snapshot.exit_code}")
    elif snapshot.state == "running":
        render.info("This run is still active on the remote machine.")
    _print_run_followups(snapshot.run_id, include_list=True)


@run_app.command("logs")
def run_logs(
    run_id: str = typer.Argument(..., help="Run ID."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow logs in real time."),
    tail: int | None = typer.Option(None, "--tail", "-n", min=1, help="Show only the last N lines."),
) -> None:
    """Show logs for a managed run."""
    if state.json_output and follow:
        render.die("--json is not supported with --follow for jl run logs.")

    record, ssh_parts = _resolve_run_ssh(run_id)
    if state.json_output:
        completed = _tail_remote_log(ssh_parts, record.remote_log, follow=False, tail=tail)
        render.print_json(
            {
                "run_id": record.run_id,
                "machine_id": record.machine_id,
                "remote_log": record.remote_log,
                "content": completed.stdout,
                "exit_code": completed.returncode,
            }
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        return

    if follow:
        render.info(
            f"Following logs for run {record.run_id} on instance {record.machine_id}. Press Ctrl+C to stop streaming."
        )
        _print_run_followups(record.run_id, include_list=True)

    try:
        exit_code = _tail_remote_log(ssh_parts, record.remote_log, follow=follow, tail=tail)
    except KeyboardInterrupt:
        render.info(f"Stopped log streaming for run {record.run_id}.")
        _print_run_followups(record.run_id, include_list=True)
        return
    raise SystemExit(exit_code)


@run_app.command("stop")
def run_stop(
    run_id: str = typer.Argument(..., help="Run ID."),
) -> None:
    """Stop a managed run by sending TERM to its tracked process."""
    record = _load_local_run(run_id)
    snapshot = _get_run_snapshot(record)

    if snapshot.state in {"succeeded", "failed"}:
        message = f"Run {run_id} already finished with state {snapshot.state}."
        if state.json_output:
            render.print_json(
                {
                    "run_id": run_id,
                    "machine_id": record.machine_id,
                    "state": snapshot.state,
                    "stopped": False,
                }
            )
            return
        render.info(message)
        _print_run_followups(run_id)
        return

    if snapshot.state in {"instance-paused", "instance-missing"}:
        message = f"Run {run_id} is not on a running instance ({snapshot.state}). Nothing to stop."
        if state.json_output:
            render.print_json(
                {
                    "run_id": run_id,
                    "machine_id": record.machine_id,
                    "state": snapshot.state,
                    "stopped": False,
                }
            )
            return
        render.warning(message)
        _print_run_followups(run_id)
        return

    resolved_record, ssh_parts = _resolve_run_ssh(run_id)
    stop_status = _stop_remote_run(ssh_parts, resolved_record.remote_pid)

    if state.json_output:
        render.print_json(
            {
                "run_id": resolved_record.run_id,
                "machine_id": resolved_record.machine_id,
                "state": snapshot.state,
                "stop_status": stop_status,
                "stopped": stop_status == "stopped",
            }
        )
        if stop_status == "error":
            raise SystemExit(1)
        return

    if stop_status == "stopped":
        render.success(f"Stop signal sent to run {resolved_record.run_id} on instance {resolved_record.machine_id}.")
        render.info("The process may take a moment to exit cleanly.")
    elif stop_status == "not-running":
        render.warning(f"Run {resolved_record.run_id} no longer has a live process to stop.")
    elif stop_status == "missing-pid":
        render.warning(f"Run {resolved_record.run_id} has no recorded PID on the remote machine.")
    else:
        render.die(f"Could not stop run {resolved_record.run_id}.")

    _print_run_followups(resolved_record.run_id)
