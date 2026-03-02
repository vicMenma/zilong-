"""
stream_extractor.py
─────────────────────────────────────────────────
Analyse un URL/fichier et liste toutes les pistes :
  - yt-dlp  : YouTube, Twitch, etc. (plateformes streaming)
  - ffprobe : TOUT le reste (liens directs, seedr, DDL, fichiers locaux)
  Les deux sont tentés, ffprobe gagne sur les liens directs.
"""
import json
import logging
import subprocess
import yt_dlp
from asyncio import get_event_loop
from concurrent.futures import ThreadPoolExecutor
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_sessions: dict = {}
_pool = ThreadPoolExecutor(max_workers=2)


# ─── formatage ────────────────────────────────

def _sz(b) -> str:
    if not b or b <= 0:
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} GB"

_FLAGS = {
    "en":"🇬🇧","fr":"🇫🇷","de":"🇩🇪","es":"🇪🇸","pt":"🇵🇹",
    "it":"🇮🇹","ru":"🇷🇺","ja":"🇯🇵","ko":"🇰🇷","zh":"🇨🇳",
    "ar":"🇸🇦","hi":"🇮🇳","tr":"🇹🇷","nl":"🇳🇱","pl":"🇵🇱",
    "sv":"🇸🇪","da":"🇩🇰","fi":"🇫🇮","cs":"🇨🇿","uk":"🇺🇦",
    "ro":"🇷🇴","hu":"🇭🇺","el":"🇬🇷","he":"🇮🇱","th":"🇹🇭",
    "vi":"🇻🇳","id":"🇮🇩","ms":"🇲🇾","no":"🇳🇴","und":"🌐",
}

def _flag(code: str) -> str:
    if not code:
        return "🌐"
    return _FLAGS.get(code.split("-")[0].lower()[:3], "🌐")


# ─── ffprobe (liens directs, fichiers locaux) ─

def _ffprobe_sync(url: str) -> dict | None:
    """
    Appelle ffprobe sur l'URL et retourne les infos JSON.
    Fonctionne sur n'importe quel fichier/lien HTTP direct.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except Exception as e:
        logging.warning(f"[ffprobe] error: {e}")
        return None


def _parse_ffprobe(info: dict, url: str) -> dict:
    """Convertit la sortie ffprobe en session standardisée."""
    streams   = info.get("streams", [])
    fmt       = info.get("format", {})
    duration  = float(fmt.get("duration") or 0)
    total_sz  = int(fmt.get("size") or 0)
    title     = fmt.get("tags", {}).get("title") or url.split("/")[-1][:80]

    videos, audios, subs = [], [], []

    for s in streams:
        codec_type = s.get("codec_type", "")
        codec_name = s.get("codec_name", "unknown")
        lang       = (s.get("tags") or {}).get("language", "")
        idx        = s.get("index", 0)
        title_tag  = (s.get("tags") or {}).get("title", "")

        if codec_type == "video":
            w   = s.get("width") or 0
            h   = s.get("height") or 0
            fps_raw = s.get("r_frame_rate", "0/1")
            try:
                num, den = fps_raw.split("/")
                fps = round(int(num) / int(den))
            except Exception:
                fps = 0
            # taille estimée par durée × bitrate
            br  = int(s.get("bit_rate") or 0)
            sz  = int(br * duration / 8) if br and duration else 0

            res = f"{h}p" if h else f"{w}×{h}"
            if fps > 30:
                res += f" {fps}fps"
            label = f"🎬  {res}  [{codec_name}]  {_sz(sz or total_sz)}"
            if title_tag:
                label += f"  {title_tag}"

            videos.append({
                "id": str(idx), "label": label,
                "h": h, "fps": fps, "sz": sz,
                "lang": lang, "codec": codec_name,
                "map": f"0:{idx}",
                "ext": "mkv",
            })

        elif codec_type == "audio":
            channels = s.get("channels") or 0
            sample   = s.get("sample_rate") or ""
            br       = int(s.get("bit_rate") or 0)
            sz       = int(br * duration / 8) if br and duration else 0
            lang_up  = lang.upper() if lang else "UNK"

            ch_str = f"{channels}ch" if channels else ""
            br_str = f"{br//1000}kbps" if br else ""
            label  = f"{_flag(lang)}  {lang_up}  [{codec_name}]  {ch_str}  {br_str}  {_sz(sz)}"
            if title_tag:
                label += f"  {title_tag}"

            audios.append({
                "id": str(idx), "label": label.strip(),
                "abr": br // 1000 if br else 0,
                "sz": sz, "lang": lang,
                "map": f"0:{idx}",
                "ext": "mka",
            })

        elif codec_type == "subtitle":
            lang_up = lang.upper() if lang else "UNK"
            label   = f"{_flag(lang)}  {lang_up}  [{codec_name}]"
            if title_tag:
                label += f"  {title_tag}"

            subs.append({
                "id": str(idx), "label": label,
                "lang": lang,
                "map": f"0:{idx}",
                "ext": "srt" if codec_name in ("subrip","mov_text") else "ass",
                "url": None,   # extraction locale
            })

    return {
        "url":    url,
        "title":  title,
        "video":  videos,
        "audio":  audios,
        "subs":   subs,
        "source": "ffprobe",
    }


# ─── yt-dlp (plateformes streaming) ──────────

def _ytdlp_sync(url: str) -> dict | None:
    opts = {
        "quiet": True, "no_warnings": True,
        "skip_download": True, "noplaylist": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        if not info.get("formats"):
            return None
        return info
    except Exception as e:
        logging.debug(f"[yt-dlp] {e}")
        return None


def _parse_ytdlp(info: dict, url: str) -> dict:
    formats   = info.get("formats") or []
    subtitles = info.get("subtitles") or {}

    videos, audios, subs = [], [], []

    for f in formats:
        vc = f.get("vcodec", "none")
        ac = f.get("acodec", "none")
        if vc == "none":
            continue
        h   = f.get("height") or 0
        fps = int(f.get("fps") or 0)
        sz  = f.get("filesize") or f.get("filesize_approx") or 0
        vc_s = vc.split(".")[0]
        ac_s = ac.split(".")[0] if ac != "none" else "—"
        lang = f.get("language") or ""

        res = f"{h}p" if h else "?"
        if fps > 30:
            res += f" {fps}fps"
        audio_tag = f"+{ac_s}" if ac_s != "—" else " (no audio)"
        label = f"{_flag(lang)}  {res}  [{vc_s}{audio_tag}]  {_sz(sz)}"

        videos.append({
            "id": f.get("format_id", ""), "label": label,
            "h": h, "fps": fps, "sz": sz,
            "lang": lang, "ext": f.get("ext", "mp4"),
            "has_audio": ac != "none",
        })

    videos.sort(key=lambda x: (x["h"], x["fps"]), reverse=True)
    seen_v, dedup_v = set(), []
    for v in videos:
        k = (v["h"], v.get("has_audio", True))
        if k not in seen_v:
            seen_v.add(k); dedup_v.append(v)
    videos = dedup_v[:12]

    for f in formats:
        vc = f.get("vcodec", "none")
        ac = f.get("acodec", "none")
        if vc != "none" or ac == "none":
            continue
        abr  = int(f.get("abr") or f.get("tbr") or 0)
        sz   = f.get("filesize") or f.get("filesize_approx") or 0
        lang = f.get("language") or ""
        ac_s = ac.split(".")[0]
        ext  = f.get("ext", "m4a")
        lang_up = lang.upper() if lang else "UNK"
        label = f"{_flag(lang)}  {lang_up}  [{ac_s}]  {abr}kbps  {_sz(sz)}"
        audios.append({
            "id": f.get("format_id",""), "label": label,
            "abr": abr, "sz": sz, "lang": lang, "ext": ext,
        })

    audios.sort(key=lambda x: (x["lang"], -x["abr"]))
    seen_a, dedup_a = set(), []
    for a in audios:
        k = (a["lang"], a["ext"])
        if k not in seen_a:
            seen_a.add(k); dedup_a.append(a)
    audios = dedup_a[:12]

    for lang_code, tracks in subtitles.items():
        best = next((t for t in tracks if t.get("ext") in ("vtt","srt")), tracks[0] if tracks else None)
        if not best:
            continue
        subs.append({
            "lang": lang_code,
            "label": f"{_flag(lang_code)}  {lang_code.upper()}  [{best.get('ext','?')}]",
            "url":  best.get("url", ""),
            "ext":  best.get("ext", "srt"),
        })
    subs.sort(key=lambda x: x["lang"])

    return {
        "url":    url,
        "title":  (info.get("title") or "Unknown")[:80],
        "video":  videos,
        "audio":  audios,
        "subs":   subs,
        "source": "ytdlp",
    }


# ─── magnet / torrent ────────────────────────

_VIDEO_EXTS = {".mkv",".mp4",".avi",".mov",".ts",".m2ts",".webm",".m4v",".mpg",".mpeg"}
_AUDIO_EXTS = {".mp3",".flac",".aac",".ogg",".wav",".m4a",".opus"}
_SUB_EXTS   = {".srt",".ass",".ssa",".vtt",".sub"}

def _is_magnet(url: str) -> bool:
    return url.strip().startswith("magnet:")

def _fetch_torrent_meta_sync(magnet: str, tmp_dir: str) -> list | None:
    """
    Uses aria2c --bt-metadata-only + --bt-save-metadata to fetch
    the .torrent file without downloading actual content.
    Parses the torrent to get file list + sizes.
    Returns list of {name, size, ext} or None on failure.
    """
    import os, shutil, time

    os.makedirs(tmp_dir, exist_ok=True)

    # aria2c will save <infohash>.torrent into tmp_dir
    cmd = [
        "aria2c",
        "--bt-metadata-only=true",
        "--bt-save-metadata=true",
        "--bt-tracker-timeout=20",
        "--bt-tracker-connect-timeout=10",
        "--summary-interval=0",
        "--console-log-level=error",
        f"--dir={tmp_dir}",
        magnet,
    ]
    try:
        proc = subprocess.run(cmd, timeout=60, capture_output=True)
    except subprocess.TimeoutExpired:
        logging.warning("[Torrent] Metadata fetch timed out")
        return None
    except Exception as e:
        logging.warning(f"[Torrent] aria2c error: {e}")
        return None

    # Find the .torrent file
    torrent_files = [f for f in os.listdir(tmp_dir) if f.endswith(".torrent")]
    if not torrent_files:
        logging.warning("[Torrent] No .torrent metadata file saved")
        return None

    torrent_path = os.path.join(tmp_dir, torrent_files[0])

    # Parse torrent with bencode (built-in approach, no new lib)
    try:
        files = _parse_torrent_files(torrent_path)
        return files
    except Exception as e:
        logging.warning(f"[Torrent] Parse error: {e}")
        return None


def _bdecode(data: bytes):
    """Minimal bencode decoder — no external lib needed."""
    def decode(pos):
        ch = chr(data[pos])
        if ch == 'i':
            end = data.index(b'e', pos)
            return int(data[pos+1:end]), end+1
        elif ch == 'l':
            lst, pos = [], pos+1
            while chr(data[pos]) != 'e':
                val, pos = decode(pos)
                lst.append(val)
            return lst, pos+1
        elif ch == 'd':
            dct, pos = {}, pos+1
            while chr(data[pos]) != 'e':
                key, pos = decode(pos)
                val, pos = decode(pos)
                if isinstance(key, bytes):
                    key = key.decode('utf-8', errors='replace')
                dct[key] = val
            return dct, pos+1
        else:
            # byte string: length:data
            colon = data.index(b':', pos)
            length = int(data[pos:colon])
            start  = colon + 1
            return data[start:start+length], start+length
    val, _ = decode(0)
    return val


def _parse_torrent_files(torrent_path: str) -> list:
    """
    Returns list of {name, size, ext, path} from a .torrent file.
    Works for both single-file and multi-file torrents.
    """
    import os
    with open(torrent_path, "rb") as f:
        data = f.read()

    meta = _bdecode(data)
    info = meta.get("info", {})
    results = []

    if "files" in info:
        # Multi-file torrent
        root = info.get("name", b"").decode("utf-8", errors="replace") if isinstance(info.get("name"), bytes) else str(info.get("name",""))
        for file_entry in info["files"]:
            path_parts = file_entry.get("path", [])
            fname = "/".join(
                p.decode("utf-8", errors="replace") if isinstance(p, bytes) else str(p)
                for p in path_parts
            )
            size  = file_entry.get("length", 0)
            _, ext = os.path.splitext(fname.lower())
            results.append({"name": fname, "size": size, "ext": ext, "path": f"{root}/{fname}"})
    else:
        # Single-file torrent
        name = info.get("name", b"").decode("utf-8", errors="replace") if isinstance(info.get("name"), bytes) else str(info.get("name",""))
        size = info.get("length", 0)
        _, ext = os.path.splitext(name.lower())
        results.append({"name": name, "size": size, "ext": ext, "path": name})

    return results


def _parse_torrent_session(files: list, magnet: str) -> dict:
    """
    Build a stream session from the torrent file list.
    Video/audio/sub files become selectable streams.
    Download uses the original magnet + file index selection.
    """
    import os
    # Sort by size desc so biggest files come first
    files_sorted = sorted(files, key=lambda x: x["size"], reverse=True)

    videos, audios, subs = [], [], []

    # Guess title from largest video file name
    title = "Torrent"
    for f in files_sorted:
        if f["ext"] in _VIDEO_EXTS:
            title = os.path.basename(f["name"])
            break

    for i, f in enumerate(files_sorted):
        ext  = f["ext"]
        name = os.path.basename(f["name"])
        sz   = _sz(f["size"])
        label_base = f"{name[:30]}  {sz}"

        if ext in _VIDEO_EXTS:
            # Try to guess resolution from filename
            res = "?"
            for tag in ["2160","1080","720","480","360"]:
                if tag in name:
                    res = f"{tag}p"; break

            # Codec hints from filename
            codec = "?"
            for tag in ["HEVC","x265","h265","AVC","x264","h264","AV1","VP9"]:
                if tag.lower() in name.lower():
                    codec = tag; break

            label = f"🎬  {res}  [{codec}]  {sz}  —  {name[:25]}"
            videos.append({
                "id":    str(i),
                "label": label,
                "h": int(res.replace("p","")) if res != "?" else 0,
                "fps": 0, "sz": f["size"],
                "lang": "", "ext": ext.lstrip("."),
                "has_audio": True,
                "torrent_file_index": i,
                "torrent_file_path":  f["path"],
                "source": "torrent",
            })

        elif ext in _AUDIO_EXTS:
            label = f"🎵  [{ext.lstrip('.')}]  {sz}  —  {name[:25]}"
            audios.append({
                "id":    str(i),
                "label": label,
                "abr": 0, "sz": f["size"],
                "lang": "", "ext": ext.lstrip("."),
                "torrent_file_index": i,
                "torrent_file_path":  f["path"],
                "source": "torrent",
            })

        elif ext in _SUB_EXTS:
            # Guess language from filename
            lang = ""
            for code in ["fr","en","de","es","ja","ar","pt","it","ru","ko","zh"]:
                if f".{code}." in name.lower() or f"_{code}_" in name.lower() or f".{code}{ext}" in name.lower():
                    lang = code; break
            label = f"{_flag(lang)}  [{ext.lstrip('.')}]  {name[:28]}"
            subs.append({
                "id":   str(i),
                "label": label,
                "lang":  lang,
                "ext":   ext.lstrip("."),
                "url":   None,
                "torrent_file_index": i,
                "torrent_file_path":  f["path"],
                "source": "torrent",
            })

    return {
        "url":    magnet,
        "title":  title[:80],
        "video":  videos[:12],
        "audio":  audios[:12],
        "subs":   subs[:20],
        "source": "torrent",
        "_all_files": files_sorted,
    }


# ─── point d'entrée principal ─────────────────

async def analyse(url: str, chat_id: int) -> dict | None:
    """
    Moteur triple :
      1. Magnet/torrent → aria2c metadata + bencode parser
      2. ffprobe        → liens HTTP directs, fichiers locaux
      3. yt-dlp         → plateformes streaming (YouTube, etc.)
    """
    loop  = get_event_loop()
    url_s = url.strip()

    # ── 1. MAGNET / TORRENT ───────────────────
    if _is_magnet(url_s) or url_s.endswith(".torrent"):
        import tempfile, os
        tmp = tempfile.mkdtemp(prefix="sx_torrent_")
        try:
            files = await loop.run_in_executor(
                _pool, _fetch_torrent_meta_sync, url_s, tmp
            )
        finally:
            try:
                import shutil; shutil.rmtree(tmp, ignore_errors=True)
            except Exception: pass

        if files:
            session = _parse_torrent_session(files, url_s)
            if session["video"] or session["audio"] or session["subs"]:
                _sessions[chat_id] = session
                return session

    # ── 2. FFPROBE — liens HTTP directs ───────
    if url_s.startswith("http"):
        raw = await loop.run_in_executor(_pool, _ffprobe_sync, url_s)
        if raw and raw.get("streams"):
            session = _parse_ffprobe(raw, url_s)
            if session["video"] or session["audio"] or session["subs"]:
                _sessions[chat_id] = session
                return session

    # ── 3. YT-DLP — plateformes streaming ─────
    info = await loop.run_in_executor(_pool, _ytdlp_sync, url_s)
    if info:
        session = _parse_ytdlp(info, url_s)
        if session["video"] or session["audio"] or session["subs"]:
            _sessions[chat_id] = session
            return session

    return None


def get_session(chat_id: int):
    return _sessions.get(chat_id)

def clear_session(chat_id: int):
    _sessions.pop(chat_id, None)


# ─── keyboards ────────────────────────────────

def kb_type(v, a, s) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎬 Vidéo  ({v})",       callback_data="sx_video"),
         InlineKeyboardButton(f"🎵 Audio  ({a})",       callback_data="sx_audio")],
        [InlineKeyboardButton(f"💬 Sous-titres  ({s})", callback_data="sx_subs")],
        [InlineKeyboardButton("⏎ Retour",               callback_data="sx_back")],
    ])

def kb_video(session) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(v["label"], callback_data=f"sx_dl_video_{i}")]
            for i, v in enumerate(session["video"])]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)

def kb_audio(session) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(a["label"], callback_data=f"sx_dl_audio_{i}")]
            for i, a in enumerate(session["audio"])]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)

def kb_subs(session) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(s["label"], callback_data=f"sx_dl_sub_{i}")]
            for i, s in enumerate(session["subs"])]
    rows.append([InlineKeyboardButton("⏎ Retour", callback_data="sx_type")])
    return InlineKeyboardMarkup(rows)


# ─── téléchargement ───────────────────────────

def _dl_ytdlp(url: str, fmt_id: str, out: str) -> str:
    opts = {
        "quiet": True, "no_warnings": True,
        "format": fmt_id,
        "outtmpl": f"{out}/%(title)s.%(ext)s",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url)
        return ydl.prepare_filename(info)


def _dl_ffmpeg(url: str, stream_map: str, out_file: str) -> str:
    """Extrait une piste précise avec ffmpeg (map 0:N)."""
    cmd = [
        "ffmpeg", "-y", "-i", url,
        "-map", stream_map,
        "-c", "copy",
        out_file,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode()[-300:])
    return out_file


def _dl_sub_url(sub_url: str, out: str, lang: str, ext: str) -> str:
    import urllib.request
    dest = f"{out}/subtitle_{lang}.{ext}"
    urllib.request.urlretrieve(sub_url, dest)
    return dest


def _dl_torrent_file(magnet: str, file_index: int, out: str) -> str:
    """
    Download ONE specific file from a torrent by its index using aria2c
    --select-file. Returns the path to the downloaded file.
    """
    import os, glob
    os.makedirs(out, exist_ok=True)
    # aria2c uses 1-based index for --select-file
    select = str(file_index + 1)
    cmd = [
        "aria2c",
        f"--select-file={select}",
        "--seed-time=0",
        "--max-connection-per-server=8",
        "--split=8",
        "--bt-tracker-timeout=30",
        "--console-log-level=error",
        "--summary-interval=0",
        f"--dir={out}",
        magnet,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=7200)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode()[-400:])
    # Find the downloaded file (newest in out dir)
    files = sorted(
        [os.path.join(r, f) for r, _, fs in os.walk(out) for f in fs],
        key=os.path.getmtime, reverse=True
    )
    if not files:
        raise RuntimeError("aria2c finished but no file found")
    return files[0]


async def dl_video(session, idx: int, out: str) -> str:
    v    = session["video"][idx]
    loop = get_event_loop()
    if v.get("source") == "torrent":
        return await loop.run_in_executor(
            _pool, _dl_torrent_file,
            session["url"], v["torrent_file_index"], out
        )
    elif session["source"] == "ytdlp":
        return await loop.run_in_executor(_pool, _dl_ytdlp, session["url"], v["id"], out)
    else:
        fname = f"{out}/video_stream_{idx}.{v['ext']}"
        return await loop.run_in_executor(_pool, _dl_ffmpeg, session["url"], v["map"], fname)


async def dl_audio(session, idx: int, out: str) -> str:
    a    = session["audio"][idx]
    loop = get_event_loop()
    if a.get("source") == "torrent":
        return await loop.run_in_executor(
            _pool, _dl_torrent_file,
            session["url"], a["torrent_file_index"], out
        )
    elif session["source"] == "ytdlp":
        return await loop.run_in_executor(_pool, _dl_ytdlp, session["url"], a["id"], out)
    else:
        fname = f"{out}/audio_stream_{idx}.{a['ext']}"
        return await loop.run_in_executor(_pool, _dl_ffmpeg, session["url"], a["map"], fname)


async def dl_sub(session, idx: int, out: str) -> str:
    s    = session["subs"][idx]
    loop = get_event_loop()
    if s.get("source") == "torrent":
        return await loop.run_in_executor(
            _pool, _dl_torrent_file,
            session["url"], s["torrent_file_index"], out
        )
    elif s.get("url"):
        return await loop.run_in_executor(_pool, _dl_sub_url, s["url"], out, s["lang"], s["ext"])
    else:
        fname = f"{out}/subtitle_{s['lang']}_{idx}.{s['ext']}"
        return await loop.run_in_executor(_pool, _dl_ffmpeg, session["url"], s["map"], fname)
