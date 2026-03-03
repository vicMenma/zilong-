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
    return quote(url, safe=":/?=&%+@#.,!~*'();$-_%")


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


async def _get_duration(filepath: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except Exception:
        return 0.0


async def _download_tg_file(message, dest_dir: str, status_msg=None) -> str:
    """Download a Telegram file message to dest_dir. Returns local path."""
    makedirs(dest_dir, exist_ok=True)
    media = message.video or message.document or message.audio
    if not media:
        raise RuntimeError("No downloadable media in message")
    fname = getattr(media, "file_name", None) or f"file_{media.file_id[-8:]}.mp4"
    dest  = ospath.join(dest_dir, fname)
    if status_msg:
        try:
            await status_msg.edit_text(
                f"⬇️ <b>DOWNLOADING FROM TELEGRAM...</b>\n{_SEP}\n\n"
                f"{_field('📁', 'File', fname[:40])}\n\n<i>Please wait...</i>"
            )
        except Exception:
            pass
    await message.download(file_name=dest)
    return dest


async def _run_ffmpeg(
    cmd: list,
    status_msg=None,
    status_text: str = "⚙️ Processing...",
    total_duration: float = 0.0,
    label: str = "",
) -> None:
    """Run ffmpeg with real-time progress parsed from stderr.
    
    Shows live progress in the unified panel (if slot context is active)
    or falls back to editing status_msg directly.
    """
    import re
    import time
    from colab_leecher.utility.variables import _panel_slots, _slot_id

    # Add -progress pipe:1 -nostats to get machine-readable progress
    # ffmpeg writes progress to stdout when -progress is set
    full_cmd = []
    # Insert -progress and -nostats before output file (last arg)
    for i, arg in enumerate(cmd):
        full_cmd.append(arg)
    # We'll parse stderr for duration + time= lines instead (more compatible)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    op_label   = label or status_text
    start_time = time.time()
    duration   = total_duration  # seconds, 0 = unknown
    stderr_buf = []

    # Regex patterns
    re_duration = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
    re_time      = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
    re_speed     = re.compile(r"speed=\s*([\d.]+)x")
    re_size      = re.compile(r"size=\s*(\d+)kB")

    # Get slot context for panel integration
    try:
        sid = _slot_id.get()
    except Exception:
        sid = None

    async def _update_panel(pct, speed_str, eta_str, done_str):
        """Push progress into unified panel slot or edit status_msg."""
        if sid and sid in _panel_slots:
            _panel_slots[sid].update({
                "pct":    pct,
                "speed":  speed_str,
                "eta":    eta_str,
                "done":   done_str,
                "status": "PROCESSING",
                "engine": "ffmpeg",
                "name":   _panel_slots[sid].get("name", op_label),
            })
        elif status_msg:
            bar_filled = int(pct / 5)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            try:
                await status_msg.edit_text(
                    f"{op_label}\n{_SEP}\n\n"
                    f"[{bar}] {pct:.1f}%\n"
                    f"- Speed : {speed_str}\n"
                    f"- ETA   : {eta_str}\n"
                    f"- Done  : {done_str}"
                )
            except Exception:
                pass

    # Initial status
    if sid and sid in _panel_slots:
        _panel_slots[sid]["status"] = "PROCESSING"
        _panel_slots[sid]["name"]   = op_label
    elif status_msg:
        try:
            await status_msg.edit_text(
                f"{op_label}\n{_SEP}\n\n"
                f"[░░░░░░░░░░░░░░░░░░░░] 0%\n"
                f"<i>Starting ffmpeg...</i>"
            )
        except Exception:
            pass

    last_update = 0.0

    while True:
        line_bytes = await proc.stderr.readline()
        if not line_bytes:
            break
        line = line_bytes.decode(errors="replace").strip()
        stderr_buf.append(line)

        # Parse total duration from ffmpeg header
        if duration == 0:
            m = re_duration.search(line)
            if m:
                h, mn, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                duration = h * 3600 + mn * 60 + s

        # Parse progress time
        m_time = re_time.search(line)
        if m_time and duration > 0:
            h, mn, s = float(m_time.group(1)), float(m_time.group(2)), float(m_time.group(3))
            elapsed_encoded = h * 3600 + mn * 60 + s
            pct = min((elapsed_encoded / duration) * 100, 99.9)

            # Speed (e.g. 2.5x means encoding 2.5x real-time)
            m_spd = re_speed.search(line)
            speed_val = float(m_spd.group(1)) if m_spd else 0.0
            speed_str = f"{speed_val:.1f}x" if speed_val > 0 else "..."

            # ETA
            if speed_val > 0 and duration > 0:
                remaining = (duration - elapsed_encoded) / speed_val
                eta_str = getTime(int(remaining))
            else:
                wall = time.time() - start_time
                eta_str = getTime(int((wall / max(pct, 1)) * (100 - pct))) if pct > 0 else "..."

            # Size processed
            m_sz = re_size.search(line)
            done_mb = (int(m_sz.group(1)) / 1024) if m_sz else 0
            done_str = f"{done_mb:.1f} MB" if done_mb > 0 else "..."

            # Throttle panel updates to every 2s
            now = time.time()
            if now - last_update >= 2.0:
                last_update = now
                await _update_panel(pct, speed_str, eta_str, done_str)

    await proc.wait()

    if proc.returncode != 0:
        err_text = "\n".join(stderr_buf[-15:])
        raise RuntimeError(err_text[-400:])

    # Mark 100% done
    await _update_panel(100.0, "-", "Done", "✅")


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
        if st.get("use_tg_file") and st.get("file_message"):
            vid_in = await _download_tg_file(st["file_message"], _WORK_DIR, status)
            # re-point vid_in and vid_out with correct ext
            vid_out = ospath.join(_WORK_DIR, f"compressed_{chat_id}{ospath.splitext(vid_in)[1]}")
        else:
            await _download_url(url, vid_in, status)
        orig_sz = ospath.getsize(vid_in)

        # Get video duration for progress bar
        _dur = await _get_duration(vid_in)
        fname = ospath.basename(vid_in)

        # Register in unified panel if not already there
        from colab_leecher.utility.variables import _panel_slots, _slot_id
        from colab_leecher.utility.task_manager import _ensure_panel, _panel_lock, _panel_msg as _pm
        sid = None
        try: sid = _slot_id.get()
        except Exception: pass
        if not sid:
            # Video op running outside download slot — create a dedicated panel entry
            sid = f"vop_{chat_id}"
            _panel_slots[sid] = {
                "name": fname, "pct": 0, "speed": "...",
                "eta": "...", "done": "...", "left": "",
                "status": "PROCESSING", "engine": "ffmpeg",
            }
            await _ensure_panel(force_new=True)

        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", vid_in,
            "-c:v", "libx264", "-crf", str(crf),
            "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            vid_out,
        ], status, f"🗜 Compressing · {fname[:30]}", total_duration=_dur, label=f"🗜 {fname[:35]}")

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
        from colab_leecher.utility.variables import _panel_slots
        from colab_leecher.utility.task_manager import _remove_panel_if_empty
        _panel_slots.pop(f"vop_{chat_id}", None)
        await _remove_panel_if_empty()
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
        if st.get("use_tg_file") and st.get("file_message"):
            vid_in  = await _download_tg_file(st["file_message"], _WORK_DIR, status)
            vid_out = ospath.join(_WORK_DIR, f"res_{res}_{chat_id}{ospath.splitext(vid_in)[1]}")
        else:
            await _download_url(url, vid_in, status)
        orig_sz = ospath.getsize(vid_in)

        _dur = await _get_duration(vid_in)
        fname = ospath.basename(vid_in)
        from colab_leecher.utility.variables import _panel_slots, _slot_id
        sid = None
        try: sid = _slot_id.get()
        except Exception: pass
        if not sid:
            sid = f"vop_{chat_id}"
            _panel_slots[sid] = {
                "name": fname, "pct": 0, "speed": "...",
                "eta": "...", "done": "...", "left": "",
                "status": "PROCESSING", "engine": "ffmpeg",
            }
            from colab_leecher.utility.task_manager import _ensure_panel
            await _ensure_panel(force_new=True)

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
        from colab_leecher.utility.variables import _panel_slots
        from colab_leecher.utility.task_manager import _remove_panel_if_empty
        _panel_slots.pop(f"vop_{chat_id}", None)
        await _remove_panel_if_empty()
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
        # Download video (from Telegram or URL) + subtitle URL
        if st.get("use_tg_file") and st.get("file_message"):
            vid_in  = await _download_tg_file(st["file_message"], _WORK_DIR, status)
            vid_out = ospath.join(_WORK_DIR, f"burned_{chat_id}{ospath.splitext(vid_in)[1]}")
        else:
            await _download_url(video_url, vid_in, status)
        await _download_url(sub_url, sub_in, status)

        orig_sz = ospath.getsize(vid_in)

        # Build vf filter
        ext = ospath.splitext(sub_in)[1].lower()
        # ffmpeg subtitles filter needs escaped path on Linux
        esc = sub_in.replace("\\", "/").replace(":", "\\:")
        vf  = f"ass={esc}" if ext in (".ass", ".ssa") else f"subtitles={esc}"

        _dur = await _get_duration(vid_in)
        fname = ospath.basename(vid_in)
        from colab_leecher.utility.variables import _panel_slots, _slot_id
        sid = None
        try: sid = _slot_id.get()
        except Exception: pass
        if not sid:
            sid = f"vop_{chat_id}"
            _panel_slots[sid] = {
                "name": fname, "pct": 0, "speed": "...",
                "eta": "...", "done": "...", "left": "",
                "status": "PROCESSING", "engine": "ffmpeg",
            }
            from colab_leecher.utility.task_manager import _ensure_panel
            await _ensure_panel(force_new=True)

        await _run_ffmpeg([
            "ffmpeg", "-y", "-i", vid_in,
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18",
            "-preset", "fast",
            "-c:a", "copy",
            vid_out,
        ], status, f"💬 Burning subs · {fname[:30]}", total_duration=_dur, label=f"💬 {fname[:35]}")

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
        from colab_leecher.utility.variables import _panel_slots
        from colab_leecher.utility.task_manager import _remove_panel_if_empty
        _panel_slots.pop(f"vop_{chat_id}", None)
        await _remove_panel_if_empty()
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
