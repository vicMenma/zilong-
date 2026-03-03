import os
import asyncio
import shutil
import logging
import pathlib
import subprocess
from asyncio import sleep
from time import time
from colab_leecher import OWNER, colab_bot
from natsort import natsorted
from datetime import datetime
from os import makedirs, path as ospath
from colab_leecher.uploader.telegram import upload_file
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Messages, Paths, Transfer
from colab_leecher.utility.converters import archive, extract, videoConverter, sizeChecker
from colab_leecher.utility.helper import (
    fileType, getSize, getTime, keyboard,
    shortFileName, sizeUnit, sysINFO, _pct_bar,
    completion_card, error_card,
    _SEP, _field,
)


async def Leech(folder_path: str, remove: bool):
    files = [str(p) for p in pathlib.Path(folder_path).glob("**/*") if p.is_file()]
    for f in natsorted(files):
        fp = ospath.join(folder_path, f)
        if BOT.Options.convert_video and fileType(fp) == "video":
            await videoConverter(fp)

    Transfer.total_down_size = getSize(folder_path)
    files = natsorted([str(p) for p in pathlib.Path(folder_path).glob("**/*") if p.is_file()])
    upload_queue = []

    for f in files:
        fp = ospath.join(folder_path, f)
        leech = await sizeChecker(fp, remove)
        if leech:
            if ospath.exists(fp) and remove: os.remove(fp)
            for part in natsorted(os.listdir(Paths.temp_zpath)):
                upload_queue.append(("split", ospath.join(Paths.temp_zpath, part)))
        else:
            upload_queue.append(("single", fp))

    total = len(upload_queue)
    split_cleaned = False

    for idx, (kind, fp) in enumerate(upload_queue):
        is_last = (idx == total - 1)

        if kind == "split":
            fname = ospath.basename(fp)
            new   = shortFileName(fp)
            os.rename(fp, new)
            BotTimes.current_time = time()
            Messages.status_head  = f"📤 UPLOADING  {idx+1}/{total}\n"
            _edit_status(
                f"📤 <b>UPLOADING PART</b>\n"
                f"{_SEP}\n\n"
                f"{_field('📁', 'File', fname[:32])}\n"
                f"{_field('📋', 'Part', f'{idx+1} / {total}')}\n\n"
                f"{_SEP}"
            )
            await upload_file(new, fname, is_last=is_last)
            Transfer.up_bytes.append(os.stat(new).st_size)
            if is_last and not split_cleaned:
                if ospath.exists(Paths.temp_zpath): shutil.rmtree(Paths.temp_zpath)
                split_cleaned = True
        else:
            if not ospath.exists(Paths.temp_files_dir): makedirs(Paths.temp_files_dir)
            if not remove: fp = shutil.copy(fp, Paths.temp_files_dir)
            fname = ospath.basename(fp)
            new   = shortFileName(fp)
            os.rename(fp, new)
            BotTimes.current_time = time()
            Messages.status_head  = f"📤 UPLOADING\n"
            _edit_status(
                f"📤 <b>UPLOADING</b>\n"
                f"{_SEP}\n\n"
                f"{_field('📁', 'File', fname[:32])}\n"
                f"{_field('📦', 'Size', sizeUnit(os.stat(new).st_size))}\n\n"
                f"{_SEP}"
            )
            fsz = os.stat(new).st_size
            await upload_file(new, fname, is_last=is_last)
            Transfer.up_bytes.append(fsz)
            if remove and ospath.exists(new): os.remove(new)
            elif not remove:
                for fi in os.listdir(Paths.temp_files_dir):
                    os.remove(ospath.join(Paths.temp_files_dir, fi))

    if remove and ospath.exists(folder_path): shutil.rmtree(folder_path)
    for d in (Paths.thumbnail_ytdl, Paths.temp_files_dir):
        if ospath.exists(d): shutil.rmtree(d)


def _edit_status(text: str):
    """Fire-and-forget status edit."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_do_edit(text))
    except Exception: pass

async def _do_edit(text: str):
    try:
        await MSG.status_msg.edit_text(text, reply_markup=keyboard())
    except Exception: pass


async def Zip_Handler(down_path: str, is_split: bool, remove: bool):
    Messages.status_head = "🗜 COMPRESSING\n"
    _edit_status(
        f"🗜 <b>COMPRESSING</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📁', 'File', Messages.download_name[:32])}\n\n"
        f"{_SEP}"
    )
    if not ospath.exists(Paths.temp_zpath): makedirs(Paths.temp_zpath)
    await archive(down_path, is_split, remove)
    await sleep(2)
    Transfer.total_down_size = getSize(Paths.temp_zpath)
    if remove and ospath.exists(down_path): shutil.rmtree(down_path)


async def Unzip_Handler(down_path: str, remove: bool):
    Messages.status_head = "📂 EXTRACTING\n"
    _edit_status(
        f"📂 <b>EXTRACTING</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📁', 'File', Messages.download_name[:32])}\n\n"
        f"{_SEP}"
    )
    filenames = natsorted([str(p) for p in pathlib.Path(down_path).glob("**/*") if p.is_file()])
    for f in filenames:
        sp = ospath.join(down_path, f)
        if not ospath.exists(Paths.temp_unzip_path): makedirs(Paths.temp_unzip_path)
        _, ext = ospath.splitext(ospath.basename(f).lower())
        try:
            if ospath.exists(sp):
                if ext in [".7z",".gz",".zip",".rar",".001",".tar",".z01"]:
                    await extract(sp, remove)
                else:
                    shutil.copy(sp, Paths.temp_unzip_path)
        except Exception as e:
            logging.warning(f"Unzip: {e}")
    if remove: shutil.rmtree(down_path)


async def cancelTask(reason: str):
    spent = getTime((datetime.now() - BotTimes.start_time).seconds)
    text = (
        f"⛔ <b>TASK STOPPED</b>\n"
        f"{_SEP}\n\n"
        f"{_field('❓', 'Reason', reason[:40])}\n"
        f"{_field('⏱', 'Spent', spent)}\n\n"
        f"{_SEP}"
    )
    if BOT.State.task_going:
        try:
            BOT.TASK.cancel()
            shutil.rmtree(Paths.WORK_PATH)
        except Exception as e:
            logging.warning(f"Cancel: {e}")
        finally:
            BOT.State.task_going = False
            try:
                await MSG.status_msg.edit_text(text)
            except Exception:
                try: await colab_bot.send_message(chat_id=OWNER, text=text)
                except Exception: pass


async def SendLogs(is_leech: bool):
    BOT.State.started    = False
    BOT.State.task_going = False


# ══════════════════════════════════════════════
#  VIDEO OPS — subtitle burn, compress, resolution
# ══════════════════════════════════════════════

async def burn_subtitles(video_path: str, sub_path: str, out_path: str) -> str:
    """
    Brûle les sous-titres dans la vidéo.
    Utilise ffmpeg -vf subtitles= (no new library).
    """
    out = ospath.join(out_path, "burned_" + ospath.basename(video_path))
    # ASS/SSA : filter complexe, SRT : subtitles filter
    ext = ospath.splitext(sub_path)[1].lower()
    if ext in (".ass", ".ssa"):
        vf = f"ass={sub_path}"
    else:
        vf = f"subtitles={sub_path}"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:a", "copy",
        "-c:v", "libx264", "-crf", "18",
        out,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-300:])
    return out


async def compress_video(video_path: str, out_path: str, crf: int = 28) -> str:
    """
    Compresse une vidéo avec ffmpeg (CRF = qualité, 18=haute, 35=basse).
    """
    out = ospath.join(out_path, "compressed_" + ospath.basename(video_path))
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-c:v", "libx264", "-crf", str(crf),
        "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        out,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-300:])
    return out


async def change_resolution(video_path: str, out_path: str, target: str) -> str:
    """
    Change la résolution. target = '1280:720', '1920:1080', '854:480', etc.
    """
    out = ospath.join(out_path, f"res{target.replace(':','x')}_" + ospath.basename(video_path))
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"scale={target}",
        "-c:v", "libx264", "-crf", "23",
        "-preset", "fast",
        "-c:a", "copy",
        out,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-300:])
    return out

