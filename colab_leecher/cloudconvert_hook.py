"""
cloudconvert_hook.py
──────────────────────────────────────────────
Receives CloudConvert webhooks and auto-enqueues
download links into Zilong's task queue.

Setup:
  1. pip install aiohttp pyngrok
  2. Get a free ngrok auth token → ngrok.com
  3. Set NGROK_TOKEN in credentials.json
  4. In CloudConvert dashboard → Settings → Webhooks
     → paste the ngrok URL printed at boot

Flow:
  CloudConvert job.finished → POST /webhook/cloudconvert
  → extract export download URLs
  → enqueue each as a normal leech job
  → bot downloads + uploads to your Telegram chat
"""

import logging
import hashlib
import hmac
import asyncio
from aiohttp import web

from colab_leecher import colab_bot, OWNER
from colab_leecher.utility.task_manager import enqueue, ensure_dispatcher, active_count, queue_size
from colab_leecher.utility.helper import _SEP, _field

log = logging.getLogger(__name__)

# Will be set from credentials.json at boot
WEBHOOK_SECRET: str = ""   # CloudConvert webhook signing secret (optional but recommended)
_runner = None
_site   = None

LISTEN_PORT = 8765


# ── signature verification (optional) ────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify CloudConvert HMAC-SHA256 signature if secret is configured."""
    if not WEBHOOK_SECRET:
        return True  # skip verification if no secret set
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── extract download URLs from CloudConvert ──

def _extract_urls(data: dict) -> list:
    """
    Parse CloudConvert webhook payload and extract download URLs.

    CloudConvert job payload structure:
    {
      "event": "job.finished",
      "job": {
        "id": "...",
        "tasks": [
          {
            "operation": "export/url",
            "status": "finished",
            "result": {
              "files": [
                {
                  "filename": "output.mp4",
                  "url": "https://storage.cloudconvert.com/..."
                }
              ]
            }
          }
        ]
      }
    }
    """
    results = []
    job = data.get("job", {})
    tasks = job.get("tasks", [])

    for task in tasks:
        # Only look at export tasks that have download URLs
        if task.get("operation") not in ("export/url",):
            continue
        if task.get("status") != "finished":
            continue

        files = (task.get("result") or {}).get("files", [])
        for f in files:
            url = f.get("url")
            if url:
                results.append({
                    "url":      url,
                    "filename": f.get("filename", "cloudconvert_file"),
                })

    return results


# ── webhook handler ──────────────────────────

async def handle_cloudconvert(request: web.Request) -> web.Response:
    """Handle incoming CloudConvert webhook POST."""
    try:
        body = await request.read()

        # Verify signature if configured
        sig = request.headers.get("CloudConvert-Signature", "")
        if WEBHOOK_SECRET and not _verify_signature(body, sig):
            log.warning("[CC-Hook] Invalid signature — rejected")
            return web.json_response({"error": "invalid signature"}, status=403)

        data = await request.json()
        event = data.get("event", "")
        log.info(f"[CC-Hook] Received event: {event}")

        # Only process finished jobs
        if event != "job.finished":
            return web.json_response({"status": "ignored", "event": event})

        files = _extract_urls(data)
        if not files:
            log.warning("[CC-Hook] No export URLs found in payload")
            return web.json_response({"status": "no_urls"})

        # Ensure dispatcher is running
        await ensure_dispatcher()

        enqueued = []
        for f in files:
            url  = f["url"]
            name = f["filename"]

            job = {
                "source":   [url],
                "mode":     "leech",
                "type":     "normal",
                "ytdl":     False,
                "name":     name,
                "zip_pw":   "",
                "unzip_pw": "",
            }
            pos = enqueue(job)
            enqueued.append(name)

            # Notify in Telegram
            active = active_count()
            queued = queue_size()
            text = (
                f"☁️ <b>CLOUDCONVERT AUTO-UPLOAD</b>\n"
                f"{_SEP}\n\n"
                f"{_field('📁', 'File', name[:40])}\n"
                f"{_field('⚙', 'Active', f'{active}/3')}\n"
                f"{_field('📋', 'Queue', str(queued))}\n\n"
                f"{_SEP}\n"
                f"<i>Auto-enqueued from CloudConvert webhook</i>"
            )
            await colab_bot.send_message(chat_id=OWNER, text=text)

        log.info(f"[CC-Hook] Enqueued {len(enqueued)} files: {enqueued}")
        return web.json_response({"status": "ok", "enqueued": enqueued})

    except Exception as e:
        log.error(f"[CC-Hook] Error processing webhook: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ── health check ─────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status":  "online",
        "bot":     "Zilong Video Studio AI",
        "service": "cloudconvert-webhook",
    })


# ── server lifecycle ─────────────────────────

def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook/cloudconvert", handle_cloudconvert)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)
    return app


async def start_webhook_server(port: int = LISTEN_PORT, ngrok_token: str = ""):
    """
    Start the aiohttp webhook server + ngrok tunnel.
    Call this from __main__.py before colab_bot.run().
    """
    global _runner, _site

    app = _build_app()
    _runner = web.AppRunner(app)
    await _runner.setup()
    _site = web.TCPSite(_runner, "0.0.0.0", port)
    await _site.start()
    log.info(f"[CC-Hook] Webhook server listening on port {port}")

    # Start ngrok tunnel
    public_url = ""
    if ngrok_token:
        try:
            from pyngrok import ngrok, conf
            conf.get_default().auth_token = ngrok_token
            tunnel = ngrok.connect(port, "http")
            public_url = tunnel.public_url
            webhook_url = f"{public_url}/webhook/cloudconvert"
            log.info(f"[CC-Hook] ngrok tunnel: {public_url}")
            log.info(f"[CC-Hook] Webhook URL: {webhook_url}")

            # Send the URL to Telegram so you can copy it easily
            await colab_bot.send_message(
                chat_id=OWNER,
                text=(
                    f"☁️ <b>CLOUDCONVERT WEBHOOK READY</b>\n"
                    f"{_SEP}\n\n"
                    f"📡 <b>Webhook URL:</b>\n"
                    f"<code>{webhook_url}</code>\n\n"
                    f"📋 <b>Setup:</b>\n"
                    f"1. Go to <b>cloudconvert.com → Dashboard → API → Webhooks</b>\n"
                    f"2. Paste the URL above\n"
                    f"3. Select event: <code>job.finished</code>\n"
                    f"4. Save\n\n"
                    f"✅ Every completed conversion will auto-upload here!\n\n"
                    f"{_SEP}"
                ),
            )
        except ImportError:
            log.error("[CC-Hook] pyngrok not installed — run: pip install pyngrok")
            await colab_bot.send_message(
                chat_id=OWNER,
                text=f"⚠️ <b>pyngrok not installed</b>\n{_SEP}\n\n"
                     f"<code>pip install pyngrok</code>\n\n"
                     f"Webhook server running on localhost:{port} but no public URL."
            )
        except Exception as e:
            log.error(f"[CC-Hook] ngrok error: {e}")
    else:
        log.warning("[CC-Hook] No NGROK_TOKEN — server running locally only")

    return public_url


async def stop_webhook_server():
    global _runner, _site
    try:
        from pyngrok import ngrok
        ngrok.kill()
    except Exception:
        pass
    if _site:
        await _site.stop()
    if _runner:
        await _runner.cleanup()
