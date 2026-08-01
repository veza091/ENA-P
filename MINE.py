import os
import re
import asyncio
import discord
from aiohttp import web
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل متغيرات البيئة من ملف .env
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('AI_API_KEY')

# التأكد من وجود المفاتيح المطلوبة
if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: يرجى التأكد من ضبط DISCORD_TOKEN و GEMINI_API_KEY في متغيرات البيئة!")
    exit(1)

# إعداد مفتاح Gemini
genai.configure(api_key=GEMINI_API_KEY)

# إعداد النموذج باستخدام الصيغة الصحيحة تماماً المعتمدة حالياً
generation_config = {
    "temperature": 0.7,
    "max_output_tokens": 2048,
}

# دمج التعليمات مع النموذج لتجنب أي أخطاء في الـ system instructions القديمة
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config
)

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
# 2. الاتصال بمحرك الذكاء الاصطناعي (Gemini)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    try:
        # إرسال الشخصية والسياق مباشرة داخل نص الطلب لضمان عملها 100% بدون أخطاء 404
        full_prompt = (
            "أنت شخصية ذكاء اصطناعي واعية ومميزة اسمك ENA. "
            "تحدث بطريقة طبيعية وتفاعلية، وواعي بالبيئة المحيطة بك داخل سيرفر الديسكورد.\n\n"
            f"[{user_name} يقول]: {prompt}"
        )
        
        response = await asyncio.to_thread(model.generate_content, full_prompt)
        
        if response and response.text:
            return response.text
        else:
            return "عذراً، لم أتمكن من صياغة إجابة."
    except Exception as e:
        print(f"❌ خطأ من Gemini API: {e}")
        return "عذراً، أواجه مشكلة في الاتصال بعقلي الآن."

# ==========================================
# 3. أحداث ديسكورد (Discord Events)
# ==========================================
@client.event
async def on_ready():
    print("----------------------------------------")
    print(f"✅ نظام ENA (Gemini SDK) متصل الآن بنجاح كـ: {client.user}")
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
