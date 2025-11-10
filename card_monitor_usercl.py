#!/usr/bin/env python3
"""
Reverse card monitor runner for regular Telegram accounts.

This script logs in with a user session, walks through the oldest messages of
configured groups, and feeds them into the existing checking pipeline defined
in `card_monitor_bot.py`. It respects Telegram flood limits and reuses the same
Stripe checker, queue, and reporting logic as the bot version.
"""

import asyncio
import logging
import os
import contextlib

from pyrogram import Client
from pyrogram.errors import FloodWait

import card_monitor_bot as monitor

# ==================== CONFIGURATION ====================
USER_API_ID = int(os.getenv("USER_API_ID", os.getenv("API_ID", "0")))
USER_API_HASH = os.getenv("USER_API_HASH", os.getenv("API_HASH", ""))
USER_SESSION_NAME = os.getenv("USER_SESSION_NAME", "card_monitor_user")
USER_SESSION_STRING = os.getenv("USER_SESSION_STRING")
USER_PHONE_NUMBER = os.getenv("USER_PHONE_NUMBER")  # optional when not using session string

TARGET_GROUPS = [
    int(os.getenv("USER_GROUP_PRIMARY", "-1002184643533")),
    int(os.getenv("USER_GROUP_SECONDARY", "-1002587158726")),
]

FETCH_BATCH_SIZE = int(os.getenv("USER_FETCH_BATCH_SIZE", "100"))
FETCH_SLEEP_SECONDS = float(os.getenv("USER_FETCH_SLEEP_SECONDS", "0.35"))
AFTER_BATCH_SLEEP_SECONDS = float(os.getenv("USER_AFTER_BATCH_SLEEP_SECONDS", "1.0"))

logger = logging.getLogger("card_monitor_usercl")


async def drain_queue() -> None:
    """Wait until current queue has been flushed."""
    while monitor.card_queue or monitor.processing_cards:
        await asyncio.sleep(1)


async def run_queue(app: Client) -> None:
    """Keep processing the shared queue."""
    try:
        await monitor.process_queue(app)
    except asyncio.CancelledError:
        logger.info("Queue processor cancelled, shutting down.")
        raise
    except Exception:
        logger.exception("Queue processor crashed.")
        raise


async def process_group(app: Client, chat_id: int) -> None:
    """Iterate group history from oldest to newest and enqueue cards."""
    logger.info("Scanning group %s", chat_id)
    offset_id = 0
    total_messages = 0
    total_cards = 0

    while True:
        try:
            history = await app.get_chat_history(chat_id, offset_id=offset_id, limit=FETCH_BATCH_SIZE)
        except FloodWait as fw:
            wait_time = fw.value + 1
            logger.warning("FloodWait %ss while fetching history for %s. Sleeping.", wait_time, chat_id)
            await asyncio.sleep(wait_time)
            continue
        except Exception:
            logger.exception("Failed to fetch history for chat %s", chat_id)
            await asyncio.sleep(5)
            continue

        if not history:
            logger.info("Reached oldest message for %s", chat_id)
            break

        history_list = list(history)
        offset_id = history_list[-1].id

        for message in reversed(history_list):
            total_messages += 1
            try:
                before_count = len(monitor.card_queue)
                await monitor.process_card_message(message, app)
                after_count = len(monitor.card_queue)
                if after_count > before_count:
                    total_cards += (after_count - before_count)
            except FloodWait as fw:
                wait_time = fw.value + 1
                logger.warning(
                    "FloodWait %ss while processing message %s in %s. Sleeping.",
                    wait_time,
                    message.id,
                    chat_id,
                )
                await asyncio.sleep(wait_time)
                continue
            except Exception:
                logger.exception("Failed to process message %s in chat %s", message.id, chat_id)
                continue

            await asyncio.sleep(FETCH_SLEEP_SECONDS)

        await asyncio.sleep(AFTER_BATCH_SLEEP_SECONDS)

    logger.info(
        "Completed scanning group %s | messages scanned: %s | cards queued: %s",
        chat_id,
        total_messages,
        total_cards,
    )


def _validate_credentials() -> None:
    if not USER_API_ID or USER_API_ID == 0:
        raise RuntimeError("USER_API_ID or API_ID must be set.")
    if not USER_API_HASH:
        raise RuntimeError("USER_API_HASH or API_HASH must be set.")
    if not USER_SESSION_STRING and not USER_PHONE_NUMBER:
        raise RuntimeError("Provide USER_SESSION_STRING or USER_PHONE_NUMBER for user login.")


def build_client() -> Client:
    kwargs = {
        "name": USER_SESSION_NAME,
        "api_id": USER_API_ID,
        "api_hash": USER_API_HASH,
        "in_memory": USER_SESSION_STRING is not None,
    }
    if USER_SESSION_STRING:
        kwargs["session_string"] = USER_SESSION_STRING
    elif USER_PHONE_NUMBER:
        kwargs["phone_number"] = USER_PHONE_NUMBER
    return Client(**kwargs)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    _validate_credentials()

    monitor.load_tested_bins()
    monitor.stripe_checker.load_keys()

    async with build_client() as app:
        queue_task = asyncio.create_task(run_queue(app))
        try:
            for chat_id in TARGET_GROUPS:
                await process_group(app, chat_id)

            await drain_queue()
        finally:
            queue_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_task
            await monitor.stripe_checker.close()
            await monitor.close_bin_lookup_session()


if __name__ == "__main__":
    asyncio.run(main())
