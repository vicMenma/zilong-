import re
import logging
import asyncio
import subprocess
from urllib.parse import quote, urlparse, unquote
from datetime import datetime
from colab_leecher.utility.helper import sizeUnit, status_bar
from colab_leecher.utility.variables import BOT, Aria2c, Paths, Messages, BotTimes


def _safe_url(url: str) -> str:
    """Encode [ ] and other chars that break aria2c, without double-encoding existing %XX."""
    return quote(url, safe=":/?=&%+@#.,!~*'();$-_%")


async def aria2_Download(link: str, num: int):
    global BotTimes, Messages
    safe_link = _safe_url(link)
    name_d = get_Aria2c_Name(link)
    BotTimes.task_start = datetime.now()
    Messages.status_head = (
        f"<b>📥 DOWNLOADING FROM » </b>"
        f"<i>🔗Link {str(num).zfill(2)}</i>\n\n"
        f"<b>🏷️ Name » </b><code>{name_d}</code>\n"
    )

    command = [
        "aria2c",
        "-x16",
        "--seed-time=0",
        "--summary-interval=1",
        "--max-tries=3",
        "--console-log-level=notice",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "-d", Paths.down_path,
        safe_link,
    ]

    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    while True:
        output = await proc.stdout.readline()
        if output == b"" and proc.returncode is not None:
            break
        if output:
            await on_output(output.decode("utf-8", errors="replace"))

    await proc.wait()
    exit_code = proc.returncode
    error_output = await proc.stderr.read()

    if exit_code != 0:
        err_msgs = {
            3:  f"Resource not found: {link}",
            9:  "Not enough disk space",
            24: "HTTP authorization failed",
            22: f"HTTP error (bad URL or range): {link[:80]}",
        }
        msg = err_msgs.get(
            exit_code,
            f"aria2c download failed with return code {exit_code} for {link}. Error: {error_output}"
        )
        logging.error(msg)


def get_Aria2c_Name(link: str) -> str:
    if len(BOT.Options.custom_name) != 0:
        return BOT.Options.custom_name
    safe = _safe_url(link)
    cmd = f'aria2c -x10 --dry-run --file-allocation=none "{safe}"'
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    stdout_str = result.stdout.decode("utf-8", errors="replace")
    filename = stdout_str.split("complete: ")[-1].split("\n")[0]
    name = filename.split("/")[-1].strip()
    if not name:
        parsed = urlparse(link)
        name = unquote(parsed.path.split("/")[-1]) or "UNKNOWN"
    return name


async def on_output(output: str):
    total_size = "0B"
    progress_percentage = "0B"
    downloaded_bytes = "0B"
    eta = "0S"
    try:
        if "ETA:" in output:
            parts = output.split()
            total_size = parts[1].split("/")[1]
            total_size = total_size.split("(")[0]
            progress_percentage = parts[1][parts[1].find("(") + 1: parts[1].find(")")]
            downloaded_bytes = parts[1].split("/")[0]
            eta = parts[4].split(":")[1][:-1]
    except Exception as do:
        logging.error(f"Couldn't get aria2c info: {do}")

    try:
        percentage = re.findall(r"\d+\.\d+|\d+", progress_percentage)[0]
        down       = re.findall(r"\d+\.\d+|\d+", downloaded_bytes)[0]
    except IndexError:
        return

    down_unit = re.findall(r"[a-zA-Z]+", downloaded_bytes)
    if not down_unit:
        return
    unit = down_unit[0]
    if   "G" in unit: spd = 3
    elif "M" in unit: spd = 2
    elif "K" in unit: spd = 1
    else:             spd = 0

    elapsed_time_seconds = (datetime.now() - BotTimes.task_start).seconds

    if elapsed_time_seconds >= 270 and not Aria2c.link_info:
        logging.error("Failed to get download info — probably dead link 💀")

    if total_size != "0B":
        Aria2c.link_info = True
        current_speed = (float(down) * 1024 ** spd) / max(elapsed_time_seconds, 1)
        speed_string  = f"{sizeUnit(current_speed)}/s"

        await status_bar(
            Messages.status_head,
            speed_string,
            int(float(percentage)),
            eta,
            downloaded_bytes,
            total_size,
            "Aria2c 🧨",
        )
