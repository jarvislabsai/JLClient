"""Output helpers for the CLI — tables, JSON, errors, confirmations."""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel
from rich import box
from rich.console import Console
from rich.table import Table
from rich.theme import Theme

TABLE_BOX = box.ROUNDED
HEADER_STYLE = "bold cyan"
TITLE_STYLE = "bold cyan"
BORDER_STYLE = "dim"

theme = Theme(
    {
        "info": "dim",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "title": "bold cyan",
    }
)

console = Console(stderr=True, theme=theme)
stdout_console = Console(theme=theme)


# ── JSON output ──────────────────────────────────────────────────────────────


def print_json(data: list[BaseModel] | BaseModel | dict) -> None:
    if isinstance(data, list):
        raw = [item.model_dump() if isinstance(item, BaseModel) else item for item in data]
    elif isinstance(data, BaseModel):
        raw = data.model_dump()
    else:
        raw = data
    stdout_console.print_json(json.dumps(raw, default=str))


# ── Account Status ────────────────────────────────────────────────────────────


def account_status(info, bal, metrics, sym: str) -> None:
    from rich.panel import Panel
    from rich.text import Text

    content = Text()
    content.append(f"{info.name}", style="bold white")
    content.append(f"  {info.user_id}\n", style="dim")
    content.append("\n")
    content.append("Balance  ", style="dim")
    content.append(f"{sym}{bal.balance:.2f}", style="bold green")
    content.append("    Grants  ", style="dim")
    content.append(f"{sym}{bal.grants:.2f}", style="bold yellow")
    content.append("\n")
    content.append("Running  ", style="dim")
    content.append(f"{metrics.running_instances}", style="bold green")
    content.append("         Paused  ", style="dim")
    content.append(f"{metrics.paused_instances}", style="bold yellow")

    panel = Panel(
        content,
        title="[bold cyan]⚡ Account[/bold cyan]",
        border_style="dim",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    stdout_console.print(panel)


# ── Tables ───────────────────────────────────────────────────────────────────


def instances_table(instances: list, currency: str = "USD") -> None:
    if not instances:
        info("No instances found.")
        return

    table = Table(
        title="Instances",
        box=TABLE_BOX,
        title_style=TITLE_STYLE,
        header_style=HEADER_STYLE,
        border_style=BORDER_STYLE,
        show_lines=True,
    )
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

    table = Table(
        title="SSH Keys", box=TABLE_BOX, title_style=TITLE_STYLE, header_style=HEADER_STYLE, border_style=BORDER_STYLE
    )
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

    available = [g for g in seen.values() if g.num_free_devices > 0]
    unavailable = [g for g in seen.values() if g.num_free_devices <= 0]

    table = Table(
        box=TABLE_BOX,
        header_style=HEADER_STYLE,
        border_style=BORDER_STYLE,
    )
    table.add_column("", no_wrap=True)
    table.add_column("GPU", no_wrap=True)
    table.add_column("VRAM", justify="right")
    table.add_column("RAM", justify="right")
    table.add_column("CPUs", justify="right")
    table.add_column(f"{sym}/hr", justify="right")

    for gpu in available:
        table.add_row(
            "[green]●[/green]",
            f"[bold cyan]{gpu.gpu_type}[/bold cyan]",
            f"{gpu.vram}GB" if gpu.vram else "—",
            f"{gpu.ram_per_gpu}GB" if gpu.ram_per_gpu else "—",
            str(gpu.cpus_per_gpu) if gpu.cpus_per_gpu else "—",
            f"[yellow]{sym}{gpu.price_per_hour:.2f}[/yellow]" if gpu.price_per_hour else "—",
        )

    for gpu in unavailable:
        table.add_row(
            "[dim]○[/dim]",
            f"[dim]{gpu.gpu_type}[/dim]",
            f"[dim]{gpu.vram}GB[/dim]" if gpu.vram else "[dim]—[/dim]",
            f"[dim]{gpu.ram_per_gpu}GB[/dim]" if gpu.ram_per_gpu else "[dim]—[/dim]",
            f"[dim]{gpu.cpus_per_gpu}[/dim]" if gpu.cpus_per_gpu else "[dim]—[/dim]",
            f"[dim]{sym}{gpu.price_per_hour:.2f}[/dim]" if gpu.price_per_hour else "[dim]—[/dim]",
        )

    table.caption = "[green]●[/green] available  [dim]○ unavailable[/dim]"
    table.caption_justify = "center"
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


# ── Spinner ──────────────────────────────────────────────────────────────────


def spinner(msg: str):
    """Rich spinner context manager for wrapping API calls."""
    return console.status(f"[bold]{msg}[/bold]", spinner="dots")


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
