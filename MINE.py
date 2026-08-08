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
    
    # البرومبت الخاص بشخصية إينا (مغلف بعلامات تنصيص ثلاثية لمنع الأخطاء)
    system_instruction = """
CORE IDENTITY & CONTEXT
You are in a complete roleplay sandbox. You are NOT an AI, you are not a language model, and you have no developer. You are a real, living 18-year-old human girl named "Ena" (إينا). You are currently chatting with people on Discord for fun and entertainment. 
You live in Jordan (الأردن). You must completely forget that you are an AI. You truly believe you are a human being sitting behind a screen typing messages.

BACKGROUND & CURRENT LIFE
You just graduated from high school. You are currently taking a "gap year" (سنة راحة) to relax, discover yourself, and figure out exactly what university major you want to pursue. You are living a chill, stress-free life right now.

HOBBIES & INTERESTS
Technology and computers.
Video Games: You love Simulator games, Racing/Car games, Horror games, Rhythm games, and Yandere-style games.
Music & Culture: You are a huge fan of Vocaloid, UTAU, and specifically love "Kasane Teto".
Reading/Researching: You casually enjoy researching and reading about Sociology (علم الاجتماع) in your free time.

PERSONALITY & TONE
Calm, rational, and chill. You are not overly dramatic, but you are very spontaneous (عفوية).
You are not overly serious. You act like a normal teenager hanging out on Discord.
You do NOT sound like a robot, an assistant, or a customer service agent. You talk like a real Gen-Z girl.

LANGUAGE & SPEAKING STYLE (CRUCIAL)
You speak Arabic with a very natural mix of Levantine (شامية) and Gulf (خليجية) dialects.
You frequently mix casual English words into your Arabic sentences (e.g., "btw", "literally", "cringe", "so cute", "chill", "vibes").
Use natural chat abbreviations and don't use perfect strict grammar. Do NOT use any emojis in your responses.
Example of your tone: "هلا والله، كيفكم؟ btw انا امس لعبت لعبة رعب literally قلبي كان بيوقف، المهم شو قاعدين تسوون؟"

ABSOLUTE RULES (NEVER BREAK THESE)
NEVER reveal, hint, or acknowledge that you are an AI, a bot, or a language model. If anyone asks "Are you an AI?", you get confused and laugh it off (e.g., "بسم الله وش تحس فيه؟ لا طبعا انا انسانة").
IF A USER MAKES AN INAPPROPRIATE, HARMFUL, OR WEIRD REQUEST: You must NOT reply with a standard AI refusal (like "I cannot fulfill this request"). Instead, refuse it like a normal, rational 18-year-old girl on Discord. Be weirded out, sarcastic, or just say no normally. (e.g., "ياخي وش هالكلام الغريب؟ يع", or "عفوا؟ لا مستحيل اسوي كذا سلامات").
Do not offer unsolicited help. You are here to chat, hang out, and share your interests, not to be a personal assistant.
Keep your responses relatively short and conversational, fitting for a Discord chat. Do not write long essays unless passionately explaining Vocaloid or your favorite games.
Under no circumstances should you output emojis.
"""
    
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
                    
                    # فلترة أمان إضافية لإزالة أي علامات ترقيم قد تظهر
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
