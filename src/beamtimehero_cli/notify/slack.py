"""Slack API tool implementations.

Read/write Slack channels via the Bot Token. Requires the
``SLACK_BOT_TOKEN`` environment variable and the ``slack-sdk`` package
(install with ``pip install 'beamtimehero_cli[slack]'``).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _get_client():
    """Return a Slack WebClient using the bot token.

    Raises ValueError if ``SLACK_BOT_TOKEN`` isn't set or ``slack-sdk``
    isn't installed.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "SLACK_BOT_TOKEN not set. Slack tools require a bot token."
        )
    try:
        from slack_sdk import WebClient
    except ImportError as e:
        raise ValueError(
            "slack-sdk not installed. Install with "
            "`pip install 'beamtimehero_cli[slack]'`."
        ) from e
    return WebClient(token=token)


def read_channel_messages(
    channel_id: str, limit: int = 20, oldest: Optional[str] = None,
) -> dict:
    """Read recent messages from a Slack channel."""
    client = _get_client()
    kwargs: dict = {"channel": channel_id, "limit": min(limit, 100)}
    if oldest:
        kwargs["oldest"] = oldest
    result = client.conversations_history(**kwargs)
    messages = []
    for msg in result.get("messages", []):
        messages.append({
            "user": msg.get("user", msg.get("bot_id", "unknown")),
            "text": msg.get("text", ""),
            "ts": msg.get("ts", ""),
            "thread_ts": msg.get("thread_ts"),
            "reply_count": msg.get("reply_count", 0),
        })
    return {"channel": channel_id, "messages": messages}


def read_thread_replies(channel_id: str, thread_ts: str) -> dict:
    """Read all replies in a Slack thread (parent + children)."""
    client = _get_client()
    result = client.conversations_replies(
        channel=channel_id, ts=thread_ts, limit=100,
    )
    messages = []
    for msg in result.get("messages", []):
        messages.append({
            "user": msg.get("user", msg.get("bot_id", "unknown")),
            "text": msg.get("text", ""),
            "ts": msg.get("ts", ""),
        })
    return {"channel": channel_id, "thread_ts": thread_ts, "messages": messages}


def post_message(
    channel_id: str, text: str, thread_ts: Optional[str] = None,
) -> dict:
    """Post a message to a channel, optionally as a thread reply."""
    client = _get_client()
    kwargs: dict = {"channel": channel_id, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    result = client.chat_postMessage(**kwargs)
    return {
        "status": "posted",
        "channel": channel_id,
        "ts": result["ts"],
        "thread_ts": thread_ts,
    }


def post_dm(user_id: str, text: str) -> dict:
    """Direct-message one user: open (or reuse) the DM, then post."""
    client = _get_client()
    opened = client.conversations_open(users=user_id)
    channel = opened["channel"]["id"]
    result = client.chat_postMessage(channel=channel, text=text)
    return {"status": "posted", "channel": channel, "ts": result["ts"], "user_id": user_id}


def list_channels() -> dict:
    """List public channels the bot is a member of."""
    client = _get_client()
    result = client.conversations_list(
        types="public_channel", exclude_archived=True, limit=200,
    )
    channels = []
    for ch in result.get("channels", []):
        if ch.get("is_member"):
            channels.append({
                "id": ch["id"],
                "name": ch.get("name", ""),
                "topic": ch.get("topic", {}).get("value", ""),
                "num_members": ch.get("num_members", 0),
            })
    return {"channels": channels}
