from __future__ import annotations

import pytest
import typer

from jarvislabs.cli import instance


def test_instance_create_prompt_includes_storage_and_core_fields(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        instance.instance_create(
            gpu="RTX5000",
            template="pytorch",
            storage=60,
            name="train-job",
            num_gpus=2,
            script_id=None,
            script_args="",
            fs_id=None,
        )

    assert captured["msg"] == "Create instance (gpu=2x RTX5000, template=pytorch, storage=60GB, name='train-job')?"


def test_instance_create_prompt_lists_script_fields_when_provided(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        instance.instance_create(
            gpu="RTX5000",
            template="pytorch",
            storage=60,
            name="train-job",
            num_gpus=2,
            script_id="11",
            script_args="--foo bar",
            fs_id=7,
        )

    assert (
        captured["msg"]
        == "Create instance (gpu=2x RTX5000, template=pytorch, storage=60GB, name='train-job', script_id=11, script_args='--foo bar', fs_id=7)?"
    )


def test_instance_resume_prompt_defaults_to_current_configuration(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        instance.instance_resume(
            machine_id=123,
            gpu=None,
            num_gpus=None,
            storage=None,
            name=None,
            script_id=None,
            script_args=None,
            fs_id=None,
        )

    assert captured["msg"] == "Resume instance 123 with current configuration?"


def test_instance_resume_prompt_lists_all_requested_changes(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        instance.instance_resume(
            machine_id=42,
            gpu="H100",
            num_gpus=4,
            storage=120,
            name="new-name",
            script_id=None,
            script_args=None,
            fs_id=None,
        )

    assert captured["msg"] == "Resume instance 42 with gpu=H100, num_gpus=4, storage=120GB, name='new-name'?"


def test_instance_resume_prompt_includes_script_changes(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        instance.instance_resume(
            machine_id=42,
            gpu=None,
            num_gpus=None,
            storage=None,
            name=None,
            script_id="9",
            script_args="--dry-run",
            fs_id=12,
        )

    assert captured["msg"] == "Resume instance 42 with script_id=9, script_args='--dry-run', fs_id=12?"


def test_instance_rename_prompt(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        instance.instance_rename(machine_id=5, name="new-name")

    assert captured["msg"] == "Rename instance 5 to 'new-name'?"


@pytest.mark.parametrize(
    ("fn", "args", "expected"),
    [
        (instance.instance_pause, {"machine_id": 7}, "Pause instance 7?"),
        (instance.instance_destroy, {"machine_id": 9}, "Destroy instance 9? This cannot be undone."),
    ],
)
def test_instance_pause_destroy_prompts(fn, args, expected, monkeypatch):
    """Pause/destroy validate instance existence before prompting."""
    from unittest.mock import MagicMock

    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    mock_client = MagicMock()
    monkeypatch.setattr(instance, "get_client", lambda: mock_client)
    monkeypatch.setattr(instance.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        fn(**args)

    assert captured["msg"] == expected
