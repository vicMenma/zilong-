"""
aria2.py — Async aria2c downloader.

Key improvements vs original:
  - asyncio.create_subprocess_exec instead of blocking subprocess.Popen
  - Robust output parser with try/except on every field — no more IndexError crashes
  - _safe_url() to percent-encode brackets and special chars
  - Dead-link detection threshold (270 s)
"""
import re
import logging
import asyncio
import subprocess
from urllib.parse import quote, urlparse, unquote
from datetime import datetime

from colab_leecher.utility.helper import sizeUnit, status_bar
from colab_leecher.utility.variables import BOT, Aria2c, Paths, Messages, BotTimes


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _safe_url(url: str) -> str:
    """Percent-encode brackets and special chars that break aria2c."""
    return quote(url, safe=":/?=&%+@#.,!~*'();$-_")


# ──────────────────────────────────────────────
#  Name discovery  (synchronous dry-run)
# ──────────────────────────────────────────────

def get_Aria2c_Name(link: str) -> str:
    if BOT.Options.custom_name:
        return BOT.Options.custom_name
    try:
        safe = _safe_url(link)
        result = subprocess.run(
            ["aria2c", "-x4", "--dry-run", "--file-allocation=none", safe],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        fname  = stdout.split("complete: ")[-1].split("\n")[0]
        name   = fname.split("/")[-1].strip()
        if name:
            return name
    except Exception as e:
        logging.debug(f"[aria2c name] {e}")

    # Fallback: extract from URL path
    try:
        parsed = urlparse(link)
        name   = unquote(parsed.path.split("/")[-1])
        if name:
            return name
    except Exception:
        pass
    return "UNKNOWN"


# ──────────────────────────────────────────────
#  Main downloader  (fully async)
# ──────────────────────────────────────────────

async def aria2_Download(link: str, num: int):
    Aria2c.link_info      = False
    BotTimes.task_start   = datetime.now()
    name_d                = get_Aria2c_Name(link)
    Messages.status_head  = (
        f"📥 <b>TÉLÉCHARGEMENT</b>  #{str(num).zfill(2)}\n\n"
        f"<code>{name_d[:50]}</code>\n"
    )

    safe = _safe_url(link)
    cmd  = [
        "aria2c",
        "-x16", "-s16",                   # max connections + split
        "--seed-time=0",                  # no seeding for torrents
        "--summary-interval=1",           # progress every second
        "--max-tries=3",
        "--retry-wait=5",
        "--console-log-level=notice",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "-d", Paths.down_path,
        safe,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    while True:
        line = await proc.stdout.readline()
        if not line:
            if proc.returncode is not None:
                break
            continue
        await _on_output(line.decode("utf-8", errors="replace"))

    await proc.wait()
    rc = proc.returncode

    if rc != 0:
        err_bytes = await proc.stderr.read()
        err_msg   = err_bytes.decode("utf-8", errors="replace")[:200]
        code_map  = {
            3:  f"Ressource introuvable : {link[:60]}",
            9:  "Espace disque insuffisant.",
            22: f"Erreur HTTP (mauvaise URL ou plage) : {link[:60]}",
            24: "Échec de l'authentification HTTP.",
        }
        msg = code_map.get(rc, f"aria2c a échoué (code {rc}). {err_msg}")
        logging.error(f"[aria2c] {msg}")
        raise RuntimeError(msg)


# ──────────────────────────────────────────────
#  Output parser
# ──────────────────────────────────────────────

async def _on_output(output: str):
    """
    Parse aria2c progress line like:
      [#abc123 100MiB/1.40GiB(7%) CN:8 DL:52MiB ETA:23s]
    and feed status_bar().
    """
    if "ETA:" not in output:
        return

    try:
        # ── sizes ──
        # Find pattern like  100MiB/1.40GiB(7%)
        m_block = re.search(
            r"([\d.]+\s*\w+)/([\d.]+\s*\w+)\(([\d.]+)%\)",
            output
        )
        if not m_block:
            return

        downloaded_bytes = m_block.group(1).replace(" ", "")
        total_size       = m_block.group(2).replace(" ", "")
        pct_str          = m_block.group(3)

        # ── ETA ──
        m_eta = re.search(r"ETA:([\w]+)", output)
        eta   = m_eta.group(1) if m_eta else "?"

        # ── speed for internal calculation ──
        down_nums = re.findall(r"[\d.]+", downloaded_bytes)
        down_unit = re.findall(r"[A-Za-z]+", downloaded_bytes)
        if not down_nums or not down_unit:
            return

        down_val  = float(down_nums[0])
        unit_char = down_unit[0][0].upper()
        multiplier = {"G": 1024**3, "M": 1024**2, "K": 1024, "B": 1}.get(unit_char, 1)
        down_bytes_val = down_val * multiplier

        elapsed = max((datetime.now() - BotTimes.task_start).seconds, 1)

        if float(pct_str) > 0:
            Aria2c.link_info  = True
            speed_bps         = down_bytes_val / elapsed
            speed_str         = f"{sizeUnit(speed_bps)}/s"

            await status_bar(
                Messages.status_head,
                speed_str,
                float(pct_str),
                eta,
                downloaded_bytes,
                total_size,
                "Aria2c 🧨",
            )
        else:
            # No progress yet — check for dead link timeout
            if elapsed >= 270 and not Aria2c.link_info:
                logging.warning("[aria2c] No download info after 270 s — possible dead link.")

    except Exception as e:
        logging.debug(f"[aria2c parser] {e}")
