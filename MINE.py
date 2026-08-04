import os
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# 1. تحميل المتغيرات البيئية
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# رابط OpenRouter الرئيسي ورابط النموذج المجاني
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NAME = "qwen/qwen-2.5-coder-32b-instruct:free"

# التحقق من المتغيرات
if not DISCORD_TOKEN or not OPENROUTER_API_KEY:
    print("❌ خطأ: تأكد من إضافة DISCORD_TOKEN و OPENROUTER_API_KEY في إعدادات Render.")
    exit(1)

# إعدادات بوت الديسكورد
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 2. خادم الويب الوهمي (لضمان عمل البوت 24/7 على Render)
# ==========================================
async def handle(request):
    return web.Response(text="ENA Bot is active 24/7 via OpenRouter Free Tier!")

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
# 3. الاتصال بعقل Qwen 2.5 عبر OpenRouter
# ==========================================
async def ask_ena_brain(prompt, user_name):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # يمكنك تشكيل وتعديل تعليمات شخصية ENA من هنا بحرية
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
            async with session.post(OPENROUTER_BASE_URL, json=payload, headers=headers, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    reply_text = data['choices'][0]['message']['content'].strip()
                    return reply_text if reply_text else "..."
                else:
                    error_text = await response.text()
                    print(f"❌ خطأ API من OpenRouter ({response.status}): {error_text}")
                    return f"عذراً، حدث خطأ أثناء الاتصال بالخدمة (كود: {response.status})."
    except asyncio.TimeoutError:
        return "عذراً، استغرق السيرفر وقتاً طويلاً في المعالجة."
    except Exception as e:
        print(f"❌ حدث خطأ غير متوقع: {e}")
        return "عذراً، لا يمكنني الوصول إلى الخدمة حالياً."

# ==========================================
# 4. أحداث ديسكورد (Discord Events)
# ==========================================
@client.event
async def on_ready():
    print("----------------------------------------")
    print(f"✅ تم تشغيل ENA بنجاح عبر OpenRouter!")
    print(f"🤖 النموذج المستخدم: {MODEL_NAME}")
    print(f"🤖 اسم البوت: {client.user}")
    print("----------------------------------------")

@client.event
async def on_message(message):
    # تجاهل الرسائل الصادرة من البوت نفسه
    if message.author == client.user:
        return

    # التفاعل حصراً عند عمل Mention للبوت
    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً!"

        print(f"\n📥 [رسالة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            final_reply = await ask_ena_brain(clean_prompt, user_name)

            # تجزئة الرسالة إذا تجاوزت حد ديسكورد (2000 حرف)
            if len(final_reply) > 2000:
                for chunk in [final_reply[i:i+1900] for i in range(0, len(final_reply), 1900)]:
                    await message.reply(chunk)
            else:
                await message.reply(final_reply)

# ==========================================
# 5. نقطة الانطلاق
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
