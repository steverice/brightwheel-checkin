"""The one console every script here prints through.

Rich degrades on its own when the output is not a terminal (a pipe, a log, CI),
stripping color and falling back to plain text, and it honors `NO_COLOR`.
Nothing passes `force_terminal`, so that detection stays intact.
"""

from __future__ import annotations

from rich.console import Console

# soft_wrap: when the output is a file or a pipe Rich would otherwise wrap every
# line at 80 columns, which splits a build log's lines in the middle of a name.
_console = Console(soft_wrap=True)
_stderr = Console(stderr=True, soft_wrap=True)


def info(msg: str) -> None:
    _console.print(msg, highlight=False)


def success(msg: str) -> None:
    _console.print(f"[green]✓[/green] {msg}", highlight=False)


def error(msg: str) -> None:
    """To stderr, so a failing tool's reason survives a redirected stdout."""
    _stderr.print(f"[red]✗[/red] {msg}", highlight=False)


def warning(msg: str) -> None:
    _console.print(f"[yellow]⚠[/yellow] {msg}", highlight=False)
