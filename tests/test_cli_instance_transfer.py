from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvislabs.cli import instance, state


def _fake_client(inst):
    return SimpleNamespace(instances=SimpleNamespace(get=lambda machine_id: inst))


def _patch_common(monkeypatch, inst):
    monkeypatch.setattr(instance, "get_client", lambda: _fake_client(inst))
    monkeypatch.setattr(instance.render, "spinner", lambda message: nullcontext())
    monkeypatch.setattr(instance.render, "info", lambda message: None)


def test_instance_upload_uses_default_remote_dest_for_file(monkeypatch, tmp_path):
    source = tmp_path / "train.py"
    source.write_text("print('hi')\n")
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")
    recorded: dict[str, list[str]] = {}

    def fake_call(parts: list[str]) -> int:
        recorded["parts"] = parts
        return 0

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "call", fake_call)
    monkeypatch.setattr(state, "json_output", False)

    with pytest.raises(SystemExit) as exc:
        instance.instance_upload(machine_id=123, source=source, dest=None)

    assert exc.value.code == 0
    assert recorded["parts"] == [
        "scp",
        "-P",
        "2222",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        str(source),
        f"root@example.com:/home/{source.name}",
    ]


def test_instance_upload_sets_recursive_for_directories(monkeypatch, tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    (source / "train.py").write_text("print('hi')\n")
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")
    recorded: list[list[str]] = []

    def fake_call(parts: list[str]) -> int:
        recorded.append(parts)
        return 0

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "call", fake_call)
    monkeypatch.setattr(state, "json_output", False)

    with pytest.raises(SystemExit) as exc:
        instance.instance_upload(machine_id=123, source=source, dest="/root/app")

    assert exc.value.code == 0
    assert recorded == [
        [
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
            "sh -lc 'mkdir -p /root/app'",
        ],
        [
            "scp",
            "-P",
            "2222",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-r",
            str(source),
            "root@example.com:/root/app",
        ],
    ]


def test_instance_upload_creates_parent_dirs_for_explicit_file_dest(monkeypatch, tmp_path):
    source = tmp_path / "train.py"
    source.write_text("print('hi')\n")
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")
    recorded: list[list[str]] = []

    def fake_call(parts: list[str]) -> int:
        recorded.append(parts)
        return 0

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "call", fake_call)
    monkeypatch.setattr(state, "json_output", False)

    with pytest.raises(SystemExit) as exc:
        instance.instance_upload(machine_id=123, source=source, dest="/home/test-upload/train.py")

    assert exc.value.code == 0
    assert recorded == [
        [
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
            "sh -lc 'mkdir -p /home/test-upload'",
        ],
        [
            "scp",
            "-P",
            "2222",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            str(source),
            "root@example.com:/home/test-upload/train.py",
        ],
    ]


def test_instance_download_defaults_local_dest(monkeypatch):
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")
    recorded: dict[str, list[str]] = {}

    def fake_call(parts: list[str]) -> int:
        recorded["parts"] = parts
        return 0

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "call", fake_call)
    monkeypatch.setattr(state, "json_output", False)

    with pytest.raises(SystemExit) as exc:
        instance.instance_download(machine_id=123, source="/root/output/model.pt", dest=None, recursive=False)

    assert exc.value.code == 0
    assert recorded["parts"] == [
        "scp",
        "-P",
        "2222",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "root@example.com:/root/output/model.pt",
        "model.pt",
    ]


def test_instance_download_supports_recursive_flag(monkeypatch, tmp_path):
    inst = SimpleNamespace(status="Running", ssh_command="ssh -o StrictHostKeyChecking=no -p 2222 root@example.com")
    recorded: dict[str, list[str]] = {}

    def fake_call(parts: list[str]) -> int:
        recorded["parts"] = parts
        return 0

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "call", fake_call)
    monkeypatch.setattr(state, "json_output", False)

    dest = tmp_path / "output"
    with pytest.raises(SystemExit) as exc:
        instance.instance_download(machine_id=123, source="/root/output", dest=dest, recursive=True)

    assert exc.value.code == 0
    assert recorded["parts"] == [
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-P",
        "2222",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-r",
        "root@example.com:/root/output",
        str(dest),
    ]


def test_instance_download_json_mode_returns_summary(monkeypatch):
    inst = SimpleNamespace(status="Running", ssh_command="ssh -p 2222 root@example.com")
    captured: dict[str, object] = {}

    def fake_run(parts: list[str], *, capture_output: bool, text: bool, check: bool):
        captured["parts"] = parts
        return SimpleNamespace(returncode=0)

    _patch_common(monkeypatch, inst)
    monkeypatch.setattr(instance.subprocess, "run", fake_run)
    monkeypatch.setattr(instance.render, "print_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(state, "json_output", True)

    instance.instance_download(machine_id=123, source="/root/output/model.pt", dest=None, recursive=False)

    assert captured["payload"] == {
        "machine_id": 123,
        "direction": "download",
        "source": "/root/output/model.pt",
        "dest": "model.pt",
        "recursive": False,
        "exit_code": 0,
    }


def test_default_download_dest_rejects_empty_remote_path():
    with pytest.raises(ValueError, match="Cannot infer a local destination"):
        instance._default_download_dest("/")


def test_default_upload_dest_uses_resolved_name_for_dot(tmp_path, monkeypatch):
    cwd = Path.cwd()
    monkeypatch.chdir(tmp_path)
    try:
        assert instance._default_upload_dest(Path(".")) == f"/home/{tmp_path.name}"
    finally:
        monkeypatch.chdir(cwd)
