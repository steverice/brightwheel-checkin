"""The one console every script here prints through.

Rich degrades on its own when the output is not a terminal (a pipe, a log, CI),
stripping color and falling back to plain text, and it honors `NO_COLOR`.
Nothing passes `force_terminal`, so that detection stays intact.
"""

from __future__ import annotations

from rich.console import Console

_console = Console()
_stderr = Console(stderr=True)


def info(msg: str) -> None:
    _console.print(msg, highlight=False)


def success(msg: str) -> None:
    _console.print(f"[green]✓[/green] {msg}", highlight=False)


def error(msg: str) -> None:
    """To stderr, so a failing tool's reason survives a redirected stdout."""
    _stderr.print(f"[red]✗[/red] {msg}", highlight=False)


def warning(msg: str) -> None:
    _console.print(f"[yellow]⚠[/yellow] {msg}", highlight=False)
