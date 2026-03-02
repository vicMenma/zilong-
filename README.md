<div align="center">

```
╔══════════════════════════════════════════════════════╗
║          ⚡  VIDEO STUDIO AI  //  CORE v4.2          ║
║          ──────────────────────────────────          ║
║           MULTI-SLOT TELEGRAM LEECHER BOT            ║
╚══════════════════════════════════════════════════════╝
```

![Python](https://img.shields.io/badge/Python-3.10%2B-00d4ff?style=flat-square&logo=python&logoColor=white)
![Pyrogram](https://img.shields.io/badge/Pyrogram-Pyrofork-00d4ff?style=flat-square)
![Colab](https://img.shields.io/badge/Google-Colab-orange?style=flat-square&logo=googlecolab&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Status](https://img.shields.io/badge/Status-ONLINE-00ff88?style=flat-square)

**A futuristic, feature-rich Telegram leecher bot running on Google Colab.**  
Download anything. Process everything. Upload instantly.

[Features](#-features) · [Setup](#-setup) · [Commands](#-commands) · [Stream Extractor](#-stream-extractor) · [File Structure](#-file-structure)

</div>

---

## ✨ Features

### 🎨 Futuristic Dark UI
Every message uses a premium dashboard design with live-updating progress cards, color-coded banners and inline controls — consistent across mobile and desktop.

```
╔══════════════════════════════════╗
║  ⚡ VIDEO STUDIO AI // CORE v4.2 ║
╠══════════════════════════════════╣
║  📁 FILE   : anime_ep01.mkv     ║
║  📦 TOTAL  : 1.42 GB            ║
║  🚀 SPEED  : 48.3 MiB/s         ║
║  ⏱ ETA    : 00m 38s             ║
║                                  ║
║  ▓▓▓▓▓▓▓▓▓▓▓░  91%              ║
║                                  ║
║  📊 DONE   : 1.29 GB            ║
║  💡 ENGINE : Pyrofork 💥         ║
╚══════════════════════════════════╝
```

### ⚡ 3-Slot Parallel Downloads
Send up to **3 links simultaneously** — each runs in its own slot with its own progress card. Additional links are queued and auto-start when a slot frees up.

```
Slot 1 ████████████░  89%   anime_s01e01.mkv
Slot 2 ████░░░░░░░░░  31%   movie_4k.mp4
Slot 3 ██████████░░░  78%   soundtrack.flac
Queue  [2 waiting...]
```

### 🎞 Stream Extractor
Analyse any URL **before downloading** — pick the exact video track, audio language or subtitle you want. Works with:

| Source | Engine |
|--------|--------|
| YouTube, Twitch, Crunchyroll… | yt-dlp |
| Direct links, Seedr, DDL `.mkv/.mp4` | ffprobe |
| **Magnet links / Torrents** | aria2c metadata + bencode parser |

### 🔧 Video Processing (no re-encode required)
All processing uses **ffmpeg** — no extra libraries needed.

| Command | What it does |
|---------|-------------|
| `/compress` | CRF-based compression (High / Medium / Low) |
| `/resolution` | Rescale to 1080p / 720p / 480p / 360p |
| `/burnsub` | Hard-burn subtitles into video |
| Stream Extractor → Video | Download specific resolution/codec track only |
| Stream Extractor → Audio | Extract single language audio track |
| Stream Extractor → Subtitles | Extract or download subtitle file |

### 📋 Queue & History
- Links beyond 3 active slots are queued automatically with a position card
- `/history` shows the last 50 downloads with status, size and duration
- `/queue` shows active slots and waiting jobs at a glance

### 📅 Scheduler
Plan downloads for later with a single command:
```
/schedule 03:30 https://example.com/bigfile.mkv
```
The bot will start the download at exactly 3:30 AM, even while you sleep.

### 🔁 Auto-Retry
Every job retries up to **3 times** with exponential backoff (5s → 10s → 15s) before reporting failure. Each retry attempt is shown in the status card.

### 📨 Forward Mode
Send `/forward` then drop any Telegram file — the bot instantly re-uploads it to your private chat with full metadata.

---

## 🚀 Setup

### 1 · Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| Google Colab | Any plan |
| Telegram Bot Token | [@BotFather](https://t.me/BotFather) |
| Telegram API ID + Hash | [my.telegram.org](https://my.telegram.org) |

### 2 · Colab Notebook — `credentials.json`

Create a `credentials.json` file in your Colab environment:

```json
{
  "BOT_TOKEN": "123456:ABC-your-bot-token",
  "API_ID": 12345678,
  "API_HASH": "your_api_hash_here",
  "OWNER_ID": 987654321,
  "DUMP_ID": 0
}
```

> `DUMP_ID` must be present but set to `0` — it is not used. All files upload directly to your private chat.

### 3 · Install Dependencies

```bash
pip install pyrofork pyrogram tgcrypto yt-dlp natsort psutil Pillow moviepy
apt-get install -y aria2 ffmpeg
```

### 4 · Run

```python
!python -m colab_leecher
```

Or mount the repo and run the notebook cell directly.

---

## 📁 File Structure

```
colab_leecher/
│
├── __main__.py              # Entry point — all handlers, commands, callbacks
├── stream_extractor.py      # Stream analysis (yt-dlp + ffprobe + torrent)
│
├── banner_welcome.jpg       # 🖼 /start banner  (blue)
├── banner_progress.jpg      # 🖼 Progress banner (amber)
├── banner_done.jpg          # 🖼 Completion banner (green)
├── banner_error.jpg         # 🖼 Error banner (red)
│
├── utility/
│   ├── variables.py         # Global state (BOT, MSG, Paths, Transfer…)
│   ├── helper.py            # UI builders, status_bar, formatters, file utils
│   ├── handler.py           # Leech, Zip, Unzip, cancel, burn_subs, compress
│   └── task_manager.py      # Queue dispatcher, retry logic, history
│
├── uploader/
│   └── telegram.py          # upload_file() — sends video/audio/doc/photo
│
├── downlader/
│   └── manager.py           # downloadManager() — aria2c, yt-dlp, gdown…
│
└── converters/
    └── ...                  # archive, extract, videoConverter, sizeChecker
```

---

## 📖 Commands

### Core

| Command | Description |
|---------|-------------|
| `/start` | Boot screen with welcome banner |
| `/help` | Full command reference |
| `/settings` | Configure upload mode, captions, prefix/suffix, thumbnail |
| `/stats` | Live server stats — CPU, RAM, disk, network, active slots |
| `/ping` | Latency test with quality bar |

### Download Control

| Command | Description |
|---------|-------------|
| `/cancel` | Terminate the current active task |
| `/stop` | Shut down the bot gracefully |
| `/queue` | Show active slots and waiting jobs |
| `/history` | Last 50 downloads with status and size |
| `/schedule HH:MM <url>` | Plan a download for a specific time |
| `/forward` | Re-upload any Telegram file |

### Video Processing

| Command | Usage | Description |
|---------|-------|-------------|
| `/compress` | `/compress <url> [high\|med\|low]` | Compress video (CRF encoding) |
| `/resolution` | `/resolution <url> 720p` | Change resolution |
| `/burnsub` | `/burnsub <video_url> <sub_url>` | Hard-burn subtitles |

### File Options (append to any link)

```
https://example.com/video.mkv
[my_custom_name.mkv]          ← rename output file
{zippassword}                 ← set zip password
(unzippassword)               ← set unzip password
```

---

## 🎞 Stream Extractor

When you send any link, tap **🎞 Streams** in the mode picker.

### Flow

```
Send link
    │
    ▼
[🎞 Streams] button
    │
    ▼
Bot analyses URL
  ├─ Magnet?  → aria2c fetches .torrent metadata (no download)
  ├─ HTTP?    → ffprobe reads remote stream headers
  └─ Platform? → yt-dlp lists available formats
    │
    ▼
Pick stream type
  ├─ 🎬 Video    (resolution · codec · size)
  ├─ 🎵 Audio    (language · codec · bitrate · size)
  └─ 💬 Subtitles (language · format)
    │
    ▼
Tap a track → download only that track → uploaded to your chat
```

### Example Display

**Video tracks:**
```
🎬  1080p 60fps  [h264+aac]  2.1 GB
🎬  1080p        [hevc]      1.4 GB   ← video only
🎬  720p         [h264+aac]  980 MB
🎬  480p         [h264+aac]  420 MB
```

**Audio tracks:**
```
🇯🇵  JA  [opus]   128kbps  45 MB
🇬🇧  EN  [aac]    192kbps  68 MB
🇫🇷  FR  [eac3]   384kbps  134 MB
```

**Subtitle tracks:**
```
🇬🇧  EN  [srt]
🇫🇷  FR  [ass]
🇯🇵  JA  [vtt]
```

### Magnet / Torrent Support

Magnets are fully supported. The bot:
1. Fetches the `.torrent` metadata via aria2c DHT (**~10 seconds, no actual download**)
2. Parses the file list with a built-in bencode decoder (no extra lib)
3. Displays every video, audio and subtitle file in the torrent
4. Downloads **only the file you select** with `aria2c --select-file`

---

## 🖼 Banner System

Four banners are generated at install time using Pillow and bundled with the bot:

| Banner | Trigger | Color |
|--------|---------|-------|
| `banner_welcome.jpg` | `/start` command | 🔵 Cyan |
| `banner_progress.jpg` | Task launched | 🟠 Amber |
| `banner_done.jpg` | Task completed | 🟢 Green |
| `banner_error.jpg` | Error / cancel | 🔴 Red |

If a banner file is missing, the bot automatically falls back to text-only mode — no crash.

> To regenerate banners with different colors or text, run the banner generation script at the bottom of `__main__.py`.

---

## ⚙️ Settings Panel

Access via `/settings`:

| Setting | Options | Default |
|---------|---------|---------|
| Upload mode | Media / Document | Media |
| Video split | Split / Zip | Split |
| Video convert | Yes / No | No |
| Caption style | Monospace / Bold / Italic / Regular | Monospace |
| Prefix | Any text | — |
| Suffix | Any text | — |
| Thumbnail | Custom image | Auto-extracted |

---

## 🔒 Security

- The bot only responds to the `OWNER_ID` defined in `credentials.json`
- All commands include an `_owner()` guard — other users are silently ignored
- No data is logged or stored outside Colab's runtime

---

## 📜 License

MIT — free to use, modify and deploy.

---

<div align="center">

```
╔══════════════════════════════════╗
║  🟢 PROCESS SUCCESSFUL           ║
║  Built with ⚡ by Zilong          ║
╚══════════════════════════════════╝
```

*Powered by [Pyrofork](https://github.com/KurimuzonAkuma/pyrogram) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [aria2](https://aria2.github.io) · [ffmpeg](https://ffmpeg.org)*

</div>
