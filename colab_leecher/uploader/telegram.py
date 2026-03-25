"""
uploader/telegram.py — Sends files to the owner's private chat.

Improvements:
  - is_last file gets a ✅ Done caption with size + elapsed time
  - FloodWait handled with recursive retry
  - Progress bar uses the shared status_bar() from helper
"""
import logging
from PIL import Image
from asyncio import sleep
from os import path as ospath
from datetime import datetime
from pyrogram.errors import FloodWait
from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.variables import BOT, Transfer, BotTimes, Messages, MSG, Paths
from colab_leecher.utility.helper import (
    sizeUnit, fileType, getTime, status_bar,
    thumbMaintainer, videoExtFix,
)


async def progress_bar(current, total):
    elapsed = max((datetime.now() - BotTimes.task_start).seconds, 1)
    speed   = current / elapsed
    done_sofar = current + sum(Transfer.up_bytes)
    eta = (Transfer.total_down_size - done_sofar) / max(speed, 1)
    pct = done_sofar / max(Transfer.total_down_size, 1) * 100

    await status_bar(
        down_msg=Messages.status_head,
        speed=f"{sizeUnit(speed)}/s",
        percentage=pct,
        eta=getTime(eta),
        done=sizeUnit(done_sofar),
        left=sizeUnit(Transfer.total_down_size),
        engine="Pyrofork 💥",
    )


def _build_caption(real_name: str, is_last: bool) -> str:
    name_part = f"{BOT.Setting.prefix} {real_name} {BOT.Setting.suffix}".strip()
    tag       = BOT.Options.caption

    if is_last and Transfer.completion_info:
        ci       = Transfer.completion_info
        start    = ci.get("_start") or BotTimes.start_time
        dur      = int((datetime.now() - start).total_seconds())
        sz       = sizeUnit(ci.get("final_sz", Transfer.total_down_size))
        Transfer.completion_info = None
        body = f"✅ {name_part}  ·  {sz}  ·  ⏱ {getTime(dur)}"
    else:
        body = name_part

    return f"<{tag}>{body}</{tag}>"


async def upload_file(file_path: str, real_name: str, is_last: bool = False):
    BotTimes.task_start = datetime.now()
    caption = _build_caption(real_name, is_last)
    ftype   = fileType(file_path)
    f_type  = ftype if BOT.Options.stream_upload else "document"

    try:
        sent = None

        if f_type == "video":
            if not BOT.Options.stream_upload:
                file_path = videoExtFix(file_path)
            thmb, seconds = thumbMaintainer(file_path)
            with Image.open(thmb) as img:
                w, h = img.size
            sent = await colab_bot.send_video(
                chat_id=OWNER, video=file_path,
                supports_streaming=True,
                width=w, height=h,
                caption=caption, thumb=thmb,
                duration=int(seconds),
                progress=progress_bar,
            )

        elif f_type == "audio":
            thmb = Paths.THMB_PATH if ospath.exists(Paths.THMB_PATH) else None
            sent = await colab_bot.send_audio(
                chat_id=OWNER, audio=file_path,
                caption=caption, thumb=thmb,
                progress=progress_bar,
            )

        elif f_type == "photo":
            sent = await colab_bot.send_photo(
                chat_id=OWNER, photo=file_path,
                caption=caption,
                progress=progress_bar,
            )

        else:  # document / unknown
            thmb = None
            if ospath.exists(Paths.THMB_PATH):
                thmb = Paths.THMB_PATH
            elif ftype == "video":
                thmb, _ = thumbMaintainer(file_path)
            sent = await colab_bot.send_document(
                chat_id=OWNER, document=file_path,
                caption=caption, thumb=thmb,
                progress=progress_bar,
            )

        if sent:
            MSG.sent_msg = sent
            Transfer.sent_file.append(sent)
            Transfer.sent_file_names.append(real_name)

        if is_last:
            try:
                await MSG.status_msg.delete()
            except Exception:
                pass

    except FloodWait as e:
        logging.warning(f"[Upload] FloodWait {e.value}s")
        await sleep(e.value + 1)
        await upload_file(file_path, real_name, is_last)

    except Exception as e:
        logging.error(f"[Upload] {e}")
        try:
            from colab_leecher.utility.helper import error_card
            await colab_bot.send_message(
                chat_id=OWNER,
                text=error_card(str(e)[:60], "Réessaie ou vérifie le fichier"),
            )
        except Exception:
            pass
