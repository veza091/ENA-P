import os
import re
import sys
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from supabase import create_client, Client

# تحميل متغيرات البيئة
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
MAIN_GEMINI_API_KEY = (os.getenv('MAIN_GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY') or "").strip()
MEMORY_GEMINI_API_KEY = (os.getenv('MEMORY_GEMINI_API_KEY') or MAIN_GEMINI_API_KEY).strip()

SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.getenv('SUPABASE_KEY', '').strip()

if not DISCORD_TOKEN or not MAIN_GEMINI_API_KEY:
    print("❌ خطأ: لم يتم العثور على المتغيرات الأساسية (DISCORD_TOKEN أو MAIN_GEMINI_API_KEY) في البيئة!")
    sys.exit(1)

# إعداد عميل Supabase
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("⚡ [Supabase] تم الاتصال بقاعدة البيانات بنجاح.")
    except Exception as e:
        print(f"⚠️ [Supabase Warning] فشل الاتصال بقاعدة البيانات: {e}")
else:
    print("⚠️ [Supabase Warning] لم يتم توفير بيانات الاتصال بـ Supabase. ستعمل الذاكرة مؤقتاً بدون حفظ دائم.")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# النماذج المعتمدة للردود المباشرة (الأساسية)
# ==========================================
MAIN_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

# ==========================================
# النماذج المعتمدة منفصلة للمصفاة الذهنية (الذاكرة)
# ==========================================
MEMORY_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite"
]

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<@!?\d+>', '', text)
    text = re.sub(r'[\*\-_•#@\$%\^&\(\)]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# 1. خادم الويب (لإبقاء Render نشطاً)
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
# 2. دوال التعامل مع قاعدة البيانات (Supabase)
# ==========================================
async def get_user_profile_db(user_id, user_name):
    if not supabase:
        return {"core_facts": "", "msg_count": 0}
    try:
        res = supabase.table("user_profiles").select("*").eq("user_id", str(user_id)).execute()
        if res.data:
            return res.data[0]
        else:
            new_profile = {"user_id": str(user_id), "user_name": user_name, "core_facts": "", "msg_count": 0}
            supabase.table("user_profiles").insert(new_profile).execute()
            return new_profile
    except Exception as e:
        print(f"⚠️ [DB Error - get_user_profile]: {e}")
        return {"core_facts": "", "msg_count": 0}

async def save_message_db(channel_id, user_id, user_name, role, content):
    if not supabase:
        return
    try:
        supabase.table("conversations").insert({
            "channel_id": str(channel_id),
            "user_id": str(user_id),
            "user_name": user_name,
            "role": role,
            "content": content
        }).execute()
    except Exception as e:
        print(f"⚠️ [DB Error - save_message]: {e}")

async def get_recent_history_db(channel_id, limit=8):
    if not supabase:
        return []
    try:
        res = supabase.table("conversations").select("*").eq("channel_id", str(channel_id)).order("created_at", desc=True).limit(limit).execute()
        messages = res.data[::-1] if res.data else []
        return messages
    except Exception as e:
        print(f"⚠️ [DB Error - get_recent_history]: {e}")
        return []

def update_user_memory_db(user_id, user_name, new_facts, new_count=None):
    if not supabase:
        return
    try:
        payload = {
            "user_id": str(user_id),
            "user_name": user_name,
            "core_facts": new_facts,
            "last_active": "now()"
        }
        if new_count is not None:
            payload["msg_count"] = new_count
        supabase.table("user_profiles").upsert(payload).execute()
    except Exception as e:
        print(f"⚠️ [DB Error - update_user_memory]: {e}")

# ==========================================
# 3. المصفاة الذهنية في الخلفية (Memory Filter)
# ==========================================
async def run_memory_filter_background(user_id, user_name, recent_chat_text, current_facts):
    """تحليل الحوار في الخلفية باستخدام مفتاح الذاكرة المخصص ونماذج الذاكرة المستقلة"""
    if not MEMORY_GEMINI_API_KEY:
        return

    prompt_filter = f"""
    أنت مصفاة ذكاء اصطناعي واعية ومباشرة. وظيفتك استخراج وتحديث الحقائق الانطباعية الثابتة عن المستخدم ({user_name}).

    الحقائق والبروفايل المسجل حالياً عن المستخدم:
    {current_facts if current_facts else "لا توجد حقائق سابقة مسجلة."}

    المحادثة الأخيرة:
    {recent_chat_text}

    المطلوب منك:
    1. استخرج أي حقائق سريعة أو ثابتة ظهرت عن المستخدم (نظامه، اهتماماته، بيئة عمله، أسلوبه).
    2. قانون الإحلال: إذا ذكر المستخدم معلومة مناقضة لحقيقة قديمة، احذف الحقيقة القديمة تماماً واكتب الجديدة مكانها.
    3. تجاهل التحيات واللغو والدردشة العابرة.
    4. اكتب النتيجة النهائية في هيئة نقاط مختصرة جداً بدون أي مقدمات أو شرح.
    """

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_filter}]}],
        "generationConfig": {
            "maxOutputTokens": 200,
            "temperature": 0.2
        }
    }

    async with aiohttp.ClientSession() as session:
        for model in MEMORY_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={MEMORY_GEMINI_API_KEY}"
            try:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get('candidates', [])
                        if candidates:
                            extracted_text = candidates[0].get('content', {}).get('parts', [])[0].get('text', '').strip()
                            if extracted_text:
                                update_user_memory_db(user_id, user_name, extracted_text)
                                print(f"🧠 [Memory Success] تم تحديث ذاكرة {user_name} بواسطة النموذج: {model}")
                                break
                    else:
                        err_body = await resp.text()
                        print(f"⚠️ نموذج الذاكرة {model} رد بالحالة {resp.status}: {err_body}")
            except Exception as e:
                print(f"⚠️ خطأ أثناء تشغيل ذاكرة النموذج {model}: {e}")
                continue

# ==========================================
# 4. الاتصال المباشر بـ Gemini (المخ الرئيسي)
# ==========================================
async def ask_ena_brain(clean_prompt, user_name, core_facts, history_messages):
    try:
        if not clean_prompt:
            clean_prompt = "هلا"

        # دمج الذاكرة في الـ System Instruction
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

        # بناء سياق الحوار المباشر من الذاكرة القريبة
        contents = []
        for msg in history_messages:
            role_tag = "model" if msg.get("role") in ["إينا", "model"] else "user"
            contents.append({
                "role": role_tag,
                "parts": [{"text": msg.get("content", "")}]
            })

        # إضافة الرسالة الحالية إذا لم تكن موجودة في السجل بعد
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

        async with aiohttp.ClientSession() as session:
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
# 5. أحداث ديسكورد
# ==========================================
@client.event
async def on_ready():
    print(f"✅ تم الاتصال بنجاح: {client.user}")

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

        # 2. جلب بروفايل المستخدم وسجل المحادثة القريب
        profile = await get_user_profile_db(user_id, user_name)
        core_facts = profile.get("core_facts", "")
        msg_count = profile.get("msg_count", 0) + 1
        
        recent_history = await get_recent_history_db(message.channel.id, limit=8)

        # 3. توليد الرد المباشر
        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name, core_facts, recent_history)
            await message.reply(reply, mention_author=False)

        # 4. حفظ رد البوت في DB
        await save_message_db(message.channel.id, user_id, user_name, "إينا", reply)

        # 5. تحديث العداد وفحص تشغيل المصفاة الذهنية في الخلفية كل 8 رسائل
        if msg_count % 8 == 0:
            recent_text_block = "\n".join([f"{m.get('role')}: {m.get('content')}" for m in recent_history])
            
            # إطلاق المهمة في الخلفية دون تعطيل استجابة ديسكورد (Non-blocking)
            asyncio.create_task(
                run_memory_filter_background(user_id, user_name, recent_text_block, core_facts)
            )

        # تحديث العداد في DB
        update_user_memory_db(user_id, user_name, core_facts, new_count=msg_count)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
