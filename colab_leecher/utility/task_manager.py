"""
task_manager.py
──────────────────────────────────────────────
Queue système : jusqu'à 3 downloads en parallèle,
les suivants attendent. Retry auto (3x). Resume.
"""
import pytz
import shutil
import logging
import asyncio
from time import time
from datetime import datetime
from asyncio import sleep, get_event_loop
from os import makedirs, path as ospath
from colab_leecher import OWNER, colab_bot
from colab_leecher.downlader.manager import calDownSize, get_d_name, downloadManager
from colab_leecher.channel_manager import kb_channel_select, get_channels
from colab_leecher.utility.helper import (
    _SEP, _field, _bar_ui,
    getSize, applyCustomName, keyboard, sysINFO,
    is_ytdl_link, queue_card, completion_card, error_card,
    sizeUnit, getTime,
)
from colab_leecher.utility.handler import (
    Leech, Unzip_Handler, Zip_Handler, SendLogs, cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, Transfer, TaskError,
)

MAX_PARALLEL = 3
_active_tasks: list  = []   # asyncio.Task objects
_queue:        list  = []   # pending job dicts
_history:      list  = []   # completed job dicts (max 50)
_slot_msgs:    dict  = {}   # slot_id -> status_msg object
_slot_state:   dict  = {}   # slot_id -> {name, pct, speed, status, done, left}
_shared_msg          = None  # single shared status message for all slots


async def _update_shared_panel():
    """Rebuild and edit the single shared status message from all slot states."""
    global _shared_msg
    if not _slot_state:
        return
    lines = ["⚡ <b>VIDEO STUDIO AI</b>  //  CORE v4.2", _SEP, ""]
    for sid in sorted(_slot_state.keys()):
        s = _slot_state[sid]
        bar = _bar_ui(float(s.get("pct", 0)), 16)
        lines.append(
            f"🔹 <b>Slot {sid+1}</b>  {s.get('name','?')[:28]}\n"
            f"   <code>[{bar}]</code>  <b>{s.get('pct',0):.0f}%</b>  ·  "
            f"🚀 {s.get('speed','?')}  ·  ⏱ {s.get('eta','?')}\n"
            f"   📦 {s.get('left','?')}  ·  ⚙ {s.get('status','?')}"
        )
        lines.append("")
    lines.append(_SEP)
    text = "\n".join(lines)
    try:
        if _shared_msg:
            from pyrogram.errors import BadRequest
            try:
                await _shared_msg.edit_text(text, reply_markup=keyboard())
            except BadRequest:
                pass
    except Exception as e:
        logging.warning(f"Panel update: {e}")


# ──────────────────────────────────────────────
#  History helpers
# ──────────────────────────────────────────────

def add_history(name, size, mode, status, duration_s):
    _history.append({
        "name":     name,
        "size":     size,
        "mode":     mode,
        "status":   status,
        "duration": getTime(duration_s),
        "time":     datetime.now().strftime("%d/%m %H:%M"),
    })
    if len(_history) > 50:
        _history.pop(0)

def get_history():
    return list(_history)


# ──────────────────────────────────────────────
#  Queue helpers
# ──────────────────────────────────────────────

def enqueue(job: dict):
    """Ajoute un job à la file. Retourne sa position (1-indexed)."""
    _queue.append(job)
    return len(_queue)

def queue_size():
    return len(_queue)

def active_count():
    return len([t for t in _active_tasks if not t.done()])


# ──────────────────────────────────────────────
#  Slot status message
# ──────────────────────────────────────────────

async def _send_slot_status(slot_id: int, text: str, kb=None):
    """Crée ou édite le message de statut pour un slot donné."""
    msg = _slot_msgs.get(slot_id)
    try:
        if msg:
            await msg.edit_text(text, reply_markup=kb)
        else:
            sent = await colab_bot.send_message(
                chat_id=OWNER, text=text, reply_markup=kb
            )
            _slot_msgs[slot_id] = sent
    except Exception as e:
        logging.debug(f"Slot msg {slot_id}: {e}")


# ──────────────────────────────────────────────
#  Core task runner (un job = une coroutine)
# ──────────────────────────────────────────────

async def _run_job(job: dict, slot_id: int):
    """
    Exécute un job complet avec retry (3x) et cleanup.
    job keys: source, mode, type, ytdl, name, zip_pw, unzip_pw
    """
    global _shared_msg
    source    = job["source"]
    mode_type = job.get("type", "normal")
    is_ytdl   = job.get("ytdl", False)
    custom_nm = job.get("name", "")
    zip_pw    = job.get("zip_pw", "")
    unzip_pw  = job.get("unzip_pw", "")

    is_zip     = mode_type == "zip"
    is_unzip   = mode_type == "unzip"
    is_dualzip = mode_type == "undzip"

    MAX_RETRY  = 3
    attempt    = 0
    start_time = datetime.now()

    import os as _os
    _bdir      = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    _ban_err   = _os.path.join(_bdir, "banner_error.jpg")

    while attempt < MAX_RETRY:
        attempt += 1
        try:
            # ── Reset per-job state ────────────────
            Messages.download_name   = custom_nm or ""
            Messages.task_msg        = ""
            Messages.status_head     = "📥 DOWNLOADING\n"
            Messages.caution_msg     = ""
            Transfer.sent_file       = []
            Transfer.sent_file_names = []
            Transfer.down_bytes      = [0, 0]
            Transfer.up_bytes        = [0, 0]

            BOT.Options.custom_name  = custom_nm
            BOT.Options.zip_pswd     = zip_pw
            BOT.Options.unzip_pswd   = unzip_pw
            BOT.SOURCE               = source

            # ── Work dir ──────────────────────────
            work = f"{Paths.WORK_PATH}/slot_{slot_id}"
            down = f"{work}/downloads"
            if ospath.exists(work):
                shutil.rmtree(work)
            makedirs(down)
            Paths.down_path = down

            # ── Status message ─────────────────────
            job_label = (source[0] if source else "?")[:40]
            if attempt > 1 or not MSG.status_msg:
                prefix = f"🔁 <b>RETRY {attempt}/{MAX_RETRY}</b>" if attempt > 1 else "🟠 <b>STARTING</b>"
                sent = await colab_bot.send_message(
                    chat_id=OWNER,
                    text=f"{prefix}  ·  {mode_type.upper()}\n{_SEP}\n\n{_field('📁', 'Job', job_label)}",
                    reply_markup=keyboard(),
                )
            else:
                sent = MSG.status_msg
                try:
                    await sent.edit_text(
                        f"🟠 <b>STARTING</b>  ·  {mode_type.upper()}\n{_SEP}\n\n{_field('📁', 'Job', job_label)}",
                        reply_markup=keyboard(),
                    )
                except Exception:
                    pass
            _slot_msgs[slot_id] = sent
            MSG.status_msg       = sent
            # Register slot in shared state
            _slot_state[slot_id] = {
                "name": (source[0].split("/")[-1] if source else "?")[:28],
                "pct": 0, "speed": "?", "eta": "?",
                "status": "INIT", "done": "0B", "left": "?",
            }
            # First slot creates the shared panel; others reuse it
            if _shared_msg is None:
                _shared_msg = sent
                MSG.status_msg = sent
            else:
                # Delete this slot's individual msg — we use the shared panel
                try: await sent.delete()
                except Exception: pass
                MSG.status_msg = _shared_msg
            BotTimes.start_time  = start_time
            BotTimes.current_time = time()

            # ── Download size ─────────────────────
            await calDownSize(source)
            if not Messages.download_name:
                await get_d_name(source[0])

            if is_zip:
                Paths.down_path = ospath.join(Paths.down_path, Messages.download_name or "files")
                makedirs(Paths.down_path, exist_ok=True)

            # ── Download ──────────────────────────
            await downloadManager(source, is_ytdl)
            Transfer.total_down_size = getSize(Paths.down_path)
            applyCustomName()

            # ── Process ───────────────────────────
            # _start stored so _build_caption can compute real elapsed time at upload moment
            Transfer.completion_info = {
                "fname":    Messages.download_name or (source[0].split("/")[-1] if source else "?"),
                "orig_sz":  Transfer.total_down_size,
                "final_sz": Transfer.total_down_size,   # updated for zip modes
                "mode":     mode_type,
                "_start":   start_time,                 # real start — duration computed at caption time
            }
            if is_zip:
                await Zip_Handler(Paths.down_path, True, True)
                Transfer.completion_info["final_sz"] = getSize(Paths.temp_zpath)
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

            # ── Success ───────────────────────────
            duration = int((datetime.now() - start_time).total_seconds())
            final_sz = Transfer.total_down_size
            fname    = Messages.download_name or (source[0].split("/")[-1] if source else "?")
            add_history(fname, final_sz, mode_type, "ok", duration)
            # Delete progress status — completion shown in last file caption
            try:
                await MSG.status_msg.delete()
            except Exception:
                pass

            # ── Channel copy prompt ───────────────
            sent_ids = [m.id for m in Transfer.sent_file if m]
            if sent_ids:
                channels = get_channels()
                ch_caption = (
                    f"📡 <b>COPY TO CHANNEL?</b>\n"
                    f"{_SEP}\n\n"
                    f"{_field('📁', 'File', fname[:32])}\n"
                    f"{_field('📦', 'Size', sizeUnit(final_sz))}\n"
                    f"{_field('📺', 'Channels', str(len(channels)))}\n\n"
                    f"{_SEP}\n"
                    f"<b>Select destination:</b>"
                )
                await colab_bot.send_message(
                    chat_id=OWNER,
                    text=ch_caption,
                    reply_markup=kb_channel_select(sent_ids),
                )

            # ── Cleanup ───────────────────────────
            if ospath.exists(work):
                shutil.rmtree(work)
            _slot_msgs.pop(slot_id, None)
            _slot_state.pop(slot_id, None)
            # If no more slots, clear shared panel ref
            if not _slot_state:
                _shared_msg = None
            return  # success → exit retry loop

        except asyncio.CancelledError:
            logging.info(f"Job slot {slot_id} cancelled")
            add_history(
                Messages.download_name or "?", 0, mode_type, "cancelled",
                int((datetime.now() - start_time).total_seconds())
            )
            return

        except Exception as e:
            logging.error(f"Job slot {slot_id} attempt {attempt} failed: {e}")
            if attempt >= MAX_RETRY:
                # Final failure
                err_text = error_card(
                    reason=str(e)[:50],
                    suggestion="Check source or retry"
                )
                try:
                    try: await MSG.status_msg.delete()
                    except Exception: pass
                    await colab_bot.send_message(chat_id=OWNER, text=err_text)
                except Exception:
                    pass
                add_history(
                    Messages.download_name or "?", 0, mode_type, "error",
                    int((datetime.now() - start_time).total_seconds())
                )
            else:
                # Retry wait with backoff
                wait = 5 * attempt
                try:
                    retry_text = (
                        f"🔁 <b>RETRYING...</b>\n"
                        f"{_SEP}\n\n"
                        f"{_field('⚠', 'Error', str(e)[:40])}\n"
                        f"{_field('🔁', 'Attempt', f'{attempt+1} / {MAX_RETRY}')}\n"
                        f"{_field('⏳', 'Wait', f'{wait}s')}\n\n"
                        f"{_SEP}"
                    )
                    if MSG.status_msg:
                        await MSG.status_msg.edit_text(retry_text)
                except Exception:
                    pass
                await sleep(wait)


# ──────────────────────────────────────────────
#  Queue dispatcher — tourne en fond
# ──────────────────────────────────────────────

async def _dispatcher():
    """
    Surveille la queue et lance les jobs en parallèle (max 3).
    """
    slot_counter = 0
    while True:
        # Clean finished tasks
        finished = [t for t in _active_tasks if t.done()]
        for t in finished:
            _active_tasks.remove(t)

        # Launch pending jobs if slots available
        while _queue and len(_active_tasks) < MAX_PARALLEL:
            job = _queue.pop(0)
            slot_counter += 1
            slot = slot_counter
            task = get_event_loop().create_task(_run_job(job, slot))
            _active_tasks.append(task)

        await sleep(1)


# ──────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────

_dispatcher_started = False

async def ensure_dispatcher():
    global _dispatcher_started
    if not _dispatcher_started:
        get_event_loop().create_task(_dispatcher())
        _dispatcher_started = True


async def taskScheduler():
    """
    Entry point called from __main__ when user selects a mode.
    Always routes through queue + dispatcher — never calls _run_job directly.
    This prevents double-execution when the callback fires multiple times.
    """
    await ensure_dispatcher()

    job = {
        "source":   list(BOT.SOURCE),   # snapshot to avoid shared-reference bugs
        "mode":     BOT.Mode.mode,
        "type":     BOT.Mode.type,
        "ytdl":     BOT.Mode.ytdl,
        "name":     BOT.Options.custom_name,
        "zip_pw":   BOT.Options.zip_pswd,
        "unzip_pw": BOT.Options.unzip_pswd,
    }

    active = active_count()
    enqueue(job)
    BOT.State.task_going = True

    if active >= MAX_PARALLEL:
        # All slots busy — show position card
        pos   = queue_size()
        label = (job["source"][0] if job["source"] else "?")[:24]
        card  = queue_card(pos, pos + active, label)
        await colab_bot.send_message(chat_id=OWNER, text=card)
