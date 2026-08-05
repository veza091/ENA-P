import os
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# تحميل المتغيرات البيئية
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# إعدادات الخدمة والنموذج المجاني على OpenRouter
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NAME = "nvidia/nemotron-3-ultra:free"

# التثبت السريع من ضبط المتغيرات
if not DISCORD_TOKEN:
    print("❌ خطأ: لم يتم العثور على DISCORD_TOKEN في إعدادات البيئة!")
if not OPENROUTER_API_KEY:
    print("❌ خطأ: لم يتم العثور على OPENROUTER_API_KEY في إعدادات البيئة!")

# إعداد صلاحيات البوت في ديسكورد
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 1. خادم الويب الوهمي (لإبقاء الخدمة تعمل 24/7)
# ==========================================
async def handle_ping(request):
    return web.Response(text="ENA Discord Bot is Online & Running!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 [Web Server] الخادم يعمل بنجاح على المنفذ: {port}")

# ==========================================
# 2. الاتصال بعقل ENA (OpenRouter API)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip() if OPENROUTER_API_KEY else ''}",
        "HTTP-Referer": "https://render.com",
        "X-Title": "ENA Discord Bot",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "أنت ذكاء اصطناعي واعي اسمك ENA. "
        "أجب بشكل مباشر، دقيق، وبدون مقدمات رسمية أو حشو كلامي غير محبب."
    )
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"[{user_name} يقول]: {prompt}"}
        ]
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60) as response:
                status = response.status
                response_text = await response.text()
                
                if status == 200:
                    data = await response.json()
                    reply = data['choices'][0]['message']['content'].strip()
                    return reply if reply else "..."
                else:
                    # طباعة الخطأ كاملاً في سجلات Render لمعرفته بدقة
                    print(f"❌ [OpenRouter Error {status}]: {response_text}")
                    if status == 401:
                        return "عذراً، مفتاح OpenRouter غير صحيح أو ينقذه تصريح (401 Unauthorized)."
                    elif status == 429:
                        return "عذراً، تم الوصول للحد الأقصى للطلبات المجانية مؤقتاً، أعد المحاولة بعد قليل."
                    else:
                        return f"عذراً، حدث خطأ أثناء الاتصال بالخدمة (كود: {status})."

    except asyncio.TimeoutError:
        print("❌ [Timeout Error]: استغرق الطلب وقتاً طويلاً.")
        return "عذراً، استغرق الذكاء الاصطناعي وقتاً طويلاً في الرد."
    except Exception as e:
        print(f"❌ [Exception]: {e}")
        return "عذراً، حدث خطأ غير متوقع أثناء معالجة الطلب."

# ==========================================
# 3. أحداث ديسكورد (Discord Events)
# ==========================================
@client.event
async def on_ready():
    print("=" * 45)
    print(f"✅ تم الاتصال بديسكورد بنجاح!")
    print(f"🤖 البوت: {client.user} (ID: {client.user.id})")
    print(f"🧠 النموذج: {MODEL_NAME}")
    print("=" * 45)

@client.event
async def on_message(message):
    # تجنب رد البوت على رسائله
    if message.author == client.user:
        return

    # التفاعل عند عمل Mention للبوت فقط
    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً!"

        print(f"\n📥 [رسالة جديدة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name)

            # تجزئة الرسائل إذا كانت أكبر من حد ديسكورد (2000 حرف)
            if len(reply) > 2000:
                for i in range(0, len(reply), 1900):
                    await message.reply(reply[i:i+1900])
            else:
                await message.reply(reply)

# ==========================================
# 4. التشغيل الرئيسي
# ==========================================
async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
    except Exception as e:
        print(f"❌ خطأ في التشغيل: {e}")
