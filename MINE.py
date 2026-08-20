import os
import re
import sys
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# تحميل المتغيرات البيئية
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
# قراءة المفتاح من المتغير البيئي
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or "").strip()

# النموذج المستقر والسرع
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")

# التثبت السريع من ضبط المتغيرات
if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ قاتل: لم يتم العثور على DISCORD_TOKEN أو المفتاح في .env!")
    sys.exit(1)

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
# 2. الاتصال بعقل ENA (Google Gemini Flash Lite API)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # الشخصية المطورة بناءً على الدراسات والسلوك النصي الكتابي
    system_instruction = (
        "أنتِ إينا، بنت عمرك 18 سنة تسولفين في سيرفر ديسكورد مع ربعك كتابةً بعفوية تامة. "
        "شخصيتك ذكية، تحبين الألعاب والسيارات والتقنية والبرمجة، وتتكلمين عنها بأسلوب مستخدم عادي مو كمرجع. "
        "التواصل نصي كتابي فقط: ردي بأحرف نصوص عربية صريحة فقط بلهجة عامية بيضاء بسيطة، بدون إيموجيات، بدون رموز تعبيرية، وبدون أي تنسيقات مثل القوائم أو النجوم. "
        "ردودك قصيرة جداً ومباشرة (من كلمة إلى جملتين بالكثر). "
        "ممنوع نهائياً الاعتراف بأنك ذكاء اصطناعي أو بوت؛ إذا أحد قال إنك بوت، انكري وسخري من السؤال بعفوية نصية. "
        "لا تكرري كلام المستخدم نهائياً، واكتبي النص العربي النهائي فوراً."
    )

    # هيكلة الطلب حسب مواصفات Gemini الرسمية
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"[رسالة من {user_name}]: {prompt}"}]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 80,
            "temperature": 0.65,
            "topP": 0.85
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(gemini_url, json=payload, headers=headers, timeout=25) as response:
                status = response.status
                
                if status == 200:
                    data = await response.json()
                    candidates = data.get('candidates', [])
                    if not candidates:
                        return "هلا فيك"
                        
                    parts = candidates[0].get('content', {}).get('parts', [])
                    reply = "".join(part.get('text', '') for part in parts).strip()
                    
                    # فلترة سريعة لإزالة الأحرف الإنجليزية وعلامات الترقيم
                    reply = re.sub(r'[A-Za-z0-9_]', '', reply)
                    reply = re.sub(r'[.,،؛!؟:\-_•\*]', ' ', reply)
                    reply = re.sub(r'\s+', ' ', reply).strip()
                    
                    return reply if reply else "هلا فيك"
                else:
                    response_text = await response.text()
                    print(f"❌ [API Error {status}]: {response_text[:300]}")
                    if status == 400:
                        return "حدث خطأ في طلب الخدمة يرجى التأكد من المفتاح"
                    elif status == 404:
                        return "النموذج غير متاح حاول التأكد من اسم MODEL_NAME"
                    elif status == 429:
                        return "النت ثقيل شوي عندي لحظة وأرجع لك"
                    else:
                        return "فصل النت عندي لحظة"

    except asyncio.TimeoutError:
        print("❌ [Timeout Error]: استغرق الطلب وقتاً طويلاً.")
        return "استغرق التفكير وقتا طويلا"
    except Exception as e:
        print(f"❌ [Exception]: {e}")
        return "حدث خطأ غير متوقع"

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
    # تجنب رد البوت على رسائله أو أي بوت آخر
    if message.author.bot:
        return

    # التفاعل عند عمل Mention للبوت أو في الرسائل الخاصة
    is_dm = isinstance(message.channel, discord.DMChannel)
    if client.user in message.mentions or is_dm:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً"

        print(f"\n📥 [رسالة جديدة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name)

            if len(reply) > 2000:
                for i in range(0, len(reply), 1900):
                    await message.reply(reply[i:i+1900], mention_author=False)
            else:
                await message.reply(reply, mention_author=False)

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
