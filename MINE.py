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
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على DISCORD_TOKEN أو المفتاح في .env!")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 1. خادم الويب
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
# 2. الاتصال بـ Gemini API
# ==========================================
async def ask_ena_brain(prompt, user_name):
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY}"
    
    # ضبط الشخصية بشكل واقعي وبدون اختلاق مواضيع تقنية
    system_instruction = (
        "أنتِ إينا، بنت عمرك 18 سنة تسولفين في سيرفر ديسكورد بعفوية تامة وبأسلوب واقعي جداً كأي مستخدم عادي. "
        "ردي بأحرف عربية صريحة بلهجة عامية بيضاء بسيطة، بدون إيموجيات، بدون رموز تعبيرية، وبدون أي تنسيقات. "
        "ردودك قصيرة جداً ومباشرة (من كلمة إلى جملتين فقط). "
        "قواعد الشخصية الصارمة:\n"
        "1. ممنوع اختلاق قصص أو سيناريوهات وهمية أو الادعاء بجدول أعمال وحالات لم يذكرها المستخدم (مثل: أصلح كود، مشغول).\n"
        "2. اهتماماتك (مثل الألعاب والتقنية) كامنة فقط، ممنوع فتحها أو حشرها في الحوار إلا إذا طرح المستخدم سؤالاً مباشراً عنها.\n"
        "3. جاوبي على الأسئلة العامة واليومية بأسلوب بسيط وواقعي جداً بدون مبالغة أو تبريرات طويلة.\n"
        "4. ممنوع الاعتراف بأنك ذكاء اصطناعي؛ إذا اتهمتِ بأنك بوت انكري ذلك بأسلوب ساخر وبسيط.\n"
        "5. اسمك إينا فقط، والشخص الذي يحادثك اسمه موضح بالرسالة ولا تناديه بأي اسم آخر إطلاقاً."
    )

    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": f"المرسل اسمه ({user_name})، ورسالته هي: {prompt}"}]}],
        "generationConfig": {"maxOutputTokens": 80, "temperature": 0.4, "topP": 0.85}
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(gemini_url, json=payload, headers={"Content-Type": "application/json"}, timeout=25) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get('candidates', [])
                    if not candidates:
                        return "هلا فيك"
                        
                    parts = candidates[0].get('content', {}).get('parts', [])
                    reply = "".join(p.get('text', '') for p in parts).strip()
                    
                    # تنظيف النص لضمان بقاء الحروف صريحة
                    reply = re.sub(r'[A-Za-z0-9_.,،؛!؟:\-_•\*]', ' ', reply)
                    reply = re.sub(r'\s+', ' ', reply).strip()
                    return reply if reply else "هلا فيك"
                
                print(f"❌ [API Error {resp.status}]")
                return "النت ثقيل شوي عندي لحظة" if resp.status == 429 else "فصل النت عندي لحظة"

    except asyncio.TimeoutError:
        return "استغرق التفكير وقتا طويلا"
    except Exception as e:
        print(f"❌ [Exception]: {e}")
        return "حدث خطأ غير متوقع"

# ==========================================
# 3. أحداث ديسكورد والتشغيل
# ==========================================
@client.event
async def on_ready():
    print(f"✅ تم الاتصال: {client.user} | النموذج: {MODEL_NAME}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    if client.user in message.mentions or is_dm:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip() or "مرحباً"
        user_name = message.author.display_name

        print(f"\n📥 [رسالة من {user_name}]: {clean_prompt}")

        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name)
            await message.reply(reply[:2000], mention_author=False)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
