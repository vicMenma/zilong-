"""
channel_manager.py
──────────────────────────────────────────────
Manages saved channels/groups for file copying.
Channels are persisted in channels.json next to __main__.py.
Each channel: { "id": int, "label": str, "type": "public"|"private" }
"""
import os
import json
import logging
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_HERE     = os.path.dirname(os.path.abspath(__file__))
_DB_PATH  = os.path.join(_HERE, "channels.json")

# ── persistence ───────────────────────────────

def _load() -> list:
    try:
        if os.path.exists(_DB_PATH):
            with open(_DB_PATH) as f:
                return json.load(f)
    except Exception as e:
        logging.warning(f"[ChannelMgr] load error: {e}")
    return []

def _save(channels: list):
    try:
        with open(_DB_PATH, "w") as f:
            json.dump(channels, f, indent=2)
    except Exception as e:
        logging.warning(f"[ChannelMgr] save error: {e}")

# ── CRUD ─────────────────────────────────────

def get_channels() -> list:
    return _load()

def add_channel(channel_id: int, label: str, ch_type: str = "private") -> bool:
    """Returns True if added, False if already exists."""
    channels = _load()
    for c in channels:
        if c["id"] == channel_id:
            return False
    channels.append({"id": channel_id, "label": label, "type": ch_type})
    _save(channels)
    return True

def remove_channel(channel_id: int) -> bool:
    """Returns True if removed, False if not found."""
    channels = _load()
    new = [c for c in channels if c["id"] != channel_id]
    if len(new) == len(channels):
        return False
    _save(new)
    return True

def get_channel(channel_id: int) -> dict | None:
    for c in _load():
        if c["id"] == channel_id:
            return c
    return None

# ── keyboard builders ─────────────────────────

def kb_channel_select(sent_msg_ids: list[int]) -> InlineKeyboardMarkup:
    """
    Inline keyboard shown after download:
    one button per saved channel + Skip button.
    sent_msg_ids = list of Telegram message IDs to forward.
    """
    channels = _load()
    ids_str  = ",".join(str(i) for i in sent_msg_ids)

    if not channels:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⚙ Add a channel first — /addchannel",
                callback_data="ch_none"
            )
        ], [
            InlineKeyboardButton("⏭ Skip", callback_data="ch_skip")
        ]])

    rows = []
    for c in channels:
        icon = "📢" if c["type"] == "public" else "🔒"
        rows.append([InlineKeyboardButton(
            f"{icon}  {c['label']}",
            callback_data=f"ch_copy_{c['id']}_{ids_str}"
        )])

    # Copy to ALL channels at once
    if len(channels) > 1:
        rows.append([InlineKeyboardButton(
            "📡  Copy to ALL channels",
            callback_data=f"ch_all_{ids_str}"
        )])

    rows.append([InlineKeyboardButton("⏭  Skip", callback_data="ch_skip")])
    return InlineKeyboardMarkup(rows)


def kb_channel_manage() -> InlineKeyboardMarkup:
    """Keyboard for /channels command — list + remove buttons."""
    channels = _load()
    rows = []
    for c in channels:
        icon = "📢" if c["type"] == "public" else "🔒"
        rows.append([
            InlineKeyboardButton(f"{icon} {c['label']}", callback_data="ch_none"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"ch_rm_{c['id']}"),
        ])
    rows.append([InlineKeyboardButton("✖ Close", callback_data="close")])
    return InlineKeyboardMarkup(rows)
