from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from jarvislabs.cli import instance, state
from jarvislabs.ssh import build_remote_shell_command


def _fake_client(inst):
    return SimpleNamespace(instances=SimpleNamespace(get=lambda machine_id: inst))


def _patch_common(monkeypatch, inst):
    monkeypatch.setattr(instance, "get_client", lambda: _fake_client(inst))
    monkeypatch.setattr(instance.render, "spinner", lambda message: nullcontext())
    monkeypatch.setattr(instance.render, "info", lambda message: None)


def test_instance_exec_requires_command(monkeypatch):
    captured: dict[str, str] = {}

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    monkeypatch.setattr(instance.render, "die", fake_die)

    with pytest.raises(SystemExit) as exc:
        instance.instance_exec(SimpleNamespace(args=[]), machine_id=123)

    assert exc.value.code == 1
    assert captured["message"] == "No command specified. Use -- to separate: jl instance exec 123 -- <command>"


def test_instance_exec_rejects_non_running_instance(monkeypatch):
    captured: dict[str, str] = {}
    inst = SimpleNamespace(status="Paused", ssh_command="ssh -p 2222 root@example.com")

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.render, "die", fake_die)

    with pytest.raises(SystemExit) as exc:
        instance.instance_exec(SimpleNamespace(args=["python", "train.py"]), machine_id=123)

    assert exc.value.code == 1
    assert captured["message"] == "Instance 123 is paused. Resume it first: jl instance resume 123"


def test_instance_exec_rejects_missing_ssh_command(monkeypatch):
    captured: dict[str, str] = {}
    inst = SimpleNamespace(status="Running", ssh_command=None)

    def fake_die(message: str, code: int = 1) -> None:
        captured["message"] = message
        raise SystemExit(code)

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.render, "die", fake_die)

    with pytest.raises(SystemExit) as exc:
        instance.instance_exec(SimpleNamespace(args=["python", "train.py"]), machine_id=123)

    assert exc.value.code == 1
    assert captured["message"] == "Instance 123 has no SSH command (status: Running)."


def test_instance_exec_runs_remote_command(monkeypatch):
    inst = SimpleNamespace(
        status="Running",
        ssh_command="ssh -o StrictHostKeyChecking=no -p 2222 root@example.com",
    )
    recorded: dict[str, list[str]] = {}

    def fake_call(parts: list[str]) -> int:
        recorded["parts"] = parts
        return 0

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "call", fake_call)
    monkeypatch.setattr(state, "json_output", False)

    with pytest.raises(SystemExit) as exc:
        instance.instance_exec(SimpleNamespace(args=["python", "train.py", "--epochs", "10"]), machine_id=123)

    assert exc.value.code == 0
    assert recorded["parts"] == [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        "2222",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "root@example.com",
        build_remote_shell_command(["python", "train.py", "--epochs", "10"]),
    ]


def test_instance_exec_json_mode_returns_summary(monkeypatch):
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    def fake_run(parts: list[str], *, capture_output: bool, text: bool, check: bool):
        captured["parts"] = parts
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout="hello\n", stderr="")

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "run", fake_run)
    monkeypatch.setattr(instance.render, "print_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(state, "json_output", True)

    instance.instance_exec(SimpleNamespace(args=["python", "train.py"]), machine_id=123)

    assert captured["parts"] == [
        "ssh",
        "-p",
        "2222",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "root@example.com",
        build_remote_shell_command(["python", "train.py"]),
    ]
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["check"] is False
    assert captured["payload"] == {
        "machine_id": 123,
        "command": "python train.py",
        "exit_code": 0,
        "stdout": "hello\n",
        "stderr": "",
    }


def test_instance_exec_json_mode_propagates_nonzero_exit(monkeypatch):
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")

    def fake_run(parts: list[str], *, capture_output: bool, text: bool, check: bool):
        return SimpleNamespace(returncode=7)

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "run", fake_run)
    monkeypatch.setattr(instance.render, "print_json", lambda payload: None)
    monkeypatch.setattr(state, "json_output", True)

    with pytest.raises(SystemExit) as exc:
        instance.instance_exec(SimpleNamespace(args=["python", "train.py"]), machine_id=123)

    assert exc.value.code == 7
