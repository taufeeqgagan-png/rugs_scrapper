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
    Multi-strategy scraper for rugs.fun round history.
    Tries class-based selectors first, then falls back to
    scanning all elements for multiplier + duration text patterns.
    """
    return await page.evaluate("""() => {

        // ── Strategy 1: known / guessed class patterns ──────────────────────
        const candidates = [
            '[class*="historyRow"]',
            '[class*="HistoryRow"]',
            '[class*="history-row"]',
            '[class*="roundItem"]',
            '[class*="RoundItem"]',
            '[class*="round-item"]',
            '[class*="history-item"]',
            '[class*="HistoryItem"]',
            '[class*="gameRow"]',
            '[class*="GameRow"]',
            '[class*="crashItem"]',
            '[class*="CrashItem"]',
            '[data-round-id]',
            '[data-id]',
        ];

        let row = null;
        let matchedSelector = null;

        for (const sel of candidates) {
            const el = document.querySelector(sel);
            if (el) {
                row = el;
                matchedSelector = sel;
                break;
            }
        }

        // ── Strategy 2: DOM text scan ─────────────────────────────────────
        // Walk every element looking for one that contains BOTH
        // a multiplier (e.g. "1234.56x") and a duration (e.g. "45s")
        // but isn't enormous (likely a wrapper).
        if (!row) {
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const childCount = el.children.length;
                if (childCount > 6) continue;          // skip large containers
                const t = (el.textContent || '').trim();
                if (t.length > 300) continue;          // skip huge text blobs
                const hasMult = /\d+\.?\d*x/.test(t);
                const hasDur  = /\d+\s*s\b/.test(t);
                if (hasMult && hasDur) {
                    row = el;
                    matchedSelector = 'fallback-text-match';
                    break;
                }
            }
        }

        // ── Strategy 3: find multiplier span + nearby duration ────────────
        // Some sites render the multiplier and duration in sibling spans.
        if (!row) {
            const spans = document.querySelectorAll('span, div, td, li');
            for (const el of spans) {
                const t = (el.textContent || '').trim();
                if (/^\d+\.?\d*x$/.test(t)) {
                    // found a pure "1234.56x" element — grab its parent
                    const parent = el.parentElement;
                    if (parent) {
                        const pt = parent.textContent || '';
                        if (/\d+\s*s\b/.test(pt)) {
                            row = parent;
                            matchedSelector = 'sibling-span-match';
                            break;
                        }
                    }
                }
            }
        }

        if (!row) {
            // Log the full page text snippet to help with debugging
            const bodySnippet = (document.body && document.body.innerText || '').slice(0, 500);
            return { debug: 'NO_ROW_FOUND', id: null, mult: 0, dur: 0, bodySnippet };
        }

        const text = row.textContent || '';
        const multMatch = text.match(/(\d+\.?\d*)x/);
        const timeMatch = text.match(/(\d+)\s*s\b/);

        // Build a stable ID from data attributes or the text content itself
        const id =
            row.getAttribute('data-round-id') ||
            row.getAttribute('data-id') ||
            row.getAttribute('id') ||
            text.slice(0, 120).replace(/\s+/g, '');

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
                        # ❌ REMOVED --single-process  → caused "Target crashed" loops
                        "--disable-blink-features=AutomationControlled",
                        "--disable-extensions",
                        "--disable-software-rasterizer",
                        "--js-flags=--max-old-space-size=512",
                    ]
                )

                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    java_script_enabled=True,
                )
                page = await context.new_page()

                # Block heavy static assets we don't need
                await page.route(
                    "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
                    lambda r: r.abort()
                )

                print("🌐 Loading rugs.fun ...")
                await page.goto("https://rugs.fun/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(15)   # give the JS app time to render

                try:
                    await page.get_by_text("Standard", exact=True).first.click(timeout=10000)
                    print("✅ Switched to MAIN OG Standard chart")
                except Exception:
                    print("✅ Already on main chart (or button not found)")

                print("🤖 Watching the main OG chart 24/7 ...")
                retry_delay = 8   # reset back-off after a clean start

                while True:
                    try:
                        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Checking for new round ...")
                        await asyncio.sleep(4)

                        latest = await scrape_round(page)

                        # Always log so you can see what's happening in Railway logs
                        print(
                            f"  ↳ selector={latest.get('debug','?')} | "
                            f"mult={latest.get('mult',0)} | "
                            f"dur={latest.get('dur',0)}"
                        )

                        # If NO_ROW_FOUND, print a body snippet to help debug
                        if latest.get('debug') == 'NO_ROW_FOUND' and latest.get('bodySnippet'):
                            print(f"  ↳ PAGE SNIPPET: {latest['bodySnippet'][:300]}")

                        if latest and latest['mult'] > 0:
                            print(f"  ↳ Detected → {latest['mult']:.2f}x | {latest['dur']}s")

                            if latest['id'] != last_id:
                                print("✅ New round! Sending notifications ...")
                                mult = latest['mult']
                                dur  = latest['dur']
                                is_long  = dur >= 130
                                is_100x  = mult >= 100
                                is_insta = dur <= 5
                                total_rounds += 1
                                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                                await send_webhook(
                                    ALL_WEBHOOK_URL,
                                    "📊 ALL CHARTS",
                                    f"**Round ended** · #{total_rounds}\n"
                                    f"⏱ Duration: **{dur}s** | 💥 Multiplier: **{mult:.2f}x**\n"
                                    f"`{ts}`"
                                )

                                if is_long:
                                    await send_webhook(
                                        LONG_WEBHOOK_URL,
                                        "🐋 LONG ROUND!",
                                        f"Duration: **{dur}s** (≥130 s) | Multiplier: **{mult:.2f}x**"
                                    )
                                    rounds_since_long = 0
                                else:
                                    rounds_since_long += 1

                                if is_100x:
                                    await send_webhook(
                                        HUNDREDX_WEBHOOK_URL,
                                        "🚀 100x+ ROUND!",
                                        f"Multiplier: **{mult:.2f}x** | Duration: **{dur}s**"
                                    )
                                    rounds_since_100x = 0
                                else:
                                    rounds_since_100x += 1

                                if is_insta:
                                    await send_webhook(
                                        INSTA_WEBHOOK_URL,
                                        "💥 INSTA-RUG!",
                                        f"Duration: **{dur}s** (≤5 s) | Multiplier: **{mult:.2f}x**"
                                    )
                                    rounds_since_insta = 0
                                else:
                                    rounds_since_insta += 1

                                last_id = latest['id']
                            else:
                                print("  ↳ Same round as last check — waiting.")
                        else:
                            print("  ↳ No valid round data yet.")

                    except Exception as e:
                        err = str(e)
                        print(f"❌ Inner loop error: {e}")
                        if any(sig in err for sig in BROWSER_CRASH_ERRORS):
                            print("🚨 Browser/page gone — restarting browser ...")
                            break   # break inner loop → triggers full browser restart

                    await asyncio.sleep(CHECK_INTERVAL)

                # Clean up before restart
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
