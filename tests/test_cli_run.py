from __future__ import annotations

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
            SimpleNamespace(args=[]),
            target="train.py",
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


def test_build_run_spec_treats_non_path_target_as_raw_command():
    spec = run._build_run_spec("python", ["-c", "print('hi')"])

    assert spec.target_kind == "command"
    assert spec.local_target is None
    assert spec.remote_target is None
    assert spec.working_dir is None
    assert spec.launch_command == run._render_command(["python", "-c", "print('hi')"])


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
        == "Directory targets require a command after --. Example: jl run . --on 123 -- python train.py"
    )


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

    def fake_run_remote(ssh_parts: list[str], script: str, *, capture_output: bool = False):
        captured["prep_script"] = script
        return 0

    def fake_upload(ssh_command: str, source, dest: str, *, recursive: bool):
        captured["upload"] = {
            "ssh_command": ssh_command,
            "source": source,
            "dest": dest,
            "recursive": recursive,
        }
        return 0

    monkeypatch.setattr(run, "_run_remote", fake_run_remote)
    monkeypatch.setattr(run, "_upload_to_remote", fake_upload)
    monkeypatch.setattr(run.render, "info", lambda message: None)

    spec = run.RunSpec(
        target_kind="directory",
        local_target=project,
        remote_target="/home/project",
        working_dir="/home/project",
        launch_command="python train.py",
    )

    run._prepare_remote_target(inst, ["ssh", "root@example.com"], spec)

    assert captured["prep_script"] == "mkdir -p /home && rm -rf /home/project"
    assert captured["upload"] == {
        "ssh_command": "ssh -p 2222 root@example.com",
        "source": project,
        "dest": "/home",
        "recursive": True,
    }


def test_start_managed_run_for_file_launches_detached_and_saves_local_record(monkeypatch, tmp_path):
    source = tmp_path / "train.py"
    source.write_text("print('hi')\n")
    inst = SimpleNamespace(machine_id=123, ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "_resolve_ssh", lambda machine_id: (inst, ["ssh", "-p", "2222", "root@example.com"]))
    monkeypatch.setattr(run, "_make_run_id", lambda: "r_test123")
    monkeypatch.setattr(run, "_run_remote", lambda ssh_parts, script, *, capture_output=False: 0)
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

    run._start_managed_run(str(source), 123, ["--epochs", "5"], follow=False)

    spec = captured["spec"]
    assert spec.target_kind == "file"
    assert spec.remote_target == "/home/train.py"
    assert spec.launch_command == "python /home/train.py --epochs 5"
    record = captured["record"]
    assert record.run_id == "r_test123"
    assert record.machine_id == 123
    assert record.remote_target == "/home/train.py"
    assert record.remote_log == "/home/.jl/runs/r_test123/output.log"
    assert captured["success"] == "Started run r_test123 on instance 123 (launcher pid 4242)."


def test_start_managed_run_json_mode_returns_summary(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_text("print('hi')\n")
    inst = SimpleNamespace(machine_id=123, ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "_resolve_ssh", lambda machine_id: (inst, ["ssh", "-p", "2222", "root@example.com"]))
    monkeypatch.setattr(run, "_make_run_id", lambda: "r_json")
    monkeypatch.setattr(run, "_run_remote", lambda ssh_parts, script, *, capture_output=False: 0)
    monkeypatch.setattr(run, "_prepare_remote_target", lambda inst, ssh_parts, spec: None)
    monkeypatch.setattr(run, "_write_remote_wrapper", lambda ssh_parts, paths, spec: None)
    monkeypatch.setattr(run, "_write_remote_metadata", lambda ssh_parts, inst, paths, spec: None)
    monkeypatch.setattr(run, "_start_remote_run", lambda ssh_parts, paths: 4242)
    monkeypatch.setattr(run, "_save_local_run", lambda record: None)
    monkeypatch.setattr(run.render, "print_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(state, "json_output", True)

    run._start_managed_run(str(project), 123, ["python", "train.py"], follow=False)

    assert captured["payload"] == {
        "run_id": "r_json",
        "machine_id": 123,
        "launcher_pid": 4242,
        "remote_log": "/home/.jl/runs/r_json/output.log",
        "remote_exit_code": "/home/.jl/runs/r_json/exit_code",
        "target_kind": "directory",
        "remote_target": "/home/project",
        "command": "python train.py",
    }
