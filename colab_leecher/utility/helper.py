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
from pyrogram.errors import BadRequest
from moviepy.video.io.VideoFileClip import VideoFileClip
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Messages, Paths

# ══════════════════════════════════════════════
#  FUTURISTIC DARK THEME — design system
# ══════════════════════════════════════════════

_SEP    = "──────────────────────────────────"
_WIDTH  = 32

# Legacy aliases kept so imports in __main__.py do not break
_TOP   = _SEP
_MID   = _SEP
_BOT   = _SEP
_EMPTY = ""

def _h(emoji: str, title: str) -> str:
    return f"{emoji} <b>{title}</b>\n{_SEP}"

def _field(emoji: str, label: str, value: str) -> str:
    return f"{emoji} <b>{label}</b>  <code>{value}</code>"

def _row(label: str, value: str) -> str:
    """Dash-style row — no box characters."""
    return f"{label}  <code>{value}</code>"

def _line(text: str = "") -> str:
    """Dash-style line — just the text, no box characters."""
    if not text:
        return ""
    if len(text) > _WIDTH:
        text = text[:_WIDTH - 1] + "\u2026"
    return text

def _bar_ui(pct: float, width: int = 18) -> str:
    filled = int(min(pct, 100) / 100 * width)
    empty  = width - filled
    bar    = "▓" * filled + "░" * empty
    return f"{bar}  {pct:.0f}%"

def _pct_bar(pct: float, length: int = 12) -> str:
    filled = int(min(pct, 100) / 100 * length)
    return "█" * filled + "░" * (length - filled)

def _speed_label(speed_str: str) -> str:
    if "GiB" in speed_str or "TiB" in speed_str: return "🚀 TURBO"
    if "MiB" in speed_str:
        try:
            v = float(speed_str.split()[0])
            if v >= 50: return "⚡ BOOST"
            if v >= 10: return "🔥 FAST"
        except Exception: pass
        return "🏃 NORMAL"
    return "🐢 SLOW"

def _ring(p): return "🟢" if p < 40 else ("🟡" if p < 70 else "🔴")

# ══════════════════════════════════════════════
#  Link / type detectors
# ══════════════════════════════════════════════

def isLink(_, __, update):
    if update.text:
        if "/content/" in str(update.text) or "/home" in str(update.text):
            return True
        if update.text.startswith("magnet:?xt=urn:btih:"):
            return True
        parsed = urlparse(update.text)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return True
    return False

def is_google_drive(link): return "drive.google.com" in link
def is_mega(link):         return "mega.nz" in link
def is_terabox(link):      return "terabox" in link or "1024tera" in link
def is_ytdl_link(link):    return "youtube.com" in link or "youtu.be" in link
def is_telegram(link):     return "t.me" in link
def is_torrent(link):      return "magnet" in link or ".torrent" in link

# ══════════════════════════════════════════════
#  Time / size
# ══════════════════════════════════════════════

def getTime(seconds):
    seconds = max(0, int(seconds))
    d = seconds // 86400; seconds %= 86400
    h = seconds // 3600;  seconds %= 3600
    m = seconds // 60;    seconds %= 60
    if d: return f"{d}d {h:02d}h {m:02d}m {seconds:02d}s"
    if h: return f"{h:02d}h {m:02d}m {seconds:02d}s"
    if m: return f"{m:02d}m {seconds:02d}s"
    return f"{seconds:02d}s"

def sizeUnit(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"

# ══════════════════════════════════════════════
#  File helpers
# ══════════════════════════════════════════════

def fileType(fp: str):
    ext_map = {
        ".mp4":"video",".avi":"video",".mkv":"video",".m2ts":"video",
        ".mov":"video",".ts":"video",".webm":"video",".m4v":"video",
        ".mpg":"video",".mpeg":"video",".vob":"video",
        ".mp3":"audio",".wav":"audio",".flac":"audio",".aac":"audio",".ogg":"audio",".m4a":"audio",
        ".jpg":"photo",".jpeg":"photo",".png":"photo",".bmp":"photo",".gif":"photo",
    }
    _, ext = ospath.splitext(fp)
    return ext_map.get(ext.lower(), "document")

def shortFileName(path):
    if ospath.isfile(path):
        d, f = ospath.split(path)
        if len(f) > 60:
            b, e = ospath.splitext(f)
            f = b[:60 - len(e)] + e
            path = ospath.join(d, f)
    elif ospath.isdir(path):
        d, dn = ospath.split(path)
        if len(dn) > 60: path = ospath.join(d, dn[:60])
    return path

def getSize(path):
    if ospath.isfile(path): return ospath.getsize(path)
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns: total += ospath.getsize(ospath.join(dp, f))
    return total

def videoExtFix(fp: str):
    if fp.endswith((".mp4", ".mkv")): return fp
    new = fp + ".mp4"
    os.rename(fp, new)
    return new

def thumbMaintainer(fp):
    if ospath.exists(Paths.VIDEO_FRAME): os.remove(Paths.VIDEO_FRAME)
    try:
        fname, _ = ospath.splitext(ospath.basename(fp))
        ytdl_thmb = f"{Paths.WORK_PATH}/ytdl_thumbnails/{fname}.webp"
        with VideoFileClip(fp) as video:
            if ospath.exists(Paths.THMB_PATH): return Paths.THMB_PATH, video.duration
            elif ospath.exists(ytdl_thmb):     return convertIMG(ytdl_thmb), video.duration
            else:
                video.save_frame(Paths.VIDEO_FRAME, t=math.floor(video.duration / 2))
                return Paths.VIDEO_FRAME, video.duration
    except Exception as e:
        logging.warning(f"Thumb: {e}")
        return (Paths.THMB_PATH if ospath.exists(Paths.THMB_PATH) else Paths.HERO_IMAGE), 0

async def setThumbnail(message):
    try:
        if ospath.exists(Paths.THMB_PATH): os.remove(Paths.THMB_PATH)
        loop = get_event_loop()
        await loop.create_task(message.download(file_name=Paths.THMB_PATH))
        BOT.Setting.thumbnail = True
        return True
    except Exception as e:
        BOT.Setting.thumbnail = False
        logging.warning(f"Thumbnail: {e}")
        return False

def isYtdlComplete():
    for _d, _, fns in os.walk(Paths.down_path):
        for f in fns:
            _, ext = ospath.splitext(f)
            if ext in [".part", ".ytdl"]: return False
    return True

def convertIMG(image_path):
    img = Image.open(image_path)
    if img.mode != "RGB": img = img.convert("RGB")
    out = ospath.splitext(image_path)[0] + ".jpg"
    img.save(out, "JPEG")
    os.remove(image_path)
    return out

def applyCustomName():
    if BOT.Options.custom_name and BOT.Mode.type not in ["zip","undzip"]:
        for f in os.listdir(Paths.down_path):
            os.rename(
                ospath.join(Paths.down_path, f),
                ospath.join(Paths.down_path, BOT.Options.custom_name),
            )

def speedETA(start, done, total):
    pct     = min((done / total) * 100, 100) if total else 0
    elapsed = (datetime.now() - start).seconds
    if done > 0 and elapsed:
        raw   = done / elapsed
        speed = f"{sizeUnit(raw)}/s"
        eta   = (total - done) / raw
    else:
        speed, eta = "N/A", 0
    return speed, eta, pct

def isTimeOver(interval: float = 3.0):
    passed = time() - BotTimes.current_time >= interval
    if passed: BotTimes.current_time = time()
    return passed

async def message_deleter(m1, m2):
    for m in (m1, m2):
        try: await m.delete()
        except Exception: pass

def multipartArchive(path, type_, remove):
    dirname, filename = ospath.split(path)
    name, _ = ospath.splitext(filename)
    c, size, rname = 1, 0, name
    if type_ == "rar":
        name_, _ = ospath.splitext(name); rname = name_
        na_p = name_ + ".part" + str(c) + ".rar"
        p_ap = ospath.join(dirname, na_p)
        while ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap); c += 1
            na_p = name_ + ".part" + str(c) + ".rar"
            p_ap = ospath.join(dirname, na_p)
    elif type_ == "7z":
        na_p = name + "." + str(c).zfill(3)
        p_ap = ospath.join(dirname, na_p)
        while ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap); c += 1
            na_p = name + "." + str(c).zfill(3)
            p_ap = ospath.join(dirname, na_p)
    elif type_ == "zip":
        na_p = name + ".zip"; p_ap = ospath.join(dirname, na_p)
        if ospath.exists(p_ap):
            if remove: os.remove(p_ap); size += getSize(p_ap)
        na_p = name + ".z" + str(c).zfill(2)
        p_ap = ospath.join(dirname, na_p)
        while ospath.exists(p_ap):
            if remove: os.remove(p_ap)
            size += getSize(p_ap); c += 1
            na_p = name + ".z" + str(c).zfill(2)
            p_ap = ospath.join(dirname, na_p)
        if rname.endswith(".zip"): rname, _ = ospath.splitext(rname)
    return rname, size

# ══════════════════════════════════════════════
#  sysINFO — compact strip
# ══════════════════════════════════════════════

def sysINFO():
    ram  = psutil.Process(os.getpid()).memory_info().rss
    disk = psutil.disk_usage("/")
    cpu  = psutil.cpu_percent()
    return (
        "\n"
        f"║  🖥 CPU [{_pct_bar(cpu,8)}] {cpu:.0f}%{' '*(10-len(str(int(cpu))))}║\n"
        f"║  💾 RAM {sizeUnit(ram):<26}║\n"
        f"║  💿 DISK FREE {sizeUnit(disk.free):<20}║"
    )

# ══════════════════════════════════════════════
#  FUTURISTIC STATUS BAR — thème dashboard
# ══════════════════════════════════════════════

async def status_bar(down_msg, speed, percentage, eta, done, left, engine):
    pct     = float(percentage)
    bar     = _bar_ui(pct, 24)
    elapsed = getTime((datetime.now() - BotTimes.start_time).seconds)
    fname   = Messages.download_name or "?"

    # Clean status text
    status_clean = down_msg.strip().replace("<b>","").replace("</b>","")

    text = (
        f"⚡ <b>VIDEO STUDIO AI</b>  ·  CORE v4.2\n"
        f"{_SEP}\n\n"
        f"📁 <b>{fname[:36]}</b>\n\n"
        f"⚙️ {status_clean}\n"
        f"🚀 <b>{speed}</b>  ·  ⏱ ETA {eta}  ·  🕰 {elapsed}\n\n"
        f"<code>[{bar}]</code>  <b>{pct:.0f}%</b>\n\n"
        f"📊 <b>{done}</b>  /  {left}  ·  💡 {engine}\n"
        f"{_SEP}"
    )

    try:
        if isTimeOver() and MSG.status_msg:
            await MSG.status_msg.edit_text(
                text=text,
                disable_web_page_preview=True,
                reply_markup=keyboard(),
            )
    except BadRequest as e:
        logging.debug(f"Status unchanged: {e}")
    except Exception as e:
        logging.warning(f"Status bar: {e}")

# ══════════════════════════════════════════════
#  COMPLETION CARD
# ══════════════════════════════════════════════

def completion_card(fname: str, orig_size: int, final_size: int, duration_s: int, mode: str) -> str:
    reduction = ""
    if orig_size > 0 and final_size > 0 and final_size != orig_size:
        pct  = (1 - final_size / orig_size) * 100
        sign = "🔻" if pct > 0 else "🔺"
        reduction = f"\n{_field(sign, 'REDUCTION', f'{abs(pct):.0f}%')}"

    return (
        f"✅ <b>TASK COMPLETED</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📁', 'FILE', fname[:32])}\n"
        f"{_field('📦', 'SIZE', sizeUnit(final_size))}\n"
        f"{reduction}\n"
        f"{_field('⏱', 'TIME', getTime(duration_s))}\n"
        f"{_field('⚙', 'MODE', mode.upper())}\n\n"
        f"{_SEP}\n"
        f"🟢 <b>PROCESS SUCCESSFUL</b>"
    )

# ══════════════════════════════════════════════
#  ERROR CARD
# ══════════════════════════════════════════════

def error_card(reason: str, suggestion: str = "") -> str:
    text = (
        f"🔴 <b>PROCESS FAILURE</b>\n"
        f"{_SEP}\n\n"
        f"{_field('⚠', 'REASON', reason[:40])}\n"
    )
    if suggestion:
        text += f"{_field('💡', 'SUGGESTION', suggestion[:40])}\n"
    text += f"\n{_SEP}"
    return text

# ══════════════════════════════════════════════
#  QUEUE CARD
# ══════════════════════════════════════════════

def queue_card(position: int, total: int, label: str) -> str:
    return (
        f"🟡 <b>QUEUED</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📋', 'POSITION', f'{position} / {total}')}\n"
        f"{_field('📁', 'JOB', label[:32])}\n\n"
        f"{_SEP}\n"
        f"⏳ <i>Waiting for available slot...</i>"
    )

# ══════════════════════════════════════════════
#  HISTORY CARD
# ══════════════════════════════════════════════

def history_card(records: list) -> str:
    if not records:
        return f"📋 <b>DOWNLOAD HISTORY</b>\n{_SEP}\n\n<i>No records yet.</i>"
    lines = [f"📋 <b>DOWNLOAD HISTORY</b>\n{_SEP}"]
    for i, r in enumerate(records[-10:], 1):
        icon = "✅" if r.get("status") == "ok" else "❌"
        name = r.get("name", "?")[:28]
        sz   = sizeUnit(r.get("size", 0))
        lines.append(
            f"\n{i:02d}. {icon} <b>{name}</b>\n"
            f"    📦 {sz}  ·  🕐 {r.get('time','?')}  ·  ⏱ {r.get('duration','?')}"
        )
    return "\n".join(lines)

# ══════════════════════════════════════════════
#  SETTINGS PANEL
# ══════════════════════════════════════════════

async def send_settings(client, message, msg_id, command: bool):
    pr   = "—" if BOT.Setting.prefix == "" else f"«{BOT.Setting.prefix}»"
    su   = "—" if BOT.Setting.suffix == "" else f"«{BOT.Setting.suffix}»"
    thmb = "✅ Set" if BOT.Setting.thumbnail else "❌ None"
    up   = "document" if not BOT.Options.stream_upload else "media"
    tog  = "📄 → Media" if not BOT.Options.stream_upload else "🎞 → Doc"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(tog,              callback_data=up),
         InlineKeyboardButton("🎥 Video",       callback_data="video")],
        [InlineKeyboardButton("✏️ Caption",     callback_data="caption"),
         InlineKeyboardButton("🖼 Thumbnail",   callback_data="thumb")],
        [InlineKeyboardButton("⬅️ Prefix",      callback_data="set-prefix"),
         InlineKeyboardButton("Suffix ➡️",      callback_data="set-suffix")],
        [InlineKeyboardButton("✖ Close",        callback_data="close")],
    ])

    text = (
        f"⚙️ <b>BOT SETTINGS</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📤', 'UPLOAD', BOT.Setting.stream_upload)}\n"
        f"{_field('✂', 'SPLIT', BOT.Setting.split_video)}\n"
        f"{_field('🔄', 'CONVERT', BOT.Setting.convert_video)}\n"
        f"{_field('✏', 'CAPTION', BOT.Setting.caption)}\n"
        f"{_field('⬅', 'PREFIX', pr)}\n"
        f"{_field('➡', 'SUFFIX', su)}\n"
        f"{_field('🖼', 'THUMB', thmb)}\n\n"
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
    except BadRequest as e:
        logging.debug(f"Settings unchanged: {e}")
    except Exception as e:
        logging.warning(f"Settings: {e}")

def keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ TERMINATE", callback_data="cancel"),
        InlineKeyboardButton("📊 LOGS",      callback_data="adv_logs"),
    ]])
