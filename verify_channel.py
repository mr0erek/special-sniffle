#!/usr/bin/env python3
"""
Telegram Channel Reconciliation / Audit
-----------------------------------------
Run this AFTER a clone run to verify integrity between source and destination.

Checks two things:
  1. MISSING: content that exists in source but has no matching copy in
     destination at all. For each, shows the source link and asks whether
     to copy it now.
  2. DUPLICATES: content that appears MORE THAN ONCE in destination
     (whether from source having its own dupes, or earlier manual forwards).
     For each set, shows all destination links and asks whether to delete
     the extras (keeping the earliest) or leave them as-is.

Nothing happens automatically — every action requires your confirmation,
except when you explicitly choose an "apply to all remaining" option.

Setup:
  pip install pyrogram tgcrypto rich click pyfiglet --break-system-packages

Usage:
  python3 verify_channels.py --api-id 123456 --api-hash xxx \
      --source -1002688769418 --dest -1004393395560
"""

import asyncio
import hashlib
from collections import defaultdict
from itertools import cycle

import click
import pyfiglet
from pyrogram import Client
from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich.text import Text

console = Console()

SET_COLORS = cycle(["bright_magenta", "bright_cyan", "bright_yellow", "bright_green", "bright_blue", "bright_red"])


def message_link(chat_id, msg_id) -> str:
    cid = str(chat_id)
    internal = cid[4:] if cid.startswith("-100") else cid.lstrip("-")
    return f"https://t.me/c/{internal}/{msg_id}"


def fingerprint(msg):
    for attr in ("video", "document", "audio", "voice", "video_note", "animation"):
        media = getattr(msg, attr, None)
        if media is not None:
            return f"media:{media.file_unique_id}"
    if getattr(msg, "photo", None) is not None:
        return f"media:{msg.photo.file_unique_id}"
    text = (msg.text or msg.caption or "").strip()
    if text:
        return f"text:{hashlib.sha256(text.encode()).hexdigest()}"
    return None


def short_desc(msg) -> str:
    for attr in ("video", "document", "audio", "voice", "video_note", "animation", "photo"):
        media = getattr(msg, attr, None)
        if media is not None:
            size = getattr(media, "file_size", None)
            size_str = f"{size / 1024 / 1024:.1f}MB" if size else "?"
            name = getattr(media, "file_name", None) or attr
            return f"[{attr}] {name} ({size_str})"
    text = (msg.text or msg.caption or "").strip()
    if text:
        return f"[text] {text[:60]}{'...' if len(text) > 60 else ''}"
    return "[unknown]"


def big_banner(text: str, color: str = "bold cyan"):
    """Large ASCII-art section header with spacing above and below."""
    console.print()
    art = pyfiglet.figlet_format(text, font="standard")
    console.print(Text(art, style=color))
    console.print()


def set_header(index: int, total: int, kind: str, color: str):
    """Distinct, colored, spaced divider marking the start of a new item's review."""
    console.print()
    console.print(Rule(f"[bold {color}] {kind} {index} / {total} [/bold {color}]", style=color))
    console.print()


async def resolve_chat(app: Client, identifier: str):
    identifier = identifier.strip()
    if identifier.startswith("https://t.me/+") or identifier.startswith("https://t.me/joinchat/"):
        try:
            return await app.join_chat(identifier)
        except Exception:
            return await app.get_chat(identifier)
    try:
        identifier = int(identifier)
    except ValueError:
        pass
    return await app.get_chat(identifier)


async def crawl_fingerprints(app: Client, chat_id) -> dict:
    """Returns fp -> list of messages (in chronological order), for all
    messages in the chat that have identifiable content."""
    msgs = [m async for m in app.get_chat_history(chat_id)]
    msgs.reverse()  # oldest -> newest
    fp_map = defaultdict(list)
    for m in msgs:
        if getattr(m, "service", None):
            continue
        fp = fingerprint(m)
        if fp:
            fp_map[fp].append(m)
    return fp_map


async def run_audit(api_id, api_hash, session, source, dest, delay):
    app = Client(session, api_id=api_id, api_hash=api_hash)

    async with app:
        src_chat = await resolve_chat(app, source)
        dst_chat = await resolve_chat(app, dest)
        console.print(f"Source: [cyan]{src_chat.title}[/cyan] (id={src_chat.id})")
        console.print(f"Destination: [cyan]{dst_chat.title}[/cyan] (id={dst_chat.id})")

        console.print("Crawling source (fresh, full scan)...")
        src_fp = await crawl_fingerprints(app, src_chat.id)
        console.print(f"Source: {sum(len(v) for v in src_fp.values())} content items across {len(src_fp)} unique fingerprints.")

        console.print("Crawling destination (fresh, full scan)...")
        dst_fp = await crawl_fingerprints(app, dst_chat.id)
        console.print(f"Destination: {sum(len(v) for v in dst_fp.values())} content items across {len(dst_fp)} unique fingerprints.")

        # ---------------- 1. MISSING content (in source, absent from dest) ----------------
        missing = []
        for fp, src_msgs in src_fp.items():
            dst_count = len(dst_fp.get(fp, []))
            src_count = len(src_msgs)
            if dst_count < src_count:
                shortfall = src_count - dst_count
                # the first `dst_count` instances are presumed already copied;
                # flag the remaining ones as missing
                missing.extend(src_msgs[dst_count:])

        big_banner("MISSING")
        console.print(f"[bold]{len(missing)} item(s) in source with no match in destination[/bold]\n")

        auto_mode = None
        for idx, msg in enumerate(missing, 1):
            if auto_mode == "skip_all":
                break
            color = next(SET_COLORS)
            set_header(idx, len(missing), "MISSING ITEM", color)
            link = message_link(src_chat.id, msg.id)
            console.print(f"[{color}]{link}[/{color}]\n{short_desc(msg)}")
            if auto_mode == "copy_all":
                choice = "c"
            else:
                choice = click.prompt(
                    "  (c)opy now / (s)kip / (a)ll-copy-remaining / (x)-skip-remaining",
                    default="s", show_default=True
                ).strip().lower()
            if choice == "a":
                auto_mode = "copy_all"
                choice = "c"
            elif choice == "x":
                auto_mode = "skip_all"
                continue
            if choice == "c":
                try:
                    await app.copy_message(dst_chat.id, src_chat.id, msg.id)
                    console.print("  [green]Copied.[/green]")
                except Exception as e:
                    console.print(f"  [red]Copy failed: {e}[/red]")
                await asyncio.sleep(delay)

        # ---------------- 2. DUPLICATES within destination ----------------
        dupes = {fp: msgs for fp, msgs in dst_fp.items() if len(msgs) > 1}

        big_banner("DUPLICATES")
        console.print(f"[bold]{len(dupes)} content item(s) with multiple copies in destination[/bold]\n")

        auto_mode = None
        for set_idx, (fp, msgs) in enumerate(dupes.items(), 1):
            if auto_mode == "keep_all":
                break
            color = next(SET_COLORS)
            set_header(set_idx, len(dupes), "DUPLICATE SET", color)
            console.print(f"[{color}]{short_desc(msgs[0])}[/{color}] — {len(msgs)} copies\n")

            table = Table()
            table.add_column("#", style="bold")
            table.add_column("Link")
            table.add_column("Date / Note")
            for i, m in enumerate(msgs):
                note = "earliest" if i == 0 else ("latest" if i == len(msgs) - 1 else "")
                table.add_row(str(i + 1), message_link(dst_chat.id, m.id), f"{m.date} {('[' + note + ']') if note else ''}")
            console.print(table)

            if auto_mode == "delete_extras_keep_earliest":
                choice = "e"
            elif auto_mode == "delete_extras_keep_latest":
                choice = "l"
            else:
                console.print(
                    "\n  (e) delete extras, keep earliest [default]\n"
                    "  (l) delete extras, keep latest\n"
                    "  (k) keep all, do nothing\n"
                    "  1,2,3.. type number(s) to delete just those specific copies (e.g. \"2\" or \"1,3\")\n"
                    "  (a) apply 'keep earliest' to ALL remaining sets automatically\n"
                    "  (b) apply 'keep latest' to ALL remaining sets automatically\n"
                    "  (x) keep all for ALL remaining sets (stop asking)"
                )
                choice = click.prompt("  Choice", default="e", show_default=True).strip().lower()

            if choice == "a":
                auto_mode = "delete_extras_keep_earliest"
                choice = "e"
            elif choice == "b":
                auto_mode = "delete_extras_keep_latest"
                choice = "l"
            elif choice == "x":
                auto_mode = "keep_all"
                continue

            if choice == "e":
                extra_ids = [m.id for m in msgs[1:]]
            elif choice == "l":
                extra_ids = [m.id for m in msgs[:-1]]
            elif choice == "k":
                extra_ids = []
            elif all(c.isdigit() or c in ", " for c in choice) and choice.strip():
                # specific numbers typed, e.g. "2" or "1,3"
                try:
                    picked = {int(x.strip()) for x in choice.split(",") if x.strip()}
                    extra_ids = [m.id for i, m in enumerate(msgs, 1) if i in picked]
                except ValueError:
                    console.print("  [red]Couldn't parse that input, skipping this set.[/red]")
                    extra_ids = []
            else:
                console.print("  [yellow]Unrecognized choice, keeping all in this set.[/yellow]")
                extra_ids = []

            if extra_ids:
                try:
                    await app.delete_messages(dst_chat.id, extra_ids)
                    console.print(f"  [green]Deleted {len(extra_ids)} cop{'y' if len(extra_ids)==1 else 'ies'}.[/green]")
                except Exception as e:
                    console.print(f"  [red]Delete failed: {e}[/red]")
                await asyncio.sleep(delay)

        console.print("\n[bold green]Audit complete.[/bold green]")


@click.command()
@click.option("--api-id", required=True, type=int)
@click.option("--api-hash", required=True)
@click.option("--session", default="my_account")
@click.option("--source", required=True, help="@username, numeric ID, or invite link")
@click.option("--dest", required=True, help="@username, numeric ID, or invite link")
@click.option("--delay", default=1.5, help="Seconds between actions (copy/delete)")
def main(api_id, api_hash, session, source, dest, delay):
    """Audit SOURCE vs DEST: find missing content and destination-side duplicates."""
    console.rule("[bold]Telegram Channel Reconciliation[/bold]")
    asyncio.run(run_audit(api_id, api_hash, session, source, dest, delay))


if __name__ == "__main__":
    main()

