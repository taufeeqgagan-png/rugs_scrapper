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

BROWSER_CRASH_ERRORS = (
    "Target crashed",
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "Connection closed",
    "page.evaluate",
    "context or browser",
)


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


async def scrape_round(page):
    """
    Tries multiple selector strategies to find the latest round row.
    Logs what it finds so you can debug the real DOM class on rugs.fun.
    """
    return await page.evaluate("""() => {
        // Strategy 1: common round/history class patterns
        const candidates = [
            '.history-row',
            '.round-item',
            '[class*="historyRow"]',
            '[class*="roundItem"]',
            '[class*="history-item"]',
            '[class*="round-row"]',
            '[data-id]',
        ];

        let row = null;
        let matchedSelector = null;
        for (const sel of candidates) {
            const el = document.querySelector(sel);
            if (el) { row = el; matchedSelector = sel; break; }
        }

        // Strategy 2: fallback — find any element whose text looks like "34582.00x"
        if (!row) {
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.children.length === 0 || el.children.length <= 3) {
                    const t = el.textContent || '';
                    if (/\\d+\\.?\\d*x/.test(t) && /\\d+s/.test(t) && t.length < 200) {
                        row = el;
                        matchedSelector = 'fallback-text-match';
                        break;
                    }
                }
            }
        }

        if (!row) return { debug: 'NO_ROW_FOUND', id: null, mult: 0, dur: 0 };

        const text = row.textContent || '';
        const multMatch = text.match(/(\\d+\\.?\\d*)x/);
        const timeMatch = text.match(/(\\d+)s/);
        const id = row.getAttribute('data-id') || row.getAttribute('id') || text.slice(0, 100).replace(/\\s+/g, '');

        return {
            debug: matchedSelector,
            id: id,
            mult: parseFloat(multMatch ? multMatch[1] : 0),
            dur: parseInt(timeMatch ? timeMatch[1] : 0),
        };
    }""")


async def main():
    global last_id, total_rounds, rounds_since_long, rounds_since_100x, rounds_since_insta

    retry_delay = 8
    max_delay = 120

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
                        "--single-process",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-extensions",
                        "--disable-software-rasterizer",
                        "--js-flags=--max-old-space-size=256",  # cap JS heap to 256MB
                    ]
                )

                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    java_script_enabled=True,
                )
                page = await context.new_page()

                # Kill any resource-heavy stuff we don't need
                await page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}", lambda r: r.abort())

                await page.goto("https://rugs.fun/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(12)

                try:
                    await page.get_by_text("Standard", exact=True).first.click(timeout=10000)
                    print("✅ Switched to MAIN OG Standard chart")
                except:
                    print("✅ Already on main chart")

                print("🤖 Robot is now watching ONLY the main OG chart 24/7...")
                retry_delay = 8  # reset backoff after clean start

                while True:
                    try:
                        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Checking for new round...")

                        await asyncio.sleep(4)

                        latest = await scrape_round(page)

                        # Always log the debug selector so you can see what's matching
                        print(f"  ↳ Selector used: {latest.get('debug', '?')} | mult={latest.get('mult', 0)} dur={latest.get('dur', 0)}")

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
                        err = str(e)
                        print(f"Error in loop: {e}")
                        # FIX: catch ALL browser-gone errors, not just "Target crashed"
                        if any(sig in err for sig in BROWSER_CRASH_ERRORS):
                            print("🚨 Browser/page gone. Restarting...")
                            break  # break inner → triggers browser restart

                    await asyncio.sleep(CHECK_INTERVAL)

        except Exception as outer_e:
            print(f"Outer crash: {outer_e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


if __name__ == "__main__":
    asyncio.run(main())
