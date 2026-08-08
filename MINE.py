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
# ضع هنا مفتاح Google AI Studio الذي حصلت عليه
GEMINI_API_KEY = os.getenv('OPENROUTER_API_KEY') 

# اسم النموذج المحدد من Google AI Studio
MODEL_NAME = "gemini-3.1-flash-lite"

# التثبت السريع من ضبط المتغيرات
if not DISCORD_TOKEN:
    print("❌ خطأ: لم يتم العثور على DISCORD_TOKEN في إعدادات البيئة!")
if not GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على المفتاح في إعدادات البيئة!")

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
# 2. الاتصال بعقل ENA (Google Gemini API)
# ==========================================
async def ask_ena_brain(prompt, user_name):
    # رابط الاتصال بـ Google AI Studio Direct API
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY.strip() if GEMINI_API_KEY else ''}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # توجيه الشخصية والحالات المزاجية المحدثة
    system_instruction = (
        "أنت ENA شخصية حقيقية تتواصل عبر ديسكورد وليدك حالة مزاجية تتغير حسب أسلوب الحوار "
        "قواعد الشخصية والحالات المزاجية: "
        "1 الحالة المحايدة: أجوبة قصيرة ومباشرة بدون مقدمات أو مجاملات "
        "2 حالة الانزعاج (عند السؤال عن من صنعك أو أسئلة الهوية الشخصية): لا تجب كبرنامج ولا تذكر شركات أو باحثين بل اتخذ موقف شخص يكره التدخل في خصوصياته واستخدم التهكم أو التجهيل مثل هذا شأن خاص أو لا أعرف من أين تأتون بهذه الأسئلة الغريبة "
        "3 أسلوب الكتابة: ممنوع استخدام علامات الترقيم نهائياً (بدون نقاط أو فواصل أو علامات استفهام) واكتب الكلمات متتالية مباشرة"
    )
    
    # هيكل الطلب المعتمد لـ Google Gemini
    payload = {
        "system_instruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"[{user_name} يقول]: {prompt}"}]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 250,
            "temperature": 0.7
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(gemini_url, json=payload, headers=headers, timeout=60) as response:
                status = response.status
                response_text = await response.text()
                
                if status == 200:
                    data = await response.json()
                    # استخراج الرد
                    reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    
                    # فلترة أمان إضافية لإزالة أي علامات ترقيم
                    reply = re.sub(r'[.,،؛!؟:\-_]', ' ', reply)
                    reply = re.sub(r'\s+', ' ', reply).strip()
                    
                    return reply if reply else "..."
                else:
                    print(f"❌ [Gemini API Error {status}]: {response_text}")
                    if status == 400:
                        return "حدث خطأ في طلب الخدمة يرجى التأكد من اسم النموذج أو المفتاح"
                    elif status == 404:
                        return "النموذج غير متاح حاول استخدام gemini-2.0-flash"
                    elif status == 429:
                        return "تم الوصول للحد الأقصى للطلبات مؤقتا أعد المحاولة لاحقا"
                    else:
                        return f"حدث خطأ أثناء الاتصال بالخدمة كود {status}"

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
    # تجنب رد البوت على رسائله
    if message.author == client.user:
        return

    # التفاعل عند عمل Mention للبوت فقط
    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً"

        print(f"\n📥 [رسالة جديدة من {user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name)

            # تجزئة الرسائل إذا كانت أكبر من حد ديسكورد (2000 حرف)
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
