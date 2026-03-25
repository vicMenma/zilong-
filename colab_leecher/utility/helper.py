"""
helper.py — UI helpers, progress bar, status panel, file utilities.

Visual design:
  ╔══════════════════════════════════════╗
  ║  ⚡ ZILONG  //  LEECHER              ║
  ╠══════════════════════════════════════╣
  ║  📁 filename.mkv                     ║
  ║  [▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░]  52%      ║
  ║  🚀 48.3 MiB/s  ·  ⏱ ETA 01m 22s   ║
  ║  ✅ 891 MiB  /  📦 1.69 GiB          ║
  ║  💡 Aria2c 🧨  ·  🕰 01m 08s        ║
  ╚══════════════════════════════════════╝
"""
import os
import math
import psutil
import logging
from time import time
from PIL import Image
from os import path as ospath
from datetime import datetime
from urllib.parse import urlparse
from asyncio import get_event_loop
from colab_leecher import colab_bot
from pyrogram.errors import BadRequest, FloodWait
from moviepy.video.io.VideoFileClip import VideoFileClip
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, _status_edit_lock,
)

# ══════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════

_SEP   = "──────────────────────────────────"
_W     = 34           # visual width used in some builders
_BAR_W = 22           # progress bar fill width

# ══════════════════════════════════════════════
#  Low-level visual primitives
# ══════════════════════════════════════════════

def _pct_bar(pct: float, length: int = 12) -> str:
    """Filled/empty block bar. E.g. ████████░░░░"""
    filled = int(min(max(pct, 0), 100) / 100 * length)
    return "█" * filled + "░" * (length - filled)


def _rich_bar(pct: float, width: int = _BAR_W) -> str:
    """Gradient-style bar using ▓ blocks."""
    filled = int(min(max(pct, 0), 100) / 100 * width)
    empty  = width - filled
    return "▓" * filled + "░" * empty


def _speed_icon(speed_str: str) -> str:
    s = speed_str.upper()
    if "GIB" in s or "TIB" in s:          return "🚀"
    if "MIB" in s:
        try:
            v = float(speed_str.split()[0])
            if v >= 50:  return "⚡"
            if v >= 10:  return "🔥"
            return "🏃"
        except Exception:
            return "🏃"
    return "🐢"


def _ring(pct: float) -> str:
    return "🟢" if pct < 40 else ("🟡" if pct < 75 else "🔴")


def _field(emoji: str, label: str, value: str) -> str:
    return f"{emoji}  <b>{label}</b>  <code>{value}</code>"


# ══════════════════════════════════════════════
#  Link / type detectors
# ══════════════════════════════════════════════

def isLink(_, __, update):
    if update.text:
        if "/content/" in update.text or "/home" in update.text:
            return True
        if update.text.startswith("magnet:?xt=urn:btih:"):
            return True
        parsed = urlparse(update.text)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return True
    return False


def is_google_drive(link: str) -> bool: return "drive.google.com" in link
def is_mega(link: str) -> bool:         return "mega.nz" in link
def is_terabox(link: str) -> bool:      return "terabox" in link or "1024tera" in link
def is_ytdl_link(link: str) -> bool:    return "youtube.com" in link or "youtu.be" in link
def is_telegram(link: str) -> bool:     return "t.me" in link
def is_torrent(link: str) -> bool:      return link.startswith("magnet:") or link.endswith(".torrent")


# ══════════════════════════════════════════════
#  Time / size formatters
# ══════════════════════════════════════════════

def getTime(seconds: float) -> str:
    s = max(0, int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d: return f"{d}j {h:02d}h {m:02d}m {s:02d}s"
    if h: return f"{h:02d}h {m:02d}m {s:02d}s"
    if m: return f"{m:02d}m {s:02d}s"
    return f"{s:02d}s"


def sizeUnit(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"


# ══════════════════════════════════════════════
#  File helpers
# ══════════════════════════════════════════════

def fileType(file_path: str) -> str:
    ext_map = {
        ".mp4":"video", ".avi":"video", ".mkv":"video", ".m2ts":"video",
        ".mov":"video", ".ts":"video",  ".webm":"video", ".m4v":"video",
        ".mpg":"video", ".mpeg":"video",".vob":"video",
        ".mp3":"audio", ".wav":"audio", ".flac":"audio", ".aac":"audio",
        ".ogg":"audio", ".m4a":"audio", ".opus":"audio",
        ".jpg":"photo", ".jpeg":"photo",".png":"photo",
        ".bmp":"photo", ".gif":"photo", ".webp":"photo",
    }
    _, ext = ospath.splitext(file_path)
    return ext_map.get(ext.lower(), "document")


def shortFileName(path: str) -> str:
    if ospath.isfile(path):
        d, f = ospath.split(path)
        if len(f) > 60:
            b, e = ospath.splitext(f)
            f = b[:60 - len(e)] + e
            path = ospath.join(d, f)
    elif ospath.isdir(path):
        d, dn = ospath.split(path)
        if len(dn) > 60:
            path = ospath.join(d, dn[:60])
    return path


def getSize(path: str) -> int:
    if ospath.isfile(path):
        return ospath.getsize(path)
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns:
            total += ospath.getsize(ospath.join(dp, f))
    return total


def videoExtFix(fp: str) -> str:
    if fp.endswith((".mp4", ".mkv")):
        return fp
    new = fp + ".mp4"
    os.rename(fp, new)
    return new


def thumbMaintainer(fp: str):
    if ospath.exists(Paths.VIDEO_FRAME):
        os.remove(Paths.VIDEO_FRAME)
    try:
        fname, _ = ospath.splitext(ospath.basename(fp))
        ytdl_thmb = f"{Paths.WORK_PATH}/ytdl_thumbnails/{fname}.webp"
        with VideoFileClip(fp) as video:
            if ospath.exists(Paths.THMB_PATH):
                return Paths.THMB_PATH, video.duration
            elif ospath.exists(ytdl_thmb):
                return convertIMG(ytdl_thmb), video.duration
            else:
                video.save_frame(Paths.VIDEO_FRAME, t=math.floor(video.duration / 2))
                return Paths.VIDEO_FRAME, video.duration
    except Exception as e:
        logging.warning(f"[Thumb] {e}")
        return (Paths.THMB_PATH if ospath.exists(Paths.THMB_PATH) else Paths.HERO_IMAGE), 0


async def setThumbnail(message) -> bool:
    try:
        if ospath.exists(Paths.THMB_PATH):
            os.remove(Paths.THMB_PATH)
        await message.download(file_name=Paths.THMB_PATH)
        BOT.Setting.thumbnail = True
        return True
    except Exception as e:
        BOT.Setting.thumbnail = False
        logging.warning(f"[Thumbnail] {e}")
        return False


def isYtdlComplete() -> bool:
    for _, _, fns in os.walk(Paths.down_path):
        for f in fns:
            if ospath.splitext(f)[1] in (".part", ".ytdl"):
                return False
    return True


def convertIMG(image_path: str) -> str:
    img = Image.open(image_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    out = ospath.splitext(image_path)[0] + ".jpg"
    img.save(out, "JPEG")
    os.remove(image_path)
    return out


def applyCustomName():
    if BOT.Options.custom_name and BOT.Mode.type not in ("zip", "undzip"):
        for f in os.listdir(Paths.down_path):
            try:
                os.rename(
                    ospath.join(Paths.down_path, f),
                    ospath.join(Paths.down_path, BOT.Options.custom_name),
                )
            except Exception:
                pass


def speedETA(start, done: int, total: int):
    pct     = min((done / total) * 100, 100) if total else 0
    elapsed = max((datetime.now() - start).seconds, 1)
    if done > 0:
        raw   = done / elapsed
        speed = f"{sizeUnit(raw)}/s"
        eta   = (total - done) / raw if raw else 0
    else:
        speed, eta = "N/A", 0
    return speed, eta, pct


def isTimeOver(interval: float = 3.0) -> bool:
    passed = (time() - BotTimes.current_time) >= interval
    if passed:
        BotTimes.current_time = time()
    return passed


async def message_deleter(*msgs):
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass


def multipartArchive(path: str, type_: str, remove: bool):
    dirname, filename = ospath.split(path)
    name, _ = ospath.splitext(filename)
    c, size, rname = 1, 0, name

    if type_ == "rar":
        name_, _ = ospath.splitext(name)
        rname = name_
        while True:
            p = ospath.join(dirname, f"{name_}.part{c}.rar")
            if not ospath.exists(p):
                break
            if remove:
                os.remove(p)
            size += getSize(p)
            c    += 1

    elif type_ == "7z":
        while True:
            p = ospath.join(dirname, f"{name}.{str(c).zfill(3)}")
            if not ospath.exists(p):
                break
            if remove:
                os.remove(p)
            size += getSize(p)
            c    += 1

    elif type_ == "zip":
        p = ospath.join(dirname, f"{name}.zip")
        if ospath.exists(p):
            if remove:
                os.remove(p)
            size += getSize(p)
        while True:
            p = ospath.join(dirname, f"{name}.z{str(c).zfill(2)}")
            if not ospath.exists(p):
                break
            if remove:
                os.remove(p)
            size += getSize(p)
            c    += 1
        if rname.endswith(".zip"):
            rname, _ = ospath.splitext(rname)

    return rname, size


# ══════════════════════════════════════════════
#  System strip (compact inline resource info)
# ══════════════════════════════════════════════

def sysINFO() -> str:
    try:
        cpu  = psutil.cpu_percent()
        ram  = psutil.Process(os.getpid()).memory_info().rss
        disk = psutil.disk_usage("/")
        return (
            f"\n{_SEP}\n"
            f"🖥 <code>[{_pct_bar(cpu,8)}]</code> {_ring(cpu)} {cpu:.0f}%  "
            f"💾 <code>{sizeUnit(ram)}</code>  "
            f"💿 <code>{sizeUnit(disk.free)}</code> libre"
            f"{Messages.caution_msg}"
        )
    except Exception:
        return ""


# ══════════════════════════════════════════════
#  ✦  MAIN PROGRESS PANEL
# ══════════════════════════════════════════════

async def status_bar(
    down_msg: str,
    speed: str,
    percentage,
    eta: str,
    done: str,
    left: str,
    engine: str,
):
    """
    Renders the live progress card.

    ⚡ ZILONG  //  LEECHER
    ──────────────────────────────────
    📁 filename.mkv

    [▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░]  52%

    ⚡ 48.3 MiB/s  ·  💡 Aria2c 🧨
    ⏱  ETA  01m 22s  ·  🕰 01m 08s
    ✅ 891 MiB  /  📦 1.69 GiB
    ──────────────────────────────────
    🖥 [████░░░░]  35%  💾 512 MiB
    """
    pct     = float(percentage)
    bar     = _rich_bar(pct)
    s_ico   = _speed_icon(str(speed))
    elapsed = getTime((datetime.now() - BotTimes.start_time).seconds)
    fname   = (Messages.download_name or "…")[:36]

    text = (
        f"⚡ <b>ZILONG</b>  //  LEECHER\n"
        f"{_SEP}\n\n"
        f"📁 <code>{fname}</code>\n\n"
        f"<code>[{bar}]</code>  <b>{pct:.1f}%</b>\n\n"
        f"{_SEP}\n"
        f"{s_ico} <b>{speed}</b>    ·    💡 {engine}\n"
        f"⏱  ETA <code>{eta}</code>    ·    🕰 <code>{elapsed}</code>\n"
        f"✅ <code>{done}</code>  /  📦 <code>{left}</code>\n"
        + sysINFO()
    )

    if not isTimeOver():
        return

    async with _status_edit_lock:
        try:
            if MSG.status_msg:
                await MSG.status_msg.edit_text(
                    text=text,
                    disable_web_page_preview=True,
                    reply_markup=keyboard(),
                )
        except BadRequest:
            pass  # message not modified — identical content, skip silently
        except FloodWait as e:
            import asyncio
            await asyncio.sleep(e.value)
        except Exception as e:
            logging.warning(f"[status_bar] {e}")


# ══════════════════════════════════════════════
#  ✦  COMPLETION CARD
# ══════════════════════════════════════════════

def completion_card(
    fname: str,
    orig_sz: int,
    final_sz: int,
    duration_s: int,
    mode: str = "leech",
) -> str:
    reduction = ""
    if orig_sz > 0 and final_sz > 0 and final_sz != orig_sz:
        pct  = (1 - final_sz / orig_sz) * 100
        sign = "🔻" if pct > 0 else "🔺"
        reduction = f"\n{_field(sign, 'RÉDUCTION', f'{abs(pct):.0f}%')}"

    return (
        f"✅ <b>TÂCHE TERMINÉE</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📁', 'FICHIER', fname[:34])}\n"
        f"{_field('📦', 'TAILLE', sizeUnit(final_sz))}\n"
        f"{reduction}\n"
        f"{_field('⏱', 'DURÉE', getTime(duration_s))}\n"
        f"{_field('⚙', 'MODE', mode.upper())}\n\n"
        f"{_SEP}\n"
        f"🟢 <b>SUCCÈS</b>"
    )


# ══════════════════════════════════════════════
#  ✦  ERROR CARD
# ══════════════════════════════════════════════

def error_card(reason: str, suggestion: str = "") -> str:
    text = (
        f"🔴 <b>ERREUR</b>\n"
        f"{_SEP}\n\n"
        f"{_field('⚠', 'RAISON', reason[:50])}\n"
    )
    if suggestion:
        text += f"{_field('💡', 'CONSEIL', suggestion[:50])}\n"
    text += f"\n{_SEP}"
    return text


# ══════════════════════════════════════════════
#  ✦  SETTINGS PANEL
# ══════════════════════════════════════════════

async def send_settings(client, message, msg_id: int, command: bool):
    pr   = "—" if not BOT.Setting.prefix else f"«{BOT.Setting.prefix}»"
    su   = "—" if not BOT.Setting.suffix else f"«{BOT.Setting.suffix}»"
    thmb = "✅ Définie" if BOT.Setting.thumbnail else "❌ Aucune"
    tog  = "📄 → Média" if not BOT.Options.stream_upload else "🎞 → Document"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(tog,              callback_data="media" if not BOT.Options.stream_upload else "document"),
         InlineKeyboardButton("🎥 Vidéo",        callback_data="video")],
        [InlineKeyboardButton("✏️ Style caption", callback_data="caption"),
         InlineKeyboardButton("🖼 Miniature",     callback_data="thumb")],
        [InlineKeyboardButton("⬅️ Préfixe",       callback_data="set-prefix"),
         InlineKeyboardButton("Suffixe ➡️",       callback_data="set-suffix")],
        [InlineKeyboardButton("✖ Fermer",         callback_data="close")],
    ])

    text = (
        f"⚙️ <b>PARAMÈTRES</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📤', 'Upload',     BOT.Setting.stream_upload)}\n"
        f"{_field('✂️',  'Découpe',   BOT.Setting.split_video)}\n"
        f"{_field('🔄', 'Convertir', BOT.Setting.convert_video)}\n"
        f"{_field('✏️',  'Caption',   BOT.Setting.caption)}\n"
        f"{_field('⬅️',  'Préfixe',  pr)}\n"
        f"{_field('➡️',  'Suffixe',  su)}\n"
        f"{_field('🖼',  'Miniature', thmb)}\n\n"
        f"{_SEP}"
    )
    try:
        if command:
            await message.reply_text(text=text, reply_markup=kb)
        else:
            await colab_bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=kb,
            )
    except BadRequest:
        pass
    except Exception as e:
        logging.warning(f"[Settings] {e}")


# ══════════════════════════════════════════════
#  ✦  KEYBOARD
# ══════════════════════════════════════════════

def keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Annuler", callback_data="cancel"),
        InlineKeyboardButton("📊 Stats",   callback_data="stats_refresh"),
    ]])
