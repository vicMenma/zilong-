"""
__main__.py — Zilong Leecher Bot entry point.

Key improvements vs original:
  1. Async _boot() function sends a personalised startup message to OWNER
     the moment the bot goes online (fetches their first name via get_users()).
  2. All UI cards use consistent design from helper.py.
  3. Inline keyboards everywhere with clear labels.
  4. Proper async shutdown — no more colab_bot.run() blocking call.
  5. Stats panel has a Refresh button; /start shows system status.
  6. Stream Extractor UI cleaned up and fully separated.
"""

import logging
import os
import platform
import psutil
import asyncio
from datetime import datetime
from asyncio import sleep, get_event_loop
from pyrogram import filters, idle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.handler import cancelTask
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Paths
from colab_leecher.utility.task_manager import taskScheduler
from colab_leecher.utility.helper import (
    isLink, setThumbnail, message_deleter, send_settings,
    sizeUnit, getTime, is_ytdl_link, _pct_bar, _ring, _SEP,
    completion_card, error_card, keyboard,
)
from colab_leecher.stream_extractor import (
    analyse, get_session, clear_session,
    kb_type, kb_video, kb_audio, kb_subs,
    dl_video, dl_audio, dl_sub,
)


def _owner(m) -> bool:
    return m.chat.id == OWNER


# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await message.delete()
    name = message.from_user.first_name or "toi"
    cpu  = psutil.cpu_percent(interval=0.5)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    text = (
        f"⚡ <b>ZILONG BOT</b>  //  LEECHER\n"
        f"{_SEP}\n\n"
        f"👋  Salut <b>{name}</b> !\n"
        f"🟢  En ligne et prêt.\n\n"
        f"🖥  CPU   <code>[{_pct_bar(cpu, 10)}]</code>  {_ring(cpu)} {cpu:.0f}%\n"
        f"💾  RAM   <code>{sizeUnit(ram.used)}</code> / <code>{sizeUnit(ram.total)}</code>\n"
        f"💿  Libre <code>{sizeUnit(disk.free)}</code>\n\n"
        f"<i>Envoie un lien · /help pour l'aide.</i>\n"
        f"{_SEP}"
    )
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Stats",      callback_data="stats_refresh"),
            InlineKeyboardButton("⚙️ Paramètres", callback_data="settings_open"),
            InlineKeyboardButton("❓ Aide",        callback_data="help_open"),
        ]]),
    )


# ══════════════════════════════════════════════
#  /help
# ══════════════════════════════════════════════

HELP_TEXT = (
    f"📖 <b>AIDE</b>\n"
    f"{_SEP}\n\n"
    f"🔗 <b>Sources supportées</b>\n"
    f"  · HTTP/HTTPS  · Magnet/Torrent\n"
    f"  · Google Drive  · Mega.nz\n"
    f"  · YouTube / yt-dlp\n"
    f"  · Liens Telegram\n\n"
    f"{_SEP}\n"
    f"⚙️ <b>Commandes</b>\n"
    f"  /settings  /stats  /ping\n"
    f"  /cancel    /stop\n\n"
    f"{_SEP}\n"
    f"🎛 <b>Options après le lien</b>\n"
    f"  <code>[nom.ext]</code>  → nom personnalisé\n"
    f"  <code>{{pass}}</code>     → mot de passe zip\n"
    f"  <code>(pass)</code>     → mot de passe unzip\n\n"
    f"{_SEP}\n"
    f"🎞 <b>Stream Extractor</b>\n"
    f"  Bouton <b>🎞 Streams</b> sur chaque lien.\n"
    f"  Choix : vidéo / audio / sous-titres\n"
    f"  avec langue, codec, résolution, taille.\n\n"
    f"🖼  Envoie une <b>photo</b> pour définir la miniature.\n"
    f"{_SEP}"
)

@colab_bot.on_message(filters.command("help") & filters.private)
async def cmd_help(client, message):
    msg = await message.reply_text(
        HELP_TEXT,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✖ Fermer", callback_data="close"),
        ]]),
    )
    await sleep(120)
    await message_deleter(message, msg)


# ══════════════════════════════════════════════
#  /stats
# ══════════════════════════════════════════════

def _stats_text() -> str:
    cpu  = psutil.cpu_percent(interval=0.5)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net  = psutil.net_io_counters()
    boot = int((datetime.now() - datetime.fromtimestamp(psutil.boot_time())).total_seconds())

    return (
        f"📊 <b>STATS SERVEUR</b>\n"
        f"{_SEP}\n\n"
        f"🖥  <b>OS</b>       <code>{platform.system()} {platform.release()}</code>\n"
        f"🐍  <b>Python</b>  <code>v{platform.python_version()}</code>\n"
        f"⏱  <b>Uptime</b>  <code>{getTime(boot)}</code>\n"
        f"🤖  <b>Tâche</b>   {'🟠 En cours' if BOT.State.task_going else '⚪ Inactif'}\n\n"
        f"{_SEP}\n"
        f"── CPU\n"
        f"  {_ring(cpu)} <code>[{_pct_bar(cpu, 16)}]</code>  <b>{cpu:.1f}%</b>\n\n"
        f"── RAM\n"
        f"  {_ring(ram.percent)} <code>[{_pct_bar(ram.percent, 16)}]</code>  "
        f"<b>{ram.percent:.1f}%</b>\n"
        f"  Utilisé <code>{sizeUnit(ram.used)}</code>  ·  Libre <code>{sizeUnit(ram.available)}</code>\n\n"
        f"── Disque\n"
        f"  {_ring(disk.percent)} <code>[{_pct_bar(disk.percent, 16)}]</code>  "
        f"<b>{disk.percent:.1f}%</b>\n"
        f"  Utilisé <code>{sizeUnit(disk.used)}</code>  ·  Libre <code>{sizeUnit(disk.free)}</code>\n\n"
        f"{_SEP}\n"
        f"── Réseau\n"
        f"  ⬆️  <code>{sizeUnit(net.bytes_sent)}</code>\n"
        f"  ⬇️  <code>{sizeUnit(net.bytes_recv)}</code>\n"
        f"{_SEP}"
    )

_STATS_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔄 Actualiser", callback_data="stats_refresh"),
    InlineKeyboardButton("✖ Fermer",      callback_data="close"),
]])

@colab_bot.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client, message):
    if not _owner(message): return
    await message.delete()
    await message.reply_text(_stats_text(), reply_markup=_STATS_KB)


# ══════════════════════════════════════════════
#  /ping
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("ping") & filters.private)
async def cmd_ping(client, message):
    t0  = datetime.now()
    msg = await message.reply_text("⏳")
    ms  = (datetime.now() - t0).microseconds // 1000
    if ms < 100:   q, fill = "🟢 Excellent", 12
    elif ms < 300: q, fill = "🟡 Bon",        8
    elif ms < 700: q, fill = "🟠 Moyen",       4
    else:          q, fill = "🔴 Mauvais",      1
    bar = "█" * fill + "░" * (12 - fill)
    await msg.edit_text(
        f"🏓 <b>PONG</b>\n"
        f"{_SEP}\n\n"
        f"<code>[{bar}]</code>\n\n"
        f"⚡ <b>Latence</b>  <code>{ms} ms</code>\n"
        f"📶 <b>Qualité</b>  {q}"
    )
    await sleep(20)
    await message_deleter(message, msg)


# ══════════════════════════════════════════════
#  Misc commands
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Annulé via /cancel")
    else:
        msg = await message.reply_text(
            f"⚠️ <b>Aucune tâche en cours.</b>\n{_SEP}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Fermer", callback_data="close")
            ]]),
        )
        await sleep(8)
        await msg.delete()


@colab_bot.on_message(filters.command("stop") & filters.private)
async def cmd_stop(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Arrêt du bot")
    await message.reply_text(f"🛑 <b>Arrêt en cours...</b> 👋\n{_SEP}")
    await sleep(2)
    await client.stop()
    os._exit(0)


@colab_bot.on_message(filters.command("settings") & filters.private)
async def cmd_settings(client, message):
    if not _owner(message): return
    await message.delete()
    await send_settings(client, message, message.id, True)


@colab_bot.on_message(filters.command("setname") & filters.private)
async def cmd_setname(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text(
            f"📛 <b>Nom personnalisé</b>\n{_SEP}\n\nUsage : <code>/setname fichier.ext</code>",
            quote=True,
        )
    else:
        BOT.Options.custom_name = message.command[1]
        msg = await message.reply_text(
            f"✅ Nom défini → <code>{BOT.Options.custom_name}</code>", quote=True
        )
    await sleep(15)
    await message_deleter(message, msg)


@colab_bot.on_message(filters.command("zipaswd") & filters.private)
async def cmd_zipaswd(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text(
            f"🔐 <b>Mot de passe zip</b>\n{_SEP}\n\nUsage : <code>/zipaswd motdepasse</code>",
            quote=True,
        )
    else:
        BOT.Options.zip_pswd = message.command[1]
        msg = await message.reply_text("✅ Mot de passe zip défini 🔐", quote=True)
    await sleep(15)
    await message_deleter(message, msg)


@colab_bot.on_message(filters.command("unzipaswd") & filters.private)
async def cmd_unzipaswd(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text(
            f"🔓 <b>Mot de passe unzip</b>\n{_SEP}\n\nUsage : <code>/unzipaswd motdepasse</code>",
            quote=True,
        )
    else:
        BOT.Options.unzip_pswd = message.command[1]
        msg = await message.reply_text("✅ Mot de passe unzip défini 🔓", quote=True)
    await sleep(15)
    await message_deleter(message, msg)


@colab_bot.on_message(filters.reply & filters.private)
async def handle_prefix_suffix(client, message):
    if BOT.State.prefix:
        BOT.Setting.prefix = message.text
        BOT.State.prefix   = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()
    elif BOT.State.suffix:
        BOT.Setting.suffix = message.text
        BOT.State.suffix   = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()


# ══════════════════════════════════════════════
#  Link reception — mode picker
# ══════════════════════════════════════════════

def _mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Normal",       callback_data="normal"),
            InlineKeyboardButton("🗜 Compresser",    callback_data="zip"),
        ],
        [
            InlineKeyboardButton("📂 Extraire",     callback_data="unzip"),
            InlineKeyboardButton("♻️ UnDoubleZip",  callback_data="undzip"),
        ],
        [
            InlineKeyboardButton("🎞 Streams",      callback_data="sx_open"),
        ],
        [
            InlineKeyboardButton("✖ Annuler",       callback_data="close"),
        ],
    ])


@colab_bot.on_message(filters.create(isLink) & ~filters.photo & filters.private)
async def handle_url(client, message):
    if not _owner(message): return

    # Block new job if one is already running
    if BOT.State.task_going:
        msg = await message.reply_text(
            f"⚠️ <b>Tâche en cours.</b>\n{_SEP}\n\n"
            f"Utilise /cancel pour l'arrêter d'abord.",
            quote=True,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ /cancel", callback_data="cancel"),
            ]]),
        )
        await sleep(10)
        await msg.delete()
        return

    # Reset per-job options
    BOT.Options.custom_name = ""
    BOT.Options.zip_pswd    = ""
    BOT.Options.unzip_pswd  = ""

    # Parse inline options appended after URL
    src = message.text.splitlines()
    for _ in range(3):
        if not src: break
        last = src[-1].strip()
        if   last.startswith("[") and last.endswith("]"): BOT.Options.custom_name = last[1:-1]; src.pop()
        elif last.startswith("{") and last.endswith("}"): BOT.Options.zip_pswd    = last[1:-1]; src.pop()
        elif last.startswith("(") and last.endswith(")"): BOT.Options.unzip_pswd  = last[1:-1]; src.pop()
        else: break

    BOT.SOURCE     = src
    BOT.Mode.ytdl  = all(is_ytdl_link(l) for l in src if l.strip())
    BOT.Mode.mode  = "leech"
    BOT.State.started = True

    n     = len([l for l in src if l.strip()])
    label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 Lien"
    opts  = []
    if BOT.Options.custom_name: opts.append(f"📛 {BOT.Options.custom_name}")
    if BOT.Options.zip_pswd:    opts.append("🔐 zip protégé")
    if BOT.Options.unzip_pswd:  opts.append("🔓 unzip protégé")
    opts_str = "  ·  ".join(opts) + "\n" if opts else ""

    await message.reply_text(
        f"{label}  ·  <code>{n}</code> source(s)\n"
        f"{opts_str}"
        f"<b>Choisir le mode de traitement :</b>",
        reply_markup=_mode_kb(),
        quote=True,
    )


# ══════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════

@colab_bot.on_callback_query()
async def callbacks(client, cq):
    data    = cq.data
    chat_id = cq.message.chat.id

    # ── Generic ────────────────────────────────
    if data == "close":
        await cq.message.delete()
        return

    if data == "stats_refresh":
        try:
            await cq.message.edit_text(_stats_text(), reply_markup=_STATS_KB)
        except Exception:
            pass
        return

    if data == "settings_open":
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data == "help_open":
        try:
            await cq.message.edit_text(
                HELP_TEXT,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✖ Fermer", callback_data="close"),
                ]]),
            )
        except Exception:
            pass
        return

    # ── Task launch ────────────────────────────
    if data in ("normal", "zip", "unzip", "undzip"):
        BOT.Mode.type = data
        try:
            await cq.message.delete()
        except Exception:
            pass

        MSG.status_msg = await colab_bot.send_message(
            chat_id=OWNER,
            text=(
                f"⏳ <b>Démarrage...</b>\n"
                f"{_SEP}\n\n"
                f"<i>Calcul de la taille, récupération du nom...</i>"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Annuler", callback_data="cancel"),
            ]]),
        )

        BOT.State.task_going = True
        BOT.State.started    = False
        BotTimes.start_time  = datetime.now()

        BOT.TASK = get_event_loop().create_task(taskScheduler())
        try:
            await BOT.TASK
        except asyncio.CancelledError:
            pass
        finally:
            BOT.State.task_going = False
        return

    if data == "cancel":
        await cancelTask("Annulé par l'utilisateur")
        return

    # ══════════════════════════════════════════
    #  STREAM EXTRACTOR
    # ══════════════════════════════════════════

    if data == "sx_open":
        url = (BOT.SOURCE or [None])[0]
        if not url:
            await cq.answer("Aucun URL.", show_alert=True)
            return
        await cq.message.edit_text(
            f"🎞 <b>STREAM EXTRACTOR</b>\n"
            f"{_SEP}\n\n"
            f"⏳ <i>Analyse en cours...</i>\n"
            f"<code>{url[:64]}{'…' if len(url)>64 else ''}</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏎ Retour", callback_data="sx_back"),
            ]]),
        )
        session = await analyse(url, chat_id)
        if not session or not (session["video"] or session["audio"] or session["subs"]):
            await cq.message.edit_text(
                f"🎞 <b>STREAM EXTRACTOR</b>\n"
                f"{_SEP}\n\n"
                f"❌ Impossible d'extraire les pistes.\n"
                f"<i>Seules les sources ffprobe / yt-dlp sont supportées.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏎ Retour", callback_data="sx_back"),
                ]]),
            )
            return
        await _show_sx_type_menu(cq.message, session)
        return

    if data == "sx_type":
        session = get_session(chat_id)
        if not session:
            await cq.answer("Session expirée. Renvoie le lien.", show_alert=True)
            return
        await _show_sx_type_menu(cq.message, session)
        return

    if data == "sx_video":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return
        if not session["video"]: await cq.answer("Aucune piste vidéo.", show_alert=True); return
        await cq.message.edit_text(
            f"🎬 <b>PISTES VIDÉO</b>\n"
            f"{_SEP}\n"
            f"<i>drapeau  résolution  [codec]  taille</i>\n\n"
            f"Appuie pour télécharger :",
            reply_markup=kb_video(session),
        )
        return

    if data == "sx_audio":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return
        if not session["audio"]: await cq.answer("Aucune piste audio.", show_alert=True); return
        await cq.message.edit_text(
            f"🎵 <b>PISTES AUDIO</b>\n"
            f"{_SEP}\n"
            f"<i>drapeau  langue  [codec]  débit  taille</i>\n\n"
            f"Appuie pour télécharger :",
            reply_markup=kb_audio(session),
        )
        return

    if data == "sx_subs":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expirée.", show_alert=True); return
        if not session["subs"]: await cq.answer("Aucun sous-titre.", show_alert=True); return
        await cq.message.edit_text(
            f"💬 <b>SOUS-TITRES</b>\n"
            f"{_SEP}\n"
            f"<i>drapeau  langue  [format]</i>\n\n"
            f"Appuie pour télécharger :",
            reply_markup=kb_subs(session),
        )
        return

    if data == "sx_back":
        clear_session(chat_id)
        n     = len([l for l in (BOT.SOURCE or []) if l.strip()])
        label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 Lien"
        await cq.message.edit_text(
            f"{label}  ·  <code>{n}</code> source(s)\n<b>Choisir le mode :</b>",
            reply_markup=_mode_kb(),
        )
        return

    if data.startswith("sx_dl_"):
        session = get_session(chat_id)
        if not session:
            await cq.answer("Session expirée.", show_alert=True)
            return

        _, _, kind, idx_str = data.split("_", 3)
        idx    = int(idx_str)
        stream = (
            session["video"]  if kind == "video"
            else session["audio"] if kind == "audio"
            else session["subs"]
        )[idx]

        label_fr = {"video": "Vidéo", "audio": "Audio", "sub": "Sous-titre"}.get(kind, kind)
        await cq.message.edit_text(
            f"🎞 <b>STREAM EXTRACTOR</b>\n"
            f"{_SEP}\n\n"
            f"⬇️ <i>Téléchargement {label_fr}...</i>\n\n"
            f"<code>{stream['label'][:60]}</code>\n\n"
            f"⏳ <i>Patiente...</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Annuler", callback_data="cancel"),
            ]]),
        )
        MSG.status_msg = cq.message
        os.makedirs(Paths.down_path, exist_ok=True)
        try:
            if kind == "video":
                fp = await dl_video(session, idx, Paths.down_path)
            elif kind == "audio":
                fp = await dl_audio(session, idx, Paths.down_path)
            else:
                fp = await dl_sub(session, idx, Paths.down_path)

            from colab_leecher.uploader.telegram import upload_file
            await upload_file(fp, os.path.basename(fp), is_last=True)
            clear_session(chat_id)
        except Exception as e:
            logging.error(f"[StreamDL] {e}")
            try:
                await cq.message.edit_text(error_card(str(e)[:50], "Vérifie la source"))
            except Exception:
                pass
        return

    # ══════════════════════════════════════════
    #  SETTINGS callbacks
    # ══════════════════════════════════════════

    if data == "video":
        await cq.message.edit_text(
            f"🎥 <b>PARAMÈTRES VIDÉO</b>\n"
            f"{_SEP}\n\n"
            f"Convertir  <code>{BOT.Setting.convert_video}</code>\n"
            f"Découper   <code>{BOT.Setting.split_video}</code>\n"
            f"Format     <code>{BOT.Options.video_out.upper()}</code>\n"
            f"Qualité    <code>{BOT.Setting.convert_quality}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✂️ Découper",   callback_data="split-true"),
                 InlineKeyboardButton("🗜 Zipper",      callback_data="split-false")],
                [InlineKeyboardButton("🔄 Convertir",  callback_data="convert-true"),
                 InlineKeyboardButton("🚫 Non",         callback_data="convert-false")],
                [InlineKeyboardButton("🎬 MP4",         callback_data="mp4"),
                 InlineKeyboardButton("📦 MKV",         callback_data="mkv")],
                [InlineKeyboardButton("🔝 Haute qualité", callback_data="q-High"),
                 InlineKeyboardButton("📉 Basse qualité", callback_data="q-Low")],
                [InlineKeyboardButton("⏎ Retour",      callback_data="back")],
            ]),
        )
        return

    if data == "caption":
        await cq.message.edit_text(
            f"✏️ <b>STYLE CAPTION</b>\n"
            f"{_SEP}\n\n"
            f"Actuel : <code>{BOT.Setting.caption}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("𝙈𝙤𝙣𝙤",     callback_data="code-Monospace"),
                 InlineKeyboardButton("𝐆𝐫𝐚𝐬",      callback_data="b-Bold")],
                [InlineKeyboardButton("𝘐𝘵𝘢𝘭𝘪𝘲𝘶𝘦", callback_data="i-Italic"),
                 InlineKeyboardButton("Souligné",   callback_data="u-Underlined")],
                [InlineKeyboardButton("Normal",     callback_data="p-Regular")],
                [InlineKeyboardButton("⏎ Retour",  callback_data="back")],
            ]),
        )
        return

    if data == "thumb":
        await cq.message.edit_text(
            f"🖼 <b>MINIATURE</b>\n"
            f"{_SEP}\n\n"
            f"Statut : {'✅ Définie' if BOT.Setting.thumbnail else '❌ Aucune'}\n\n"
            f"<i>Envoie une image pour mettre à jour.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Supprimer", callback_data="del-thumb")],
                [InlineKeyboardButton("⏎ Retour",    callback_data="back")],
            ]),
        )
        return

    if data == "del-thumb":
        if BOT.Setting.thumbnail:
            try: os.remove(Paths.THMB_PATH)
            except Exception: pass
        BOT.Setting.thumbnail = False
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data == "set-prefix":
        await cq.message.edit_text(
            f"✏️ <b>Réponds avec ton texte de préfixe</b>\n{_SEP}"
        )
        BOT.State.prefix = True
        return

    if data == "set-suffix":
        await cq.message.edit_text(
            f"✏️ <b>Réponds avec ton texte de suffixe</b>\n{_SEP}"
        )
        BOT.State.suffix = True
        return

    if data in ("code-Monospace", "p-Regular", "b-Bold", "i-Italic", "u-Underlined"):
        r = data.split("-")
        BOT.Options.caption = r[0]
        BOT.Setting.caption = r[1]
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data in ("split-true", "split-false"):
        BOT.Options.is_split    = data == "split-true"
        BOT.Setting.split_video = "Découper" if BOT.Options.is_split else "Zipper"
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data in ("convert-true", "convert-false", "mp4", "mkv", "q-High", "q-Low"):
        if   data == "convert-true":  BOT.Options.convert_video = True;  BOT.Setting.convert_video = "Oui"
        elif data == "convert-false": BOT.Options.convert_video = False; BOT.Setting.convert_video = "Non"
        elif data == "q-High":        BOT.Setting.convert_quality = "Haute"; BOT.Options.convert_quality = True
        elif data == "q-Low":         BOT.Setting.convert_quality = "Basse"; BOT.Options.convert_quality = False
        else:                         BOT.Options.video_out = data
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data in ("media", "document"):
        BOT.Options.stream_upload = data == "media"
        BOT.Setting.stream_upload = "Média" if BOT.Options.stream_upload else "Document"
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data == "back":
        await send_settings(client, cq.message, cq.message.id, False)
        return


# ══════════════════════════════════════════════
#  Stream extractor type menu
# ══════════════════════════════════════════════

async def _show_sx_type_menu(msg, session):
    v = len(session["video"])
    a = len(session["audio"])
    s = len(session["subs"])
    title = (session.get("title") or "?")[:40]
    await msg.edit_text(
        f"🎞 <b>STREAM EXTRACTOR</b>\n"
        f"{_SEP}\n\n"
        f"📌 <b>{title}</b>\n\n"
        f"🎬  Pistes vidéo    <code>{v}</code>\n"
        f"🎵  Pistes audio    <code>{a}</code>\n"
        f"💬  Sous-titres     <code>{s}</code>\n\n"
        f"Choisir un type de piste :",
        reply_markup=kb_type(v, a, s),
    )


# ══════════════════════════════════════════════
#  Photo → thumbnail
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.photo & filters.private)
async def handle_photo(client, message):
    msg = await message.reply_text(f"⏳ <i>Sauvegarde de la miniature...</i>")
    if await setThumbnail(message):
        await msg.edit_text(
            f"✅ <b>Miniature mise à jour.</b>\n{_SEP}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Fermer", callback_data="close"),
            ]]),
        )
        await message.delete()
    else:
        await msg.edit_text(f"❌ <b>Impossible de définir la miniature.</b>\n{_SEP}")
    await sleep(10)
    await message_deleter(msg)


# ══════════════════════════════════════════════
#  Boot — async startup with owner notification
# ══════════════════════════════════════════════

async def _boot():
    """
    Start Pyrogram, send a personalised boot message to the owner,
    then hand off to idle() so the bot keeps running until stopped.
    """
    await colab_bot.start()
    logging.info("⚡ Zilong Leecher démarré.")

    # ── Personalised startup notification ────
    try:
        user  = await colab_bot.get_users(OWNER)
        fname = getattr(user, "first_name", None) or "toi"
        cpu   = psutil.cpu_percent(interval=0.5)
        ram   = psutil.virtual_memory()
        disk  = psutil.disk_usage("/")

        boot_text = (
            f"⚡ <b>ZILONG BOT — EN LIGNE</b>\n"
            f"{_SEP}\n\n"
            f"👋  Bienvenue <b>{fname}</b> !\n"
            f"🟢  Bot démarré et prêt à l'emploi.\n\n"
            f"🖥  CPU   <code>[{_pct_bar(cpu, 10)}]</code>  {_ring(cpu)} {cpu:.0f}%\n"
            f"💾  RAM   <code>{sizeUnit(ram.used)}</code> / <code>{sizeUnit(ram.total)}</code>\n"
            f"💿  Libre <code>{sizeUnit(disk.free)}</code>\n\n"
            f"<i>Envoie un lien pour commencer.</i>\n"
            f"{_SEP}"
        )
        await colab_bot.send_message(
            chat_id=OWNER,
            text=boot_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Stats",      callback_data="stats_refresh"),
                InlineKeyboardButton("⚙️ Paramètres", callback_data="settings_open"),
                InlineKeyboardButton("❓ Aide",        callback_data="help_open"),
            ]]),
        )
    except Exception as e:
        logging.warning(f"[Boot message] {e}")

    await idle()
    await colab_bot.stop()


# ── Entry point ──────────────────────────────
loop = get_event_loop()
loop.run_until_complete(_boot())
