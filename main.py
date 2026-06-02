import asyncio
import json
import re
import os
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from discord_webhook import DiscordWebhook, DiscordEmbed

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RUGS_URL = "https://rugs.fun"
HEARTBEAT_S = 30

ALL_WEBHOOK_URL      = os.getenv("ALL_WEBHOOK_URL")
LONG_WEBHOOK_URL     = os.getenv("LONG_WEBHOOK_URL")
HUNDREDX_WEBHOOK_URL = os.getenv("HUNDREDX_WEBHOOK_URL")
INSTA_WEBHOOK_URL    = os.getenv("INSTA_WEBHOOK_URL")

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

total_rounds       = 0
rounds_since_long  = 0
rounds_since_100x  = 0
rounds_since_insta = 0

# ---------------------------------------------------------------------------
# Round state
# ---------------------------------------------------------------------------

phase           = "prep"          # "prep" | "round"
round_start_ms  = None
peak_multiplier = 1.0
last_game_id    = None
processed_ids   = set()

def reset_round_state():
    global phase, round_start_ms, peak_multiplier
    phase           = "prep"
    round_start_ms  = None
    peak_multiplier = 1.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = round(seconds % 60)
    return f"{m}m {s}s" if m > 0 else f"{s}s"

def fmt_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def now_ms() -> float:
    return datetime.now(timezone.utc).timestamp() * 1000

# ---------------------------------------------------------------------------
# Discord webhook
# ---------------------------------------------------------------------------

async def send_webhook(url: str | None, title: str, desc: str):
    if not url:
        return
    def _send():
        wh = DiscordWebhook(url=url)
        embed = DiscordEmbed(title=title, description=desc, color=0x00FF00)
        embed.add_embed_field(name="Live Chart", value=RUGS_URL, inline=False)
        wh.add_embed(embed)
        wh.execute()
    try:
        await asyncio.get_event_loop().run_in_executor(None, _send)
        print(f"✅ Webhook sent: {title}")
    except Exception as e:
        print(f"❌ Webhook error: {e}")

# ---------------------------------------------------------------------------
# Round end handler
# ---------------------------------------------------------------------------

async def on_round_end(mult: float, dur_s: float):
    global total_rounds, rounds_since_long, rounds_since_100x, rounds_since_insta

    total_rounds += 1
    dur    = fmt_duration(dur_s)
    ts     = fmt_ts()
    is_long  = dur_s >= 130
    is_100x  = mult >= 100
    is_insta = dur_s <= 5

    print(f"🏁 Round #{total_rounds} ended | {mult:.2f}x | {dur}")

    await send_webhook(
        ALL_WEBHOOK_URL,
        "📊 Round Ended",
        f"**Round ended** · #{total_rounds}\n"
        f"⏱ Duration: **{dur}**  |  💥 Multiplier: **{mult:.2f}x**\n"
        f"`{ts}`"
    )

    if is_long:
        await send_webhook(
            LONG_WEBHOOK_URL,
            "🐋 LONG ROUND!",
            f"Duration: **{dur}** (≥130 s)  |  Multiplier: **{mult:.2f}x**\n"
            f"Long round came after **{rounds_since_long}** rounds since the last one\n"
            f"`{ts}`"
        )
        rounds_since_long = 0
    else:
        rounds_since_long += 1

    if is_100x:
        await send_webhook(
            HUNDREDX_WEBHOOK_URL,
            "🚀 100x+ ROUND!",
            f"Multiplier: **{mult:.2f}x**  |  Duration: **{dur}**\n"
            f"100x+ came after **{rounds_since_100x}** rounds since the last one\n"
            f"`{ts}`"
        )
        rounds_since_100x = 0
    else:
        rounds_since_100x += 1

    if is_insta:
        await send_webhook(
            INSTA_WEBHOOK_URL,
            "💥 INSTA-RUG!",
            f"Duration: **{dur}** (≤5 s)  |  Multiplier: **{mult:.2f}x**\n"
            f"Insta-rug came after **{rounds_since_insta}** rounds since the last one\n"
            f"`{ts}`"
        )
        rounds_since_insta = 0
    else:
        rounds_since_insta += 1

# ---------------------------------------------------------------------------
# socket.io v4 frame parser  (same logic as the Node version)
# ---------------------------------------------------------------------------

def parse_socketio_frame(raw: str):
    """
    socket.io v4 message frames look like:
      42["event:name", {...}]
      42/namespace,["event:name", {...}]
    Returns (event_name, data_dict) or None.
    """
    if not isinstance(raw, str):
        return None
    m = re.match(r'^42(?:/[^,]*,)?\d*(\[.*)', raw, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(arr, list) or len(arr) < 1:
        return None
    event_name = arr[0]
    if not isinstance(event_name, str):
        return None
    data = arr[-1] if len(arr) > 1 else {}
    if not isinstance(data, dict):
        return None
    return event_name, data

# ---------------------------------------------------------------------------
# Game event handler  — ONLY standard OG chart events
# ---------------------------------------------------------------------------

def handle_game_event(event_name: str, data: dict):
    global phase, round_start_ms, peak_multiplier, last_game_id, processed_ids

    # ── round started ──────────────────────────────────────────────────────
    if event_name == "game:standard:phase":
        p       = data.get("phase")
        game_id = data.get("gameId")

        if p == "round":
            phase          = "round"
            round_start_ms = now_ms()
            peak_multiplier = 1.0
            if game_id:
                last_game_id = game_id
            print(f"🟢 Round started (gameId={game_id})")

        elif p == "crash":
            if game_id and game_id in processed_ids:
                return  # deduplicate

            prices   = data.get("prices") or []
            peak     = max(prices) if prices else peak_multiplier
            dur_s    = (now_ms() - round_start_ms) / 1000 if round_start_ms else 0

            if game_id:
                processed_ids.add(game_id)
                if len(processed_ids) > 500:
                    processed_ids.discard(next(iter(processed_ids)))
                last_game_id = game_id

            print(f"🔴 Round crashed | peak={peak:.2f}x | dur={dur_s:.1f}s")
            reset_round_state()
            asyncio.create_task(on_round_end(peak, dur_s))

        else:
            reset_round_state()

    # ── live tick — track peak multiplier while round is live ─────────────
    elif event_name == "game:standard:tick":
        prices = data.get("p") or []
        if prices:
            current = prices[-1]
            if isinstance(current, (int, float)) and current > peak_multiplier:
                peak_multiplier = current

# ---------------------------------------------------------------------------
# CDP WebSocket interception  (mirrors the Node.js CDP approach exactly)
# ---------------------------------------------------------------------------

async def attach_ws_interceptor(page):
    client = await page.context.new_cdp_session(page)
    await client.send("Network.enable")

    def on_ws_frame(params):
        payload = params.get("response", {}).get("payloadData", "")
        parsed = parse_socketio_frame(payload)
        if not parsed:
            return
        event_name, data = parsed
        if not event_name.startswith("game:standard:"):
            return                          # ignore everything else
        handle_game_event(event_name, data)

    client.on("Network.webSocketFrameReceived", on_ws_frame)
    print("📡 CDP WebSocket interceptor attached")

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main():
    retry_delay = 8
    max_delay   = 120

    while True:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-software-rasterizer",
                        "--disable-extensions",
                        "--disable-background-timer-throttling",
                        "--no-first-run",
                        "--no-zygote",
                        "--single-process",
                        "--mute-audio",
                        "--disable-blink-features=AutomationControlled",
                    ]
                )

                page = await browser.new_page()
                await page.set_viewport_size({"width": 1280, "height": 800})

                # Attach CDP interceptor BEFORE navigating
                await attach_ws_interceptor(page)

                print(f"🌐 Navigating to {RUGS_URL} ...")
                await page.goto(RUGS_URL, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(5)
                print("🤖 Monitoring ONLY the main OG standard chart via WebSocket...")

                retry_delay = 8  # reset backoff after clean start

                # Heartbeat — just keeps the process alive and logs status
                while True:
                    await asyncio.sleep(HEARTBEAT_S)
                    print(
                        f"💓 Alive | phase={phase} | total={total_rounds} | "
                        f"peak={peak_multiplier:.2f}x | lastId={last_game_id}"
                    )
                    # If the page died, break out to restart
                    if page.is_closed():
                        print("⚠️  Page closed — restarting browser...")
                        break

        except Exception as e:
            print(f"💥 Crash: {e} — retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


if __name__ == "__main__":
    asyncio.run(main())
