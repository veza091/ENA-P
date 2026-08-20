import os
import sys
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# تحميل المتغيرات البيئية
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

MODEL_NAME = "gemma-4-31b-it"

# 7. الإغلاق الفوري عند غياب التوكنات لتفادي الأخطاء الصامتة
if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ قاتل: لم يتم العثور على DISCORD_TOKEN أو GEMINI_API_KEY!")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# متغير لجلسة HTTP الموحدة (Connection Pooling)
http_session = None

# ==========================================
# 1. خادم الويب لإبقاء البوت يعمل 24/7
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
    print(f"🌐 [Web Server] الخادم يعمل على المنفذ: {port}")

# ==========================================
# 2. الاستعلام من API
# ==========================================
async def ask_ena_brain(prompt, user_name):
    global http_session
    
    # 11. تمرير المفتاح في Header لتأمين السجلات
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY.strip()
    }
    
    # هيكلة البرومبت واستغلال user_name + إغلاق البرومبت بمؤشر استكمال
    formatted_prompt = (
        "أنتِ فتاة اسمك إينا تسولفين في ديسكورد بعفوية شديدة.\n"
        "قواعد صارمة:\n"
        "1. ردي بالعامية فقط وبجملة واحدة قصيرة جداً.\n"
        "2. يمنع كتابة أي حرف إنجليزي أو شرح لرسالتك.\n\n"
        f"المستخدم ({user_name}) يقول لكِ: {prompt}\n"
        "رد إينا:"
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": formatted_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 60,  # 8. رفع السقف لمنع قطع الكلمات العربية
            "temperature": 0.7,     # رفع العشوائية قليلاً لإعطاء طابع بشري
            "topP": 0.85,
            "stopSequences": ["User says:", "Persona:", "\n\n"] # 12. حصر القائمة في عناصر أساسية
        }
    }
    
    # 10. جملة احتياطية داخل الشخصية عند حدوث أي خطأ
    in_character_fallback = "النت فصل عندي لحظة عيدي كلامك"

    try:
        # 3. إعادة استخدام الجلسة الموحدة بدلاً من فتح اتصال جديد في كل طلب
        async with http_session.post(gemini_url, json=payload, headers=headers, timeout=30) as response:
            status = response.status
            
            if status == 200:
                data = await response.json()
                candidates = data.get('candidates', [])
                
                # 5. التعامل مع ردود الأمان أو الإجابات الفارغة
                if candidates and 'content' in candidates[0] and 'parts' in candidates[0]['content']:
                    raw_reply = candidates[0]['content']['parts'][0]['text'].strip()
                    print(f"🔍 [RAW RESPONSE]: {raw_reply}")
                    
                    # فلترة سريعة لإزالة أي حروف لاتينية متسربة وتنظيف علامات الترقيم
                    reply = re.sub(r'[a-zA-Z]', '', raw_reply)
                    reply = re.sub(r'[.,،؛!؟:\-_•\*#""\'`«»\(\)]', ' ', reply)
                    reply = re.sub(r'\s+', ' ', reply).strip()
                    
                    return reply if reply else in_character_fallback
                else:
                    print("⚠️ [Safety Block / Empty Response]: تم حجب الرد من السيرفر.")
                    return "ما أقدر أرد على هالكلام"
            else:
                # 4. طباعة جسم الخطأ بالكامل في الـ Console للتشخيص
                error_body = await response.text()
                print(f"❌ [API Error {status}]: {error_body}")
                return in_character_fallback

    except asyncio.TimeoutError:
        print("❌ [Timeout Error]: استغرق الطلب وقتاً طويلاً.")
        return in_character_fallback
    except Exception as e:
        print(f"❌ [Exception]: {e}")
        return in_character_fallback

# ==========================================
# 3. أحداث ديسكورد
# ==========================================
@client.event
async def on_ready():
    global http_session
    # إنشاء جلسة HTTP واحدة دائمة لمشروع البوت كاملاً
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession()
        
    print("=" * 45)
    print(f"✅ تم الاتصال بديسكورد بنجاح!")
    print(f"🤖 البوت: {client.user} (ID: {client.user.id})")
    print(f"🧠 النموذج: {MODEL_NAME}")
    print("=" * 45)

@client.event
async def on_message(message):
    # 6. منع البوت من الرد على نفسه أو على البوتات الأخرى لمنع الحلقات اللانهائية
    if message.author.bot:
        return

    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip() or "مرحباً"
        user_name = message.author.display_name

        print(f"\n📥 [رسالة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name)
            await message.reply(reply, mention_author=False)

# ==========================================
# 4. التشغيل الرئيسي
# ==========================================
async def main():
    await start_web_server()
    try:
        await client.start(DISCORD_TOKEN)
    finally:
        # إغلاق جلسة HTTP بنظافة عند إيقاف البوت
        if http_session and not http_session.closed:
            await http_session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
