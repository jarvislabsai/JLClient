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
HEADER_STYLE = "bold"
TITLE_STYLE = "bold"
BORDER_STYLE = "dim"

theme = Theme(
    {
        "info": "dim",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "title": "bold",
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
    content.append(f"{info.name}", style="bold")
    content.append(f"  {info.user_id}\n", style="cyan")
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
        title="[bold]⚡ Account[/bold]",
        border_style="dim",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    stdout_console.print(panel)


# ── Tables ───────────────────────────────────────────────────────────────────


def _table(title: str | None = None, **kwargs) -> Table:
    """Create a table with standard styling."""
    return Table(
        title=title,
        box=TABLE_BOX,
        title_style=TITLE_STYLE,
        header_style=HEADER_STYLE,
        border_style=BORDER_STYLE,
        **kwargs,
    )


def instances_table(instances: list, currency: str = "USD") -> None:
    if not instances:
        info("No instances found.")
        return

    sym = "₹" if currency == "INR" else "$"

    table = _table("Instances", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("GPU", style="bold", no_wrap=True)
    table.add_column("GPUs", justify="right")
    table.add_column("Storage", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Template")

    for inst in instances:
        status_style = _status_style(inst.status)
        table.add_row(
            str(inst.machine_id),
            inst.name or "—",
            f"[{status_style}]{inst.status}[/{status_style}]",
            inst.gpu_type or "—",
            str(inst.num_gpus or "—"),
            f"{inst.storage_gb}GB" if inst.storage_gb else "—",
            _cost_cell(inst, sym),
            inst.template,
        )

    stdout_console.print(table)


def instance_detail(inst, currency: str = "USD") -> None:
    sym = "₹" if currency == "INR" else "$"
    table = Table(show_header=False, box=None, padding=(0, 2), border_style=BORDER_STYLE)
    table.add_column("Field", style="dim")
    # Avoid cutting off long values like notebook URLs with auth tokens.
    table.add_column("Value", overflow="fold")

    status_style = _status_style(inst.status)

    cost_label = "Storage cost" if inst.status == "Paused" else "Session cost"

    url_value = f"[link={inst.url}][magenta]{inst.url}[/magenta][/link]" if inst.url else "—"

    rows = [
        ("ID", f"[cyan]{inst.machine_id}[/cyan]"),
        ("Name", f"[bold]{inst.name or '—'}[/bold]"),
        ("Status", f"[{status_style}]{inst.status}[/{status_style}]"),
        ("GPU", f"[bold]{inst.num_gpus or 1}x {inst.gpu_type or '—'}[/bold]"),
        ("Template", inst.template),
        ("Storage", f"{inst.storage_gb}GB" if inst.storage_gb else "—"),
        (cost_label, f"[green]{sym}{inst.cost:.2f}[/green]"),
        ("SSH", f"[cyan]{inst.ssh_command}[/cyan]" if inst.ssh_command else "—"),
        ("URL", url_value),
    ]

    for field, value in rows:
        table.add_row(field, value)

    stdout_console.print(table)


def ssh_keys_table(keys: list) -> None:
    if not keys:
        info("No SSH keys found.")
        return

    table = _table("SSH Keys")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Key", style="dim", max_width=50)

    for key in keys:
        display_key = key.ssh_key[:40] + "..." if len(key.ssh_key) > 40 else key.ssh_key
        table.add_row(key.key_id, key.key_name, display_key)

    stdout_console.print(table)


def scripts_table(scripts: list) -> None:
    if not scripts:
        info("No startup scripts found.")
        return

    table = _table("Startup Scripts")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")

    for script in scripts:
        table.add_row(str(script.script_id), script.script_name or "—")

    stdout_console.print(table)


def templates_table(templates: list) -> None:
    if not templates:
        info("No templates found.")
        return

    table = _table("Templates")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Category", style="dim")

    for template in templates:
        table.add_row(template.id, template.title, template.category or "—")

    stdout_console.print(table)


def filesystems_table(filesystems: list) -> None:
    if not filesystems:
        info("No filesystems found.")
        return

    table = _table("Filesystems")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Storage", justify="right")

    for filesystem in filesystems:
        storage = f"{filesystem.storage}GB" if filesystem.storage is not None else "—"
        table.add_row(str(filesystem.fs_id), filesystem.fs_name or "—", storage)

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

    table = _table()
    table.add_column("", no_wrap=True)
    table.add_column("GPU", no_wrap=True)
    table.add_column("VRAM", justify="right")
    table.add_column("RAM", justify="right")
    table.add_column("CPUs", justify="right")
    table.add_column(f"{sym}/hr", justify="right")

    for gpu in available:
        table.add_row(
            "[green]●[/green]",
            f"[bold]{gpu.gpu_type}[/bold]",
            f"{gpu.vram}GB" if gpu.vram else "—",
            f"{gpu.ram_per_gpu}GB" if gpu.ram_per_gpu else "—",
            str(gpu.cpus_per_gpu) if gpu.cpus_per_gpu else "—",
            f"[green]{sym}{gpu.price_per_hour:.2f}[/green]" if gpu.price_per_hour else "—",
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
    """Rich spinner context manager for wrapping API calls. Suppressed in --json mode."""
    from contextlib import nullcontext

    from jarvislabs.cli import state

    if state.json_output:
        return nullcontext()
    return console.status(f"[bold]{msg}[/bold]", spinner="dots")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _cost_cell(inst, sym: str) -> str:
    """Format cost for table display with contextual color."""
    if inst.cost <= 0:
        return "[dim]—[/dim]"
    return f"[green]{sym}{inst.cost:.2f}[/green]"


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
    """Print error and exit. Emits JSON to stdout when --json is active."""
    from jarvislabs.cli import state

    if state.json_output:
        print_json({"error": msg})
    else:
        error(msg)
    sys.exit(code)
