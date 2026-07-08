#!/usr/bin/env python3
"""
Telegram Bulk Forwarder (TUI)
-----------------------------
Forwards all messages from a source channel to a destination channel
as independent copies (no "Forwarded from" header), with:
  - Resumable state (safe to Ctrl+C and restart)
  - Live progress bar + stats panel
  - Flood-wait handling
  - Per-run log file

Intended use: recovering/archiving your own content (e.g. client footage)
from a channel you and/or your collaborator administer, into your own
personal/archive channel.

Setup:
  1. Get api_id / api_hash from https://my.telegram.org
  2. pip install telethon rich click --break-system-packages
  3. python3 forwarder.py --source <channel> --dest <channel>
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.layout import Layout

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

console = Console()

STATE_DIR = Path.home() / ".tg_forwarder"
STATE_DIR.mkdir(exist_ok=True)


@dataclass
class RunStats:
    total_found: int = 0
    forwarded: int = 0
    skipped: int = 0
    failed: int = 0
    flood_waits: int = 0
    last_id: int = 0
    started_at: float = field(default_factory=time.time)

    def elapsed(self) -> str:
        secs = int(time.time() - self.started_at)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


def state_path(source: str, dest: str) -> Path:
    safe = f"{source}__{dest}".replace("/", "_").replace(":", "_")
    return STATE_DIR / f"{safe}.json"


def load_state(source: str, dest: str) -> dict:
    p = state_path(source, dest)
    if p.exists():
        return json.loads(p.read_text())
    return {"last_forwarded_id": 0, "forwarded_ids": []}


def save_state(source: str, dest: str, state: dict) -> None:
    state_path(source, dest).write_text(json.dumps(state))


def build_stats_panel(stats: RunStats) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="bold cyan")
    table.add_column()
    table.add_row("Found:", str(stats.total_found))
    table.add_row("Forwarded:", f"[green]{stats.forwarded}[/green]")
    table.add_row("Skipped (already done):", str(stats.skipped))
    table.add_row("Failed:", f"[red]{stats.failed}[/red]" if stats.failed else "0")
    table.add_row("Flood waits hit:", str(stats.flood_waits))
    table.add_row("Elapsed:", stats.elapsed())
    return Panel(table, title="Run Stats", border_style="cyan")


async def run_forward(
    api_id: int,
    api_hash: str,
    session: str,
    source: str,
    dest: str,
    batch_size: int,
    drop_author: bool,
    delay: float,
):
    client = TelegramClient(session, api_id, api_hash)
    await client.start()

    console.print(f"[bold green]Connected.[/bold green] Resolving channels...")
    src_entity = await client.get_entity(source)
    dst_entity = await client.get_entity(dest)

    state = load_state(source, dest)
    already_done = set(state.get("forwarded_ids", []))

    console.print("Scanning source channel for messages (oldest -> newest)...")
    messages: list[Message] = []
    async for msg in client.iter_messages(src_entity, reverse=True):
        if msg.id in already_done:
            continue
        messages.append(msg)

    stats = RunStats(total_found=len(messages) + len(already_done))
    stats.skipped = len(already_done)

    if not messages:
        console.print("[yellow]Nothing new to forward — already up to date.[/yellow]")
        return

    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    task_id = progress.add_task("Forwarding", total=len(messages))

    def render():
        layout = Table.grid()
        layout.add_row(progress)
        layout.add_row(build_stats_panel(stats))
        return layout

    with Live(render(), console=console, refresh_per_second=4) as live:
        i = 0
        while i < len(messages):
            batch = messages[i : i + batch_size]
            batch_ids = [m.id for m in batch]
            try:
                await client.forward_messages(
                    entity=dst_entity,
                    messages=batch_ids,
                    from_peer=src_entity,
                    drop_author=drop_author,
                )
                stats.forwarded += len(batch)
                already_done.update(batch_ids)
                state["forwarded_ids"] = list(already_done)
                state["last_forwarded_id"] = max(batch_ids)
                save_state(source, dest, state)
                i += batch_size
                progress.update(task_id, advance=len(batch))
            except FloodWaitError as e:
                stats.flood_waits += 1
                live.console.print(
                    f"[yellow]Flood wait: sleeping {e.seconds}s...[/yellow]"
                )
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                stats.failed += len(batch)
                live.console.print(f"[red]Error on batch starting id {batch_ids[0]}: {e}[/red]")
                i += batch_size  # move on; failed ids are NOT marked done, so a retry run will catch them
            live.update(render())
            await asyncio.sleep(delay)

    console.print(Panel(build_stats_panel(stats).renderable, title="Final Result", border_style="green"))
    console.print(f"[bold green]Done.[/bold green] State saved to {state_path(source, dest)}")
    console.print("Re-run the same command anytime to pick up any new messages posted since.")


@click.command()
@click.option("--api-id", required=True, type=int, help="API ID from my.telegram.org")
@click.option("--api-hash", required=True, help="API hash from my.telegram.org")
@click.option("--session", default="tg_forwarder_session", help="Telethon session file name")
@click.option("--source", required=True, help="Source channel username or ID")
@click.option("--dest", required=True, help="Destination channel username or ID")
@click.option("--batch-size", default=90, help="Messages per forward call (max ~100)")
@click.option("--keep-author/--strip-author", default=False,
              help="--keep-author shows 'Forwarded from'; --strip-author (default) copies without it")
@click.option("--delay", default=3.0, help="Seconds to wait between batches")
def main(api_id, api_hash, session, source, dest, batch_size, keep_author, delay):
    """Bulk-forward all messages from SOURCE to DEST, resumable, with live TUI progress."""
    console.rule("[bold]Telegram Bulk Forwarder[/bold]")
    asyncio.run(
        run_forward(
            api_id=api_id,
            api_hash=api_hash,
            session=session,
            source=source,
            dest=dest,
            batch_size=batch_size,
            drop_author=not keep_author,
            delay=delay,
        )
    )


if __name__ == "__main__":
    main()
