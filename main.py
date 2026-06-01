import asyncio
from collections import deque
from playwright.async_api import async_playwright
from discord_webhook import DiscordWebhook, DiscordEmbed
from datetime import datetime, timezone
import os

# Webhooks from Railway Environment Variables
ALL_WEBHOOK_URL = os.getenv("ALL_WEBHOOK_URL")
LONG_WEBHOOK_URL = os.getenv("LONG_WEBHOOK_URL")
HUNDREDX_WEBHOOK_URL = os.getenv("HUNDREDX_WEBHOOK_URL")
INSTA_WEBHOOK_URL = os.getenv("INSTA_WEBHOOK_URL")

CHECK_INTERVAL = 12

history = deque(maxlen=500)
last_id = None
total_rounds = 0
rounds_since_long = 0
rounds_since_100x = 0
rounds_since_insta = 0


# FIX #3: Run blocking webhook.execute() in a thread pool so it doesn't block the event loop
async def send_webhook(url, title, desc):
    if not url or url == "PASTE_..._HERE":
        return
    def _send():
        webhook = DiscordWebhook(url=url)
        embed = DiscordEmbed(title=title, description=desc, color=0x00ff00)
        embed.add_embed_field(name="Live Main Chart", value="https://rugs.fun/", inline=False)
        webhook.add_embed(embed)
        webhook.execute()
    try:
        await asyncio.get_event_loop().run_in_executor(None, _send)
        print(f"✅ Webhook sent: {title}")
    except Exception as e:
        print(f"Webhook error: {e}")


async def main():
    global last_id, total_rounds, rounds_since_long, rounds_since_100x, rounds_since_insta

    # FIX #5: Exponential backoff on crash instead of fixed 8s delay
    retry_delay = 8
    max_delay = 120

    while True:
        try:
            async with async_playwright() as p:
                # FIX #1: Added --disable-dev-shm-usage and other stability flags for Railway containers
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",   # KEY FIX: Railway /dev/shm is only 64MB by default
                        "--disable-gpu",
                        "--single-process",
                        "--disable-blink-features=AutomationControlled",
                    ]
                )
                page = await browser.new_page()
                await page.goto("https://rugs.fun/", wait_until="domcontentloaded")
                await asyncio.sleep(12)

                try:
                    await page.get_by_text("Standard", exact=True).first.click(timeout=10000)
                    print("✅ Switched to MAIN OG Standard chart")
                except:
                    print("✅ Already on main chart")

                print("🤖 Robot is now watching ONLY the main OG chart 24/7...")

                retry_delay = 8  # Reset backoff after a clean browser start

                while True:
                    try:
                        # FIX #4: Use timezone-aware datetime instead of deprecated utcnow()
                        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Checking for new round...")

                        await asyncio.sleep(4)

                        # FIX #2: Narrowed JS selector — targets likely round/history row classes only.
                        # ⚠️  If this still returns null, open rugs.fun in Chrome DevTools,
                        #     right-click a round row → Inspect, find its real CSS class,
                        #     and replace the selector string below.
                        latest = await page.evaluate("""() => {
                            const selectors = [
                                '.history-row',
                                '.round-item',
                                '[class*="historyRow"]',
                                '[class*="roundItem"]',
                                '[class*="history-item"]',
                                '[data-id]'
                            ];
                            let row = null;
                            for (const sel of selectors) {
                                row = document.querySelector(sel);
                                if (row) break;
                            }
                            if (!row) return null;

                            const text = row.textContent || '';
                            const multMatch = text.match(/(\\d+\\.?\\d*)x/);
                            const timeMatch = text.match(/(\\d+)s/);
                            const id = row.getAttribute('data-id') || row.getAttribute('id') || text.slice(0, 100).replace(/\\s+/g, '');

                            return {
                                id: id,
                                mult: parseFloat(multMatch ? multMatch[1] : 0),
                                dur: parseInt(timeMatch ? timeMatch[1] : 0),
                            };
                        }""")

                        if latest and latest['mult'] > 0:
                            print(f"Detected → Multiplier: {latest['mult']:.2f}x | Duration: {latest['dur']}s")

                            if latest['id'] != last_id:
                                print("✅ New round detected! Sending notifications...")
                                mult = latest['mult']
                                dur = latest['dur']
                                is_long = dur >= 130
                                is_100x = mult >= 100
                                is_insta = dur <= 5
                                total_rounds += 1
                                # FIX #4: timezone-aware datetime
                                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                                await send_webhook(ALL_WEBHOOK_URL, "📊 ALL CHARTS",
                                    f"**Round ended** · #{total_rounds}\n⏱ Duration: **{dur}s** | 💥 Multiplier: **{mult:.2f}x**\n`{ts}`")

                                if is_long:
                                    await send_webhook(LONG_WEBHOOK_URL, "🐋 LONG ROUND!", f"Duration: **{dur}s** (≥130 s) | Multiplier: **{mult:.2f}x**")
                                    rounds_since_long = 0
                                else:
                                    rounds_since_long += 1

                                if is_100x:
                                    await send_webhook(HUNDREDX_WEBHOOK_URL, "🚀 100x+ ROUND!", f"Multiplier: **{mult:.2f}x** | Duration: **{dur}s**")
                                    rounds_since_100x = 0
                                else:
                                    rounds_since_100x += 1

                                if is_insta:
                                    await send_webhook(INSTA_WEBHOOK_URL, "💥 INSTA-RUG!", f"Duration: **{dur}s** (≤5 s) | Multiplier: **{mult:.2f}x**")
                                    rounds_since_insta = 0
                                else:
                                    rounds_since_insta += 1

                                last_id = latest['id']
                            else:
                                print("No new round yet.")
                        else:
                            print("No valid round data found.")

                    except Exception as e:
                        print(f"Error in loop: {e}")
                        if "Target crashed" in str(e) or "page.evaluate" in str(e):
                            print("🚨 Browser crashed. Restarting...")
                            break  # Break inner loop to trigger browser restart

                    await asyncio.sleep(CHECK_INTERVAL)

        except Exception as outer_e:
            print(f"Browser crashed completely: {outer_e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)  # FIX #5: exponential backoff


if __name__ == "__main__":
    asyncio.run(main())
