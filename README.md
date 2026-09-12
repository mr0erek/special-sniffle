# Special-Sniffle | A TELEGRAM Bulk Forward (TUI-based)

Resumable, rate-limit-aware bulk forwarder for moving your own content
between Telegram channels — built for the case where you and a collaborator
need to recover/archive content from a channel he administers into your
own personal/archive channel.

> [!NOTE]
> *suggesting to use `verify_channel.py`  instead of `forwarder.py`*

## Features
- Live terminal UI: progress bar, elapsed time, ETA, running stats
- **Resumable**: safe to Ctrl+C anytime; re-running picks up where it left off
- Forwards as independent copies (no "Forwarded from" header) by default
- Flood-wait handling (auto-backoff instead of crashing)
- Per-source/dest state file so you can run multiple channel pairs independently

## Setup

```bash
pip install telethon rich click --break-system-packages
```

Get your API credentials (the account doing the forwarding must do this):
1. Go to https://my.telegram.org
2. Log in with the phone number of the account that has access to the source channel
3. "API development tools" → create an app → note the `api_id` and `api_hash`

## Usage

```bash
python3 forwarder.py \
  --api-id 123456 \
  --api-hash your_api_hash_here \
  --source @old_channel_username \
  --dest @your_archive_channel
```

First run will prompt for the phone number + login code (Telethon handles this
interactively) and save a session file so you don't need to log in again.

### Options

| Flag | Default | Description |
|---|---|---|
| `--batch-size` | 90 | Messages forwarded per API call (Telegram caps around 100) |
| `--strip-author` / `--keep-author` | strip (default) | Whether to hide the "Forwarded from" header |
| `--delay` | 3.0 | Seconds between batches, to stay under rate limits |
| `--session` | tg_forwarder_session | Name of the saved login session file |

### Resuming after interruption
Just re-run the exact same command. State is tracked per source/dest pair in
`~/.tg_forwarder/`, so already-forwarded messages are skipped automatically.

### If you hit "Flood wait"
This is Telegram temporarily rate-limiting your account — normal for large
bulk operations. The script sleeps automatically and resumes; no action needed.

## Notes
- This must be run by an account that is a **member/admin** of the source
  channel (it uses the account's own session, not a bot token) — bots can't
  read full channel history by default.
- File content itself is never re-uploaded; Telegram forwards reference the
  same server-side blob, so this works fine even for very large video files.
