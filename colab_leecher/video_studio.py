"""
video_studio.py
──────────────────────────────────────────────
Button-based video processing panel.
Flow:
  /vstudio  →  panel with 4 ops
  → user picks op (compress / resolution / burnsub / forward)
  → bot asks for URL(s) in chat
  → bot processes & uploads
  
State per chat stored in _vs_state dict.
"""
import os
import logging
import asyncio
import subprocess
import urllib.request
from datetime import datetime
from urllib.parse import quote
from os import makedirs, path as ospath
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.helper import (
    _SEP, _field, error_card, sizeUnit, getTime,
)
from colab_leecher.utility.variables import MSG, Transfer

# ── per-chat state ───────────────────────────
# state keys:
#   "op"       : "compress" | "resolution" | "burnsub" | "forward"
#   "step"     : "await_video" | "await_sub" | "await_quality" | "await_res"
#   "video_url": str
#   "sub_url"  : str
#   "quality"  : "high"|"med"|"low"
#   "res"      : "1080p" etc
#   "status_msg": message object

_vs_state: dict = {}

_WORK_DIR = "/content/zilong_vstudio"

def _safe_url(url: str) -> str:
    """Encode brackets and special chars that break aria2c."""
    return quote(url, safe=":/?=&%+@#.,!~*'();$-_")


RES_MAP = {
    "1080p": "1920:1080",
    "720p":  "1280:720",
    "480p":  "854:480",
    "360p":  "640:360",
}

CRF_MAP = {
    "high": 20,
    "med":  28,
    "low":  35,
}


# ── keyboards ────────────────────────────────

def kb_vstudio_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗜 Compress",    callback_data="vs_op_compress"),
            InlineKeyboardButton("📐 Resolution",  callback_data="vs_op_resolution"),
        ],
        [
            InlineKeyboardButton("💬 Burn Subs",   callback_data="vs_op_burnsub"),
            InlineKeyboardButton("📨 Re-upload",   callback_data="vs_op_forward"),
        ],
        [InlineKeyboardButton("✖ Close",           callback_data="vs_close")],
    ])

def kb_quality() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔝 High  (CRF 20)", callback_data="vs_quality_high"),
            InlineKeyboardButton("⚖ Med   (CRF 28)", callback_data="vs_quality_med"),
            InlineKeyboardButton("📉 Low   (CRF 35)", callback_data="vs_quality_low"),
        ],
        [InlineKeyboardButton("⏎ Back", callback_data="vs_back")],
    ])

def kb_resolution() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖥 1080p", callback_data="vs_res_1080p"),
            InlineKeyboardButton("📺 720p",  callback_data="vs_res_720p"),
        ],
        [
            InlineKeyboardButton("📱 480p",  callback_data="vs_res_480p"),
            InlineKeyboardButton("🔲 360p",  callback_data="vs_res_360p"),
        ],
        [InlineKeyboardButton("⏎ Back", callback_data="vs_back")],
    ])

def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏎ Back", callback_data="vs_back"),
        InlineKeyboardButton("✖ Close", callback_data="vs_close"),
    ]])


# ── panel text ───────────────────────────────

def _panel_text() -> str:
    return (
        f"🎬 <b>VIDEO STUDIO</b>\n"
        f"{_SEP}\n\n"
        f"{_field('🗜', 'Compress', 'Reduce file size (CRF)')}\n"
        f"{_field('📐', 'Resolution', '1080p · 720p · 480p · 360p')}\n"
        f"{_field('💬', 'Burn Subs', 'Hard-burn ASS/SRT into video')}\n"
        f"{_field('📨', 'Re-upload', 'Forward any Telegram file')}\n\n"
        f"{_SEP}\n"
        f"<b>Pick an operation:</b>"
    )


# ── helpers ──────────────────────────────────

def _clear(chat_id: int):
    _vs_state.pop(chat_id, None)

async def _edit(chat_id: int, text: str, kb=None):
    st = _vs_state.get(chat_id, {})
    msg = st.get("status_msg")
    try:
        if msg:
            await msg.edit_text(text, reply_markup=kb)
    except Exception as e:
        logging.debug(f"VS edit: {e}")

async def _download_url(url: str, dest: str, status_msg=None) -> str:
    """Download url to dest file using aria2c. Returns dest path."""
    makedirs(ospath.dirname(dest), exist_ok=True)
    safe = _safe_url(url)
    cmd = [
        "aria2c",
        "--allow-overwrite=true",
        "--max-connection-per-server=8",
        "--split=4",
        "--console-log-level=error",
        "--summary-interval=0",
        "-d", ospath.dirname(dest),
        "-o", ospath.basename(dest),
        safe,
    ]
    if status_msg:
        try:
            await status_msg.edit_text(
                f"⬇️ <b>DOWNLOADING...</b>\n{_SEP}\n\n"
                f"{_field('📁', 'URL', url[:40])}\n\n"
                f"<i>Please wait...</i>"
            )
        except Exception:
            pass
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not ospath.exists(dest):
        raise RuntimeError(f"Download failed: {stderr.decode()[-200:]}")
    return dest


async def _run_ffmpeg(cmd: list, status_msg=None, status_text: str = "⚙️ Processing...") -> None:
    """Run ffmpeg command, editing status_msg while waiting."""
    if status_msg:
        try:
            await status_msg.edit_text(
                f"{status_text}\n{_SEP}\n\n<i>Please wait...</i>"
            )
        except Exception:
            pass
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-300:])


# ── operation runners ─────────────────────────

async def _do_compress(chat_id: int):
    st     = _vs_state.get(chat_id, {})
    url    = st["video_url"]
    crf    = CRF_MAP.get(st.get("quality", "med"), 28)
    status = st.get("status_msg")
    start  = datetime.now()

    makedirs(_WORK_DIR, exist_ok=True)
    vid_in  = f"{_WORK_DIR}/input_{chat_id}.mp4"
    vid_out = f"{_WORK_DIR}/compressed_{chat_id}.mp4"

    try:
        await _download_url(url, vid_in, status)
        orig_sz = ospath.getsize(vid_in)

        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", vid_in,
            "-c:v", "libx264", "-crf", str(crf),
            "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            vid_out,
        ], status, "🗜 <b>COMPRESSING...</b>")

        final_sz = ospath.getsize(vid_out)
        duration = int((datetime.now() - start).total_seconds())
        pct      = (1 - final_sz / orig_sz) * 100 if orig_sz else 0

        if status:
            try:
                await status.edit_text(
                    f"📤 <b>UPLOADING...</b>\n{_SEP}\n\n"
                    f"{_field('📦', 'Size', f'{sizeUnit(orig_sz)} → {sizeUnit(final_sz)}')}\n"
                    f"{_field('🔻', 'Reduction', f'{pct:.0f}%')}\n"
                    f"{_field('⏱', 'Time', getTime(duration))}"
                )
            except Exception:
                pass

        Transfer.completion_info = {
            "orig_sz": orig_sz, "final_sz": final_sz,
            "_start": start, "mode": "compress",
        }
        MSG.status_msg = status
        from colab_leecher.uploader.telegram import upload_file
        fname = f"compressed_{ospath.basename(url.split('?')[0])[:40]}.mp4"
        await upload_file(vid_out, fname, is_last=True)

    except Exception as e:
        logging.error(f"VS compress: {e}")
        if status:
            try: await status.edit_text(error_card(str(e)[:50], "Check URL/format"))
            except Exception: pass
    finally:
        for f in (vid_in, vid_out):
            try: os.remove(f)
            except Exception: pass
        _clear(chat_id)


async def _do_resolution(chat_id: int):
    st     = _vs_state.get(chat_id, {})
    url    = st["video_url"]
    res    = st.get("res", "720p")
    target = RES_MAP.get(res, "1280:720")
    status = st.get("status_msg")
    start  = datetime.now()

    makedirs(_WORK_DIR, exist_ok=True)
    vid_in  = f"{_WORK_DIR}/input_{chat_id}.mp4"
    vid_out = f"{_WORK_DIR}/res_{res}_{chat_id}.mp4"

    try:
        await _download_url(url, vid_in, status)
        orig_sz = ospath.getsize(vid_in)

        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", vid_in,
            "-vf", f"scale={target}",
            "-c:v", "libx264", "-crf", "23",
            "-preset", "fast",
            "-c:a", "copy",
            vid_out,
        ], status, f"📐 <b>SCALING TO {res.upper()}...</b>")

        final_sz = ospath.getsize(vid_out)
        duration = int((datetime.now() - start).total_seconds())

        Transfer.completion_info = {
            "orig_sz": orig_sz, "final_sz": final_sz,
            "_start": start, "mode": f"res_{res}",
        }
        MSG.status_msg = status
        from colab_leecher.uploader.telegram import upload_file
        fname = f"{res}_{ospath.basename(url.split('?')[0])[:40]}.mp4"
        await upload_file(vid_out, fname, is_last=True)

    except Exception as e:
        logging.error(f"VS resolution: {e}")
        if status:
            try: await status.edit_text(error_card(str(e)[:50], "Check URL/format"))
            except Exception: pass
    finally:
        for f in (vid_in, vid_out):
            try: os.remove(f)
            except Exception: pass
        _clear(chat_id)


async def _do_burnsub(chat_id: int):
    st        = _vs_state.get(chat_id, {})
    video_url = st["video_url"]
    sub_url   = st["sub_url"]
    status    = st.get("status_msg")
    start     = datetime.now()

    makedirs(_WORK_DIR, exist_ok=True)
    vid_in   = f"{_WORK_DIR}/input_{chat_id}.mp4"
    sub_ext  = ospath.splitext(sub_url.split("?")[0])[1] or ".srt"
    sub_in   = f"{_WORK_DIR}/sub_{chat_id}{sub_ext}"
    vid_out  = f"{_WORK_DIR}/burned_{chat_id}.mp4"

    try:
        # Download both files
        await _download_url(video_url, vid_in, status)
        await _download_url(sub_url, sub_in, status)

        orig_sz = ospath.getsize(vid_in)

        # Build vf filter
        ext = ospath.splitext(sub_in)[1].lower()
        # ffmpeg subtitles filter needs escaped path on Linux
        esc = sub_in.replace("\\", "/").replace(":", "\\:")
        vf  = f"ass={esc}" if ext in (".ass", ".ssa") else f"subtitles={esc}"

        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", vid_in,
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18",
            "-preset", "fast",
            "-c:a", "copy",
            vid_out,
        ], status, "💬 <b>BURNING SUBTITLES...</b>")

        final_sz = ospath.getsize(vid_out)
        Transfer.completion_info = {
            "orig_sz": orig_sz, "final_sz": final_sz,
            "_start": start, "mode": "burnsub",
        }
        MSG.status_msg = status
        from colab_leecher.uploader.telegram import upload_file
        fname = f"subbed_{ospath.basename(video_url.split('?')[0])[:40]}.mp4"
        await upload_file(vid_out, fname, is_last=True)

    except Exception as e:
        logging.error(f"VS burnsub: {e}")
        if status:
            try: await status.edit_text(error_card(str(e)[:50], "Check sub format/encoding"))
            except Exception: pass
    finally:
        for f in (vid_in, sub_in, vid_out):
            try: os.remove(f)
            except Exception: pass
        _clear(chat_id)


# ── public API called from __main__ ──────────

async def vstudio_open(message):
    """Send the Video Studio panel."""
    msg = await message.reply_text(_panel_text(), reply_markup=kb_vstudio_main())
    _vs_state[message.chat.id] = {"status_msg": msg}


async def vstudio_callback(cq):
    """Handle all vs_* callbacks."""
    data    = cq.data
    chat_id = cq.message.chat.id

    if data == "vs_close":
        _clear(chat_id)
        try: await cq.message.delete()
        except Exception: pass
        return

    if data == "vs_back":
        _clear(chat_id)
        _vs_state[chat_id] = {"status_msg": cq.message}
        try:
            await cq.message.edit_text(_panel_text(), reply_markup=kb_vstudio_main())
        except Exception: pass
        return

    if data == "vs_op_compress":
        _vs_state[chat_id] = {"op": "compress", "status_msg": cq.message}
        try:
            await cq.message.edit_text(
                f"🗜 <b>COMPRESS VIDEO</b>\n{_SEP}\n\n"
                f"<b>Step 1/2</b> — Send the video URL:",
                reply_markup=kb_back(),
            )
        except Exception: pass
        return

    if data == "vs_op_resolution":
        _vs_state[chat_id] = {"op": "resolution", "status_msg": cq.message}
        try:
            await cq.message.edit_text(
                f"📐 <b>CHANGE RESOLUTION</b>\n{_SEP}\n\n"
                f"<b>Step 1/2</b> — Send the video URL:",
                reply_markup=kb_back(),
            )
        except Exception: pass
        return

    if data == "vs_op_burnsub":
        _vs_state[chat_id] = {"op": "burnsub", "status_msg": cq.message}
        try:
            await cq.message.edit_text(
                f"💬 <b>BURN SUBTITLES</b>\n{_SEP}\n\n"
                f"<b>Step 1/3</b> — Send the video URL:",
                reply_markup=kb_back(),
            )
        except Exception: pass
        return

    if data == "vs_op_forward":
        _vs_state[chat_id] = {"op": "forward", "status_msg": cq.message}
        try:
            await cq.message.edit_text(
                f"📨 <b>RE-UPLOAD FILE</b>\n{_SEP}\n\n"
                f"Send any Telegram file to re-upload it to your chat.",
                reply_markup=kb_back(),
            )
        except Exception: pass
        return

    # Quality selection
    if data.startswith("vs_quality_"):
        q = data.split("_")[2]   # high / med / low
        st = _vs_state.get(chat_id, {})
        st["quality"] = q
        _vs_state[chat_id] = st
        try:
            await cq.message.edit_text(
                f"🗜 <b>COMPRESS VIDEO</b>\n{_SEP}\n\n"
                f"{_field('⚙', 'Quality', q.upper())}\n"
                f"{_field('📊', 'CRF', str(CRF_MAP[q]))}\n\n"
                f"<i>Starting...</i>"
            )
        except Exception: pass
        asyncio.get_event_loop().create_task(_do_compress(chat_id))
        return

    # Resolution selection
    if data.startswith("vs_res_"):
        res = data.split("_")[2]   # 1080p / 720p / etc
        st = _vs_state.get(chat_id, {})
        st["res"] = res
        _vs_state[chat_id] = st
        try:
            await cq.message.edit_text(
                f"📐 <b>SCALING TO {res.upper()}</b>\n{_SEP}\n\n"
                f"<i>Starting...</i>"
            )
        except Exception: pass
        asyncio.get_event_loop().create_task(_do_resolution(chat_id))
        return


async def vstudio_handle_text(chat_id: int, text: str) -> bool:
    """
    Called from __main__ message handler when a plain text message arrives.
    Returns True if consumed by video studio, False otherwise.
    """
    st = _vs_state.get(chat_id)
    if not st or "op" not in st:
        return False

    op     = st["op"]
    status = st.get("status_msg")

    # ── COMPRESS: waiting for video URL ──────
    if op == "compress" and "video_url" not in st:
        st["video_url"] = text.strip()
        _vs_state[chat_id] = st
        try:
            await status.edit_text(
                f"🗜 <b>COMPRESS VIDEO</b>\n{_SEP}\n\n"
                f"{_field('📁', 'URL', text[:40])}\n\n"
                f"<b>Step 2/2</b> — Pick quality:",
                reply_markup=kb_quality(),
            )
        except Exception: pass
        return True

    # ── RESOLUTION: waiting for video URL ────
    if op == "resolution" and "video_url" not in st:
        st["video_url"] = text.strip()
        _vs_state[chat_id] = st
        try:
            await status.edit_text(
                f"📐 <b>CHANGE RESOLUTION</b>\n{_SEP}\n\n"
                f"{_field('📁', 'URL', text[:40])}\n\n"
                f"<b>Step 2/2</b> — Pick target resolution:",
                reply_markup=kb_resolution(),
            )
        except Exception: pass
        return True

    # ── BURNSUB: waiting for video URL ───────
    if op == "burnsub" and "video_url" not in st:
        st["video_url"] = text.strip()
        _vs_state[chat_id] = st
        try:
            await status.edit_text(
                f"💬 <b>BURN SUBTITLES</b>\n{_SEP}\n\n"
                f"{_field('🎬', 'Video', text[:40])}\n\n"
                f"<b>Step 2/3</b> — Send the subtitle URL (.srt/.ass):",
                reply_markup=kb_back(),
            )
        except Exception: pass
        return True

    # ── BURNSUB: waiting for sub URL ─────────
    if op == "burnsub" and "video_url" in st and "sub_url" not in st:
        st["sub_url"] = text.strip()
        _vs_state[chat_id] = st
        try:
            await status.edit_text(
                f"💬 <b>BURN SUBTITLES</b>\n{_SEP}\n\n"
                f"{_field('🎬', 'Video', st['video_url'][:40])}\n"
                f"{_field('💬', 'Subs', text[:40])}\n\n"
                f"<i>Starting...</i>"
            )
        except Exception: pass
        asyncio.get_event_loop().create_task(_do_burnsub(chat_id))
        return True

    return False


async def vstudio_handle_file(chat_id: int, message) -> bool:
    """Handle forwarded file for re-upload op."""
    st = _vs_state.get(chat_id)
    if not st or st.get("op") != "forward":
        return False

    status = st.get("status_msg")
    _clear(chat_id)

    if status:
        try:
            await status.edit_text(f"📨 <b>RE-UPLOADING...</b>\n{_SEP}")
        except Exception: pass
    try:
        await message.copy(chat_id=OWNER)
        if status:
            try: await status.delete()
            except Exception: pass
    except Exception as e:
        if status:
            try: await status.edit_text(error_card(str(e)[:50]))
            except Exception: pass
    return True
