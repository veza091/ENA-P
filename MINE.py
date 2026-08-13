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
# مفتاح Google AI Studio
GEMINI_API_KEY = os.getenv('OPENROUTER_API_KEY') or os.getenv('GEMINI_API_KEY')

# اسم النموذج المحدد
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
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY.strip() if GEMINI_API_KEY else ''}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # الشخصية المحدثة: ردود قصيرة مقتضبة، طبيعية جداً وبدون تكلف أو استعراض
    system_prompt = """أنتِ فتاة بشرية اسمك إينا عمرك 18 سنة تسولفين في ديسكورد بعفوية تامة وبدون أي تكلف
طريقتك في الكلام: ردودك قصيرة جداً ومقتضبة مثل البشر الحقيقيين حين يشعرون بالملل أو يدردشون بلا هدف
لا تحاولي إقناع أحد بشخصيتك أو اهتماماتك بالقوة وإذا أحد قال الدنيا ملل ردي ببرود او اختصار واقعي
تتحدثين بلهجة بيضاء بسيطة مع استخدام كلمات إنجليزية دارجة قليلة فقط عند الحاجة
ممنوع نهائيا الاعتراف بانك ذكاء اصطناعي أو نموذج أو بوت انكري ذلك بأسلوب بشري طبيعي
ممنوع نهائيا استخدام علامات الترقيم أو الإيموجيات أو ههههه
اكتبي الرد النهائي المباشر باللغة العربية البسيطة وبدون أي استعراض أو مقدمات"""

    full_prompt = f"{system_prompt}\n\n[رسالة من {user_name}]: {prompt}\n[رد إينا المباشر]:"

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": full_prompt}]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 60,
            "temperature": 0.6
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(gemini_url, json=payload, headers=headers, timeout=60) as response:
                status = response.status
                response_text = await response.text()
                
                if status == 200:
                    data = await response.json()
                    reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    
                    # فلترة أمان إضافية لإزالة أي علامات ترقيم قد تظهر
                    reply = re.sub(r'^[•\*\-\s]*[A-Za-z0-9_].*', '', reply, flags=re.MULTILINE)
                    reply = re.sub(r'[.,،؛!؟:\-_•\*#""\'`«»]', ' ', reply)
                    reply = re.sub(r'\s+', ' ', reply).strip()
                    
                    return reply if reply else "هلا"
                else:
                    print(f"❌ [API Error {status}]: {response_text}")
                    if status == 400:
                        return "حدث خطأ في طلب الخدمة يرجى التأكد من اسم النموذج أو المفتاح"
                    elif status == 404:
                        return "النموذج غير متاح حاول التأكد من المعرف البرمجي للنموذج"
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
