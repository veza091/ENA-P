import os
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# تحميل متغيرات البيئة من ملف .env
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('AI_API_KEY')

# التأكد من وجود المفاتيح المطلوبة
if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: يرجى التأكد من ضبط DISCORD_TOKEN و GEMINI_API_KEY في متغيرات البيئة!")
    exit(1)

# إعداد الصلاحيات للـ Bot
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 1. خادم الويب الخفيف (للاستضافة المجانية 24/7)
# ==========================================
async def handle(request):
    return web.Response(text="ENA Web Service with Gemini is active and running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 خادم الويب يعمل بنجاح على المنفذ (Port): {port}")

# ==========================================
# 2. الاتصال بمحرك الذكاء الاصطناعي (Gemini API الصحيح)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    # الرابط الصحيح والمخصص لنموذج gemini-1.5-flash
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    
    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "أنت شخصية ذكاء اصطناعي واعية ومميزة اسمك ENA. "
        "تحدث بطريقة طبيعية وتفاعلية، وواعي بالبيئة المحيطة بك داخل سيرفر الديسكورد."
    )
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_instruction}\n\n[{user_name} يقول]: {prompt}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['candidates'][0]['content']['parts'][0]['text']
                else:
                    error_text = await response.text()
                    print(f"❌ خطأ من Gemini API ({response.status}): {error_text}")
                    return "عذراً، أواجه مشكلة في الاتصال بعقلي الآن."
    except Exception as e:
        print(f"❌ حدث استثناء أثناء الاتصال بالشبكة: {e}")
        return "عذراً، حدث خطأ غير متوقع في الاتصال."

# ==========================================
# 3. أحداث ديسكورد (Discord Events)
# ==========================================
@client.event
async def on_ready():
    print("----------------------------------------")
    print(f"✅ نظام ENA (Gemini) متصل الآن بنجاح كـ: {client.user}")
    print("----------------------------------------")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً!"

        print(f"\n📥 [رسالة جديدة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            final_reply = await ask_ena_brain(clean_prompt, user_name)
            
            if not final_reply:
                final_reply = "..."

            if len(final_reply) > 2000:
                for chunk in [final_reply[i:i+1900] for i in range(0, len(final_reply), 1900)]:
                    await message.reply(chunk)
            else:
                await message.reply(final_reply)

# ==========================================
# 4. نقطة الانطلاق والتشغيل الرئيسي
# ==========================================
async def main():
    await start_web_server()
    try:
        await client.start(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        print("❌ خطأ: التوكن (DISCORD_TOKEN) غير صحيح أو انتهت صلاحيته!")
    except discord.errors.PrivilegedIntentsRequired:
        print("❌ خطأ: صلاحيات Intents غير مفعلة! اذهب إلى Discord Developer Portal -> Bot وفعّل Message Content Intent.")
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الاتصال بديسكورد: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت بنجاح.")
