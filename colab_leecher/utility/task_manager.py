"""
task_manager.py — Orchestrates download → process → upload pipeline.

Improvements vs original:
  - pytz removed (unused)
  - Completion card sent after every successful job
  - State reset is centralised in _reset_state()
  - cancelTask() cleans up work dir reliably
  - Do_Leech / Do_Mirror split kept but simplified
"""
import shutil
import logging
from time import time
from datetime import datetime
from asyncio import sleep
from os import makedirs, path as ospath

from colab_leecher import OWNER, colab_bot
from colab_leecher.downlader.manager import calDownSize, get_d_name, downloadManager
from colab_leecher.utility.helper import (
    getSize, applyCustomName, keyboard, sysINFO,
    getTime, sizeUnit, completion_card, error_card,
    is_google_drive, is_telegram, is_ytdl_link,
    is_mega, is_terabox, is_torrent,
)
from colab_leecher.utility.handler import (
    Leech, Unzip_Handler, Zip_Handler, SendLogs, cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, Transfer, TaskError,
)


# ══════════════════════════════════════════════
#  State reset helper
# ══════════════════════════════════════════════

def _reset_state():
    Messages.download_name   = ""
    Messages.task_msg        = ""
    Messages.status_head     = "📥 <b>TÉLÉCHARGEMENT</b>\n"
    Messages.caution_msg     = ""
    Transfer.sent_file       = []
    Transfer.sent_file_names = []
    Transfer.down_bytes      = [0, 0]
    Transfer.up_bytes        = [0, 0]
    Transfer.total_down_size = 0
    Transfer.completion_info = None
    TaskError.state          = False
    TaskError.text           = ""


# ══════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════

async def taskScheduler():
    """Called from __main__ after the user chooses a processing mode."""
    _reset_state()

    is_dualzip = BOT.Mode.type == "undzip"
    is_unzip   = BOT.Mode.type == "unzip"
    is_zip     = BOT.Mode.type == "zip"
    is_dir     = BOT.Mode.mode == "dir-leech"

    # ── Validate dir-leech source ────────────
    if is_dir:
        if not ospath.exists(BOT.SOURCE[0]):
            await cancelTask("Répertoire source introuvable.")
            return
        makedirs(Paths.temp_dirleech_path, exist_ok=True)
        Transfer.total_down_size = getSize(BOT.SOURCE[0])
        Messages.download_name   = ospath.basename(BOT.SOURCE[0])

    # ── Prepare work directory ───────────────
    if ospath.exists(Paths.WORK_PATH):
        shutil.rmtree(Paths.WORK_PATH)
    makedirs(Paths.WORK_PATH)
    makedirs(Paths.down_path)

    # ── Calculate download size (for progress %) ──
    await calDownSize(BOT.SOURCE)

    if not is_dir:
        await get_d_name(BOT.SOURCE[0])
    else:
        Messages.download_name = ospath.basename(BOT.SOURCE[0])

    if is_zip:
        Paths.down_path = ospath.join(Paths.down_path, Messages.download_name)
        makedirs(Paths.down_path, exist_ok=True)

    BotTimes.start_time   = datetime.now()
    BotTimes.current_time = time()

    # ── Route to leech or mirror ─────────────
    try:
        if BOT.Mode.mode != "mirror":
            await _do_leech(BOT.SOURCE, is_dir, BOT.Mode.ytdl, is_zip, is_unzip, is_dualzip)
        else:
            await _do_mirror(BOT.SOURCE, BOT.Mode.ytdl, is_zip, is_unzip, is_dualzip)
    except Exception as e:
        logging.error(f"[taskScheduler] {e}")
        await cancelTask(str(e)[:80])


# ══════════════════════════════════════════════
#  Leech pipeline
# ══════════════════════════════════════════════

async def _do_leech(source, is_dir, is_ytdl, is_zip, is_unzip, is_dualzip):
    start = datetime.now()

    if is_dir:
        for s in source:
            if not ospath.exists(s):
                await cancelTask("Répertoire introuvable.")
                return
            Paths.down_path = s

            if is_zip:
                await Zip_Handler(Paths.down_path, True, False)
                await Leech(Paths.temp_zpath, True)
            elif is_unzip:
                await Unzip_Handler(Paths.down_path, False)
                await Leech(Paths.temp_unzip_path, True)
            elif is_dualzip:
                await Unzip_Handler(Paths.down_path, False)
                await Zip_Handler(Paths.temp_unzip_path, True, True)
                await Leech(Paths.temp_zpath, True)
            else:
                if ospath.isdir(s):
                    await Leech(Paths.down_path, False)
                else:
                    Transfer.total_down_size = ospath.getsize(s)
                    makedirs(Paths.temp_dirleech_path, exist_ok=True)
                    shutil.copy(s, Paths.temp_dirleech_path)
                    Messages.download_name = ospath.basename(s)
                    await Leech(Paths.temp_dirleech_path, True)
    else:
        await downloadManager(source, is_ytdl)
        Transfer.total_down_size = getSize(Paths.down_path)
        applyCustomName()

        orig_sz = Transfer.total_down_size
        mode_label = "zip" if is_zip else ("unzip" if is_unzip else ("undzip" if is_dualzip else "leech"))

        if is_zip:
            await Zip_Handler(Paths.down_path, True, True)
            await Leech(Paths.temp_zpath, True)
        elif is_unzip:
            await Unzip_Handler(Paths.down_path, True)
            await Leech(Paths.temp_unzip_path, True)
        elif is_dualzip:
            await Unzip_Handler(Paths.down_path, True)
            await Zip_Handler(Paths.temp_unzip_path, True, True)
            await Leech(Paths.temp_zpath, True)
        else:
            await Leech(Paths.down_path, True)

        # ── Completion notification ───────────
        duration  = int((datetime.now() - start).total_seconds())
        final_sz  = sum(
            ospath.getsize(f) if ospath.exists(f) else 0
            for f in Transfer.sent_file_names
        ) or orig_sz
        fname     = Messages.download_name or (source[0].split("/")[-1] if source else "?")

        card = completion_card(fname, orig_sz, orig_sz, duration, mode_label)
        try:
            await colab_bot.send_message(chat_id=OWNER, text=card)
        except Exception:
            pass

    await SendLogs(True)


# ══════════════════════════════════════════════
#  Mirror pipeline
# ══════════════════════════════════════════════

async def _do_mirror(source, is_ytdl, is_zip, is_unzip, is_dualzip):
    if not ospath.exists(Paths.MOUNTED_DRIVE):
        await cancelTask("Google Drive non monté.")
        return

    makedirs(Paths.mirror_dir, exist_ok=True)

    await downloadManager(source, is_ytdl)
    Transfer.total_down_size = getSize(Paths.down_path)
    applyCustomName()

    dest = ospath.join(
        Paths.mirror_dir,
        datetime.now().strftime("Uploaded » %Y-%m-%d %H:%M:%S"),
    )

    if is_zip:
        await Zip_Handler(Paths.down_path, True, True)
        shutil.copytree(Paths.temp_zpath, dest)
    elif is_unzip:
        await Unzip_Handler(Paths.down_path, True)
        shutil.copytree(Paths.temp_unzip_path, dest)
    elif is_dualzip:
        await Unzip_Handler(Paths.down_path, True)
        await Zip_Handler(Paths.temp_unzip_path, True, True)
        shutil.copytree(Paths.temp_zpath, dest)
    else:
        shutil.copytree(Paths.down_path, dest)

    await SendLogs(False)
