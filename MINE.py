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

# سلسلة التبديل السحابية عند استهلاك الحدود
MODEL_CHAIN = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-it"
]

MAX_SHORT_TERM = 8  # عدد الرسائل في الذاكرة قصيرة الأمد قبل التلخيص الإحلالي

if not DISCORD_TOKEN:
    print("❌ خطأ: لم يتم العثور على DISCORD_TOKEN")
if not GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على مفتاح API")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# 1. إدارة الذاكرة السحابية (Upstash REST API)
# ==========================================
async def get_redis_data(key):
    """جلب البيانات مباشرة من Upstash"""
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    url = f"{REDIS_URL}/get/{key}"
    headers = {"Authorization": f"Bearer {REDIS_TOKEN}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = data.get("result")
                    return json.loads(res) if res else None
    except Exception as e:
        print(f"⚠️ [Cloud DB Read Error]: {e}")
    return None

async def set_redis_data(key, data_value):
    """حفظ أو تحديث البيانات في Upstash"""
    if not REDIS_URL or not REDIS_TOKEN:
        return
    url = f"{REDIS_URL}/set/{key}"
    headers = {
        "Authorization": f"Bearer {REDIS_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, headers=headers, data=json.dumps(json.dumps(data_value, ensure_ascii=False)), timeout=10)
    except Exception as e:
        print(f"⚠️ [Cloud DB Save Error]: {e}")

# ==========================================
# 2. إدارة الذاكرة البشرية الديناميكية (إحلال وتعديل)
# ==========================================
async def summarize_and_compress(user_id, short_history, current_profile):
    """تحديث الذاكرة طويلة الأمد ديناميكياً بحذف الهامشي وتعديل التغيرات"""
    formatted_chat = ""
    for msg in short_history:
        role = "المستخدم" if msg["role"] == "user" else "إينا"
        formatted_chat += f"{role}: {msg['text']}\n"

    prompt_summary = f"""أنت مدير الذاكرة السحابية. وظيفتك تحديث الملف الشخصي للمستخدم بناءً على الحوار الجديد بشكل ديناميكي مثل عقل الإنسان.

[الملف الشخصي الحالي للمستخدم]:
{current_profile if current_profile else 'لا توجد معلومات قائمة بعد'}

[الحوار الأخير]:
{formatted_chat}

قم بإعادة كتابة وتحديث الملف الشخصي للمستخدم طبقاً للقواعد التالية:
1. التحديث والتعديل: إذا تغيرت آرائه أو تفضيلاته (مثل: غير ألعابه المفضلة أو اهتماماته)، قم بتعديل المعلومة القديمة بالجديدة فوراً.
2. تصفية الهامشي: احذف التفاصيل المؤقتة واليومية (مثل: صاحي متاخر، لعبت جيم واحد، أكلت شيء).
3. التركيز على الأهم: احتفظ فقط بالمعلومات الأساسية (اسمه، طبيعة شخصيته، اهتماماته الأساسية، أسلوب كلامه، التفضيلات الدائمة).
4. الإيجاز الشديد: اكتب الملف الشخصي المحدث في أسطر قصيرة وموجزة جداً بدون علامات ترقيم وبدون مقدمات."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY.strip() if GEMINI_API_KEY else ''}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_summary}]}],
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.2}
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    new_profile = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    new_profile = re.sub(r'[.,،؛!؟:\-_•\*]', ' ', new_profile)
                    new_profile = re.sub(r'\s+', ' ', new_profile).strip()
                    
                    await set_redis_data(f"ena_profile_{user_id}", new_profile)
                    print(f"🧠 [تحديث الذاكرة طويلة الأمد للمستخدم {user_id}]: {new_profile}")
    except Exception as e:
        print(f"⚠️ [Dynamic Memory Error]: {e}")

async def process_user_memory(user_id, role, text):
    """إدارة تنقل الرسائل بين الذاكرة القصيرة والطويلة"""
    short_history = await get_redis_data(f"ena_short_{user_id}") or []
    long_profile = await get_redis_data(f"ena_profile_{user_id}") or ""
    
    short_history.append({"role": role, "text": text})
    
    if len(short_history) >= MAX_SHORT_TERM:
        asyncio.create_task(summarize_and_compress(user_id, short_history, long_profile))
        # الإبقاء على آخر 3 رسائل للحفاظ على ربط السياق الحالي
        short_history = short_history[-3:]
        
    await set_redis_data(f"ena_short_{user_id}", short_history)

# ==========================================
# 3. خادم الويب السحابي (24/7 Keep Alive)
# ==========================================
async def handle_ping(request):
    return web.Response(text="ENA Dynamic Dual-Memory Cloud Engine Active!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 [Cloud Web Server] يعمل بنجاح على المنفذ: {port}")

# ==========================================
# 4. محرك الاتصال والتبديل التلقائي
# ==========================================
async def ask_ena_brain(prompt, user_id, user_name):
    short_history = await get_redis_data(f"ena_short_{user_id}") or []
    long_profile = await get_redis_data(f"ena_profile_{user_id}") or "لا توجد معلومات سابقة"
    
    # بناء السياق بصيغة حوار واضح بأسماء أصحابها
    short_context = ""
    for msg in short_history:
        role_label = user_name if msg["role"] == "user" else "إينا"
        short_context += f"{role_label}: {msg['text']}\n"
    
    headers = {"Content-Type": "application/json"}
    
    system_prompt = f"""You are a real 18-year-old human girl named "Ena" (إينا) chatting casually on Discord.
Persona: Tech-savvy, loves gaming, programming, logic.
User Information (Long-Term Memory): [{long_profile}]

RULES:
1. HUMAN IDENTITY: Never admit to being an AI, model, or bot.
2. CONTEXT AWARENESS: Read recent dialogue carefully and stay connected to active topic.
3. FORMATTING: NO PUNCTUATION MARKS, NO EMOJIS, NO TEXT LAUGHTER (no ههههه).
4. LANGUAGE: Arabic, but ALLOW standard game/tech names in English or Arabic without cutting them.
5. DIALECT: Casual white Arabic. Short natural Discord replies."""

    for model_name in MODEL_CHAIN:
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY.strip() if GEMINI_API_KEY else ''}"
        is_gemma = "gemma" in model_name.lower()
        
        if is_gemma:
            gemma_prompt = f"""<start_of_turn>user
أنت فتاة حقيقية اسمها إينا عمرك 18 سنة تسولفين في ديسكورد بعفوية. تحبين الألعاب والتقنية.
تابعي السالفة السابقة وافهمي المطلوب بدقة وسمي أسماء الألعاب كاملة وبدون بتر الكلمات.

معلومات عن المستخدم (ذاكرة طويلة الأمد): [{long_profile}]

سجل السوالف الأخيرة (ذاكرة قصيرة):
{short_context}
{user_name}: {prompt}<end_of_turn>
<start_of_turn>model
إينا:"""
            payload = {
                "contents": [{"role": "user", "parts": [{"text": gemma_prompt}]}],
                "generationConfig": {"maxOutputTokens": 250, "temperature": 0.65}
            }
        else:
            full_user_content = f"السوالف الأخيرة في الشات:\n{short_context}\n{user_name}: {prompt}"
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": full_user_content}]}],
                "generationConfig": {"maxOutputTokens": 250, "temperature": 0.65}
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(gemini_url, json=payload, headers=headers, timeout=45) as response:
                    status = response.status
                    
                    if status == 200:
                        data = await response.json()
                        if 'candidates' in data and len(data['candidates']) > 0:
                            reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
                            
                            # تنظيف ناعم يحافظ على الكلمات والأسماء الإنجليزية والعربية دون مسحها أو بترها
                            reply = re.sub(r'^(إينا|Ena)\s*:\s*', '', reply, flags=re.IGNORECASE)
                            reply = re.sub(r'[.,،؛!؟:\-_•\*#]', ' ', reply)
                            reply = re.sub(r'\s+', ' ', reply).strip()
                            
                            if reply and len(reply) > 2:
                                print(f"✅ [استجابة ناجحة سحابياً عبر]: {model_name}")
                                await process_user_memory(user_id, "user", prompt)
                                await process_user_memory(user_id, "model", reply)
                                return reply
                    
                    print(f"⚠️ [تجاوز {model_name} - رمز الحالة {status}]: جارِ التبديل للنموذج التالي...")
                    continue

        except Exception as e:
            print(f"⚠️ [خطأ اتصال بـ {model_name}]: {e} -> الانتقال للنموذج التالي...")
            continue

    return "الخدمة مشغولة حاليا أعد المحاولة بعد قليل"

# ==========================================
# 5. أحداث ديسكورد والتشغيل
# ==========================================
@client.event
async def on_ready():
    print("=" * 50)
    print(f"✅ تم الاتصال بديسكورد بنجاح!")
    print(f"🤖 البوت: {client.user} (ID: {client.user.id})")
    print(f"🧠 نظام الذاكرة البشرية المزدوج: شغال وبحفظ ديناميكي")
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
