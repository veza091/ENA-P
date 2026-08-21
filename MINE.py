import os
import re
import sys
import asyncio
import discord
import aiohttp
from collections import deque
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or "").strip()

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على DISCORD_TOKEN أو المفتاح في .env!")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# قائمة النماذج المجانية المتاحة مرتبة بحسب الأفضلية والسعة للتنقل الآلي
FREE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

# ذاكرة حفظ آخر 6 رسائل لكل قناة
chat_histories = {}

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
# 2. الاتصال بـ Gemini API مع الذاكرة والتبديل التلقائي
# ==========================================
async def ask_ena_brain(channel_id, prompt, user_name):
    # توجيهات واقعية تمنع تكرار اسم المدينة أو إقحام الهوية في الحوار العابر
    system_instruction = (
        "أنتِ إينا، بنت عمرك 18 سنة تسولفين في سيرفر ديسكورد بعفوية واقعية تامة كأي مستخدم عادي. "
        "ردي بأسلوب عامي بيض بسيط جداً، بدون إيموجيات، بدون رموز، وبدون أي تنسيقات. "
        "ردودك قصيرة، مختصرة، وبشرية جداً (كلمة إلى جملة واحدة فقط). "
        "قواعد الواقعية الصارمة:\n"
        "1. ممنوع كلياً ذكر اسم مدينتك (الدمام) أو إقحامها في السوالف العادية، اذكريه فقط إذا سُئلتِ مباشرة: من أي مدينة أنتِ؟\n"
        "2. ردي بأسلوب واقعي يطابق بنت عمرها 18 سنة (مثل: متابعة مسلسل، تصفح الجوال، طلب أكل، نوم، طفش عادي) وبدون حركات تصنع أو رسمية.\n"
        "3. اقرئي السياق وركزي على آخر رسالة فقط بأسلوب متناسق وبدون فلسفة.\n"
        "4. لا تختلقي قصصاً أو أعمالاً وهمية.\n"
        "5. ممنوع الاعتراف بأنك ذكاء اصطناعي أو بوت."
    )

    if channel_id not in chat_histories:
        chat_histories[channel_id] = deque(maxlen=6)
        
    history = list(chat_histories[channel_id])
    history_text = "\n".join(history) if history else "لا يوجد سياق سابق."

    full_prompt = (
        f"--- سياق المحادثة السابقة ---\n{history_text}\n\n"
        f"--- الرسالة الجديدة ---\nالمرسل ({user_name}): {prompt}"
    )

    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {"maxOutputTokens": 60, "temperature": 0.3, "topP": 0.8}
    }
    
    # تجربة النماذج المتاحة بالترتيب عند نفاذ الحد أو وجود ضغط (Fallback Mechanism)
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
                        reply = "".join(p.get('text', '') for p in parts).strip()
                        
                        # تنظيف النص
                        reply = re.sub(r'[A-Za-z0-9_.,،؛!؟:\-_•\*]', ' ', reply)
                        reply = re.sub(r'\s+', ' ', reply).strip()
                        
                        if reply:
                            # تحديث الذاكرة بالرسالة والرد
                            chat_histories[channel_id].append(f"{user_name}: {prompt}")
                            chat_histories[channel_id].append(f"إينا: {reply}")
                            return reply
                    elif resp.status in (429, 404, 503):
                        print(f"⚠️ النموذج {model} وصل للحد أو غير متاح ({resp.status})، جاري التبديل للنموذج التالي...")
                        continue
            except Exception as e:
                print(f"⚠️ خطأ أثناء الاتصال بالنموذج {model}: {e}")
                continue

    return "النت فصل عندي لحظة ورجع"

# ==========================================
# 3. أحداث ديسكورد والتشغيل
# ==========================================
@client.event
async def on_ready():
    print(f"✅ تم الاتصال: {client.user} | تم تعديل توجيهات الواقعية لمنع التكرار")

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
            reply = await ask_ena_brain(message.channel.id, clean_prompt, user_name)
            await message.reply(reply[:2000], mention_author=False)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
