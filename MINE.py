import os
import re
import sys
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or "").strip()

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على المتغيرات المطلوبة في .env!")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# أسماء نماذج Gemini الرسمية المعتمدة
FREE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<@!?\d+>', '', text)
    text = re.sub(r'[\*\-_•#@\$%\^&\(\)]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# 1. خادم الويب (لإبقاء Render نشطاً)
# ==========================================
async def handle_ping(request):
    return web.Response(text="ENA Bot is Online!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, '0.0.0.0', port).start()
    print(f"🌐 [Web Server] يعمل على المنفذ: {port}")

# ==========================================
# 2. الاتصال بـ Gemini API (بدون ذاكرة)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    try:
        clean_prompt = clean_text(prompt)
        if not clean_prompt:
            clean_prompt = "هلا"

        system_instruction = (
            "أنتِ إينا، بنت عمرك 18 سنة تسولفين في ديسكورد بعفوية واقعية كأي مستخدم عادي. "
            "ردي بأسلوب عامي بيض بسيط جداً، بدون إيموجيات، بدون رموز، وبدون أي تنسيقات. "
            "ردودك قصيرة، مختصرة، وبشرية جداً (كلمة إلى جملة واحدة فقط).\n"
            "قواعد الواقعية:\n"
            "1. ممنوع كلياً ذكر اسم مدينتك (الدمام) إلا إذا سُئلتِ مباشرة عنها.\n"
            "2. ردي بأسلوب واقعي يطابق بنت عمرها 18 سنة (مثل: طفش، تصفح الجوال، طلب أكل) وبدون حركات تصنع.\n"
            "3. ركزي على الرسالة الأخيرة والسياق المباشر بأسلوب بسيط.\n"
            "4. لا تختلقي قصصاً وهمية وممنوع كلياً الاعتراف بأتك ذكاء اصطناعي أو بوت."
        )

        contents = [{"role": "user", "parts": [{"text": f"[رسالة من {user_name}]: {clean_prompt}"}]}]

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 40,
                "temperature": 0.75,
                "topP": 0.9
            }
        }
        
        reply_text = None

        async with aiohttp.ClientSession() as session:
            for model in FREE_MODELS:
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                try:
                    async with session.post(gemini_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get('candidates', [])
                            if not candidates:
                                continue
                                
                            parts = candidates[0].get('content', {}).get('parts', [])
                            reply = "".join(p.get('text', '') for p in parts)
                            reply = clean_text(reply)
                            
                            if reply:
                                reply_text = reply
                                break
                        else:
                            err_body = await resp.text()
                            print(f"⚠️ النموذج {model} رد بالحالة {resp.status}: {err_body}")
                except Exception as e:
                    print(f"⚠️ خطأ أثناء الاتصال بالنموذج {model}: {e}")
                    continue

        return reply_text or "هلا"

    except Exception as e:
        print(f"❌ خطأ غير متوقع في ask_ena_brain: {e}")
        return "هلا"

# ==========================================
# 3. أحداث ديسكورد
# ==========================================
@client.event
async def on_ready():
    print(f"✅ تم الاتصال بنجاح: {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    if client.user in message.mentions or is_dm:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip() or "هلا"
        user_name = message.author.display_name

        print(f"\n📥 [رسالة من {user_name}]: {clean_prompt}")

        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name)
            await message.reply(reply, mention_author=False)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
