import logging
from natsort import natsorted
from datetime import datetime
from asyncio import sleep, get_running_loop
from colab_leecher.downlader.mega import megadl
from colab_leecher.utility.handler import cancelTask
from colab_leecher.downlader.terabox import terabox_download
from colab_leecher.downlader.ytdl import YTDL_Status, get_YT_Name
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from colab_leecher.downlader.aria2 import aria2_Download, get_Aria2c_Name
from colab_leecher.downlader.telegram import TelegramDownload, media_Identifier
from colab_leecher.utility.variables import (
    BOT, Gdrive, Transfer, MSG, Messages, Aria2c, BotTimes,
)
from colab_leecher.utility.helper import (
    isYtdlComplete, keyboard, sysINFO,
    is_google_drive, is_mega, is_terabox, is_ytdl_link, is_telegram,
)
from colab_leecher.downlader.gdrive import (
    build_service, g_DownLoad, get_Gfolder_size, getFileMetadata, getIDFromURL,
)


async def _safe_edit(text):
    """Edit status_msg safely — skip if None."""
    try:
        if MSG.status_msg:
            await MSG.status_msg.edit_text(text=text, reply_markup=keyboard())
    except Exception as e:
        logging.debug(f"Status edit skipped: {e}")


async def downloadManager(source, is_ytdl: bool):
    message = "\n<b>Please Wait...</b> ⏳\n<i>Merging YTDL Video...</i> 🐬"
    BotTimes.task_start = datetime.now()
    if is_ytdl:
        for i, link in enumerate(source):
            await YTDL_Status(link, i + 1)
        try:
            await _safe_edit(Messages.task_msg + Messages.status_head + message + sysINFO())
        except Exception:
            pass
        while not isYtdlComplete():
            await sleep(2)
    else:
        for i, link in enumerate(source):
            try:
                if is_google_drive(link):
                    await g_DownLoad(link, i + 1)
                elif is_telegram(link):
                    await TelegramDownload(link, i + 1)
                elif is_ytdl_link(link):
                    await YTDL_Status(link, i + 1)
                    try:
                        await _safe_edit(Messages.task_msg + Messages.status_head + message + sysINFO())
                    except Exception:
                        pass
                    while not isYtdlComplete():
                        await sleep(2)
                elif is_mega(link):
                    await megadl(link, i + 1)
                elif is_terabox(link):
                    tera_dn = f"<b>PLEASE WAIT ⌛</b>\n\n__Generating Download Link For__\n\n<code>{link}</code>"
                    await _safe_edit(tera_dn + sysINFO())
                    await terabox_download(link, i + 1)
                else:
                    aria2_dn = f"<b>PLEASE WAIT ⌛</b>\n\n__Getting Download Info For__\n\n<code>{link}</code>"
                    await _safe_edit(aria2_dn + sysINFO())
                    Aria2c.link_info = False
                    await aria2_Download(link, i + 1)
            except Exception as Error:
                await cancelTask(f"Download Error: {str(Error)}")
                logging.error(f"Error While Downloading: {Error}")
                return


async def calDownSize(sources):
    for link in natsorted(sources):
        if is_google_drive(link):
            await build_service()
            id = await getIDFromURL(link)
            try:
                meta = getFileMetadata(id)
            except Exception as e:
                if "File not found" in str(e):
                    err = "The file link you gave either doesn't exist or You don't have access to it!"
                elif "Failed to retrieve" in str(e):
                    err = "Authorization Error with Google ! Make Sure you generated token.pickle !"
                else:
                    err = f"Error in G-API: {e}"
                logging.error(err)
                await cancelTask(err)
            else:
                if meta.get("mimeType") == "application/vnd.google-apps.folder":
                    Transfer.total_down_size += get_Gfolder_size(id)
                else:
                    Transfer.total_down_size += int(meta["size"])
        elif is_telegram(link):
            media, _ = await media_Identifier(link)
            if media is not None:
                Transfer.total_down_size += media.file_size
            else:
                logging.error("Couldn't Download Telegram Message")
        else:
            pass


async def get_d_name(link: str):
    if len(BOT.Options.custom_name) != 0:
        Messages.download_name = BOT.Options.custom_name
        return
    if is_google_drive(link):
        id = await getIDFromURL(link)
        meta = getFileMetadata(id)
        Messages.download_name = meta["name"]
    elif is_telegram(link):
        media, _ = await media_Identifier(link)
        Messages.download_name = media.file_name if hasattr(media, "file_name") else "None"
    elif is_ytdl_link(link):
        Messages.download_name = await get_YT_Name(link)
    elif is_mega(link):
        Messages.download_name = "Don't Know 🥲 (Trying)"
    else:
        Messages.download_name = get_Aria2c_Name(link)
