"""Output helpers for the CLI — tables, JSON, errors, confirmations."""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)
stdout_console = Console()


# ── JSON output ──────────────────────────────────────────────────────────────


def print_json(data: list[BaseModel] | BaseModel | dict) -> None:
    if isinstance(data, list):
        raw = [item.model_dump() if isinstance(item, BaseModel) else item for item in data]
    elif isinstance(data, BaseModel):
        raw = data.model_dump()
    else:
        raw = data
    stdout_console.print_json(json.dumps(raw, default=str))


# ── Tables ───────────────────────────────────────────────────────────────────


def instances_table(instances: list, currency: str = "USD") -> None:
    if not instances:
        info("No instances found.")
        return

    table = Table(title="Instances", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Status", no_wrap=True)
    table.add_column("GPU", style="magenta", no_wrap=True)
    table.add_column("GPUs", justify="right")
    table.add_column("Storage", justify="right")
    table.add_column("Template", style="dim")
    table.add_column("Region", style="dim")

    for inst in instances:
        status_style = _status_style(inst.status)
        table.add_row(
            str(inst.machine_id),
            inst.name or "—",
            f"[{status_style}]{inst.status}[/{status_style}]",
            inst.gpu_type or "—",
            str(inst.num_gpus or "—"),
            f"{inst.storage_gb}GB" if inst.storage_gb else "—",
            inst.template,
            inst.region or "—",
        )

    stdout_console.print(table)


def instance_detail(inst, currency: str = "USD") -> None:
    sym = "₹" if currency == "INR" else "$"
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")

    status_style = _status_style(inst.status)

    rows = [
        ("ID", str(inst.machine_id)),
        ("Name", inst.name or "—"),
        ("Status", f"[{status_style}]{inst.status}[/{status_style}]"),
        ("GPU", f"{inst.num_gpus or 1}x {inst.gpu_type or '—'}"),
        ("Template", inst.template),
        ("Storage", f"{inst.storage_gb}GB" if inst.storage_gb else "—"),
        ("Region", inst.region or "—"),
        ("Cost", f"{sym}{inst.cost:.2f}"),
        ("SSH", inst.ssh_command or "—"),
        ("URL", inst.url or "—"),
    ]

    for field, value in rows:
        table.add_row(field, value)

    stdout_console.print(table)


def ssh_keys_table(keys: list) -> None:
    if not keys:
        info("No SSH keys found.")
        return

    table = Table(title="SSH Keys")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Key", style="dim", max_width=50)

    for key in keys:
        display_key = key.ssh_key[:40] + "..." if len(key.ssh_key) > 40 else key.ssh_key
        table.add_row(key.key_id, key.key_name, display_key)

    stdout_console.print(table)


def gpu_table(gpus: list, currency: str = "USD") -> None:
    if not gpus:
        info("No GPU data available.")
        return

    sym = "₹" if currency == "INR" else "$"

    # Deduplicate: prefer entry with availability > 0, else first seen
    seen: dict[str, object] = {}
    for gpu in gpus:
        prev = seen.get(gpu.gpu_type)
        if prev is None or (prev.num_free_devices <= 0 and gpu.num_free_devices > 0):
            seen[gpu.gpu_type] = gpu

    table = Table(title="GPU Availability")
    table.add_column("GPU", style="magenta", no_wrap=True)
    table.add_column("VRAM", justify="right")
    table.add_column("RAM", justify="right")
    table.add_column("CPUs", justify="right")
    table.add_column(f"{sym}/hr", justify="right", style="yellow")

    for gpu in seen.values():
        table.add_row(
            gpu.gpu_type,
            f"{gpu.vram}GB" if gpu.vram else "—",
            f"{gpu.ram_per_gpu}GB" if gpu.ram_per_gpu else "—",
            str(gpu.cpus_per_gpu) if gpu.cpus_per_gpu else "—",
            f"{sym}{gpu.price_per_hour:.2f}" if gpu.price_per_hour else "—",
        )

    stdout_console.print(table)


# ── Messages ─────────────────────────────────────────────────────────────────


def success(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def error(msg: str) -> None:
    console.print(f"[red]✗[/red] {msg}", style="red")


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")


def warning(msg: str) -> None:
    console.print(f"[yellow]![/yellow] {msg}")


# ── Confirmation ─────────────────────────────────────────────────────────────


def confirm(msg: str, *, skip: bool = False) -> bool:
    """Ask for confirmation. Returns True if confirmed or skip=True (--yes flag)."""
    if skip:
        return True
    try:
        response = console.input(f"[yellow]?[/yellow] {msg} [dim]\\[y/N][/dim] ")
        return response.strip().lower() in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False


# ── Helpers ──────────────────────────────────────────────────────────────────


def _status_style(status: str) -> str:
    """Map instance status to a Rich color."""
    return {
        "Running": "green",
        "Paused": "yellow",
        "Failed": "red",
        "Creating": "blue",
        "Resuming": "blue",
        "Pausing": "yellow",
        "Destroying": "red",
    }.get(status, "white")


def die(msg: str, code: int = 1) -> None:
    """Print error and exit."""
    error(msg)
    sys.exit(code)
