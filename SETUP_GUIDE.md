# ☁️ CloudConvert → Zilong Auto-Upload Integration

## How It Works

```
CloudConvert finishes job
        │
        ▼
POST webhook to ngrok URL
        │
        ▼
aiohttp server in Zilong receives it
        │
        ▼
Extracts download URL from payload
        │
        ▼
Enqueues as normal leech job
        │
        ▼
Bot downloads file + uploads to your Telegram
```

---

## Setup (5 minutes)

### Step 1 — Get a free ngrok token

1. Go to [ngrok.com](https://ngrok.com) → Sign up (free)
2. Copy your **Authtoken** from the dashboard

### Step 2 — Add the new file to your repo

Copy `cloudconvert_hook.py` into `colab_leecher/` alongside `__main__.py`.

### Step 3 — Patch `colab_leecher/__init__.py`

After this existing line:

```python
DUMP_ID = str(credentials["DUMP_ID"])
```

Add:

```python
NGROK_TOKEN       = str(credentials.get("NGROK_TOKEN", ""))
CC_WEBHOOK_SECRET = str(credentials.get("CC_WEBHOOK_SECRET", ""))
```

### Step 4 — Patch `colab_leecher/__main__.py`

**Add import** (at the top with other imports):

```python
from colab_leecher import NGROK_TOKEN, CC_WEBHOOK_SECRET
import colab_leecher.cloudconvert_hook as cc_hook
```

**Replace the last 3 lines** of the file:

```python
# OLD:
logging.info("⚡ Video Studio AI started.")
get_event_loop().run_until_complete(ensure_dispatcher())
colab_bot.run()

# NEW:
from colab_leecher.cloudconvert_hook import start_webhook_server

async def _boot():
    await ensure_dispatcher()
    if CC_WEBHOOK_SECRET:
        cc_hook.WEBHOOK_SECRET = CC_WEBHOOK_SECRET
    if NGROK_TOKEN:
        await start_webhook_server(port=8765, ngrok_token=NGROK_TOKEN)
        logging.info("☁️ CloudConvert webhook server started")

logging.info("⚡ Video Studio AI started.")
get_event_loop().run_until_complete(_boot())
colab_bot.run()
```

### Step 5 — Patch `main.py` (Colab notebook)

**Add params at the top:**

```python
NGROK_TOKEN = ""        # @param {type: "string"}
CC_WEBHOOK_SECRET = ""  # @param {type: "string"}
```

**Add to pip install:**

```python
subprocess.run("pip3 install -r /content/zilong/requirements.txt pyngrok", shell=True)
```

**Add to credentials dict:**

```python
credentials = {
    "API_ID":            API_ID,
    "API_HASH":          API_HASH,
    "BOT_TOKEN":         BOT_TOKEN,
    "USER_ID":           USER_ID,
    "DUMP_ID":           DUMP_ID,
    "NGROK_TOKEN":       NGROK_TOKEN,          # ← NEW
    "CC_WEBHOOK_SECRET": CC_WEBHOOK_SECRET,     # ← NEW
}
```

### Step 6 — Configure CloudConvert

1. Start the bot on Colab — it will send you a message with the webhook URL
2. Go to **cloudconvert.com → Dashboard → API → Webhooks**
3. Click **Create Webhook**
4. Paste the ngrok URL: `https://xxxx.ngrok-free.app/webhook/cloudconvert`
5. Select event: **job.finished**
6. (Optional) Copy the **Signing Secret** → paste as `CC_WEBHOOK_SECRET` in Colab
7. Save

### Done!

Every time a CloudConvert job finishes, the download link is automatically
sent to your bot, downloaded, and uploaded to your Telegram chat.

---

## Notes

- **ngrok URL changes** each time you restart Colab — update CloudConvert webhook URL accordingly
- For a **permanent URL**, upgrade ngrok to a paid plan ($8/mo) for a static domain, or deploy a small webhook relay on Koyeb/Railway
- The webhook secret is optional but recommended to prevent random POST requests from triggering downloads
- Works with all CloudConvert output types (video, audio, documents, etc.)
- Files go through your normal 3-slot queue system with retry logic
