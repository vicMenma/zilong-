import logging
from PIL import Image
from asyncio import sleep
from os import path as ospath
from datetime import datetime
from pyrogram.errors import FloodWait
from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.variables import BOT, Transfer, BotTimes, Messages, MSG, Paths
from colab_leecher.utility.helper import (
    sizeUnit, fileType, getTime, status_bar, thumbMaintainer, videoExtFix,
    _SEP, _field,
)


async def progress_bar(current, total):
    elapsed = (datetime.now() - BotTimes.task_start).seconds
    speed   = current / elapsed if (current > 0 and elapsed > 0) else 4 * 1024 * 1024
    remaining = Transfer.total_down_size - current - sum(Transfer.up_bytes)
    eta     = remaining / max(speed, 1)
    pct     = (current + sum(Transfer.up_bytes)) / max(Transfer.total_down_size, 1) * 100
    await status_bar(
        down_msg=Messages.status_head,
        speed=f"{sizeUnit(speed)}/s",
        percentage=pct,
        eta=getTime(eta),
        done=sizeUnit(current + sum(Transfer.up_bytes)),
        left=sizeUnit(Transfer.total_down_size),
        engine="Pyrofork 💥",
    )


def _build_caption(name_part: str, is_last: bool) -> str:
    """Build file caption. Last file gets completion info embedded."""
    base = f"<b>{name_part}</b>"
    if is_last and hasattr(Transfer, "completion_info") and Transfer.completion_info:
        ci  = Transfer.completion_info
        dur = getTime(ci.get("duration", 0))
        sz  = sizeUnit(ci.get("final_sz", 0))
        reduction = ""
        orig = ci.get("orig_sz", 0)
        final = ci.get("final_sz", 0)
        if orig > 0 and final > 0 and final != orig:
            pct  = (1 - final / orig) * 100
            sign = "🔻" if pct > 0 else "🔺"
            reduction = f"  {sign} {abs(pct):.0f}%"
        Transfer.completion_info = None
        return (
            f"{base}\n\n"
            f"✅ <b>DONE</b>  ·  {sz}  ·  ⏱ {dur}{reduction}"
        )
    return base


async def upload_file(file_path, real_name, is_last: bool = False):
    global Transfer, MSG
    BotTimes.task_start = datetime.now()

    name_part = f"{BOT.Setting.prefix} {real_name} {BOT.Setting.suffix}".strip()
    tag       = BOT.Options.caption
    caption   = f"<{tag}>{_build_caption(name_part, is_last)}</{tag}>"

    ftype  = fileType(file_path)
    f_type = ftype if BOT.Options.stream_upload else "document"

    try:
        if f_type == "video":
            if not BOT.Options.stream_upload:
                file_path = videoExtFix(file_path)
            thmb_path, seconds = thumbMaintainer(file_path)
            with Image.open(thmb_path) as img:
                width, height = img.size
            sent = await colab_bot.send_video(
                chat_id=OWNER, video=file_path,
                supports_streaming=True,
                width=width, height=height,
                caption=caption, thumb=thmb_path,
                duration=int(seconds), progress=progress_bar,
            )
        elif f_type == "audio":
            thmb = Paths.THMB_PATH if ospath.exists(Paths.THMB_PATH) else None
            sent = await colab_bot.send_audio(
                chat_id=OWNER, audio=file_path,
                caption=caption, thumb=thmb, progress=progress_bar,
            )
        elif f_type == "photo":
            sent = await colab_bot.send_photo(
                chat_id=OWNER, photo=file_path,
                caption=caption, progress=progress_bar,
            )
        else:
            thmb = None
            if ospath.exists(Paths.THMB_PATH):
                thmb = Paths.THMB_PATH
            elif ftype == "video":
                thmb, _ = thumbMaintainer(file_path)
            sent = await colab_bot.send_document(
                chat_id=OWNER, document=file_path,
                caption=caption, thumb=thmb, progress=progress_bar,
            )

        MSG.sent_msg = sent
        Transfer.sent_file.append(sent)
        Transfer.sent_file_names.append(real_name)

        if is_last:
            try: await MSG.status_msg.delete()
            except Exception: pass

    except FloodWait as e:
        logging.warning(f"FloodWait {e.value}s")
        await sleep(e.value)
        await upload_file(file_path, real_name, is_last)

    except Exception as e:
        logging.error(f"Upload error: {e}")
        from colab_leecher.utility.helper import error_card
        try:
            await colab_bot.send_message(
                chat_id=OWNER,
                text=error_card(str(e)[:50], "Check file or retry")
            )
        except Exception:
            pass
