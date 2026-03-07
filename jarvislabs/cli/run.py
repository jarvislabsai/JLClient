"""Managed run commands built on top of instance SSH primitives."""

from __future__ import annotations

import json
import secrets
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import typer

from jarvislabs.cli import render, state
from jarvislabs.cli.app import app, get_client
from jarvislabs.cli.instance import _default_upload_dest, _resolve_ssh
from jarvislabs.ssh import build_scp_command

_REMOTE_RUNS_ROOT = PurePosixPath("/home/.jl/runs")
_LOCAL_RUNS_ROOT = Path.home() / ".jl" / "runs"


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


@dataclass(frozen=True, slots=True)
class ManagedRunResult:
    run_id: str
    machine_id: int
    remote_log: str
    remote_exit_code: str
    exit_code: int | None


def _make_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"r_{stamp}_{secrets.token_hex(3)}"


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


def _build_remote_command(script: str) -> str:
    return f"sh -lc {shlex.quote(script)}"


def _run_remote(
    ssh_parts: list[str], script: str, *, capture_output: bool = False
) -> subprocess.CompletedProcess[str] | int:
    parts = [*ssh_parts, _build_remote_command(script)]
    if capture_output:
        return subprocess.run(parts, capture_output=True, text=True, check=False)
    return subprocess.call(parts)


def _upload_to_remote(ssh_command: str, source: Path, dest: str, *, recursive: bool) -> int:
    parts = build_scp_command(
        ssh_command,
        source=str(source),
        dest=dest,
        upload=True,
        recursive=recursive,
    )
    return subprocess.call(parts)


def _write_remote_text(ssh_parts: list[str], destination: str, content: str) -> int:
    remote_parent = PurePosixPath(destination).parent.as_posix()
    script = f"mkdir -p {shlex.quote(remote_parent)} && cat > {shlex.quote(destination)}"
    parts = [*ssh_parts, _build_remote_command(script)]
    completed = subprocess.run(parts, input=content, text=True, check=False)
    return completed.returncode


def _render_command(parts: list[str]) -> str:
    return shlex.join(parts)


def _build_run_spec(target: str | None, extra_args: list[str]) -> RunSpec:
    if target is None:
        if not extra_args:
            render.die(
                "No target or remote command provided. Use jl run <file|folder> --on <id> or jl run --on <id> -- <command>."
            )
        return RunSpec(
            target_kind="command",
            local_target=None,
            remote_target=None,
            working_dir=None,
            launch_command=_render_command(extra_args),
        )

    local_target = Path(target).expanduser()
    if not local_target.exists():
        looks_like_path = "/" in target or target.startswith(".") or target.endswith(".py")
        if looks_like_path:
            render.die(f"Target does not exist: {target}")
        return RunSpec(
            target_kind="command",
            local_target=None,
            remote_target=None,
            working_dir=None,
            launch_command=_render_command([target, *extra_args]),
        )

    if local_target.is_dir():
        if not extra_args:
            render.die("Directory targets require a command after --. Example: jl run . --on 123 -- python train.py")
        remote_target = _default_upload_dest(local_target)
        return RunSpec(
            target_kind="directory",
            local_target=local_target,
            remote_target=remote_target,
            working_dir=remote_target,
            launch_command=_render_command(extra_args),
        )

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
        launch_command=_render_command(["python", remote_target, *extra_args]),
    )


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


def _prepare_remote_target(inst, ssh_parts: list[str], spec: RunSpec) -> None:
    if spec.local_target is None or spec.remote_target is None:
        return

    if spec.target_kind == "directory":
        remote_parent = PurePosixPath(spec.remote_target).parent.as_posix()
        prep = f"mkdir -p {shlex.quote(remote_parent)} && rm -rf {shlex.quote(spec.remote_target)}"
        if _run_remote(ssh_parts, prep) != 0:
            render.die(f"Failed to prepare remote directory {spec.remote_target}.")

        render.info(f"Uploading project to {spec.remote_target}")
        if _upload_to_remote(inst.ssh_command, spec.local_target, remote_parent, recursive=True) != 0:
            render.die(f"Failed to upload {spec.local_target} to instance {inst.machine_id}.")
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
    completed = _run_remote(ssh_parts, script, capture_output=True)
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


def _fetch_exit_code(ssh_parts: list[str], paths: RunPaths) -> int | None:
    completed = _run_remote(ssh_parts, f"cat {shlex.quote(paths.remote_exit_code)}", capture_output=True)
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def _print_detach_instructions(machine_id: int, paths: RunPaths) -> None:
    render.warning(f"Detached from logs. Run {paths.run_id} is still running on instance {machine_id}.")
    render.info(f"Log file: {paths.remote_log}")
    render.info(f"Manual follow: jl instance exec {machine_id} -- tail -f {paths.remote_log}")
    render.info(
        f"Manual status: jl instance exec {machine_id} -- sh -lc 'cat {paths.remote_exit_code} 2>/dev/null || echo running'"
    )


def _start_managed_run(target: str | None, machine_id: int, extra_args: list[str], *, follow: bool) -> ManagedRunResult:
    inst, ssh_parts = _resolve_ssh(machine_id)
    spec = _build_run_spec(target, extra_args)
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
    }

    if state.json_output:
        render.print_json(summary)
        return ManagedRunResult(
            run_id=paths.run_id,
            machine_id=machine_id,
            remote_log=paths.remote_log,
            remote_exit_code=paths.remote_exit_code,
            exit_code=None,
        )

    render.success(f"Started run {paths.run_id} on instance {machine_id} (launcher pid {launcher_pid}).")

    if not follow:
        render.info("Run started in the background. The workload will keep going if you disconnect.")
        render.info(f"Log file: {paths.remote_log}")
        return ManagedRunResult(
            run_id=paths.run_id,
            machine_id=machine_id,
            remote_log=paths.remote_log,
            remote_exit_code=paths.remote_exit_code,
            exit_code=None,
        )

    render.info("Streaming logs. Press Ctrl+C to detach; the run will keep going.")

    followed_to_completion = _follow_run_logs(ssh_parts, paths)
    if not followed_to_completion:
        _print_detach_instructions(machine_id, paths)
        return ManagedRunResult(
            run_id=paths.run_id,
            machine_id=machine_id,
            remote_log=paths.remote_log,
            remote_exit_code=paths.remote_exit_code,
            exit_code=None,
        )

    exit_code = _fetch_exit_code(ssh_parts, paths)
    if exit_code is None:
        render.warning(f"Run {paths.run_id} finished log streaming, but exit status could not be read.")
        return ManagedRunResult(
            run_id=paths.run_id,
            machine_id=machine_id,
            remote_log=paths.remote_log,
            remote_exit_code=paths.remote_exit_code,
            exit_code=None,
        )

    if exit_code == 0:
        render.success(f"Run {paths.run_id} completed successfully.")
    else:
        render.warning(f"Run {paths.run_id} finished with exit code {exit_code}.")

    return ManagedRunResult(
        run_id=paths.run_id,
        machine_id=machine_id,
        remote_log=paths.remote_log,
        remote_exit_code=paths.remote_exit_code,
        exit_code=exit_code,
    )


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


@app.command(
    "run",
    rich_help_panel="Workloads",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_start(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Python file or directory to run."),
    on: int | None = typer.Option(None, "--on", help="Run on an existing instance."),
    gpu: str | None = typer.Option(None, "--gpu", "-g", help="Create a fresh instance with this GPU."),
    template: str = typer.Option("pytorch", "--template", "-t", help="Framework template for fresh instances."),
    storage: int = typer.Option(40, "--storage", "-s", help="Storage in GB for fresh instances."),
    name: str = typer.Option("jl-run", "--name", "-n", help="Instance name for fresh runs."),
    num_gpus: int = typer.Option(1, "--num-gpus", help="Number of GPUs for fresh runs."),
    pause: bool = typer.Option(False, "--pause", help="Pause a fresh instance after the run completes."),
    destroy: bool = typer.Option(False, "--destroy", help="Destroy a fresh instance after the run completes."),
    keep: bool = typer.Option(False, "--keep", help="Leave a fresh instance running after the run completes."),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream logs after starting the run."),
) -> None:
    """Start a managed run on an existing instance."""
    lifecycle = _pick_lifecycle_policy(pause=pause, destroy=destroy, keep=keep)

    if on is not None and gpu is not None:
        render.die("Use either --on <instance_id> or --gpu <type>, not both.")

    if on is not None:
        if lifecycle is not None:
            render.die("Lifecycle flags are only for fresh instances. Do not use --pause/--destroy/--keep with --on.")
        result = _start_managed_run(target, on, list(ctx.args), follow=follow)
        if result.exit_code not in (None, 0):
            raise SystemExit(result.exit_code)
        return

    if gpu is None:
        render.die("Use --on <instance_id> to run on an existing instance, or --gpu <type> to create a fresh one.")

    if lifecycle is None:
        render.die("Fresh runs must choose a lifecycle policy: --pause, --destroy, or --keep.")

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
        result = _start_managed_run(target, inst.machine_id, list(ctx.args), follow=follow)
    except SystemExit:
        render.warning(
            f"Run setup failed after creating instance {inst.machine_id}. Manage it manually with jl instance ssh/pause/destroy."
        )
        raise

    if result.exit_code is None:
        render.warning(
            f"Run {result.run_id} is detached. Automatic {lifecycle} will not happen unless the CLI is still connected when the run ends."
        )
        render.info(f"Instance {inst.machine_id} is still running.")
        return

    _apply_lifecycle(inst.machine_id, lifecycle)
    if result.exit_code != 0:
        raise SystemExit(result.exit_code)
