from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jarvislabs.cli import run, state


def test_run_start_requires_existing_instance_for_now(monkeypatch):
    captured: dict[str, str] = {}

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    monkeypatch.setattr(run.render, "die", fake_die)

    with pytest.raises(SystemExit) as exc:
        run.run_start(
            SimpleNamespace(args=["train.py"]),
            on=None,
            gpu=None,
            pause=False,
            destroy=False,
            keep=False,
            follow=True,
        )

    assert exc.value.code == 1
    assert (
        captured["message"]
        == "Use --on <instance_id> to run on an existing instance, or --gpu <type> to create a fresh one."
    )


def test_build_run_spec_for_python_file(monkeypatch, tmp_path):
    source = tmp_path / "train.py"
    source.write_text("print('hi')\n")

    spec = run._build_run_spec(str(source), ["--epochs", "5"])

    assert spec.target_kind == "file"
    assert spec.local_target == source
    assert spec.remote_target == f"/home/{source.name}"
    assert spec.working_dir == "/home"
    assert spec.launch_command == f"python /home/{source.name} --epochs 5"


def test_build_run_spec_for_directory_uses_stable_home_path(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_text("print('hi')\n")

    spec = run._build_run_spec(str(project), ["python", "train.py"])

    assert spec.target_kind == "directory"
    assert spec.local_target == project
    assert spec.remote_target == "/home/project"
    assert spec.working_dir == "/home/project"
    assert spec.launch_command == "python train.py"


def test_build_run_spec_for_directory_with_script_option(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_text("print('hi')\n")

    spec = run._build_run_spec(str(project), ["--epochs", "5"], script_path="scripts/train.py")

    assert spec.target_kind == "directory"
    assert spec.remote_target == "/home/project"
    assert spec.working_dir == "/home/project"
    assert spec.launch_command == "python scripts/train.py --epochs 5"


def test_build_run_spec_treats_non_path_target_as_raw_command():
    spec = run._build_run_spec("python", ["-c", "print('hi')"])

    assert spec.target_kind == "command"
    assert spec.local_target is None
    assert spec.remote_target is None
    assert spec.working_dir is None
    assert spec.launch_command == "python -c 'print('\"'\"'hi'\"'\"')'"


def test_build_run_spec_rejects_directory_without_command(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    captured: dict[str, str] = {}

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    monkeypatch.setattr(run.render, "die", fake_die)

    with pytest.raises(SystemExit):
        run._build_run_spec(str(project), [])

    assert (
        captured["message"]
        == "Directory targets require --script <path> or a command after --. Example: jl run . --script train.py --gpu RTX5000"
    )


def test_build_run_spec_rejects_script_option_for_file(monkeypatch, tmp_path):
    source = tmp_path / "train.py"
    source.write_text("print('hi')\n")
    captured: dict[str, str] = {}

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    monkeypatch.setattr(run.render, "die", fake_die)

    with pytest.raises(SystemExit):
        run._build_run_spec(str(source), [], script_path="other.py")

    assert captured["message"] == "--script can only be used with a directory target."


def test_write_remote_text_streams_content_over_ssh(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(parts: list[str], *, input: str, text: bool, check: bool):
        captured["parts"] = parts
        captured["input"] = input
        captured["text"] = text
        captured["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    status = run._write_remote_text(["ssh", "root@example.com"], "/home/.jl/runs/r1/meta.json", '{"ok": true}\n')

    assert status == 0
    assert captured["parts"] == [
        "ssh",
        "root@example.com",
        run._build_remote_command("mkdir -p /home/.jl/runs/r1 && cat > /home/.jl/runs/r1/meta.json"),
    ]
    assert captured["input"] == '{"ok": true}\n'
    assert captured["text"] is True
    assert captured["check"] is False


def test_prepare_remote_target_for_directory_replaces_stable_remote_path(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_text("print('hi')\n")
    inst = SimpleNamespace(machine_id=123, ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    def fake_run_remote(ssh_parts: list[str], script: str):
        captured["prep_script"] = script
        return 0

    def fake_sync(ssh_command: str, source, dest: str):
        captured["sync"] = {
            "ssh_command": ssh_command,
            "source": source,
            "dest": dest,
        }
        return 0

    monkeypatch.setattr(
        run, "_ensure_remote_rsync", lambda machine_id, ssh_parts: captured.setdefault("rsync", machine_id)
    )
    monkeypatch.setattr(run, "_run_remote", fake_run_remote)
    monkeypatch.setattr(run, "_sync_directory_to_remote", fake_sync)
    monkeypatch.setattr(run.render, "info", lambda message: None)

    spec = run.RunSpec(
        target_kind="directory",
        local_target=project,
        remote_target="/home/project",
        working_dir="/home/project",
        launch_command="python train.py",
    )

    run._prepare_remote_target(inst, ["ssh", "root@example.com"], spec)

    assert captured["rsync"] == 123
    assert captured["prep_script"] == "mkdir -p /home/project"
    assert captured["sync"] == {
        "ssh_command": "ssh -p 2222 root@example.com",
        "source": project,
        "dest": "/home/project",
    }


def test_start_managed_run_for_file_launches_detached_and_saves_local_record(monkeypatch, tmp_path):
    source = tmp_path / "train.py"
    source.write_text("print('hi')\n")
    inst = SimpleNamespace(machine_id=123, ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "_resolve_ssh", lambda machine_id: (inst, ["ssh", "-p", "2222", "root@example.com"]))
    monkeypatch.setattr(run, "_make_run_id", lambda: "r_test123")
    monkeypatch.setattr(run, "_run_remote", lambda ssh_parts, script: 0)
    monkeypatch.setattr(run, "_prepare_remote_target", lambda inst, ssh_parts, spec: captured.setdefault("spec", spec))
    monkeypatch.setattr(
        run,
        "_write_remote_wrapper",
        lambda ssh_parts, paths, spec: captured.setdefault("wrapper", paths.remote_wrapper),
    )
    monkeypatch.setattr(
        run,
        "_write_remote_metadata",
        lambda ssh_parts, inst, paths, spec: captured.setdefault("meta", paths.remote_meta),
    )
    monkeypatch.setattr(run, "_start_remote_run", lambda ssh_parts, paths: 4242)
    monkeypatch.setattr(run, "_save_local_run", lambda record: captured.setdefault("record", record))
    monkeypatch.setattr(run.render, "info", lambda message: None)
    monkeypatch.setattr(run.render, "success", lambda message: captured.setdefault("success", message))
    monkeypatch.setattr(state, "json_output", False)

    run_id, exit_code = run._start_managed_run(
        str(source),
        123,
        ["--epochs", "5"],
        follow=False,
        instance_origin="existing",
        lifecycle_policy="none",
    )

    spec = captured["spec"]
    assert spec.target_kind == "file"
    assert spec.remote_target == "/home/train.py"
    assert spec.launch_command == "python /home/train.py --epochs 5"
    record = captured["record"]
    assert record.run_id == "r_test123"
    assert record.machine_id == 123
    assert record.remote_target == "/home/train.py"
    assert record.remote_log == "/home/.jl/runs/r_test123/output.log"
    assert record.instance_origin == "existing"
    assert record.lifecycle_policy == "none"
    assert captured["success"] == "Started run r_test123 on instance 123 (launcher pid 4242)."
    assert run_id == "r_test123"
    assert exit_code is None


def test_start_managed_run_json_mode_returns_summary(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_text("print('hi')\n")
    inst = SimpleNamespace(machine_id=123, ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "_resolve_ssh", lambda machine_id: (inst, ["ssh", "-p", "2222", "root@example.com"]))
    monkeypatch.setattr(run, "_make_run_id", lambda: "r_json")
    monkeypatch.setattr(run, "_run_remote", lambda ssh_parts, script: 0)
    monkeypatch.setattr(run, "_prepare_remote_target", lambda inst, ssh_parts, spec: None)
    monkeypatch.setattr(run, "_write_remote_wrapper", lambda ssh_parts, paths, spec: None)
    monkeypatch.setattr(run, "_write_remote_metadata", lambda ssh_parts, inst, paths, spec: None)
    monkeypatch.setattr(run, "_start_remote_run", lambda ssh_parts, paths: 4242)
    monkeypatch.setattr(run, "_save_local_run", lambda record: None)
    monkeypatch.setattr(run.render, "print_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(state, "json_output", True)

    run_id, exit_code = run._start_managed_run(
        str(project),
        123,
        ["python", "train.py"],
        follow=False,
        instance_origin="fresh",
        lifecycle_policy="keep",
    )

    assert captured["payload"] == {
        "run_id": "r_json",
        "machine_id": 123,
        "launcher_pid": 4242,
        "remote_log": "/home/.jl/runs/r_json/output.log",
        "remote_exit_code": "/home/.jl/runs/r_json/exit_code",
        "target_kind": "directory",
        "remote_target": "/home/project",
        "command": "python train.py",
        "instance_origin": "fresh",
        "lifecycle_policy": "keep",
    }
    assert run_id == "r_json"
    assert exit_code is None


def test_iter_local_runs_sorts_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "_LOCAL_RUNS_ROOT", tmp_path)
    older = run.LocalRunRecord(
        run_id="r_old",
        machine_id=1,
        target_kind="file",
        local_target="/tmp/old.py",
        remote_target="/home/old.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_old",
        remote_log="/home/.jl/runs/r_old/output.log",
        remote_pid="/home/.jl/runs/r_old/pid",
        remote_exit_code="/home/.jl/runs/r_old/exit_code",
        remote_meta="/home/.jl/runs/r_old/meta.json",
        remote_wrapper="/home/.jl/runs/r_old/run.sh",
        launch_command="python /home/old.py",
        started_at="2026-03-08T10:00:00+00:00",
    )
    newer = run.LocalRunRecord(
        run_id="r_new",
        machine_id=2,
        target_kind="directory",
        local_target="/tmp/proj",
        remote_target="/home/proj",
        working_dir="/home/proj",
        remote_run_dir="/home/.jl/runs/r_new",
        remote_log="/home/.jl/runs/r_new/output.log",
        remote_pid="/home/.jl/runs/r_new/pid",
        remote_exit_code="/home/.jl/runs/r_new/exit_code",
        remote_meta="/home/.jl/runs/r_new/meta.json",
        remote_wrapper="/home/.jl/runs/r_new/run.sh",
        launch_command="python train.py",
        started_at="2026-03-09T10:00:00+00:00",
        instance_origin="fresh",
        lifecycle_policy="keep",
    )
    (tmp_path / "r_old.json").write_text(json.dumps(run.asdict(older)))
    (tmp_path / "r_new.json").write_text(json.dumps(run.asdict(newer)))

    records = run._iter_local_runs()

    assert [record.run_id for record in records] == ["r_new", "r_old"]


def test_get_run_snapshot_reports_running(monkeypatch):
    record = run.LocalRunRecord(
        run_id="r_run",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_run",
        remote_log="/home/.jl/runs/r_run/output.log",
        remote_pid="/home/.jl/runs/r_run/pid",
        remote_exit_code="/home/.jl/runs/r_run/exit_code",
        remote_meta="/home/.jl/runs/r_run/meta.json",
        remote_wrapper="/home/.jl/runs/r_run/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    monkeypatch.setattr(
        run, "_get_instance", lambda machine_id: SimpleNamespace(status="Running", ssh_command="ssh root@example.com")
    )
    monkeypatch.setattr(run, "_ssh_parts_from_instance", lambda inst: ["ssh", "root@example.com"])
    monkeypatch.setattr(run, "_fetch_exit_code_path", lambda ssh_parts, path: None)

    snapshot = run._get_run_snapshot(record)

    assert snapshot.state == "running"
    assert snapshot.exit_code is None


def test_get_run_snapshot_reports_instance_paused(monkeypatch):
    record = run.LocalRunRecord(
        run_id="r_paused",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_paused",
        remote_log="/home/.jl/runs/r_paused/output.log",
        remote_pid="/home/.jl/runs/r_paused/pid",
        remote_exit_code="/home/.jl/runs/r_paused/exit_code",
        remote_meta="/home/.jl/runs/r_paused/meta.json",
        remote_wrapper="/home/.jl/runs/r_paused/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    monkeypatch.setattr(
        run, "_get_instance", lambda machine_id: SimpleNamespace(status="Paused", ssh_command="ssh root@example.com")
    )

    snapshot = run._get_run_snapshot(record)

    assert snapshot.state == "instance-paused"


def test_run_logs_json_returns_content(monkeypatch):
    record = run.LocalRunRecord(
        run_id="r_logs",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_logs",
        remote_log="/home/.jl/runs/r_logs/output.log",
        remote_pid="/home/.jl/runs/r_logs/pid",
        remote_exit_code="/home/.jl/runs/r_logs/exit_code",
        remote_meta="/home/.jl/runs/r_logs/meta.json",
        remote_wrapper="/home/.jl/runs/r_logs/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "_resolve_run_ssh", lambda run_id: (record, ["ssh", "root@example.com"]))
    monkeypatch.setattr(
        run,
        "_tail_remote_log",
        lambda ssh_parts, remote_log, *, follow, tail: SimpleNamespace(stdout="hello\n", returncode=0),
    )
    monkeypatch.setattr(run.render, "print_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(state, "json_output", True)

    run.run_logs("r_logs", follow=False, tail=5)

    assert captured["payload"] == {
        "run_id": "r_logs",
        "machine_id": 123,
        "remote_log": "/home/.jl/runs/r_logs/output.log",
        "content": "hello\n",
        "exit_code": 0,
    }


def test_run_logs_follow_shows_followups(monkeypatch):
    record = run.LocalRunRecord(
        run_id="r_logs",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_logs",
        remote_log="/home/.jl/runs/r_logs/output.log",
        remote_pid="/home/.jl/runs/r_logs/pid",
        remote_exit_code="/home/.jl/runs/r_logs/exit_code",
        remote_meta="/home/.jl/runs/r_logs/meta.json",
        remote_wrapper="/home/.jl/runs/r_logs/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    captured: list[str] = []

    monkeypatch.setattr(run, "_resolve_run_ssh", lambda run_id: (record, ["ssh", "root@example.com"]))
    monkeypatch.setattr(run, "_tail_remote_log", lambda ssh_parts, remote_log, *, follow, tail: 0)
    monkeypatch.setattr(run.render, "info", lambda message: captured.append(message))
    monkeypatch.setattr(run.render.console, "print", lambda message: None)
    monkeypatch.setattr(state, "json_output", False)

    with pytest.raises(SystemExit) as exc:
        run.run_logs("r_logs", follow=True, tail=None)

    assert exc.value.code == 0
    assert captured[0] == "Following logs for run r_logs on instance 123. Press Ctrl+C to stop streaming."
    assert "Run ID: r_logs" in captured[1]


def test_run_list_does_not_refresh_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "_LOCAL_RUNS_ROOT", tmp_path)
    record = run.LocalRunRecord(
        run_id="r_saved",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_saved",
        remote_log="/home/.jl/runs/r_saved/output.log",
        remote_pid="/home/.jl/runs/r_saved/pid",
        remote_exit_code="/home/.jl/runs/r_saved/exit_code",
        remote_meta="/home/.jl/runs/r_saved/meta.json",
        remote_wrapper="/home/.jl/runs/r_saved/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    (tmp_path / "r_saved.json").write_text(json.dumps(run.asdict(record)))

    monkeypatch.setattr(
        run,
        "_get_run_snapshot",
        lambda record: (_ for _ in ()).throw(AssertionError("run list should not refresh by default")),
    )
    monkeypatch.setattr(run.render, "stdout_console", SimpleNamespace(print=lambda value: None))
    monkeypatch.setattr(state, "json_output", False)

    run.run_list(refresh=False)


def test_ensure_remote_rsync_rejects_missing_local_binary(monkeypatch):
    captured: dict[str, str] = {}

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    monkeypatch.setattr(run.shutil, "which", lambda name: None)
    monkeypatch.setattr(run.render, "die", fake_die)

    with pytest.raises(SystemExit):
        run._ensure_remote_rsync(123, ["ssh", "root@example.com"])

    assert captured["message"] == "rsync is required locally for directory runs. Install rsync and try again."


def test_run_stop_reports_completed_run(monkeypatch):
    record = run.LocalRunRecord(
        run_id="r_done",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_done",
        remote_log="/home/.jl/runs/r_done/output.log",
        remote_pid="/home/.jl/runs/r_done/pid",
        remote_exit_code="/home/.jl/runs/r_done/exit_code",
        remote_meta="/home/.jl/runs/r_done/meta.json",
        remote_wrapper="/home/.jl/runs/r_done/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    captured: list[str] = []

    monkeypatch.setattr(run, "_load_local_run", lambda run_id: record)
    monkeypatch.setattr(
        run,
        "_get_run_snapshot",
        lambda record: run.RunStatusSnapshot(
            run_id=record.run_id,
            machine_id=record.machine_id,
            target_kind=record.target_kind,
            started_at=record.started_at,
            state="succeeded",
            instance_status="Running",
            exit_code=0,
            remote_log=record.remote_log,
            lifecycle_policy=record.lifecycle_policy,
        ),
    )
    monkeypatch.setattr(run.render, "info", lambda message: captured.append(message))
    monkeypatch.setattr(run.render.console, "print", lambda message: None)
    monkeypatch.setattr(state, "json_output", False)

    run.run_stop("r_done")

    assert captured[0] == "Run r_done already finished with state succeeded."


def test_run_stop_sends_term_to_running_process(monkeypatch):
    record = run.LocalRunRecord(
        run_id="r_live",
        machine_id=123,
        target_kind="file",
        local_target="/tmp/train.py",
        remote_target="/home/train.py",
        working_dir="/home",
        remote_run_dir="/home/.jl/runs/r_live",
        remote_log="/home/.jl/runs/r_live/output.log",
        remote_pid="/home/.jl/runs/r_live/pid",
        remote_exit_code="/home/.jl/runs/r_live/exit_code",
        remote_meta="/home/.jl/runs/r_live/meta.json",
        remote_wrapper="/home/.jl/runs/r_live/run.sh",
        launch_command="python /home/train.py",
        started_at="2026-03-09T10:00:00+00:00",
    )
    captured: dict[str, str] = {}

    monkeypatch.setattr(run, "_load_local_run", lambda run_id: record)
    monkeypatch.setattr(
        run,
        "_get_run_snapshot",
        lambda record: run.RunStatusSnapshot(
            run_id=record.run_id,
            machine_id=record.machine_id,
            target_kind=record.target_kind,
            started_at=record.started_at,
            state="running",
            instance_status="Running",
            exit_code=None,
            remote_log=record.remote_log,
            lifecycle_policy=record.lifecycle_policy,
        ),
    )
    monkeypatch.setattr(run, "_resolve_run_ssh", lambda run_id: (record, ["ssh", "root@example.com"]))
    monkeypatch.setattr(run, "_stop_remote_run", lambda ssh_parts, pid_file: "stopped")
    monkeypatch.setattr(run.render, "success", lambda message: captured.setdefault("success", message))
    monkeypatch.setattr(run.render.console, "print", lambda message: None)
    monkeypatch.setattr(state, "json_output", False)

    run.run_stop("r_live")

    assert captured["success"] == "Stop signal sent to run r_live on instance 123."


def test_run_start_defaults_fresh_runs_to_pause(monkeypatch):
    inst = SimpleNamespace(machine_id=123, ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    monkeypatch.setattr(run.render, "confirm", lambda prompt, skip=False: True)
    monkeypatch.setattr(run.render, "info", lambda message: captured.setdefault("info", []).append(message))
    monkeypatch.setattr(run.render, "success", lambda message: None)
    monkeypatch.setattr(run.render, "warning", lambda message: None)
    monkeypatch.setattr(run.render, "spinner", lambda message: __import__("contextlib").nullcontext())
    monkeypatch.setattr(
        run,
        "get_client",
        lambda: SimpleNamespace(instances=SimpleNamespace(create=lambda **kwargs: inst)),
    )

    def fake_start(target, machine_id, extra_args, *, follow, instance_origin, lifecycle_policy, script_path=None):
        captured["start"] = {
            "target": target,
            "machine_id": machine_id,
            "extra_args": extra_args,
            "follow": follow,
            "instance_origin": instance_origin,
            "lifecycle_policy": lifecycle_policy,
            "script_path": script_path,
        }
        return "r_fresh", 0

    monkeypatch.setattr(run, "_start_managed_run", fake_start)
    monkeypatch.setattr(
        run, "_apply_lifecycle", lambda machine_id, policy: captured.setdefault("apply", (machine_id, policy))
    )

    run.run_start(
        SimpleNamespace(args=["train.py"]),
        on=None,
        gpu="RTX5000",
        script=None,
        pause=False,
        destroy=False,
        keep=False,
        follow=True,
    )

    assert captured["start"]["lifecycle_policy"] == "pause"
    assert captured["apply"] == (123, "pause")
    assert any("Defaulting to --pause" in message for message in captured["info"])
