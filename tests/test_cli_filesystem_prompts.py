from __future__ import annotations

import pytest
import typer

from jarvislabs.cli import commands


def test_filesystem_create_prompt(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(commands.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        commands.filesystem_create(name="data", storage=120)

    assert captured["msg"] == "Create filesystem (name='data', storage=120GB)?"


def test_filesystem_edit_prompt(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(commands.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        commands.filesystem_edit(fs_id=7, storage=200)

    assert captured["msg"] == "Expand filesystem 7 to 200GB?"


def test_filesystem_remove_prompt(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(commands.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        commands.filesystem_remove(fs_id=9)

    assert captured["msg"] == "Remove filesystem 9?"
