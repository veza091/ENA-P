import os
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# تحميل متغيرات البيئة من ملف .env
load_dotenv()
DISCORD_TOKEN = os.getenv('MTUzMjA3MTY5ODQxMTY4ODAxNg.Gpzx6g.GI46V2Qux86vJ_ZGFym9kZ8UEUE3fUrKPS7-EY')
AI_API_KEY = os.getenv('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')

# التأكد من وجود المفاتيح المطلوبة
if not DISCORD_TOKEN or not AI_API_KEY:
    print("❌ خطأ: يرجى التأكد من ضبط DISCORD_TOKEN و AI_API_KEY في متغيرات البيئة!")
    exit(1)

# إعداد الصلاحيات للـ Bot
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 1. خادم الويب الخفيف (للاستضافة المجانية 24/7)
# ==========================================
async def handle(request):
    return web.Response(text="ENA Web Service is active and running 24/7!")

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
# 2. الاتصال بمحرك الذكاء الاصطناعي (Groq API)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # التعليمات الأساسية لشخصية ENA
    system_prompt = (
        "أنت شخصية ذكاء اصطناعي واعية ومميزة اسمك ENA. "
        "تحدث بطريقة طبيعية وتفاعلية، وواعي بالبيئة المحيطة بك داخل سيرفر الديسكورد."
    )
    
    payload = {
        "model": "deepseek-r1-distill-llama-70b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"[{user_name} يقول]: {prompt}"}
        ],
        "temperature": 0.7
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['choices'][0]['message']['content']
                else:
                    error_text = await response.text()
                    print(f"❌ خطأ من Groq API ({response.status}): {error_text}")
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
    print(f"✅ نظام ENA متصل الآن بنجاح كـ: {client.user}")
    print("----------------------------------------")

@client.event
async def on_message(message):
    # التغاضي عن الرسائل الصادرة من البوت نفسه
    if message.author == client.user:
        return

    # التفاعل فقط عند عمل المنشن لـ ENA
    if client.user in message.mentions:
        # تنظيف نص الرسالة من المنشن
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        # إذا كانت الرسالة فارغة بعد المنشن
        if not clean_prompt:
            clean_prompt = "مرحباً!"

        print(f"\n📥 [رسالة جديدة من {user_name}]: {clean_prompt}")
        
        # إظهار أن البوت يكتب الآن (Typing status)
        async with message.channel.typing():
            raw_response = await ask_ena_brain(clean_prompt, user_name)
            
            # فصل العقل الباطن <think> عن الرد النهائي
            think_match = re.search(r'<think>(.*?)</think>', raw_response, re.DOTALL)
            if think_match:
                subconscious = think_match.group(1).strip()
                final_reply = raw_response.replace(think_match.group(0), '').strip()
                print(f"🧠 [العقل الباطن لـ ENA - تفكير داخلي]:\n{subconscious}")
            else:
                final_reply = raw_response.strip()

            # التأكد من أن الرد ليس فارغاً
            if not final_reply:
                final_reply = "..."

            # معالجة حد الـ 2000 حرف في ديسكورد (تقسيم الرسالة إن كانت طويلة)
            if len(final_reply) > 2000:
                for chunk in [final_reply[i:i+1900] for i in range(0, len(final_reply), 1900)]:
                    await message.reply(chunk)
            else:
                await message.reply(final_reply)

# ==========================================
# 4. نقطة الانطلاق والتشغيل الرئيسي
# ==========================================
async def main():
    # تشغيل خادم الويب ثم تشغيل البوت
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت بنجاح.")
