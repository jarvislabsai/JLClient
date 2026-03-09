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


def harden_ssh_parts(
    parts: list[str],
    *,
    batch_mode: bool = True,
    connect_timeout: int = 15,
    server_alive_interval: int = 15,
    server_alive_count_max: int = 3,
) -> list[str]:
    """Add safe defaults for non-interactive SSH usage when they are missing."""
    if not parts or parts[0] != "ssh":
        raise SSHError("Cannot harden a non-ssh command")

    saw_batch_mode = False
    saw_connect_timeout = False
    saw_server_alive_interval = False
    saw_server_alive_count_max = False

    i = 1
    while i < len(parts):
        token = parts[i]
        if token == "-o" and i + 1 < len(parts):
            option = parts[i + 1]
            if option.startswith("BatchMode="):
                saw_batch_mode = True
            elif option.startswith("ConnectTimeout="):
                saw_connect_timeout = True
            elif option.startswith("ServerAliveInterval="):
                saw_server_alive_interval = True
            elif option.startswith("ServerAliveCountMax="):
                saw_server_alive_count_max = True
            i += 2
            continue

        if token in {"-i", "-F", "-J", "-l", "-p"}:
            i += 2
            continue

        if token.startswith("-"):
            i += 1
            continue

        break

    additions: list[str] = []
    if batch_mode and not saw_batch_mode:
        additions.extend(["-o", "BatchMode=yes"])
    if not saw_connect_timeout:
        additions.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if not saw_server_alive_interval:
        additions.extend(["-o", f"ServerAliveInterval={server_alive_interval}"])
    if not saw_server_alive_count_max:
        additions.extend(["-o", f"ServerAliveCountMax={server_alive_count_max}"])

    if not additions:
        return parts

    insert_at = len(parts) - 1 if len(parts) > 1 else 1
    return [*parts[:insert_at], *additions, *parts[insert_at:]]


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
    saw_batch_mode = False
    saw_connect_timeout = False
    saw_server_alive_interval = False
    saw_server_alive_count_max = False

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
            if option.startswith("BatchMode="):
                saw_batch_mode = True
            elif option.startswith("ConnectTimeout="):
                saw_connect_timeout = True
            elif option.startswith("ServerAliveInterval="):
                saw_server_alive_interval = True
            elif option.startswith("ServerAliveCountMax="):
                saw_server_alive_count_max = True
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

    if not saw_batch_mode:
        command.extend(["-o", "BatchMode=yes"])
    if not saw_connect_timeout:
        command.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if not saw_server_alive_interval:
        command.extend(["-o", "ServerAliveInterval=15"])
    if not saw_server_alive_count_max:
        command.extend(["-o", "ServerAliveCountMax=3"])

    if recursive:
        command.append("-r")

    if upload:
        command.extend([source, f"{target}:{dest}"])
    else:
        command.extend([f"{target}:{source}", dest])

    return command


def build_rsync_upload_command(
    ssh_command: str,
    *,
    source: str,
    dest: str,
    delete: bool = True,
    connect_timeout: int = 15,
) -> list[str]:
    """Build an rsync command for uploading a directory over the backend SSH transport."""
    parts = split_ssh_command(ssh_command)
    info = parse_ssh_command(ssh_command)

    transport = ["ssh"]
    saw_batch_mode = False
    saw_connect_timeout = False
    saw_server_alive_interval = False
    saw_server_alive_count_max = False

    i = 1
    while i < len(parts):
        token = parts[i]

        if token == "-p":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing port in SSH command: {ssh_command}")
            transport.extend(["-p", parts[i + 1]])
            i += 2
            continue

        if token == "-o":
            if i + 1 >= len(parts):
                raise SSHError(f"Missing SSH option value in command: {ssh_command}")
            option = parts[i + 1]
            if option.startswith("BatchMode="):
                saw_batch_mode = True
            elif option.startswith("ConnectTimeout="):
                saw_connect_timeout = True
            elif option.startswith("ServerAliveInterval="):
                saw_server_alive_interval = True
            elif option.startswith("ServerAliveCountMax="):
                saw_server_alive_count_max = True
            transport.extend(["-o", option])
            i += 2
            continue

        if token in {"-i", "-F", "-J"}:
            if i + 1 >= len(parts):
                raise SSHError(f"Missing SSH option value in command: {ssh_command}")
            transport.extend([token, parts[i + 1]])
            i += 2
            continue

        if token == "-l":
            i += 2
            continue

        if token.startswith("-"):
            transport.append(token)

        i += 1

    if not saw_batch_mode:
        transport.extend(["-o", "BatchMode=yes"])
    if not saw_connect_timeout:
        transport.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if not saw_server_alive_interval:
        transport.extend(["-o", "ServerAliveInterval=15"])
    if not saw_server_alive_count_max:
        transport.extend(["-o", "ServerAliveCountMax=3"])

    source_path = source.rstrip("/") + "/"
    dest_path = dest.rstrip("/") + "/"
    command = ["rsync", "-az", "-e", shlex.join(transport)]
    if delete:
        command.append("--delete")
    command.extend([source_path, f"{info.user}@{info.host}:{dest_path}"])
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
