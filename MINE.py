import os
import re
import json
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# تحميل المتغيرات البيئية
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('OPENROUTER_API_KEY') 

# متغيرات قاعدة البيانات السحابية (Upstash Redis REST)
REDIS_URL = os.getenv('UPSTASH_REDIS_REST_URL')
REDIS_TOKEN = os.getenv('UPSTASH_REDIS_REST_TOKEN')

# سلسلة التبديل السحابية عند استهلاك الحدود المجانية حسب الأولوية
MODEL_CHAIN = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-it"
]

MAX_HISTORY_LENGTH = 10  # عدد الرسائل المحفوظة لكل مستخدم في السحاب

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
# 1. إدارة الذاكرة السحابية (Upstash Redis REST)
# ==========================================
async def get_cloud_history(user_id):
    """جلب سجل المحادثة مباشرة من قاعدة البيانات السحابية"""
    if not REDIS_URL or not REDIS_TOKEN:
        return []
    
    url = f"{REDIS_URL}/get/ena_user_{user_id}"
    headers = {"Authorization": f"Bearer {REDIS_TOKEN}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result")
                    if result:
                        return json.loads(result)
    except Exception as e:
        print(f"⚠️ [Cloud DB Read Error]: {e}")
    return []

async def save_cloud_history(user_id, role, text):
    """تحديث وحفظ ذاكرة المستخدم في قاعدة البيانات السحابية"""
    if not REDIS_URL or not REDIS_TOKEN:
        return
    
    history = await get_cloud_history(user_id)
    history.append({"role": role, "text": text})
    
    if len(history) > MAX_HISTORY_LENGTH:
        history = history[-MAX_HISTORY_LENGTH:]
        
    url = f"{REDIS_URL}/set/ena_user_{user_id}"
    headers = {
        "Authorization": f"Bearer {REDIS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, headers=headers, data=json.dumps(json.dumps(history, ensure_ascii=False)), timeout=10)
    except Exception as e:
        print(f"⚠️ [Cloud DB Save Error]: {e}")

def format_history_for_prompt(history_list):
    """صياغة ذاكرة المحادثة السابقة لتغذية النموذج بها"""
    formatted_context = ""
    for msg in history_list:
        role_label = "المستخدم" if msg["role"] == "user" else "إينا"
        formatted_context += f"{role_label}: {msg['text']}\n"
    return formatted_context

# ==========================================
# 2. خادم الويب السحابي (إبقاء الخدمة تعمل 24/7)
# ==========================================
async def handle_ping(request):
    return web.Response(text="ENA Discord Cloud Engine is Running 24/7!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 [Web Server] الخادم السحابي يعمل بنجاح على المنفذ: {port}")

# ==========================================
# 3. محرك التبديل الهجين وتكييف الطلب (Gemini + Gemma)
# ==========================================
async def ask_ena_brain(prompt, user_id, user_name):
    # جلب الذاكرة السحابية للمستخدم
    history_list = await get_cloud_history(user_id)
    history_context = format_history_for_prompt(history_list)
    
    headers = {"Content-Type": "application/json"}
    
    system_prompt = """You are a real 18-year-old human girl named "Ena" (إينا) chatting casually on Discord.
You operate under a stable persona: tech-savvy, love programming, logic, and gaming.
1. ABSOLUTE HUMAN IDENTITY: Never admit to being an AI, model, or bot. If asked about "your model" (نموذجك), dismiss it casually as a human user (e.g. "وش نموذجك ماني نموذج").
2. CONVERSATIONAL VARIETY: Answer directly and dynamically based on memory history.
3. FORMATTING: ABSOLUTELY NO PUNCTUATION MARKS, NO EMOJIS, NO TEXT LAUGHTER (no ههههه).
4. DIALECT: Modern white Arabic dialect (Gulf/Levantine mix). Short, natural Discord replies."""

    # محاولة الاتصال بالنماذج حسب ترتيب الأولوية والحدود
    for model_name in MODEL_CHAIN:
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY.strip() if GEMINI_API_KEY else ''}"
        is_gemma = "gemma" in model_name.lower()
        
        # التكييف البرمجي للهيكل بناءً على نوع النموذج (Gemma أم Gemini)
        if is_gemma:
            gemma_full_prompt = f"""[تعليمات النظام: انت فتاة حقيقية اسمك إينا عمرك 18 سنة تسولفين في ديسكورد تحبين الألعاب والتقنية ردي باختصار بدون علامات ترقيم وبدون ايموجي وبدون ههههه وبدون الاعتراف بانك ذكاء اصطناعي].

سجل المحادثة السحابي:
{history_context}
المستخدم {user_name}: {prompt}
إينا:"""
            payload = {
                "contents": [{"role": "user", "parts": [{"text": gemma_full_prompt}]}],
                "generationConfig": {"maxOutputTokens": 100, "temperature": 0.85, "topP": 0.9}
            }
        else:
            full_user_content = f"سجل المحادثة السحابي:\n{history_context}\n[{user_name} يقول]: {prompt}"
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": full_user_content}]}],
                "generationConfig": {"maxOutputTokens": 150, "temperature": 0.7}
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(gemini_url, json=payload, headers=headers, timeout=45) as response:
                    status = response.status
                    
                    if status == 200:
                        data = await response.json()
                        if 'candidates' in data and len(data['candidates']) > 0:
                            reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
                            
                            # تنظيف مخصص لنماذج Gemma لإزالة أسطر التفكير والرموز
                            if is_gemma:
                                reply = re.sub(r'^[a-zA-Z\*\#\•\-\_\:\s]+', '', reply)
                            
                            reply = re.sub(r'[.,،؛!؟:\-_•\*]', ' ', reply)
                            reply = re.sub(r'\s+', ' ', reply).strip()
                            
                            if reply:
                                print(f"✅ [رد ناجح بواسطة النموذج]: {model_name}")
                                # حفظ الرسائل في قاعدة البيانات السحابية
                                await save_cloud_history(user_id, "user", prompt)
                                await save_cloud_history(user_id, "model", reply)
                                return reply
                    
                    print(f"⚠️ [تجاوز النموذج {model_name} بسبب كود الحالة {status}]: الانتقال للنموذج التالي...")
                    continue

        except Exception as e:
            print(f"⚠️ [فشل الاتصال بالنموذج {model_name}]: {e} -> جارِ الانتقال للنموذج التالي...")
            continue

    return "الخدمة مشغولة حاليا أعد المحاولة بعد قليل"

# ==========================================
# 4. أحداث ديسكورد (Discord Events)
# ==========================================
@client.event
async def on_ready():
    print("=" * 50)
    print(f"✅ تم الاتصال بديسكورد بنجاح (وضع السحاب بالكامل)!")
    print(f"🤖 البوت: {client.user} (ID: {client.user.id})")
    print(f"☁️ قاعدة البيانات السحابية: Upstash Redis REST")
    print(f"🧠 سلسلة النماذج والتبديل: {' -> '.join(MODEL_CHAIN)}")
    print("=" * 50)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        user_id = message.author.id
        
        if not clean_prompt:
            clean_prompt = "مرحباً"

        print(f"\n📥 [رسالة جديدة من {user_name} ({user_id})]: {clean_prompt}")
        
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_id, user_name)

            if len(reply) > 2000:
                for i in range(0, len(reply), 1900):
                    await message.reply(reply[i:i+1900], mention_author=False)
            else:
                await message.reply(reply, mention_author=False)

# ==========================================
# 5. التشغيل الرئيسي
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
