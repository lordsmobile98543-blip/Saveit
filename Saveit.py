import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"

# Default local download directory.
# Can be overridden with DOWNLOAD_DIR in .env.
DEFAULT_DOWNLOAD_DIR = BASE_DIR / "downloads"


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv(ENV_FILE)


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value.strip()


# Telegram credentials
API_ID = int(get_required_env("API_ID"))
API_HASH = get_required_env("API_HASH")
SESSION_STRING = get_required_env("SESSION_STRING")

# Command
HANDLER = os.getenv("HANDLER", ".saveit").strip()

# Download directory
DOWNLOAD_DIR = Path(
    os.getenv(
        "DOWNLOAD_DIR",
        str(DEFAULT_DOWNLOAD_DIR)
    )
).expanduser()

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)


# ============================================================
# GLOBAL STATE
# ============================================================

YOUR_USER_ID = None

processed_messages = set()

save_lock = asyncio.Lock()


# ============================================================
# HELPERS
# ============================================================

def format_size(value):
    """Convert bytes to a human-readable size."""

    if value is None:
        return "0 B"

    value = float(value)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB"
    ]

    for unit in units:

        if value < 1024:
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} PB"


def format_duration(seconds):
    """Convert seconds to readable time."""

    if seconds is None or seconds < 0:
        return "--"

    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s"

    minutes, seconds = divmod(
        seconds,
        60
    )

    if minutes < 60:
        return f"{minutes}m {seconds}s"

    hours, minutes = divmod(
        minutes,
        60
    )

    return f"{hours}h {minutes}m"


class Progress:
    """Simple terminal progress display."""

    def __init__(self, name):
        self.name = name
        self.start_time = time.monotonic()
        self.last_update = 0

    def callback(self, current, total):

        now = time.monotonic()

        # Limit terminal updates.
        if (
            now - self.last_update < 0.5
            and current < total
        ):
            return

        self.last_update = now

        elapsed = now - self.start_time

        if elapsed <= 0:
            elapsed = 0.001

        speed = current / elapsed

        if total:
            percentage = (
                current / total
            ) * 100

            remaining = (
                total - current
            ) / speed if speed else None

        else:
            percentage = 0
            remaining = None

        bar_length = 30

        filled = int(
            bar_length
            * percentage
            / 100
        )

        bar = (
            "█" * filled
            + "░" * (bar_length - filled)
        )

        line = (
            f"\r{self.name}: "
            f"[{bar}] "
            f"{percentage:6.2f}% | "
            f"{format_size(current)}"
        )

        if total:
            line += (
                f" / {format_size(total)}"
            )

        line += (
            f" | {format_size(speed)}/s"
            f" | ETA {format_duration(remaining)}"
        )

        print(
            line,
            end="",
            flush=True
        )

        if total and current >= total:
            print()


# ============================================================
# SAVE MEDIA
# ============================================================

async def save_media(
    message,
    sender_id
):
    """
    Download media to DOWNLOAD_DIR and
    upload it to Telegram Saved Messages.
    """

    if not message:
        raise RuntimeError(
            "Message not found."
        )

    if not message.media:
        raise RuntimeError(
            "The replied message has no media."
        )

    message_key = (
        message.chat_id,
        message.id
    )

    # Prevent duplicate processing.
    async with save_lock:

        if message_key in processed_messages:
            raise RuntimeError(
                "This message has already "
                "been processed."
            )

        processed_messages.add(
            message_key
        )

    try:

        print()
        print("=" * 70)

        print(
            f"Message ID : {message.id}"
        )

        print(
            f"Sender     : {sender_id}"
        )

        print(
            f"Download   : {DOWNLOAD_DIR}"
        )

        print("=" * 70)

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        download_progress = Progress(
            "Downloading"
        )

        file_path = await client.download_media(
            message,
            file=str(DOWNLOAD_DIR),
            progress_callback=(
                download_progress.callback
            )
        )

        if not file_path:
            raise RuntimeError(
                "Telegram did not return "
                "a downloaded file."
            )

        file_path = Path(file_path)

        print()
        print(
            f"Downloaded: {file_path}"
        )

        if file_path.exists():

            size = file_path.stat().st_size

            print(
                f"Size: {format_size(size)}"
            )

        # ----------------------------------------------------
        # UPLOAD TO SAVED MESSAGES
        # ----------------------------------------------------

        print()
        print(
            "Uploading to Saved Messages..."
        )

        upload_progress = Progress(
            "Uploading"
        )

        await client.send_file(
            "me",
            str(file_path),
            caption=(
                f"Saved from {sender_id}"
            ),
            force_document=True,
            progress_callback=(
                upload_progress.callback
            )
        )

        print()
        print(
            "✓ Successfully saved "
            "to Telegram Saved Messages."
        )

        print(
            f"Local copy: {file_path}"
        )

        print("=" * 70)
        print()

    except Exception:

        async with save_lock:
            processed_messages.discard(
                message_key
            )

        raise


# ============================================================
# MANUAL SAVE COMMAND
# ============================================================

@client.on(
    events.NewMessage(
        pattern=lambda message: (
            message.text or ""
        ).strip() == HANDLER
    )
)
async def save_command(event):

    # Only allow your own account
    # to trigger the command.
    if event.sender_id != YOUR_USER_ID:
        return

    status = await event.respond(
        "⏳ Downloading..."
    )

    # Command must be a reply.
    if not event.reply_to_msg_id:

        await status.edit(
            f"Reply to a media message "
            f"and send {HANDLER}"
        )

        return

    try:

        message = await event.get_reply_message()

        if not message:
            await status.edit(
                "❌ Could not find the "
                "replied message."
            )
            return

        if not message.media:
            await status.edit(
                "❌ The replied message "
                "doesn't contain media."
            )
            return

        # Delete command message.
        try:
            await event.delete()
        except Exception:
            pass

        await save_media(
            message,
            str(message.sender_id)
        )

        # Delete status message.
        try:
            await status.delete()
        except Exception:
            pass

    except Exception as error:

        print(
            f"Save error: {error}"
        )

        try:
            await status.edit(
                f"❌ Failed to save:\n{error}"
            )
        except Exception:
            pass


# ============================================================
# START CLIENT
# ============================================================

async def main():

    global YOUR_USER_ID

    print()
    print("=" * 70)
    print("Telegram Media Saver")
    print("=" * 70)

    print(
        f"Command      : {HANDLER}"
    )

    print(
        f"Download dir : {DOWNLOAD_DIR}"
    )

    print()
    print(
        "Connecting to Telegram..."
    )

    await client.connect()

    if not await client.is_user_authorized():

        raise RuntimeError(
            "The StringSession is not authorized. "
            "Create a valid Telethon StringSession."
        )

    me = await client.get_me()

    YOUR_USER_ID = me.id

    account_name = (
        me.username
        or me.first_name
        or "Unknown"
    )

    print()
    print(
        f"✓ Logged in as: {account_name}"
    )

    print(
        f"✓ User ID: {YOUR_USER_ID}"
    )

    print()
    print(
        f"Reply to a photo/video/document "
        f"with {HANDLER}"
    )

    print()
    print(
        "Waiting for messages..."
    )

    print("=" * 70)
    print()

    await client.run_until_disconnected()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:

        print(
            "\nStopped."
        )

    except Exception as error:

        print(
            f"\nERROR: {error}"
        )

        sys.exit(1)
