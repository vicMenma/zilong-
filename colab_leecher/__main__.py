import logging
import os
import platform
import psutil
from datetime import datetime, timedelta
from asyncio import sleep, get_event_loop
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.handler import (
    cancelTask, burn_subtitles, compress_video, change_resolution,
)
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Paths
from colab_leecher.utility.task_manager import (
    taskScheduler, ensure_dispatcher,
    get_history, active_count, queue_size, enqueue,
)
from colab_leecher.utility.helper import (
    isLink, setThumbnail, message_deleter, send_settings,
    sizeUnit, getTime, is_ytdl_link, _pct_bar,
    _TOP, _MID, _BOT, _EMPTY, _row, _line, _bar_ui,
    _SEP, _h, _field,
    completion_card, error_card, queue_card, history_card,
    _ring,
)
from colab_leecher.channel_manager import (
    get_channels, add_channel, remove_channel,
    kb_channel_select, kb_channel_manage,
)
from colab_leecher.video_studio import (
    vstudio_open, vstudio_callback,
    vstudio_handle_text, vstudio_handle_file,
)
from colab_leecher.stream_extractor import (
    analyse, get_session, clear_session,
    kb_type, kb_video, kb_audio, kb_subs,
    dl_video, dl_audio, dl_sub,
)


# ── Banner image paths (generated at install time) ──
_BANNER_DIR      = os.path.dirname(os.path.abspath(__file__))
_BANNER_WELCOME  = os.path.join(_BANNER_DIR, "banner_welcome.jpg")
_BANNER_PROGRESS = os.path.join(_BANNER_DIR, "banner_progress.jpg")
_BANNER_DONE     = os.path.join(_BANNER_DIR, "banner_done.jpg")
_BANNER_ERROR    = os.path.join(_BANNER_DIR, "banner_error.jpg")


def _owner(m): return m.chat.id == OWNER

# pending video ops state per chat
_video_ops: dict = {}   # chat_id -> {"path","name","op","sub_path"}

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.delete()
    await ensure_dispatcher()
    caption = (
        f"⚡ <b>VIDEO STUDIO AI  //  CORE v4.2</b>\n"
        f"{_SEP}\n\n"
        f"{_field('🟢', 'STATUS', 'ONLINE')}\n"
        f"{_field('👤', 'USER', message.from_user.first_name[:20])}\n"
        f"{_field('💎', 'PLAN', 'PRO')}\n"
        f"{_field('🔁', 'PARALLEL', '3 slots')}\n\n"
        f"<i>Send link  ·  /help for commands</i>\n"
        f"{_SEP}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📣 Support", url="https://t.me/New_Animes_2025"),
        InlineKeyboardButton("📊 Stats",   callback_data="stats_refresh"),
    ]])
    if os.path.exists(_BANNER_WELCOME):
        await colab_bot.send_photo(
            chat_id=message.chat.id, photo=_BANNER_WELCOME,
            caption=caption, reply_markup=kb,
        )
    else:
        await message.reply_text(caption, reply_markup=kb)

# ══════════════════════════════════════════════
#  /help
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    text = (
        f"📖 <b>HELP CENTER</b>\n"
        f"{_SEP}\n\n"
        f"🔗 <b>Sources</b>\n"
        f"  HTTP · Magnet · GDrive\n"
        f"  Mega · YouTube · Telegram\n"
        f"  Local paths · Direct links\n\n"
        f"⚙️ <b>Commands</b>\n"
        f"  /settings /stats /ping\n"
        f"  /queue /history /schedule\n"
        f"  /cancel /stop /vstudio\n\n"
        f"🎛 <b>Link options</b>\n"
        f"  <code>[nom.ext]</code>  <code>{{zip}}</code>  <code>(unzip)</code>\n\n"
        f"🎞 <b>Streams</b> button on every link\n\n"
        f"{_SEP}"
    )
    msg = await message.reply_text(text)
    await sleep(90)
    await message_deleter(message, msg)

# ══════════════════════════════════════════════
#  /stats
# ══════════════════════════════════════════════
def _stats_text():
    cpu  = psutil.cpu_percent(interval=1)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net  = psutil.net_io_counters()
    up_s = int((datetime.now() - datetime.fromtimestamp(psutil.boot_time())).total_seconds())
    active = active_count()
    queued = queue_size()
    return (
        f"📊 <b>SERVER STATS</b>\n"
        f"{_SEP}\n\n"
        f"{_field('🖥', 'OS', f'{platform.system()} {platform.release()}'[:20])}\n"
        f"{_field('🐍', 'Python', f'v{platform.python_version()}')}\n"
        f"{_field('⏱', 'Uptime', getTime(up_s))}\n"
        f"{_field('⚙', 'Active', f'{active} / 3 slots')}\n"
        f"{_field('📋', 'Queued', str(queued))}\n\n"
        f"── CPU  <code>[{_pct_bar(cpu,12)}]</code>  <b>{cpu:.0f}%</b>\n"
        f"── RAM  <code>[{_pct_bar(ram.percent,12)}]</code>  <b>{ram.percent:.0f}%</b>\n"
        f"    Free <code>{sizeUnit(ram.available)}</code>\n"
        f"── Disk <code>[{_pct_bar(disk.percent,12)}]</code>  <b>{disk.percent:.0f}%</b>\n"
        f"    Free <code>{sizeUnit(disk.free)}</code>\n\n"
        f"{_field('⬆', 'Sent', sizeUnit(net.bytes_sent))}\n"
        f"{_field('⬇', 'Recv', sizeUnit(net.bytes_recv))}\n\n"
        f"{_SEP}"
    )

_STATS_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔄 Refresh", callback_data="stats_refresh"),
    InlineKeyboardButton("✖ Close",   callback_data="close"),
]])

@colab_bot.on_message(filters.command("stats") & filters.private)
async def stats(client, message):
    if not _owner(message): return
    await message.delete()
    await message.reply_text(_stats_text(), reply_markup=_STATS_KB)

# ══════════════════════════════════════════════
#  /ping
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("ping") & filters.private)
async def ping(client, message):
    t0  = datetime.now()
    msg = await message.reply_text("⏳")
    ms  = (datetime.now() - t0).microseconds // 1000
    if ms < 100:   q, fill = "EXCELLENT", 12
    elif ms < 300: q, fill = "GOOD",       8
    elif ms < 700: q, fill = "FAIR",        4
    else:          q, fill = "POOR",         1
    bar = "▓" * fill + "░" * (12 - fill)
    await msg.edit_text(
        f"🏓 <b>PONG</b>\n"
        f"{_SEP}\n\n"
        f"⚡ <b>Latency</b>  <code>{ms} ms</code>\n"
        f"📶 <b>Quality</b>  {q}\n\n"
        f"<code>[{bar}]</code>\n\n"
        f"{_SEP}"
    )
    await sleep(20)
    await message_deleter(message, msg)

# ══════════════════════════════════════════════
#  /queue
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("queue") & filters.private)
async def queue_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    active = active_count()
    queued = queue_size()
    text = (
        f"📋 <b>QUEUE STATUS</b>\n"
        f"{_SEP}\n\n"
        f"{_field('⚙', 'Active', f'{active} / 3 slots')}\n"
        f"{_field('⏳', 'Waiting', str(queued))}\n\n"
        f"{_SEP}\n"
        f"💡 <i>Max 3 parallel downloads</i>"
    )
    await message.reply_text(text)

# ══════════════════════════════════════════════
#  /history
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("history") & filters.private)
async def history_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    records = get_history()
    if not records:
        await message.reply_text(
            f"📋 <b>HISTORY</b>\n{_SEP}\n\n"
            f"<i>No records yet.</i>"
        )
        return
    lines = ["📋 <b>DOWNLOAD HISTORY</b>", _SEP]
    for i, r in enumerate(records[-10:], 1):
        icon = "✅" if r["status"] == "ok" else ("❌" if r["status"] == "error" else "⚠️")
        nm = r['name'][:22]
        lines.append(f"\n{i:02}. {icon} <b>{nm}</b>")
        lines.append(f"    📦 {sizeUnit(r['size'])}")
        lines.append(f"    🕐 {r['time']}")
        lines.append(f"    ⏱ {r['duration']}")
        
    
    await message.reply_text("\n".join(lines))

# ══════════════════════════════════════════════
#  /schedule  — planifier un download
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("schedule") & filters.private)
async def schedule_cmd(client, message):
    if not _owner(message): return
    # Usage: /schedule HH:MM <url>
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply_text(
            f"📅 <b>SCHEDULER</b>\n{_SEP}\n\n"
            f"<b>Usage:</b>\n"
            f"  <code>/schedule HH:MM &lt;url&gt;</code>\n"
            f"  <i>Ex: /schedule 03:30 https://...</i>\n\n{_SEP}"
            f"{_SEP}"
        )
        return

    time_str = parts[1]
    url      = parts[2].strip()
    try:
        h, m = map(int, time_str.split(":"))
        now  = datetime.now()
        run_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        delay = (run_at - now).total_seconds()
    except Exception:
        await message.reply_text("❌ Format invalide. Ex: /schedule 03:30 http://...")
        return

    label = url[:30]
    text  = (
        f"📅 <b>SCHEDULED</b>\n{_SEP}\n\n"
        f"{_field('🕐', 'Run at', time_str)}\n"
        f"{_field('⏳', 'In', getTime(delay))}\n"
        f"{_field('📁', 'URL', label)}\n\n{_SEP}"
        f"{_SEP}"
    )
    await message.reply_text(text)

    async def _run_later():
        await sleep(delay)
        job = {"source":[url],"mode":"leech","type":"normal","ytdl":is_ytdl_link(url),
               "name":"","zip_pw":"","unzip_pw":""}
        enqueue(job)
        await colab_bot.send_message(
            chat_id=OWNER,
            text=f"📅 <b>SCHEDULED JOB STARTED</b>\n{_SEP}\n\n"
                 f"{_field('📁', 'URL', label)}\n\n{_SEP}"
        )

    get_event_loop().create_task(_run_later())

# ══════════════════════════════════════════════
#  /vstudio  — button-based Video Studio panel
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("vstudio") & filters.private)
async def vstudio_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    await vstudio_open(message)

# ══════════════════════════════════════════════
#  Commandes diverses
# ══════════════════════════════════════════════

# ══════════════════════════════════════════════
#  /addchannel — save a channel for auto-copy
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("addchannel") & filters.private)
async def addchannel_cmd(client, message):
    if not _owner(message): return
    # Usage: /addchannel <channel_id> <label>
    # channel_id can be @username or -100xxxxxxxxxx
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply_text(
            f"📺 <b>ADD CHANNEL</b>\n{_SEP}\n\n"
            f"  <code>/addchannel &lt;id&gt; &lt;label&gt;</code>\n\n"
            f"\n"
            f"<b>Examples:</b>\n"
            f"  <code>/addchannel -1001234567890 Anime</code>\n"
            f"  <code>/addchannel @mychannel Movies</code>\n\n"
            f"\n"
            f"⚠️ <i>Bot must be admin in the channel</i>\n\n{_SEP}"
            f"{_SEP}"
        )
        return

    raw_id = parts[1].strip()
    label  = parts[2].strip()

    # Resolve @username to numeric ID
    try:
        if raw_id.startswith("@"):
            chat = await client.get_chat(raw_id)
            channel_id = chat.id
            ch_type    = "public"
        else:
            channel_id = int(raw_id)
            ch_type    = "private"
    except Exception as e:
        await message.reply_text(
            f"❌ <b>CHANNEL NOT FOUND</b>\n{_SEP}\n\n"
            f"{_field('⚠', 'Error', str(e)[:40])}\n"
            f"<i>Check ID or make bot admin</i>\n\n{_SEP}"
        )
        return

    # Test that bot can actually post there
    try:
        test = await colab_bot.send_message(
            chat_id=channel_id,
            text="🤖 <i>Video Studio AI connected. Test message — will be deleted.</i>"
        )
        await test.delete()
    except Exception as e:
        await message.reply_text(
            f"❌ <b>CANNOT POST</b>\n{_SEP}\n\n"
            f"{_field('⚠', 'Error', str(e)[:40])}\n"
            f"<i>Make bot an admin first</i>\n\n{_SEP}"
        )
        return

    added = add_channel(channel_id, label, ch_type)
    if added:
        icon = "📢" if ch_type == "public" else "🔒"
        await message.reply_text(
            f"✅ <b>CHANNEL SAVED</b>\n{_SEP}\n\n"
            f"{_field(icon, 'Label', label)}\n"
            f"{_field('🆔', 'ID', str(channel_id))}\n"
            f"{_field('🔑', 'Type', ch_type.upper())}\n"
            f"\n"
            f"\n<i>Files will be copied here after each download.</i>\n\n{_SEP}"
            f"{_SEP}"
        )
    else:
        await message.reply_text(
            f"⚠️ <b>ALREADY SAVED</b>\n{_SEP}\n\n"
            f"{_field('📺', 'Channel', label)}\n\n{_SEP}"
        )


# ══════════════════════════════════════════════
#  /removechannel  — remove a saved channel
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("removechannel") & filters.private)
async def removechannel_cmd(client, message):
    if not _owner(message): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            f"🗑 <b>REMOVE CHANNEL</b>\n{_SEP}\n\n"
            f"  <code>/removechannel &lt;id&gt;</code>\n\n{_SEP}"
        )
        return
    try:
        channel_id = int(parts[1].strip())
    except ValueError:
        await message.reply_text("❌ Invalid ID — must be numeric.")
        return
    removed = remove_channel(channel_id)
    if removed:
        await message.reply_text(
            f"✅ <b>CHANNEL REMOVED</b>\n{_SEP}\n\n"
            f"{_field('🆔', 'ID', str(channel_id))}\n\n{_SEP}"
        )
    else:
        await message.reply_text(
            f"⚠️ <b>NOT FOUND</b>\n{_SEP}\n\n"
            f"{_field('🆔', 'ID', str(channel_id))}\n\n{_SEP}"
        )


# ══════════════════════════════════════════════
#  /channels  — list all saved channels
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.command("channels") & filters.private)
async def channels_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    channels = get_channels()
    if not channels:
        await message.reply_text(
            f"📺 <b>SAVED CHANNELS</b>\n{_SEP}\n\n"
            f"<i>No channels saved yet.</i>\n"
            f"\n"
            f"<code>/addchannel &lt;id&gt; &lt;label&gt;</code>\n\n{_SEP}"
        )
        return
    lines = ["📺 <b>SAVED CHANNELS</b>", _SEP]
    for c in channels:
        icon = "📢" if c["type"] == "public" else "🔒"
        lines.append(f"\n{icon} <b>{c['label']}</b>")
        lines.append(f"    🆔 <code>{c['id']}</code>")
        
    
    await message.reply_text(
        "\n".join(lines),
        reply_markup=kb_channel_manage()
    )

@colab_bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Annulé via /cancel")
    else:
        msg = await message.reply_text(
            f"⚠️ <b>NO ACTIVE TASK</b>\n{_SEP}"
        )
        await sleep(8); await msg.delete()

@colab_bot.on_message(filters.command("stop") & filters.private)
async def stop_bot(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going: await cancelTask("Arrêt du bot")
    await message.reply_text(
        f"🛑 <b>SHUTTING DOWN</b>\n{_SEP}\n\n"
        f"<i>All processes terminated.</i>\n\n{_SEP}"
    )
    await sleep(2); await client.stop(); os._exit(0)

@colab_bot.on_message(filters.command("settings") & filters.private)
async def settings(client, message):
    if _owner(message):
        await message.delete()
        await send_settings(client, message, message.id, True)

@colab_bot.on_message(filters.command("setname") & filters.private)
async def custom_name(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage: <code>/setname fichier.ext</code>", quote=True)
    else:
        BOT.Options.custom_name = message.command[1]
        msg = await message.reply_text(f"✅ Name → <code>{BOT.Options.custom_name}</code>", quote=True)
    await sleep(15); await message_deleter(message, msg)

@colab_bot.on_message(filters.command("zipaswd") & filters.private)
async def zip_pswd(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage: <code>/zipaswd pass</code>", quote=True)
    else:
        BOT.Options.zip_pswd = message.command[1]
        msg = await message.reply_text("✅ Zip password set 🔐", quote=True)
    await sleep(15); await message_deleter(message, msg)

@colab_bot.on_message(filters.command("unzipaswd") & filters.private)
async def unzip_pswd(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage: <code>/unzipaswd pass</code>", quote=True)
    else:
        BOT.Options.unzip_pswd = message.command[1]
        msg = await message.reply_text("✅ Unzip password set 🔓", quote=True)
    await sleep(15); await message_deleter(message, msg)

@colab_bot.on_message(filters.reply & filters.private)
async def setFix(client, message):
    if BOT.State.prefix:
        BOT.Setting.prefix = message.text; BOT.State.prefix = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()
    elif BOT.State.suffix:
        BOT.Setting.suffix = message.text; BOT.State.suffix = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()

# ══════════════════════════════════════════════
#  Video Studio — intercept text/file for step-by-step ops
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.text & filters.private & ~filters.command(["start","help","stats","ping","queue","history","schedule","cancel","stop","settings","setname","zipaswd","unzipaswd","vstudio","addchannel","removechannel","channels"]))
async def handle_text_or_vstudio(client, message):
    if not _owner(message): return
    # Let vstudio consume it first if a session is active
    if await vstudio_handle_text(message.chat.id, message.text or ""):
        return
    # Otherwise fall through to link handler (isLink filter handles it)

@colab_bot.on_message((filters.document | filters.video | filters.audio) & filters.private)
async def handle_file_vstudio(client, message):
    if not _owner(message): return
    await vstudio_handle_file(message.chat.id, message)

# ══════════════════════════════════════════════
#  Réception lien
# ══════════════════════════════════════════════
def _mode_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Normal",      callback_data="normal"),
         InlineKeyboardButton("🗜 Compresser",  callback_data="zip")],
        [InlineKeyboardButton("📂 Extraire",    callback_data="unzip"),
         InlineKeyboardButton("♻️ UnDoubleZip", callback_data="undzip")],
        [InlineKeyboardButton("🎞 Streams",     callback_data="sx_open"),
         InlineKeyboardButton("➕ Add Queue",   callback_data="add_queue")],
    ])

@colab_bot.on_message(filters.create(isLink) & ~filters.photo & filters.private)
async def handle_url(client, message):
    if not _owner(message): return
    BOT.Options.custom_name = ""
    BOT.Options.zip_pswd    = ""
    BOT.Options.unzip_pswd  = ""
    await ensure_dispatcher()

    src = message.text.splitlines()
    for _ in range(3):
        if not src: break
        last = src[-1].strip()
        if   last.startswith("[") and last.endswith("]"): BOT.Options.custom_name = last[1:-1]; src.pop()
        elif last.startswith("{") and last.endswith("}"): BOT.Options.zip_pswd    = last[1:-1]; src.pop()
        elif last.startswith("(") and last.endswith(")"): BOT.Options.unzip_pswd  = last[1:-1]; src.pop()
        else: break

    BOT.SOURCE    = src
    BOT.Mode.ytdl = all(is_ytdl_link(l) for l in src if l.strip())
    BOT.Mode.mode = "leech"
    BOT.State.started = True

    n     = len([l for l in src if l.strip()])
    label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 LINK"
    active = active_count()
    queued = queue_size()

    text = (
        f"⚡ <b>NEW JOB</b>\n"
        f"{_SEP}\n\n"
        f"{_field('📁', 'Sources', str(n))}\n"
        f"{_field('🔗', 'Type', label)}\n"
        f"{_field('⚙', 'Active', f'{active}/3')}\n"
        f"{_field('📋', 'Queue', str(queued))}\n\n"
        f"{_SEP}\n"
        f"<b>Select processing mode:</b>"
    )
    await message.reply_text(text, reply_markup=_mode_kb(), quote=True)

# ══════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════
@colab_bot.on_callback_query()
async def callbacks(client, cq):
    data    = cq.data
    chat_id = cq.message.chat.id

    # ── Video Studio callbacks ────────────────
    if data.startswith("vs_"):
        await vstudio_callback(cq)
        return

    if data == "stats_refresh":
        try: await cq.message.edit_text(_stats_text(), reply_markup=_STATS_KB)
        except Exception: pass
        return

    if data == "adv_logs":
        records = get_history()
        last = records[-3:] if records else []
        lines = [f"📊 <b>ADVANCED LOGS</b>\n{_SEP}"]
        if not last:
            lines.append("\n<i>No logs yet.</i>")
        for r in last:
            icon = "✅" if r["status"] == "ok" else "❌"
            lines.append(f"\n{icon} <b>{r['name'][:22]}</b>")
            lines.append(f"    🕐 {r['time']}  ·  <code>{r['status'].upper()}</code>")
        try: await cq.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✖ Close", callback_data="close")
        ]]))
        except Exception: pass
        return

    if data in ["normal","zip","unzip","undzip"]:
        BOT.Mode.type = data
        BOT.State.started   = False
        BotTimes.start_time = datetime.now()
        try: await cq.message.delete()
        except Exception: pass
        task = get_event_loop().create_task(taskScheduler())
        BOT.TASK = task
        return

    if data == "add_queue":
        # Add current SOURCE to queue without launching immediately
        job = {
            "source":   BOT.SOURCE,
            "mode":     "leech",
            "type":     "normal",
            "ytdl":     BOT.Mode.ytdl,
            "name":     BOT.Options.custom_name,
            "zip_pw":   BOT.Options.zip_pswd,
            "unzip_pw": BOT.Options.unzip_pswd,
        }
        pos = enqueue(job)
        label = (BOT.SOURCE[0] if BOT.SOURCE else "?")[:24]
        await cq.message.edit_text(queue_card(pos, pos, label))
        return

    # ── Stream extractor ──────────────────────
    if data == "sx_open":
        url = (BOT.SOURCE or [None])[0]
        if not url: await cq.answer("No URL.", show_alert=True); return
        await cq.message.edit_text(
            f"🎞 <b>STREAM EXTRACTOR</b>\n"
            f"{_SEP}\n\n"
            f"⏳ <i>Analysing streams...</i>\n"
            f"<code>{url[:50]}</code>"
        )
        session = await analyse(url, chat_id)
        if not session or (not session["video"] and not session["audio"] and not session["subs"]):
            await cq.message.edit_text(
                error_card("No streams found", "Try yt-dlp source"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏎ Back", callback_data="sx_back")
                ]])
            )
            return
        await _show_sx_menu(cq.message, session)
        return

    if data == "sx_type":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expired.", show_alert=True); return
        await _show_sx_menu(cq.message, session)
        return

    if data == "sx_video":
        session = get_session(chat_id)
        if not session or not session["video"]: await cq.answer("No video tracks.", show_alert=True); return
        await cq.message.edit_text(
            f"🎬 <b>VIDEO STREAMS</b>\n"
            f"{_SEP}\n"
            f"<i>flag · resolution · [codec] · size</i>\n\n"
            f"Tap to download:",
            reply_markup=kb_video(session)
        )
        return

    if data == "sx_audio":
        session = get_session(chat_id)
        if not session or not session["audio"]: await cq.answer("No audio tracks.", show_alert=True); return
        await cq.message.edit_text(
            f"🎵 <b>AUDIO STREAMS</b>\n"
            f"{_SEP}\n"
            f"<i>flag · language · [codec] · kbps · size</i>\n\n"
            f"Tap to download:",
            reply_markup=kb_audio(session)
        )
        return

    if data == "sx_subs":
        session = get_session(chat_id)
        if not session or not session["subs"]: await cq.answer("No subtitles.", show_alert=True); return
        await cq.message.edit_text(
            f"💬 <b>SUBTITLE TRACKS</b>\n"
            f"{_SEP}\n"
            f"<i>flag · language · [format]</i>\n\n"
            f"Tap to download:",
            reply_markup=kb_subs(session)
        )
        return

    if data == "sx_back":
        clear_session(chat_id)
        n = len([l for l in (BOT.SOURCE or []) if l.strip()])
        label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 LINK"
        await cq.message.edit_text(
            f"⚡ <b>NEW JOB</b>\n"
            f"{_SEP}\n\n"
            f"{_field('📁', 'Sources', str(n))}\n"
            f"{_field('🔗', 'Type', label)}\n\n"
            f"{_SEP}\n"
            f"<b>Select processing mode:</b>",
            reply_markup=_mode_kb()
        )
        return

    if data.startswith("sx_dl_"):
        session = get_session(chat_id)
        if not session: await cq.answer("Session expired.", show_alert=True); return
        parts = data.split("_"); kind = parts[2]; idx = int(parts[3])
        stream = (session["video"] if kind=="video" else session["audio"] if kind=="audio" else session["subs"])[idx]
        await cq.message.edit_text(
            f"⬇️ <b>DOWNLOADING STREAM</b>\n"
            f"{_SEP}\n\n"
            f"{_field('🎯', 'Type', kind.upper())}\n"
            f"<code>{stream['label'][:50]}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
        )
        MSG.status_msg = cq.message
        os.makedirs(Paths.down_path, exist_ok=True)
        try:
            if kind == "video":   fp = await dl_video(session, idx, Paths.down_path)
            elif kind == "audio": fp = await dl_audio(session, idx, Paths.down_path)
            else:                 fp = await dl_sub(session, idx, Paths.down_path)
            from colab_leecher.uploader.telegram import upload_file
            await upload_file(fp, os.path.basename(fp), is_last=True)
            clear_session(chat_id)
        except Exception as e:
            logging.error(f"Stream DL: {e}")
            try: await cq.message.edit_text(error_card(str(e)[:40]))
            except Exception: pass
        return

    # ── Settings ──────────────────────────────
    if data == "video":
        await cq.message.edit_text(
            f"🎥 <b>VIDEO SETTINGS</b>\n{_SEP}\n\n"
            f"{_field('🔄', 'Convert', BOT.Setting.convert_video)}\n"
            f"{_field('✂', 'Split', BOT.Setting.split_video)}\n"
            f"{_field('🎬', 'Format', BOT.Options.video_out.upper())}\n"
            f"{_field('📊', 'Quality', BOT.Setting.convert_quality)}\n\n{_SEP}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✂ Split",    callback_data="split-true"),
                 InlineKeyboardButton("🗜 Zip",      callback_data="split-false")],
                [InlineKeyboardButton("🔄 Convert", callback_data="convert-true"),
                 InlineKeyboardButton("🚫 Skip",    callback_data="convert-false")],
                [InlineKeyboardButton("🎬 MP4",     callback_data="mp4"),
                 InlineKeyboardButton("📦 MKV",     callback_data="mkv")],
                [InlineKeyboardButton("🔝 High",    callback_data="q-High"),
                 InlineKeyboardButton("📉 Low",     callback_data="q-Low")],
                [InlineKeyboardButton("⏎ Back",     callback_data="back")],
            ]))
    elif data == "caption":
        await cq.message.edit_text(
            f"✏️ <b>CAPTION STYLE</b>\n{_SEP}\n\n"
            f"{_field('📝', 'Current', BOT.Setting.caption)}\n\n{_SEP}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Monospace", callback_data="code-Monospace"),
                 InlineKeyboardButton("Bold",      callback_data="b-Bold")],
                [InlineKeyboardButton("Italic",    callback_data="i-Italic"),
                 InlineKeyboardButton("Underline", callback_data="u-Underlined")],
                [InlineKeyboardButton("Regular",   callback_data="p-Regular")],
                [InlineKeyboardButton("⏎ Back",    callback_data="back")],
            ]))
    elif data == "thumb":
        await cq.message.edit_text(
            f"🖼 <b>THUMBNAIL</b>\n{_SEP}\n\n"
            f"{_field('📷', 'Status', '✅ Set' if BOT.Setting.thumbnail else '❌ None')}\n"
            f"<i>Send image to update.</i>\n\n{_SEP}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Delete", callback_data="del-thumb")],
                [InlineKeyboardButton("⏎ Back",   callback_data="back")],
            ]))
    elif data == "del-thumb":
        if BOT.Setting.thumbnail:
            try: os.remove(Paths.THMB_PATH)
            except Exception: pass
        BOT.Setting.thumbnail = False
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "set-prefix":
        await cq.message.edit_text(f"✏️ <b>Reply with PREFIX text:</b>\n{_SEP}")
        BOT.State.prefix = True
    elif data == "set-suffix":
        await cq.message.edit_text(f"✏️ <b>Reply with SUFFIX text:</b>\n{_SEP}")
        BOT.State.suffix = True
    elif data in ["code-Monospace","p-Regular","b-Bold","i-Italic","u-Underlined"]:
        r = data.split("-"); BOT.Options.caption = r[0]; BOT.Setting.caption = r[1]
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["split-true","split-false"]:
        BOT.Options.is_split    = data == "split-true"
        BOT.Setting.split_video = "Split" if data == "split-true" else "Zip"
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["convert-true","convert-false","mp4","mkv","q-High","q-Low"]:
        if   data == "convert-true":  BOT.Options.convert_video=True;  BOT.Setting.convert_video="Yes"
        elif data == "convert-false": BOT.Options.convert_video=False; BOT.Setting.convert_video="No"
        elif data == "q-High": BOT.Setting.convert_quality="High"; BOT.Options.convert_quality=True
        elif data == "q-Low":  BOT.Setting.convert_quality="Low";  BOT.Options.convert_quality=False
        else: BOT.Options.video_out = data
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["media","document"]:
        BOT.Options.stream_upload = data == "media"
        BOT.Setting.stream_upload = "Media" if data == "media" else "Document"
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "close":
        await cq.message.delete()
    elif data == "back":
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cancel":
        await cancelTask("Annulé par l'utilisateur")

    # ── Channel copy callbacks ─────────────────
    elif data == "ch_skip":
        try: await cq.message.delete()
        except Exception: pass

    elif data == "ch_none":
        await cq.answer("Add channels with /addchannel", show_alert=True)

    elif data.startswith("ch_rm_"):
        try:
            ch_id = int(data.split("_")[2])
            remove_channel(ch_id)
            # Refresh the channels list message
            channels = get_channels()
            if not channels:
                await cq.message.edit_text(
                    f"📺 <b>SAVED CHANNELS</b>\n{_SEP}\n\n"
                    f"<i>No channels saved.</i>\n\n{_SEP}"
                )
            else:
                lines = ["📺 <b>SAVED CHANNELS</b>", _SEP]
                for c in channels:
                    icon = "📢" if c["type"] == "public" else "🔒"
                    lines.append(f"\n{icon} <b>{c['label']}</b>")
                    lines.append(f"    🆔 <code>{c['id']}</code>")
                    
                
                await cq.message.edit_text(
                    "\n".join(lines),
                    reply_markup=kb_channel_manage()
                )
            await cq.answer("Channel removed ✅")
        except Exception as e:
            await cq.answer(f"Error: {e}", show_alert=True)

    elif data.startswith("ch_copy_"):
        # ch_copy_<channel_id>_<msg_id1>,<msg_id2>,...
        parts    = data.split("_", 3)
        ch_id    = int(parts[2])
        msg_ids  = [int(x) for x in parts[3].split(",") if x]
        await _do_channel_copy(cq, [ch_id], msg_ids)

    elif data.startswith("ch_all_"):
        # ch_all_<msg_id1>,<msg_id2>,...
        ids_str = data[len("ch_all_"):]
        msg_ids = [int(x) for x in ids_str.split(",") if x]
        ch_ids  = [c["id"] for c in get_channels()]
        await _do_channel_copy(cq, ch_ids, msg_ids)


async def _do_channel_copy(cq, channel_ids: list, msg_ids: list):
    """Forward the uploaded messages (by ID from OWNER chat) to each channel silently."""
    total   = len(channel_ids) * len(msg_ids)
    success = 0
    failed  = []

    for ch_id in channel_ids:
        for msg_id in msg_ids:
            try:
                await colab_bot.forward_messages(
                    chat_id=ch_id,
                    from_chat_id=OWNER,
                    message_ids=msg_id,
                    drop_author=True,      # silent — no "forwarded from" header
                )
                success += 1
            except Exception as e:
                logging.warning(f"[ChannelCopy] {ch_id} msg {msg_id}: {e}")
                failed.append(str(ch_id))

    # Build result card
    ch_labels = []
    for cid in channel_ids:
        c = get_channels()
        found = next((x for x in c if x["id"] == cid), None)
        ch_labels.append(found["label"] if found else str(cid))

    if failed:
        result_text = (
            f"⚠️ <b>COPY PARTIAL</b>\n{_SEP}\n\n"
            f"{_field('✅', 'Sent', str(success))}\n"
            f"{_field('❌', 'Failed', str(len(failed)))}\n\n{_SEP}"
            f"{_SEP}"
        )
    else:
        dest = ", ".join(ch_labels[:3])
        if len(ch_labels) > 3: dest += f" +{len(ch_labels)-3}"
        result_text = (
            f"✅ <b>COPIED TO CHANNEL</b>\n{_SEP}\n\n"
            f"{_field('📁', 'Files', str(success))}\n"
            f"{_field('📺', 'Dest', dest[:28])}\n\n{_SEP}"
            f"{_SEP}"
        )
    try:
        await cq.message.edit_text(result_text)
    except Exception:
        await colab_bot.send_message(chat_id=OWNER, text=result_text)


async def _show_sx_menu(msg, session):
    v = len(session["video"]); a = len(session["audio"]); s = len(session["subs"])
    title = session["title"][:28]
    await msg.edit_text(
        f"🎞 <b>STREAM EXTRACTOR</b>\n"
        f"{_SEP}\n\n"
        f"📌 <b>{title}</b>\n\n"
        f"{_field('🎬', 'Video', str(v))}\n"
        f"{_field('🎵', 'Audio', str(a))}\n"
        f"{_field('💬', 'Subs',  str(s))}\n\n"
        f"{_SEP}\n"
        f"<b>Select stream type:</b>",
        reply_markup=kb_type(v, a, s)
    )

# ══════════════════════════════════════════════
#  Photo → thumbnail
# ══════════════════════════════════════════════
@colab_bot.on_message(filters.photo & filters.private)
async def handle_photo(client, message):
    msg = await message.reply_text(f"🖼 <b>Saving thumbnail...</b>\n{_SEP}")
    if await setThumbnail(message):
        await msg.edit_text(f"✅ <b>THUMBNAIL UPDATED</b>\n{_SEP}")
        await message.delete()
    else:
        await msg.edit_text(f"❌ <b>Failed to set thumbnail</b>\n{_SEP}")
    await sleep(10)
    await message_deleter(message, msg)

logging.info("⚡ Video Studio AI started.")
get_event_loop().run_until_complete(ensure_dispatcher())
colab_bot.run()
