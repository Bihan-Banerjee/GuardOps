"""
cli/utils/output.py

Centralizes all terminal output styling.

WHY: If we used print() everywhere, changing the style of error messages
would mean editing 50 files. Instead, every part of the codebase calls
output.error(), output.success(), etc. — one place to change styles.

Rich Markup language:
  [bold]text[/bold]       → bold text
  [green]text[/green]     → green text
  [bold red]text[/bold]   → bold red text
  [cyan]text[/cyan]       → cyan text
  These tags work inside any console.print() call.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich import box

# stderr=False means output goes to stdout (normal output stream).
# stderr=True would send to stderr — useful for logs so they don't
# interfere with piped output. We use stdout for now.
console = Console()

# ─── Simple one-line messages ─────────────────────────────────────────────────

def success(message: str) -> None:
    """Green checkmark for successful operations."""
    console.print(f"[bold green]  ✓[/bold green]  {message}")


def info(message: str) -> None:
    """Cyan info symbol for informational messages."""
    console.print(f"[bold cyan]  ℹ[/bold cyan]  {message}")


def warn(message: str) -> None:
    """Yellow warning for non-fatal issues."""
    console.print(f"[bold yellow]  ⚠[/bold yellow]  {message}")


def error(message: str) -> None:
    """Red X for errors. Does NOT exit — caller decides whether to exit."""
    console.print(f"[bold red]  ✗[/bold red]  {message}")


def step(number: int, total: int, message: str) -> None:
    """
    Shows progress like: [1/5] Building Docker image...
    Used to show numbered steps in a multi-step process.
    """
    console.print(f"[bold blue]  [{number}/{total}][/bold blue]  {message}")


def blank() -> None:
    """Print an empty line. Cleaner than print() scattered everywhere."""
    console.print()


# ─── Structured output ────────────────────────────────────────────────────────

def header(title: str, subtitle: str = "") -> None:
    """
    Prints a prominent header panel at the start of a command.
    Example:
        ╭─────────────────────────────╮
        │  GuardOps · Init            │
        │  Setting up your project    │
        ╰─────────────────────────────╯
    """
    content = f"[bold purple]{title}[/bold purple]"
    if subtitle:
        content += f"\n[dim]{subtitle}[/dim]"
    console.print(Panel(content, border_style="purple", padding=(0, 2)))
    blank()


def section(title: str) -> None:
    """
    Prints a section divider.
    Example: ── Security Scans ───────────────────────
    """
    console.print(Rule(f"[bold]{title}[/bold]", style="dim"))


def key_value_table(title: str, data: dict) -> None:
    """
    Prints a two-column table of key-value pairs.
    Used by `guardops status` to show deployment info.

    Args:
        title: Table caption shown above
        data:  Dict of {label: value} pairs to display
    """
    table = Table(
        title=title,
        box=box.ROUNDED,          # Rounded corners on the table border
        show_header=False,        # No column headers — just data rows
        padding=(0, 2),           # Horizontal padding inside cells
        border_style="dim",
    )
    # Two columns: key (right-aligned, dim) and value (left-aligned, bold)
    table.add_column("Key", style="dim", justify="right", min_width=18)
    table.add_column("Value", style="bold")

    for key, value in data.items():
        table.add_row(key, str(value))

    console.print(table)
    blank()


def result_panel(title: str, lines: list[str], style: str = "green") -> None:
    """
    Prints a summary panel at the end of a command.
    Used by `guardops deploy` to show the final deployment info.

    Args:
        title:  Bold title shown in the panel header
        lines:  List of strings shown in the panel body
        style:  Border color ("green" for success, "red" for failure)
    """
    content = "\n".join(lines)
    console.print(Panel(
        content,
        title=f"[bold]{title}[/bold]",
        border_style=style,
        padding=(1, 2),
    ))