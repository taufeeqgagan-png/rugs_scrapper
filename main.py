import asyncio
import json
import re
from collections import deque
from playwright.async_api import async_playwright
from discord_webhook import DiscordWebhook, DiscordEmbed
from datetime import datetime, timezone
import os

# ── Webhooks from Railway Environment Variables ───────────────────────────────
ALL_WEBHOOK_URL      = os.getenv("ALL_WEBHOOK_URL")
LONG_WEBHOOK_URL     = os.getenv("LONG_WEBHOOK_URL")
HUNDREDX_WEBHOOK_URL = os.getenv("HUNDREDX_WEBHOOK_URL")
INSTA_WEBHOOK_URL    = os.getenv("INSTA_WEBHOOK_URL")

CHECK_INTERVAL = 10

history            = deque(maxlen=500)
last_id            = None
total_rounds       = 0
rounds_since_long  = 0
rounds_since_100x  = 0
rounds_since_insta = 0

ws_queue: asyncio.Queue = asyncio.Queue()

# Known noise events to silently skip
WS_NOISE_EVENTS = {
    "general:onlineCount", "newChatMessage", "maintenanceUpdate",
    "pinpointPartyEventUpdate", "serverTime", "sideBetEvent",
    "sidebetevent",
}

# Event names we've already logged (to avoid spam)
ws_seen_events: set = set()

BROWSER_CRASH_ERRORS = (
    "Target crashed",
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "Connection closed",
    "context or browser",
)


# ── Discord helper ─────────────────────────────────────────────────────────────
async def send_webhook(url, title, desc, color=0x00ff00):
    if not url:
        print(f"  ⚠️  Webhook URL not set for: {title}")
        return
    def _send():
        webhook = DiscordWebhook(url=url)
        embed   = DiscordEmbed(title=title, description=desc, color=color)
        embed.add_embed_field(name="Live Chart", value="https://rugs.fun/", inline=False)
        webhook.add_embed(embed)
        webhook.execute()
    try:
        await asyncio.get_event_loop().run_in_executor(None, _send)
        print(f"  ✅ Webhook sent: {title}")
    except Exception as e:
        print(f"  ❌ Webhook error: {e}")


# ── Process a completed round ─────────────────────────────────────────────────
async def process_round(mult: float, dur: int, round_id: str):
    global last_id, total_rounds, rounds_since_long, rounds_since_100x, rounds_since_insta

    if round_id == last_id:
        print(f"  ↳ Duplicate round {round_id!r} — skipping.")
        return

    last_id = round_id
    total_rounds += 1
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    is_long  = dur  >= 130
    is_100x  = mult >= 100
    is_insta = dur  <= 5

    flags = "".join([
        "  🐋 LONG"  if is_long  else "",
        "  🚀 100x"  if is_100x  else "",
        "  💥 INSTA" if is_insta else "",
    ])
    print(f"  🎯 NEW ROUND #{total_rounds} → {mult:.2f}x | {dur}s{flags}")

    await send_webhook(
        ALL_WEBHOOK_URL,
        "📊 Round Ended",
        f"**Round #{total_rounds}**\n"
        f"⏱ Duration: **{dur}s** · 💥 Multiplier: **{mult:.2f}x**\n"
        f"`{ts}`",
    )

    if is_long:
        await send_webhook(LONG_WEBHOOK_URL, "🐋 LONG ROUND!",
            f"Duration: **{dur}s** ≥ 130 s | Multiplier: **{mult:.2f}x**", 0x1d8fe1)
        rounds_since_long = 0
    else:
        rounds_since_long += 1

    if is_100x:
        await send_webhook(HUNDREDX_WEBHOOK_URL, "🚀 100x ROUND!",
            f"Multiplier: **{mult:.2f}x** | Duration: **{dur}s**", 0xffd700)
        rounds_since_100x = 0
    else:
        rounds_since_100x += 1

    if is_insta:
        await send_webhook(INSTA_WEBHOOK_URL, "💥 INSTA-RUG!",
            f"Duration: **{dur}s** ≤ 5 s | Multiplier: **{mult:.2f}x**", 0xff0000)
        rounds_since_insta = 0
    else:
        rounds_since_insta += 1


# ── Deep key search: find a value by many possible key names ──────────────────
def deep_get(d, *keys):
    """Search a nested dict for any of the given keys. Returns first match."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k.lower() in [x.lower() for x in keys]:
            return v
        if isinstance(v, dict):
            result = deep_get(v, *keys)
            if result is not None:
                return result
        if isinstance(v, list):
            for item in v:
                result = deep_get(item, *keys) if isinstance(item, dict) else None
                if result is not None:
                    return result
    return None


# ── WebSocket handler ──────────────────────────────────────────────────────────
def make_ws_handler():
    async def on_websocket(ws):
        print(f"  🔌 WebSocket opened: {ws.url}")

        async def on_frame(payload: str):
            global ws_seen_events
            raw = str(payload).strip()

            # ── Parse Socket.IO format: 42["eventName", {...}] ───────────
            # Socket.IO prepends "42" (message type) before the JSON array.
            # Strip any leading digits before the first "[" or "{".
            json_str = re.sub(r'^\d+', '', raw)
            if not json_str:
                return

            try:
                parsed = json.loads(json_str)
            except Exception:
                return

            # Socket.IO event: ["eventName", payload1, payload2, ...]
            if isinstance(parsed, list) and len(parsed) >= 1:
                event_name = str(parsed[0]).lower()
                payloads   = parsed[1:]
            elif isinstance(parsed, dict):
                event_name = "raw_object"
                payloads   = [parsed]
            else:
                return

            # ── Skip known noise ──────────────────────────────────────────
            if event_name in WS_NOISE_EVENTS:
                return

            # ── Log every NEW event name we see (helps identify round-end) 
            if event_name not in ws_seen_events:
                ws_seen_events.add(event_name)
                print(f"  🔍 NEW EVENT TYPE: {event_name!r}  sample={json_str[:200]}")

            # ── Only process events that look like round completions ───────
            # We check the event name AND the payload for round-end signals.
            name_is_round_end = any(k in event_name for k in [
                "crash", "round", "game", "bust", "end", "result",
                "finish", "complete", "history", "chart",
            ])

            for item in payloads:
                if not isinstance(item, dict):
                    continue

                raw_item = json.dumps(item).lower()

                # Extract multiplier
                mult = deep_get(item,
                    "multiplier", "mult", "crashPoint", "crash_point",
                    "finalMultiplier", "final_multiplier", "bustedAt",
                    "busted_at", "endMultiplier", "end_multiplier",
                    "crashedAt", "crashed_at",
                )

                # Extract duration (in seconds — rugs.fun may send ms, handle both)
                dur_raw = deep_get(item,
                    "duration", "dur", "elapsed", "roundDuration",
                    "round_duration", "elapsedTime", "elapsed_time",
                    "timeElapsed", "time_elapsed", "length",
                )

                # Extract round ID
                rid = str(deep_get(item,
                    "roundId", "round_id", "id", "gameId", "game_id",
                    "hash", "seed", "nonce",
                ) or "")

                if mult is None:
                    continue

                try:
                    mult_f = float(mult)
                except (ValueError, TypeError):
                    continue

                if mult_f <= 0:
                    continue

                # Convert duration — if > 10000 assume milliseconds
                dur_i = 0
                if dur_raw is not None:
                    try:
                        dur_f = float(dur_raw)
                        dur_i = int(dur_f / 1000) if dur_f > 10000 else int(dur_f)
                    except (ValueError, TypeError):
                        dur_i = 0

                # Only queue if event name suggests a round end
                if not name_is_round_end:
                    print(f"  ⚠️  Skipping {event_name!r} — has mult={mult_f} but not a round-end event")
                    continue

                unique_id = rid or f"{mult_f}_{dur_i}_{datetime.now().timestamp():.0f}"
                await ws_queue.put({"mult": mult_f, "dur": dur_i, "id": unique_id})
                print(f"  📡 WS captured: {mult_f:.2f}x / {dur_i}s  event={event_name!r}")

        ws.on("framereceived", lambda f: asyncio.ensure_future(on_frame(f["data"] if isinstance(f, dict) else str(f))))

    return on_websocket


# ── DOM fallback scraper ───────────────────────────────────────────────────────
async def scrape_dom(page):
    return await page.evaluate("""() => {
        const selectors = [
            '[class*="historyRow"]','[class*="HistoryRow"]','[class*="history-row"]',
            '[class*="roundItem"]','[class*="RoundItem"]','[class*="round-item"]',
            '[class*="history-item"]','[class*="HistoryItem"]',
            '[class*="gameRow"]','[class*="GameRow"]',
            '[class*="crashItem"]','[class*="CrashItem"]',
            '[class*="betRow"]','[class*="BetRow"]',
            '[data-round-id]','[data-id]',
        ];
        let row = null, matchedSelector = null;
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) { row = el; matchedSelector = sel; break; }
        }
        if (!row) {
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length > 8) continue;
                const t = (el.textContent || '').trim();
                if (t.length > 400) continue;
                if (/\d+\.?\d*x/.test(t) && /\d+\s*s\b/.test(t)) {
                    row = el; matchedSelector = 'text-scan'; break;
                }
            }
        }
        if (!row) {
            for (const el of document.querySelectorAll('span,div,td,li,p')) {
                const t = (el.textContent || '').trim();
                if (/^\d+\.?\d*x$/.test(t)) {
                    const p = el.parentElement;
                    if (p && /\d+\s*s\b/.test(p.textContent || '')) {
                        row = p; matchedSelector = 'sibling-span'; break;
                    }
                }
            }
        }
        if (!row) {
            const snippet = (document.body && document.body.innerText || '').slice(0, 500);
            return { debug:'NO_ROW_FOUND', id:null, mult:0, dur:0, bodySnippet: snippet };
        }
        const text = row.textContent || '';
        const multMatch = text.match(/(\d+\.?\d*)x/);
        const timeMatch = text.match(/(\d+)\s*s\b/);
        const id = row.getAttribute('data-round-id') || row.getAttribute('data-id') ||
                   row.getAttribute('id') || text.slice(0, 120).replace(/\s+/g,'');
        return {
            debug: matchedSelector,
            id:   id,
            mult: parseFloat(multMatch ? multMatch[1] : 0),
            dur:  parseInt(timeMatch   ? timeMatch[1]  : 0),
        };
    }""")


# ── Main loop ──────────────────────────────────────────────────────────────────
async def main():
    retry_delay = 8
    max_delay   = 120

    # Validate env vars at startup so you see immediately if something's missing
    print("=" * 55)
    print("  rugs.fun Discord Alert Bot — starting up")
    print("=" * 55)
    for name, val in [
        ("ALL_WEBHOOK_URL",      ALL_WEBHOOK_URL),
        ("LONG_WEBHOOK_URL",     LONG_WEBHOOK_URL),
        ("HUNDREDX_WEBHOOK_URL", HUNDREDX_WEBHOOK_URL),
        ("INSTA_WEBHOOK_URL",    INSTA_WEBHOOK_URL),
    ]:
        status = "✅ set" if val else "❌ NOT SET — notifications won't send!"
        print(f"  {name}: {status}")
    print("=" * 55)

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
                        "--disable-blink-features=AutomationControlled",
                        "--disable-extensions",
                        "--disable-software-rasterizer",
                        "--js-flags=--max-old-space-size=512",
                    ],
                )

                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    java_script_enabled=True,
                )
                page = await context.new_page()

                # ✅ Do NOT block fonts — rugs.fun gates rendering on font load.
                #    Only block images/video we truly don't need.
                await page.route(
                    "**/*.{png,jpg,jpeg,gif,webp,svg,mp4,mp3,ogg,wav}",
                    lambda r: r.abort()
                )

                # ✅ Attach WS listener BEFORE navigating so we don't miss
                #    the first frames that fire right after page load.
                page.on("websocket", make_ws_handler())

                print("🌐 Loading rugs.fun ...")
                await page.goto("https://rugs.fun/", wait_until="domcontentloaded", timeout=45000)

                # ✅ Wait until the actual app is rendered (not just DOM loaded)
                print("⏳ Waiting for app to finish rendering ...")
                try:
                    await page.wait_for_function(
                        """() => {
                            const t = document.body && document.body.innerText || '';
                            return t.length > 100 &&
                                   !t.includes('Installing fonts') &&
                                   !t.includes('Loading...');
                        }""",
                        timeout=60000,
                    )
                    print("✅ App rendered!")
                except Exception:
                    print("⚠️  Render wait timed out — proceeding anyway.")

                await asyncio.sleep(8)   # let WS connect and initial history arrive

                try:
                    await page.get_by_text("Standard", exact=True).first.click(timeout=8000)
                    print("✅ Clicked Standard chart tab")
                    await asyncio.sleep(3)
                except Exception:
                    print("ℹ️  Standard tab not found — already on main chart")

                print("\n🤖 Watching 24/7 — waiting for rounds ...\n")
                retry_delay = 8

                while True:
                    try:
                        now = datetime.now(timezone.utc).strftime('%H:%M:%S')
                        print(f"[{now}] Checking ...")

                        # Priority 1 — drain WebSocket queue
                        ws_hit = False
                        while not ws_queue.empty():
                            item = await ws_queue.get()
                            ws_hit = True
                            await process_round(item["mult"], item["dur"], item["id"])

                        if ws_hit:
                            await asyncio.sleep(CHECK_INTERVAL)
                            continue

                        # Priority 2 — DOM fallback
                        latest = await scrape_dom(page)
                        debug  = latest.get("debug", "?")
                        mult   = latest.get("mult", 0)
                        dur    = latest.get("dur", 0)

                        print(f"  ↳ selector={debug} | mult={mult} | dur={dur}")

                        if debug == "NO_ROW_FOUND":
                            print(f"  ↳ PAGE: {latest.get('bodySnippet','')[:250]}")
                            print("  ↳ No DOM data yet — retrying.")
                        elif mult > 0:
                            rid = latest.get("id") or f"{mult}_{dur}"
                            await process_round(mult, dur, str(rid))
                        else:
                            print("  ↳ Row found but mult=0 — round still live.")

                    except Exception as e:
                        err = str(e)
                        print(f"❌ Inner loop error: {e}")
                        if any(sig in err for sig in BROWSER_CRASH_ERRORS):
                            print("🚨 Browser gone — restarting ...")
                            break

                    await asyncio.sleep(CHECK_INTERVAL)

                try:
                    await browser.close()
                except Exception:
                    pass

        except Exception as outer_e:
            print(f"💥 Outer crash: {outer_e}. Retrying in {retry_delay}s ...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


if __name__ == "__main__":
    asyncio.run(main())
