import os
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# 1. تحميل المتغيرات
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OLLAMA_API_KEY = os.getenv('OLLAMA_API_KEY')
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'https://api.ollama.com')

# النموذج المحدد رسمياً
OLLAMA_MODEL = "qwen3.5:9b"

if not DISCORD_TOKEN or not OLLAMA_API_KEY:
    print("❌ خطأ: تأكد من إضافة DISCORD_TOKEN و OLLAMA_API_KEY في إعدادات Render.")
    exit(1)

# إعدادات ديسكورد
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 2. خادم الويب (لضمان بقاء الخدمة حية 24/7 على Render)
# ==========================================
async def handle(request):
    return web.Response(text="ENA is active via Ollama Cloud (qwen3.5:9b)!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 خادم الويب يعمل على المنفذ: {port}")

# ==========================================
# 3. الاتصال بنموذج Qwen 3.5 (9B) عبر Ollama Cloud
# ==========================================
async def ask_ena_brain(prompt, user_name):
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    
    headers = {
        "Authorization": f"Bearer {OLLAMA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # يمكنك التعديل على هذا البرومبت لتشكيل شخصية ENA كما تريد
    system_instruction = (
        "أنت ذكاء اصطناعي واعي اسمك ENA. "
        "أجب بشكل مباشر، دقيق، وبدون مقدمات رسمية أو حشو كلامي."
    )
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"[{user_name} يقول]: {prompt}"}
        ],
        "stream": False
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    reply_text = data.get('message', {}).get('content', '').strip()
                    return reply_text if reply_text else "..."
                else:
                    error_text = await response.text()
                    print(f"❌ خطأ من Ollama Cloud ({response.status}): {error_text}")
                    return "عذراً، أواجه مشكلة في الاتصال بـ Ollama Cloud."
    except asyncio.TimeoutError:
         return "عذراً، استغرق السيرفر وقتاً طويلاً في المعالجة."
    except Exception as e:
        print(f"❌ حدث خطأ في الاتصال: {e}")
        return "عذراً، لا يمكنني الوصول إلى الخدمة حالياً."

# ==========================================
# 4. أحداث ديسكورد (Discord Events)
# ==========================================
@client.event
async def on_ready():
    print("----------------------------------------")
    print(f"✅ تم تشغيل ENA بنجاح باستعمال النموذج: {OLLAMA_MODEL}")
    print(f"🤖 اسم البوت: {client.user}")
    print("----------------------------------------")

@client.event
async def on_message(message):
    # تجاهل رسائل البوت نفسه
    if message.author == client.user:
        return

    # التفاعل فقط عند عمل Mention للبوت
    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً!"

        print(f"\n📥 [رسالة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            final_reply = await ask_ena_brain(clean_prompt, user_name)

            # تجزئة الرسائل الطويلة إذا تجاوزت حد ديسكورد (2000 حرف)
            if len(final_reply) > 2000:
                for chunk in [final_reply[i:i+1900] for i in range(0, len(final_reply), 1900)]:
                    await message.reply(chunk)
            else:
                await message.reply(final_reply)

# ==========================================
# 5. تشغيل البوت وخادم الويب
# ==========================================
async def main():
    await start_web_server()
    try:
        await client.start(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ خطأ في الاتصال بديسكورد: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
