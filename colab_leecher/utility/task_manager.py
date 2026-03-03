"""
task_manager.py
──────────────────────────────────────────────
Queue système : jusqu'à 3 downloads en parallèle,
les suivants attendent. Retry auto (3x). Resume.

Single unified panel — one message shows all active slots,
updated every 3 s by a background loop.
"""
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
    _SEP, _field, _pct_bar,
    getSize, applyCustomName, keyboard,
    queue_card, error_card,
    sizeUnit, getTime,
)
from colab_leecher.utility.handler import (
    Leech, Unzip_Handler, Zip_Handler, SendLogs, cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, Transfer, TaskError,
    _slot_status_msg, _slot_id, _panel_slots,
)

MAX_PARALLEL = 3
_active_tasks: list = []
_queue:        list = []
_history:      list = []

# ── Unified panel state ───────────────────────
_panel_msg         = None
_panel_lock        = asyncio.Lock()


# ──────────────────────────────────────────────
#  Panel rendering
# ──────────────────────────────────────────────

def _render_panel() -> str:
    lines = ["⚡ <b>VIDEO STUDIO AI</b>  //  CORE v4.2", _SEP, ""]
    if not _panel_slots:
        lines.append("<i>No active tasks.</i>")
    else:
        for sid in sorted(_panel_slots.keys()):
            s   = _panel_slots[sid]
            pct = float(s.get("pct", 0))
            bar = _pct_bar(pct, 16)
            lines.append(
                f"🔹 <b>Slot {sid}</b>  —  {s.get('name', '?')[:30]}\n"
                f"   ⚙ {s.get('status','—')}  ·  💡 {s.get('engine','—')}\n"
                f"   <code>[{bar}]</code>  <b>{pct:.0f}%</b>\n"
                f"   🚀 {s.get('speed','?')}  ·  ⏱ ETA {s.get('eta','?')}\n"
                f"   📦 {s.get('done','?')} / {s.get('left','?')}"
            )
            lines.append("")
    lines.append(_SEP)
    q = len(_queue)
    if q:
        lines.append(f"📋 <b>Queue:</b>  {q} waiting")
    return "\n".join(lines)


async def _panel_loop():
    """Background task: edit the unified panel every 3 s."""
    global _panel_msg
    while True:
        await sleep(3)
        if not _panel_slots and _panel_msg is None:
            continue
        async with _panel_lock:
            if _panel_msg is None:
                continue
            text = _render_panel()
            try:
                await _panel_msg.edit_text(text, reply_markup=keyboard())
            except Exception:
                pass


async def _ensure_panel():
    """Create the panel message if it doesn't exist yet."""
    global _panel_msg
    async with _panel_lock:
        if _panel_msg is None:
            _panel_msg = await colab_bot.send_message(
                chat_id=OWNER,
                text=_render_panel(),
                reply_markup=keyboard(),
            )
            MSG.status_msg = _panel_msg


async def _remove_panel_if_empty():
    """Delete the panel message when all slots finish."""
    global _panel_msg
    async with _panel_lock:
        if not _panel_slots and _panel_msg:
            try:
                await _panel_msg.delete()
            except Exception:
                pass
            _panel_msg = None
            MSG.status_msg = None


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
    _queue.append(job)
    return len(_queue)

def queue_size():
    return len(_queue)

def active_count():
    return len([t for t in _active_tasks if not t.done()])


# ──────────────────────────────────────────────
#  Core task runner
# ──────────────────────────────────────────────

async def _run_job(job: dict, slot_id: int):
    global _panel_msg

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

    # Register slot in the shared panel
    _panel_slots[slot_id] = {
        "name":   (source[0].split("/")[-1] if source else "?")[:30],
        "pct":    0, "speed": "—", "eta": "—",
        "done":   "0 B", "left": "?",
        "status": "STARTING", "engine": "—",
    }
    await _ensure_panel()
    # Point ContextVar + global at the panel so status_bar() edits it
    _slot_status_msg.set(_panel_msg)
    _slot_id.set(slot_id)
    MSG.status_msg = _panel_msg

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

            BotTimes.start_time   = start_time
            BotTimes.current_time = time()

            _panel_slots[slot_id]["status"] = "GETTING INFO"

            # ── Download size ─────────────────────
            await calDownSize(source)
            if not Messages.download_name:
                await get_d_name(source[0])

            if Messages.download_name:
                _panel_slots[slot_id]["name"] = Messages.download_name[:30]

            if is_zip:
                Paths.down_path = ospath.join(Paths.down_path, Messages.download_name or "files")
                makedirs(Paths.down_path, exist_ok=True)

            _panel_slots[slot_id]["status"] = "DOWNLOADING"

            # ── Download ──────────────────────────
            await downloadManager(source, is_ytdl)
            Transfer.total_down_size = getSize(Paths.down_path)
            applyCustomName()

            _panel_slots[slot_id]["status"] = "PROCESSING"

            # ── Process ───────────────────────────
            Transfer.completion_info = {
                "fname":    Messages.download_name or (source[0].split("/")[-1] if source else "?"),
                "orig_sz":  Transfer.total_down_size,
                "final_sz": Transfer.total_down_size,
                "mode":     mode_type,
                "_start":   start_time,
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

            _panel_slots.pop(slot_id, None)
            await _remove_panel_if_empty()

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
            return

        except asyncio.CancelledError:
            logging.info(f"Job slot {slot_id} cancelled")
            add_history(
                Messages.download_name or "?", 0, mode_type, "cancelled",
                int((datetime.now() - start_time).total_seconds())
            )
            _panel_slots.pop(slot_id, None)
            await _remove_panel_if_empty()
            return

        except Exception as e:
            logging.error(f"Job slot {slot_id} attempt {attempt} failed: {e}")
            if attempt >= MAX_RETRY:
                err_text = error_card(reason=str(e)[:50], suggestion="Check source or retry")
                _panel_slots.pop(slot_id, None)
                await _remove_panel_if_empty()
                await colab_bot.send_message(chat_id=OWNER, text=err_text)
                add_history(
                    Messages.download_name or "?", 0, mode_type, "error",
                    int((datetime.now() - start_time).total_seconds())
                )
            else:
                wait = 5 * attempt
                _panel_slots[slot_id]["status"] = f"RETRY {attempt+1}/{MAX_RETRY} in {wait}s"
                await sleep(wait)


# ──────────────────────────────────────────────
#  Queue dispatcher
# ──────────────────────────────────────────────

async def _dispatcher():
    slot_counter = 0
    while True:
        finished = [t for t in _active_tasks if t.done()]
        for t in finished:
            _active_tasks.remove(t)

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
        get_event_loop().create_task(_panel_loop())
        _dispatcher_started = True


async def taskScheduler():
    await ensure_dispatcher()

    job = {
        "source":   list(BOT.SOURCE),
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
        pos   = queue_size()
        label = (job["source"][0] if job["source"] else "?")[:24]
        card  = queue_card(pos, pos + active, label)
        await colab_bot.send_message(chat_id=OWNER, text=card)
