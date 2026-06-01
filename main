import asyncio
from collections import deque
from playwright.async_api import async_playwright
from discord_webhook import DiscordWebhook, DiscordEmbed
from datetime import datetime

# ←←← PASTE YOUR 4 WEBHOOK URLS HERE (one per line)
ALL_WEBHOOK_URL = "https://discord.com/api/webhooks/1508178745985601689/Z2BRQd8MyDUSeE8G29eGfZlK8iX_ztAPQP6YywWkUM23vC6MnTKvswKvYHOQAwlG1O6f"
LONG_WEBHOOK_URL = "https://discord.com/api/webhooks/1508074485629321246/hMR3IhGqcOkHnrLUroFJcikMOCMK9RjbApmfToDDCzy2jX-cYL5qc795OuVMybkmaTfn"
HUNDREDX_WEBHOOK_URL = "https://discord.com/api/webhooks/1508178898972577940/RetnNzKSrGeZnREb3Jxj_BQFrdYXxvhTRGm5aDvp7uYnxv44d2zIYLo8Fn60gNKR1LCW"
INSTA_WEBHOOK_URL = "https://discord.com/api/webhooks/1508179356697231600/4V0TusNcBpdHsdg5Y-16jXLvmEkQNL7ui4hq0y27tlG4cXqlV0ltW-7uEl-EA-7eVt4y"

CHECK_INTERVAL = 12

history = deque(maxlen=500)
last_id = None
total_rounds = 0
rounds_since_long = 0
rounds_since_100x = 0
rounds_since_insta = 0

async def send_webhook(url, title, desc):
if not url or url == "PASTE_..._HERE":
return
webhook = DiscordWebhook(url=url)
embed = DiscordEmbed(title=title, description=desc, color=0x00ff00)
embed.add_embed_field(name="Live Main Chart", value="https://rugs.fun/", inline=False)
webhook.add_embed(embed)
webhook.execute()

async def main():
global last_id, total_rounds, rounds_since_long, rounds_since_100x, rounds_since_insta
async with async_playwright() as p:
browser = await p.chromium.launch(headless=True)
page = await browser.new_page()
await page.goto("https://rugs.fun/")
await asyncio.sleep(5)

try:
await page.get_by_text("Standard", exact=True).first.click(timeout=10000)
print("✅ Switched to MAIN OG Standard chart")
except:
print("✅ Already on main chart")

print("🤖 Robot is now watching ONLY the main OG chart 24/7...")

while True:
try:
await page.wait_for_selector("text=Last 100", timeout=12000)

latest = await page.evaluate("""() => {
const rows = document.querySelectorAll('.history-row, .round-item, tr, [class*="round"], [class*="chart"]');
if (rows.length === 0) return null;
const row = rows[0];
const text = row.textContent || '';
const multMatch = text.match(/(\\d+\\.?\\d*)x/);
const timeMatch = text.match(/(\\d+)s/);
const id = row.getAttribute('data-id') || text.slice(0,40);
return {
id: id,
mult: parseFloat(multMatch ? multMatch[1] : 0),
dur: parseInt(timeMatch ? timeMatch[1] : 0)
};
}""")

if latest and latest['id'] != last_id and latest['mult'] > 0:
mult = latest['mult']
dur = latest['dur']
is_long = dur >= 130
is_100x = mult >= 100
is_insta = dur <= 5

total_rounds += 1

ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# 1. ALL CHARTS - every round
await send_webhook(ALL_WEBHOOK_URL, "📊 ALL CHARTS",
f"**Round ended** · #{total_rounds}\n⏱ Duration: **{dur}s** | 💥 Multiplier: **{mult:.2f}x**\n`{ts}`")

# 2. LONG ROUND
if is_long:
await send_webhook(LONG_WEBHOOK_URL, "🐋 LONG ROUND!",
f"Duration: **{dur}s** (≥130 s) | Multiplier: **{mult:.2f}x**\nLong round came after **{rounds_since_long}** rounds since the last one")
rounds_since_long = 0
else:
rounds_since_long += 1

# 3. 100x+ ROUND
if is_100x:
await send_webhook(HUNDREDX_WEBHOOK_URL, "🚀 100x+ ROUND!",
f"Multiplier: **{mult:.2f}x** | Duration: **{dur}s**\n100x+ came after **{rounds_since_100x}** rounds since the last one")
rounds_since_100x = 0
else:
rounds_since_100x += 1

# 4. INSTA-RUG
if is_insta:
await send_webhook(INSTA_WEBHOOK_URL, "💥 INSTA-RUG!",
f"Duration: **{dur}s** (≤5 s) | Multiplier: **{mult:.2f}x**\nInsta-rug came after **{rounds_since_insta}** rounds since the last one")
rounds_since_insta = 0
else:
rounds_since_insta += 1

last_id = latest['id']

except:
pass

await asyncio.sleep(CHECK_INTERVAL)

asyncio.run(main())
