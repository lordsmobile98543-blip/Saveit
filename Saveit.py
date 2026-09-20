import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession


load_dotenv()

api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
handler = os.getenv("HANDLER", ".saveit")
auto_save_timed = os.getenv("AUTO_SAVE_TIMED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Auto-delete configuration
AUTO_DELETE_HOURS = float(os.getenv("AUTO_DELETE_HOURS", "4"))
auto_delete_enabled = os.getenv("AUTO_DELETE_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Session string — paste yours in .env
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING not found in .env. "
        "Get one from @StringFatherBot and add it: SESSION_STRING=your_string_here"
    )

downloads_path = Path("downloads")
saved_message_ids = set()
save_lock = asyncio.Lock()
your_user_id = None

# Initialize client with your existing session string
client = TelegramClient(
    StringSession(SESSION_STRING),
    api_id,
    api_hash
)


def is_timed_media(message):
    """Telegram exposes the self-destruct timer on the media object."""
    return bool(
        message
        and message.media
        and getattr(message.media, "ttl_seconds", None)
    )


async def save_media(message, sender_id):
    message_key = (message.chat_id, message.id)

    async with save_lock:
        if message_key in saved_message_ids:
            return
        saved_message_ids.add(message_key)

    downloads_path.mkdir(parents=True, exist_ok=True)

    try:
        file_path = await client.download_media(message, file=str(downloads_path))
        if not file_path:
            raise RuntimeError("Telegram did not return a downloadable file")

        await client.send_file(
            "me",
            file_path,
            caption=f"File saved from {sender_id}",
            force_document=True,
        )
        print(f"Saved media from {sender_id}: {file_path}")
    except Exception:
        async with save_lock:
            saved_message_ids.discard(message_key)
        raise


# ============================================================
# AUTO-DELETE OLD FILES (every N hours)
# ============================================================

async def auto_delete_loop():
    """Periodically delete files older than AUTO_DELETE_HOURS from downloads/."""
    while True:
        await asyncio.sleep(AUTO_DELETE_HOURS * 3600)
        try:
            if not downloads_path.exists():
                continue

            now = time.time()
            max_age = AUTO_DELETE_HOURS * 3600
            deleted = 0

            for file in downloads_path.iterdir():
                if not file.is_file():
                    continue

                file_age = now - file.stat().st_mtime
                if file_age > max_age:
                    file.unlink()
                    deleted += 1
                    print(f"Auto-deleted old file: {file.name}")

            if deleted:
                print(f"Auto-delete cleanup: removed {deleted} file(s) older than {AUTO_DELETE_HOURS}h")

        except Exception as err:
            print(f"Auto-delete error: {err}")


# ============================================================
# AUTO-SAVE TIMED MEDIA
# ============================================================

@client.on(events.NewMessage(incoming=True))
async def auto_save_timed_media(event):
    if not auto_save_timed or not is_timed_media(event.message):
        return

    try:
        await save_media(event.message, event.sender_id)
    except Exception as err:
        print(f"Failed to auto-save timed media {event.chat_id}/{event.id}: {err}")


# ============================================================
# MANUAL SAVE COMMAND (silent — no visible status messages)
# ============================================================

@client.on(events.NewMessage(pattern=rf"^{handler}$"))
async def download_with_handler(event):
    if event.sender_id != your_user_id:
        return

    # Silently delete the command message — no "Downloading..." shown in chat
    try:
        await event.delete()
    except Exception:
        pass

    if not event.reply_to_msg_id:
        # Silently notify user in Saved Messages instead of the chat
        try:
            await client.send_message(
                "me",
                f"Reply to a media message with {handler} to save it."
            )
        except Exception:
            pass
        return

    message = await event.get_reply_message()

    if not message or not message.media:
        try:
            await client.send_message(
                "me",
                "No media found in the replied message."
            )
        except Exception:
            pass
        return

    try:
        await save_media(message, str(message.sender_id))
    except Exception as err:
        # Send error only to Saved Messages, not the chat
        try:
            await client.send_message("me", f"Failed to save media: {err}")
        except Exception:
            pass


# ============================================================
# MAIN
# ============================================================

async def main():
    global your_user_id

    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError(
            "SESSION_STRING is invalid or expired. "
            "Generate a new one from @StringFatherBot and update .env"
        )

    me = await client.get_me()
    your_user_id = me.id

    print(f"Running as {me.username or me.first_name} (ID: {your_user_id})")
    print(f"Automatic timed-media saving: {'enabled' if auto_save_timed else 'disabled'}")
    print(f"Auto-delete files: {'enabled' if auto_delete_enabled else 'disabled'} (every {AUTO_DELETE_HOURS}h)")

    # Start background auto-delete task
    if auto_delete_enabled:
        asyncio.create_task(auto_delete_loop())

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
