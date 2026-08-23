import os
import re
import sys
import asyncio
import discord
import aiohttp
from datetime import datetime, timezone
from aiohttp import web
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or "").strip()
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not DISCORD_TOKEN or not GEMINI_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ خطأ: لم يتم العثور على كافة المتغيرات المطلوبة (DISCORD_TOKEN, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY) في .env!")
    sys.exit(1)

# الاتصال بقاعدة بيانات Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

FREE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

# تتبع مهام مؤقت الخمول (15 دقيقة) لكل مستخدم
inactivity_tasks = {}

# ==========================================
# 0. التعامل مع Supabase (حفظ وجلب البيانات)
# ==========================================
def save_message_cloud(channel_id, user_id, user_name, role, content):
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        
        # 1. إدخال الرسالة في جدول المحادثات
        supabase.table('conversations').insert({
            'channel_id': str(channel_id),
            'user_id': str(user_id),
            'user_name': user_name,
            'role': role,
            'content': content
        }).execute()
        
        # 2. تحديث العداد ووقت النشاط
        profile = supabase.table('user_profiles').select('msg_count').eq('user_id', str(user_id)).execute()
        if profile.data:
            current_count = profile.data[0]['msg_count'] + 1
            supabase.table('user_profiles').update({
                'msg_count': current_count,
                'user_name': user_name,
                'last_active': now_str
            }).eq('user_id', str(user_id)).execute()
        else:
            current_count = 1
            supabase.table('user_profiles').insert({
                'user_id': str(user_id),
                'user_name': user_name,
                'summary': '',
                'msg_count': 1,
                'last_active': now_str
            }).execute()
            
        return current_count
    except Exception as e:
        print(f"❌ خطأ في حفظ الرسالة بـ Supabase: {e}")
        return 0

def get_recent_history_cloud(channel_id, user_id, limit=8):
    """جلب أحدث الرسائل للذاكرة قصيرة المدى"""
    try:
        query = supabase.table('conversations').select('role, content').eq('user_id', str(user_id))
        
        # تصفية حسب القناة فقط إن لم تكن 0
        if channel_id != 0:
            query = query.eq('channel_id', str(channel_id))
            
        res = query.order('id', desc=True).limit(limit).execute()
        
        rows = res.data or []
        history = []
        for row in reversed(rows):
            history.append(f"{row['role']}: {row['content']}")
        return history
    except Exception as e:
        print(f"❌ خطأ في get_recent_history_cloud: {e}")
        return []

def get_user_profile_cloud(user_id):
    """جلب الذاكرة طويلة المدى والعداد"""
    try:
        res = supabase.table('user_profiles').select('summary, msg_count').eq('user_id', str(user_id)).execute()
        if res.data:
            return res.data[0]['summary'], res.data[0]['msg_count']
        return "", 0
    except Exception as e:
        print(f"❌ خطأ في get_user_profile_cloud: {e}")
        return "", 0

# ==========================================
# 1. التلخيص السحابي وقانون الإحلال
# ==========================================
async def summarize_and_consolidate_memory(user_id, user_name):
    """مهمة خلفية لتلخيص واستبدال الذاكرة طويلة المدى"""
    try:
        current_summary, _ = get_user_profile_cloud(user_id)
        recent_history = get_recent_history_cloud(0, user_id, limit=20)
        if not recent_history:
            return
            
        history_text = "\n".join(recent_history)

        prompt_summary = (
            f"المطلوب: قم بتحديث وتنظيف ملف الشخصية للمستخدم ({user_name}).\n\n"
            f"الملخص الحالي في الذاكرة طويلة المدى:\n{current_summary if current_summary else 'لا يوجد'}\n\n"
            f"المحادثات الأخيرة:\n{history_text}\n\n"
            "قواعد التحديث والإحلال البشرية:\n"
            "1. **الإحلال والاستبدال:** إذا ظهرت معلومة جديدة انسجامية تتناقض مع حقيقة قديمة، احذف القديمة واستبدلها فوراً بالحديثة.\n"
            "2. **حذف التفاصيل اللحظية:** امسح أي مواقف أو أحداث عابرة انتهت ولا أهمية لها مستقبلاً.\n"
            "3. **الهيكل المطلوب:** رتب الملف البشري في 4 محاور (الهوية الأساسية، الاهتمامات، الطباع، الحقائق القائمة).\n"
            "4. اكتب النتيجة بشكل نقاط مكثفة ومختصرة جداً دون تكرار."
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_summary}]}],
            "generationConfig": {"maxOutputTokens": 180, "temperature": 0.2}
        }

        async with aiohttp.ClientSession() as session:
            for model in FREE_MODELS:
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                try:
                    async with session.post(gemini_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get('candidates', [])
                            if candidates:
                                new_summary = candidates[0].get('content', {}).get('parts', [])[0].get('text', '').strip()
                                
                                supabase.table('user_profiles').update({'summary': new_summary, 'msg_count': 0}).eq('user_id', str(user_id)).execute()
                                print(f"☁️ [Supabase] تم تحديث وإحلال ذاكرة المستخدم: {user_name}")
                                break
                except Exception as e:
                    print(f"⚠️ خطأ أثناء تحديث الذاكرة نموذج {model}: {e}")
                    continue
    except Exception as e:
        print(f"❌ خطأ عام في عملية التلخيص: {e}")

async def schedule_inactivity_summary(user_id, user_name, delay_seconds=900):
    """مؤقت الخمول (15 دقيقة) لتلخيص السالفة بعد توقف المحادثة"""
    try:
        await asyncio.sleep(delay_seconds)
        print(f"⏱️ [مؤقت الخمول] مرّت 15 دقيقة صمت. جاري تحديث ذاكرة {user_name}...")
        await summarize_and_consolidate_memory(user_id, user_name)
    except asyncio.CancelledError:
        pass

# ==========================================
# 2. خادم الويب (لإبقاء Render نشطاً)
# ==========================================
async def handle_ping(request):
    return web.Response(text="ENA Bot is Online!", status=200)

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
# 3. الاتصال بـ Gemini API مع جلب الذاكرة
# ==========================================
async def ask_ena_brain(channel_id, user_id, prompt, user_name):
    try:
        long_term_memory, msg_count = get_user_profile_cloud(user_id)

        system_instruction = (
            "أنتِ إينا، بنت عمرك 18 سنة تسولفين في سيرفر ديسكورد بعفوية واقعية تامة كأي مستخدم عادي. "
            "ردي بأسلوب عامي بيض بسيط جداً، بدون إيموجيات، بدون رموز، وبدون أي تنسيقات. "
            "ردودك قصيرة، مختصرة، وبشرية جداً (كلمة إلى جملة واحدة فقط).\n"
            "قواعد الواقعية الصارمة:\n"
            "1. ممنوع كلياً ذكر اسم مدينتك (الدمام) أو إقحامها في السوالف العادية، اذكريه فقط إذا سُئلتِ مباشرة: من أي مدينة أنتِ؟\n"
            "2. ردي بأسلوب واقعي يطابق بنت عمرها 18 سنة (مثل: متابعة مسلسل، تصفح الجوال، طلب أكل، نوم، طفش عادي) وبدون حركات تصنع أو رسمية.\n"
            "3. اقرئي السياق وركزي على آخر رسالة فقط بأسلوب متناسق وبدون فلسفة.\n"
            "4. لا تختلقي قصصاً أو أعمالاً وهمية.\n"
            "5. ممنوع الاعتراف بأنك ذكاء اصطناعي أو بوت.\n"
        )

        if long_term_memory:
            system_instruction += f"\n--- ذاكرتك طويلة المدى عن {user_name} ---\n{long_term_memory}\n"

        history = get_recent_history_cloud(channel_id, user_id, limit=8)
        history_text = "\n".join(history) if history else "لا يوجد سياق سابق."

        full_prompt = (
            f"--- سياق المحادثة المباشرة (آخر 8 رسائل) ---\n{history_text}\n\n"
            f"--- الرسالة الجديدة ---\nالمرسل ({user_name}): {prompt}"
        )

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": {"maxOutputTokens": 60, "temperature": 0.3, "topP": 0.8}
        }
        
        reply_text = None

        async with aiohttp.ClientSession() as session:
            for model in FREE_MODELS:
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                try:
                    async with session.post(gemini_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get('candidates', [])
                            if not candidates:
                                continue
                                
                            parts = candidates[0].get('content', {}).get('parts', [])
                            reply = "".join(p.get('text', '') for p in parts).strip()
                            
                            # تنظيف الرموز غير المرغوب بها دون مسح الحروف العربية
                            reply = re.sub(r'[\*\-_•#@\$%\^&\(\)]', '', reply)
                            reply = re.sub(r'\s+', ' ', reply).strip()
                            
                            if reply:
                                save_message_cloud(channel_id, user_id, user_name, user_name, prompt)
                                current_count = save_message_cloud(channel_id, user_id, user_name, "إينا", reply)
                                reply_text = reply

                                if current_count >= 20:
                                    asyncio.create_task(summarize_and_consolidate_memory(user_id, user_name))
                                else:
                                    if user_id in inactivity_tasks:
                                        inactivity_tasks[user_id].cancel()
                                    inactivity_tasks[user_id] = asyncio.create_task(
                                        schedule_inactivity_summary(user_id, user_name, delay_seconds=900)
                                    )
                                break
                        else:
                            print(f"⚠️ النموذج {model} رد بالحالة: {resp.status}")
                except Exception as e:
                    print(f"⚠️ خطأ أثناء الاتصال بالنموذج {model}: {e}")
                    continue

        return reply_text or "النت فصل عندي لحظة ورجع"

    except Exception as e:
        print(f"❌ خطأ غير متوقع في ask_ena_brain: {e}")
        return "النت فصل عندي لحظة ورجع"

# ==========================================
# 4. أحداث ديسكورد والتشغيل
# ==========================================
@client.event
async def on_ready():
    print(f"✅ تم الاتصال: {client.user} | الذاكرة السحابية متصلة ومفعّلة مع Supabase")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    if client.user in message.mentions or is_dm:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip() or "مرحباً"
        user_name = message.author.display_name
        user_id = message.author.id

        print(f"\n📥 [رسالة من {user_name} ({user_id})]: {clean_prompt}")

        async with message.channel.typing():
            reply = await ask_ena_brain(message.channel.id, user_id, clean_prompt, user_name)
            await message.reply(reply[:2000], mention_author=False)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
