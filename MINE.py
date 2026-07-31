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
# يفحص كلاً من AI_API_KEY و GROQ_API_KEY لضمان المرونة
AI_API_KEY = os.getenv('AI_API_KEY') or os.getenv('GROQ_API_KEY')

# التأكد من وجود المفاتيح المطلوبة
if not DISCORD_TOKEN or not AI_API_KEY:
    print("❌ خطأ: يرجى التأكد من ضبط DISCORD_TOKEN و AI_API_KEY (أو GROQ_API_KEY) في متغيرات البيئة!")
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
        "temperature": 0.6,   # الدرجة المثالية لمنع أخطاء التنسيق في DeepSeek R1
        "max_tokens": 2048    # ضروري جداً لإعطاء مساحة للتفكير + الإجابة
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
            
            # طباعة العقل الباطن <think> في السجلات (Logs) للتحليل
            think_match = re.search(r'<think>(.*?)</think>', raw_response, re.DOTALL)
            if think_match:
                subconscious = think_match.group(1).strip()
                print(f"🧠 [العقل الباطن لـ ENA - تفكير داخلي]:\n{subconscious}")

            # إزالة مسودة التفكير الداخلي لتنظيف الرد قبل إرساله للديسكورد
            final_reply = re.sub(r'<think>.*?</think>', '', raw_response, flags=re.DOTALL).strip()
            
            # إذا كانت الإجابة فارغة بعد القص، يتم الاعتماد على النص الخام
            if not final_reply:
                final_reply = raw_response.strip() if raw_response.strip() else "..."

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
