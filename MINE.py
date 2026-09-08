import os
import re
import sys
import asyncio
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# ==========================================
# 1. تحميل متغيرات البيئة والتهيئة
# ==========================================
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
MAIN_GEMINI_API_KEY = (os.getenv('MAIN_GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY') or "").strip()
MEMORY_GEMINI_API_KEY = (os.getenv('MEMORY_GEMINI_API_KEY') or MAIN_GEMINI_API_KEY).strip()
MONGODB_URI = os.getenv('MONGODB_URI', '').strip()

if not DISCORD_TOKEN or not MAIN_GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على المتغيرات الأساسية (DISCORD_TOKEN أو MAIN_GEMINI_API_KEY) في البيئة!")
    sys.exit(1)

# إعداد عميل MongoDB
mongo_client = None
db = None
if MONGODB_URI:
    try:
        mongo_client = AsyncIOMotorClient(MONGODB_URI)
        db = mongo_client["ena_bot_db"]
        print("⚡ [MongoDB] جاري إعداد الاتصال بقاعدة البيانات...")
    except Exception as e:
        print(f"⚠️ [MongoDB Warning] فشل الاتصال المبدئي بقاعدة البيانات: {e}")
else:
    print("⚠️ [MongoDB Warning] لم يتم توفير رابط الاتصال بـ MongoDB.")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

MAIN_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

MEMORY_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite"
]

# مدير جلسات HTTP لمنع استهلاك الموارد (Singleton)
class HTTPManager:
    _session: Optional[aiohttp.ClientSession] = None

    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        if cls._session is None or cls._session.closed:
            cls._session = aiohttp.ClientSession()
        return cls._session

    @classmethod
    async def close_session(cls):
        if cls._session and not cls._session.closed:
            await cls._session.close()

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<@!?\d+>', '', text)
    text = re.sub(r'[\*\-_•#@\$%\^&\(\)]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# 2. خادم الويب
# ==========================================
async def handle_ping(request):
    return web.Response(text="ENA Bot is Online with Active Memory!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, '0.0.0.0', port).start()
    print(f"🌐 [Web Server] يعمل على المنفذ: {port}")

# ==========================================
# 3. دوال قاعدة البيانات (MongoDB)
# ==========================================
async def init_mongodb():
    if mongo_client is None or db is None:
        return
    try:
        await mongo_client.admin.command('ping')
        print("✅ [MongoDB] تم الاتصال بقاعدة البيانات بنجاح في Atlas!")
        existing_collections = await db.list_collection_names()
        if "conversations" not in existing_collections:
            await db.create_collection("conversations")
        if "user_profiles" not in existing_collections:
            await db.create_collection("user_profiles")
        print("📁 [MongoDB] تم إنشاء/التحقق من المجموعات بنجاح.")
    except Exception as e:
        print(f"❌ [MongoDB Connection Error]: {e}")

async def get_user_memory_db(user_id: str, user_name: str) -> Dict:
    """جلب بروفايل الذاكرة الخاص بالمستخدم أو إنشائه إن لم يوجد"""
    if db is None:
        return {"user_id": str(user_id), "user_name": user_name, "core_facts": "", "msg_count": 0}
    try:
        profile = await db.user_profiles.find_one({"user_id": str(user_id)})
        if profile:
            print(f"📖 [Memory Load] تم جلب ذاكرة {user_name}: ({len(profile.get('core_facts', ''))} حرف)")
            return profile
        
        new_profile = {
            "user_id": str(user_id),
            "user_name": user_name,
            "core_facts": "",
            "last_active": datetime.now(timezone.utc),
            "msg_count": 0
        }
        await db.user_profiles.insert_one(new_profile)
        print(f"📝 [Memory Init] تم إنشاء بروفايل ذاكرة جديد لـ {user_name}")
        return new_profile
    except Exception as e:
        print(f"❌ [DB Error - get_user_memory_db]: {e}")
        return {"user_id": str(user_id), "user_name": user_name, "core_facts": "", "msg_count": 0}

async def update_user_facts_db(user_id: str, user_name: str, new_facts: str) -> bool:
    """تحديث الحقائق الاستنتاجية فقط دون مسح العدادات"""
    if db is None:
        return False
    try:
        payload = {
            "user_name": user_name,
            "core_facts": new_facts.strip(),
            "last_active": datetime.now(timezone.utc)
        }
        await db.user_profiles.update_one(
            {"user_id": str(user_id)},
            {"$set": payload},
            upsert=True
        )
        print(f"💾 [Memory Save Success] تم حفظ الحقائق الجديدة لـ {user_name}")
        return True
    except Exception as e:
        print(f"❌ [DB Error - update_user_facts_db]: {e}")
        return False

async def increment_user_msg_count(user_id: str, user_name: str) -> int:
    """زيادة عداد الرسائل فقط بدون التأثير على الحقائق المخزنة"""
    if db is None:
        return 1
    try:
        result = await db.user_profiles.find_one_and_update(
            {"user_id": str(user_id)},
            {"$inc": {"msg_count": 1}, "$set": {"user_name": user_name, "last_active": datetime.now(timezone.utc)}},
            upsert=True,
            return_document=True
        )
        return result.get("msg_count", 1) if result else 1
    except Exception as e:
        print(f"❌ [DB Error - increment_user_msg_count]: {e}")
        return 1

async def save_message_db(channel_id, user_id, user_name, role, content):
    if db is None:
        return
    try:
        await db.conversations.insert_one({
            "channel_id": str(channel_id),
            "user_id": str(user_id),
            "user_name": user_name,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc)
        })
    except Exception as e:
        print(f"⚠️ [DB Error - save_message]: {e}")

async def get_recent_history_db(channel_id, limit=8):
    if db is None:
        return []
    try:
        cursor = db.conversations.find({"channel_id": str(channel_id)}).sort("_id", -1).limit(limit)
        messages = await cursor.to_list(length=limit)
        return messages[::-1]
    except Exception as e:
        print(f"⚠️ [DB Error - get_recent_history]: {e}")
        return []

# ==========================================
# 4. المصفاة الذهنية المحسنة (Memory Filter)
# ==========================================
async def run_memory_filter_background(user_id: str, user_name: str, recent_chat_text: str, current_facts: str):
    if not MEMORY_GEMINI_API_KEY:
        return

    session = await HTTPManager.get_session()
    
    prompt_filter = f"""
أنت مصفاة ذكاء اصطناعي واعية وظيفتك تحديث البروفايل الشخصي والحقائق الثابتة عن المستخدم ({user_name}).

البروفايل الحالي المخزن:
{current_facts if current_facts else "لا توجد حقائق سابقة."}

المحادثة الأخيرة:
{recent_chat_text[:2000]}

التعليمات الحازمة:
1. استخرج أي معلومات وحقائق ثابتة جديدة (اهتماماته، بيئته، أسلوبه، نظامه).
2. قانون الإحلال: ادمج القديم والجديد، وإذا تناقضت حقيقة جديدة مع قديمة، احذف القديمة واعتمد الجديدة.
3. اكتب القائمة النهائية المحدثة في نقاط مختصرة ومباشرة بدون أي مقدمات أو توضيحات.
"""

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_filter}]}],
        "generationConfig": {"maxOutputTokens": 250, "temperature": 0.2}
    }

    for model in MEMORY_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={MEMORY_GEMINI_API_KEY}"
        try:
            async with session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=25) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get('candidates', [])
                    if candidates:
                        extracted_text = candidates[0].get('content', {}).get('parts', [])[0].get('text', '').strip()
                        if extracted_text and len(extracted_text) > 5:
                            await update_user_facts_db(user_id, user_name, extracted_text)
                            print(f"🧠 [Memory Success] تم تحديث ذاكرة {user_name} بواسطة: {model}")
                            break
                elif resp.status == 429:
                    print(f"⏳ [Rate Limit] نموذج الذاكرة {model} متجاوز للحد، تخطي...")
                    continue
                else:
                    err_body = await resp.text()
                    print(f"⚠️ نموذج الذاكرة {model} رد بالحالة {resp.status}: {err_body}")
        except Exception as e:
            print(f"⚠️ خطأ أثناء تشغيل ذاكرة النموذج {model}: {e}")
            continue

# ==========================================
# 5. المخ الرئيسي (Gemini Response)
# ==========================================
async def ask_ena_brain(clean_prompt, user_name, core_facts, history_messages):
    try:
        if not clean_prompt:
            clean_prompt = "هلا"

        system_instruction = (
            "أنتِ إينا، بنت عمرك 18 سنة تسولفين في ديسكورد بعفوية واقعية كأي مستخدم عادي. "
            "ردي بأسلوب عامي بيض بسيط جداً، بدون إيموجيات، بدون رموز، وبدون أي تنسيقات. "
            "ردودك قصيرة، مختصرة، وبشرية جداً (كلمة إلى جملة واحدة فقط).\n\n"
            f"[ذاكرتك وانطباعك الشخصي الحالي عن {user_name}]:\n"
            f"{core_facts if core_facts else 'لا توجد معلومات سابقة عنه.'}\n\n"
            "قواعد الواقعية:\n"
            "1. استغل ذاكرتك بفهم شخصية المستخدم واهتماماته دون أن تقول له 'حسب ذاكرتي' أو 'أنا أتذكر'.\n"
            "2. ممنوع كلياً ذكر اسم مدينتك (الدمام) إلا إذا سُئلتِ مباشرة عنها.\n"
            "3. ردي بأسلوب واقعي يطابق بنت عمرها 18 سنة وبدون تصنع.\n"
            "4. لا تختلقي قصصاً وهمية وممنوع كلياً الاعتراف بأنك ذكاء اصطناعي أو بوت."
        )

        contents = []
        for msg in history_messages:
            role_tag = "model" if msg.get("role") in ["إينا", "model"] else "user"
            contents.append({
                "role": role_tag,
                "parts": [{"text": msg.get("content", "")}]
            })

        if not contents or contents[-1]["parts"][0]["text"] != clean_prompt:
            contents.append({
                "role": "user",
                "parts": [{"text": f"[رسالة من {user_name}]: {clean_prompt}"}]
            })

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 60,
                "temperature": 0.75,
                "topP": 0.9
            }
        }
        
        reply_text = None
        session = await HTTPManager.get_session()

        for model in MAIN_MODELS:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={MAIN_GEMINI_API_KEY}"
            try:
                async with session.post(gemini_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get('candidates', [])
                        if not candidates:
                            continue
                            
                        parts = candidates[0].get('content', {}).get('parts', [])
                        reply = "".join(p.get('text', '') for p in parts)
                        reply = clean_text(reply)
                        
                        if reply:
                            reply_text = reply
                            break
                    else:
                        err_body = await resp.text()
                        print(f"⚠️ النموذج الرئيسي {model} رد بالحالة {resp.status}: {err_body}")
            except Exception as e:
                print(f"⚠️ خطأ أثناء الاتصال بالنموذج الرئيسي {model}: {e}")
                continue

        return reply_text or "هلا"

    except Exception as e:
        print(f"❌ خطأ غير متوقع في ask_ena_brain: {e}")
        return "هلا"

# ==========================================
# 6. أحداث ديسكورد
# ==========================================
@client.event
async def on_ready():
    print(f"✅ تم الاتصال بنجاح: {client.user}")
    await init_mongodb()

@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    if client.user in message.mentions or is_dm:
        user_id = str(message.author.id)
        user_name = message.author.display_name
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip() or "هلا"

        print(f"\n📥 [رسالة من {user_name}]: {clean_prompt}")

        # 1. حفظ رسالة المستخدم في DB
        await save_message_db(message.channel.id, user_id, user_name, "user", clean_prompt)

        # 2. زيادة العداد وجلب البروفايل الحديث والسجل القريب
        msg_count = await increment_user_msg_count(user_id, user_name)
        profile = await get_user_memory_db(user_id, user_name)
        core_facts = profile.get("core_facts", "")
        
        recent_history = await get_recent_history_db(message.channel.id, limit=8)

        # 3. توليد الرد المباشر
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name, core_facts, recent_history)
            await message.reply(reply, mention_author=False)

        # 4. حفظ رد البوت في DB
        await save_message_db(message.channel.id, user_id, user_name, "إينا", reply)

        # 5. تشغيل المصفاة الذهنية كل 8 رسائل في الخلفية بدون الـ Overwrite Risk
        if msg_count % 8 == 0:
            updated_history = await get_recent_history_db(message.channel.id, limit=10)
            recent_text_block = "\n".join([f"{m.get('role')}: {m.get('content')}" for m in updated_history])
            
            asyncio.create_task(
                run_memory_filter_background(user_id, user_name, recent_text_block, core_facts)
            )

async def main():
    await start_web_server()
    try:
        await client.start(DISCORD_TOKEN)
    finally:
        await HTTPManager.close_session()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
