from __future__ import annotations

import pytest
import typer

from jarvislabs.cli import commands


def test_scripts_remove_prompt(monkeypatch):
    captured: dict[str, str] = {}

    def fake_confirm(msg: str, *, skip: bool = False) -> bool:
        captured["msg"] = msg
        return False

    monkeypatch.setattr(commands.render, "confirm", fake_confirm)

    with pytest.raises(typer.Exit):
        commands.scripts_remove(script_id=13)

    assert captured["msg"] == "Remove startup script 13?"


def test_scripts_add_empty_file_fails(tmp_path):
    script = tmp_path / "empty.sh"
    script.write_text("")

    handle = script.open("rb")
    with pytest.raises(SystemExit):
        commands.scripts_add(script_file=handle, name="empty")
    handle.close()
