"""Helpers for working with SSH command strings returned by the backend."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvislabs.exceptions import SSHError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SSHInfo:
    """Structured view of a backend-provided SSH command."""

    user: str
    host: str
    port: int


def split_ssh_command(ssh_command: str) -> list[str]:
    """Split and validate a backend-provided SSH command string."""
    try:
        parts = shlex.split(ssh_command)
    except ValueError as exc:
        raise SSHError(f"Cannot parse SSH command: {ssh_command}") from exc
    if not parts or parts[0] != "ssh":
        raise SSHError(f"Cannot parse SSH command: {ssh_command}")
    return parts


def parse_ssh_command(ssh_command: str) -> SSHInfo:
    """Extract user/host/port from a backend-provided SSH command string."""
    parts = split_ssh_command(ssh_command)

    target: str | None = None
    user: str | None = None
    port = 22

    i = 1
    while i < len(parts):
        token = parts[i]

        if token == "-l":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing SSH user in command: {ssh_command}")
            user = parts[i + 1]
            i += 2
            continue

        if token == "-p":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing port in SSH command: {ssh_command}")
            try:
                port = int(parts[i + 1])
            except ValueError as exc:
                raise SSHError(f"Invalid SSH port in command: {ssh_command}") from exc
            i += 2
            continue

        # Backend-provided commands currently use `-o <option>` pairs.
        if token == "-o":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing SSH option value in command: {ssh_command}")
            i += 2
            continue

        if token.startswith("-"):
            i += 1
            continue

        target = token
        i += 1

    if not target:
        raise SSHError(f"Missing target in SSH command: {ssh_command}")

    if "@" in target:
        user, host = target.split("@", 1)
        return SSHInfo(user=user or "root", host=host, port=port)

    return SSHInfo(user=user or "root", host=target, port=port)


def build_scp_command(
    ssh_command: str,
    *,
    source: str,
    dest: str,
    upload: bool,
    recursive: bool = False,
    connect_timeout: int = 15,
) -> list[str]:
    """Build an scp command that reuses the backend-provided SSH options."""
    parts = split_ssh_command(ssh_command)
    info = parse_ssh_command(ssh_command)
    target = f"{info.user}@{info.host}"

    command = ["scp"]
    saw_connect_timeout = False

    i = 1
    while i < len(parts):
        token = parts[i]

        if token == "-p":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing port in SSH command: {ssh_command}")
            command.extend(["-P", parts[i + 1]])
            i += 2
            continue

        if token == "-o":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing SSH option value in command: {ssh_command}")
            option = parts[i + 1]
            if option.startswith("ConnectTimeout="):
                saw_connect_timeout = True
            command.extend(["-o", option])
            i += 2
            continue

        if token in {"-i", "-F", "-J"}:
            if i + 1 >= len(parts):
                raise SSHError(f"Missing SSH option value in command: {ssh_command}")
            command.extend([token, parts[i + 1]])
            i += 2
            continue

        if token == "-l":
            i += 2
            continue

        if token.startswith("-"):
            command.append(token)
            i += 1
            continue

        i += 1

    if not saw_connect_timeout:
        command.extend(["-o", f"ConnectTimeout={connect_timeout}"])

    if recursive:
        command.append("-r")

    if upload:
        command.extend([source, f"{target}:{dest}"])
    else:
        command.extend([f"{target}:{source}", dest])

    return command


def build_remote_shell_command(
    command: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Build a safely quoted remote shell command for `ssh ... <command>`."""
    if not command:
        raise ValidationError("command cannot be empty")

    segments: list[str] = []

    if cwd:
        segments.append(f"cd {shlex.quote(cwd)}")

    if env:
        for key, value in env.items():
            if not _ENV_KEY_RE.match(key):
                raise ValidationError(f"Invalid environment variable name: {key}")
            segments.append(f"export {key}={shlex.quote(value)}")

    rendered_command = " ".join(shlex.quote(part) for part in command)
    segments.append(rendered_command)

    script = " && ".join(segments)
    return f"sh -lc {shlex.quote(script)}"
