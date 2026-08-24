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
    print("❌ خطأ: لم يتم العثور على كافة المتغيرات المطلوبة في .env!")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# أسماء نماذج Gemini الرسمية المعتمدة
FREE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

inactivity_tasks = {}

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<@!?\d+>', '', text)
    text = re.sub(r'[\*\-_•#@\$%\^&\(\)]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ==========================================
# 0. التعامل مع Supabase (مُصحح ومُؤمن)
# ==========================================
def save_message_cloud(channel_id, user_id, user_name, role, content):
    try:
        clean_content = clean_text(content)
        if not clean_content:
            return 0

        now_str = datetime.now(timezone.utc).isoformat()
        c_id = str(channel_id)
        u_id = str(user_id)
        u_name = str(user_name)
        
        supabase.table('conversations').insert({
            'channel_id': c_id,
            'user_id': u_id,
            'user_name': u_name,
            'role': str(role),
            'content': clean_content
        }).execute()
        
        profile = supabase.table('user_profiles').select('msg_count').eq('user_id', u_id).execute()
        if profile.data:
            current_count = profile.data[0]['msg_count'] + 1
            supabase.table('user_profiles').update({
                'msg_count': current_count,
                'user_name': u_name,
                'last_active': now_str
            }).eq('user_id', u_id).execute()
        else:
            current_count = 1
            supabase.table('user_profiles').insert({
                'user_id': u_id,
                'user_name': u_name,
                'summary': '',
                'msg_count': 1,
                'last_active': now_str
            }).execute()
            
        print(f"💾 [Supabase Success] تم حفظ رسالة ({role}) للمستخدم {u_name}")
        return current_count
    except Exception as e:
        print(f"❌ [Supabase Error] فشل الحفظ في القاعدة: {e}")
        return 0

def get_recent_history_structured(channel_id, user_id, limit=8):
    """جلب المحادثات مع معالجة وتناوب الأدوار لمنع خطأ 400 من Gemini"""
    try:
        u_id = str(user_id)
        query = supabase.table('conversations').select('role, content').eq('user_id', u_id)
        
        if str(channel_id) != "0":
            query = query.eq('channel_id', str(channel_id))
            
        res = query.order('id', desc=True).limit(limit).execute()
        
        rows = res.data or []
        raw_contents = []
        for row in reversed(rows):
            role_type = "model" if row['role'] == "إينا" else "user"
            raw_contents.append({
                "role": role_type,
                "parts": [{"text": row['content']}]
            })
            
        # دمج الرسائل المتتالية ذات نفس الدور وضمان البدء بـ user
        cleaned_contents = []
        for item in raw_contents:
            if not cleaned_contents:
                if item['role'] == 'user':
                    cleaned_contents.append(item)
            else:
                if cleaned_contents[-1]['role'] == item['role']:
                    cleaned_contents[-1]['parts'][0]['text'] += f"\n{item['parts'][0]['text']}"
                else:
                    cleaned_contents.append(item)
                    
        return cleaned_contents
    except Exception as e:
        print(f"❌ [Supabase Error] فشل جلب الذاكرة: {e}")
        return []

def get_user_profile_cloud(user_id):
    try:
        res = supabase.table('user_profiles').select('summary, msg_count').eq('user_id', str(user_id)).execute()
        if res.data:
            return res.data[0]['summary'], res.data[0]['msg_count']
        return "", 0
    except Exception as e:
        print(f"❌ [Supabase Error] فشل جلب البروفايل: {e}")
        return "", 0

# ==========================================
# 1. التلخيص السحابي وقانون الإحلال
# ==========================================
async def summarize_and_consolidate_memory(user_id, user_name):
    try:
        current_summary, _ = get_user_profile_cloud(user_id)
        recent_history = get_recent_history_structured(0, user_id, limit=20)
        if not recent_history:
            return
            
        history_text = "\n".join([f"{item['role']}: {item['parts'][0]['text']}" for item in recent_history])

        prompt_summary = (
            f"المطلوب: قم بتحديث وتنظيف ملف الشخصية للمستخدم ({user_name}).\n\n"
            f"الملخص الحالي في الذاكرة طويلة المدى:\n{current_summary if current_summary else 'لا يوجد'}\n\n"
            f"المحادثات الأخيرة:\n{history_text}\n\n"
            "قواعد التحديث والإحلال البشرية:\n"
            "1. **الإحلال والاستبدال:** إذا ظهرت معلومة جديدة تناقض حقيقة قديمة، احذف القديمة واستبدلها فوراً.\n"
            "2. **حذف التفاصيل اللحظية:** امسح أي مواقف عابرة انتهت.\n"
            "3. رتب الملف في نقاط مكثفة ومختصرة جداً دون تكرار."
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
                                supabase.table('user_profiles').update({'summary': clean_text(new_summary), 'msg_count': 0}).eq('user_id', str(user_id)).execute()
                                print(f"☁️ [Supabase] تم تحديث وإحلال ذاكرة المستخدم: {user_name}")
                                break
                except Exception as e:
                    print(f"⚠️ خطأ أثناء تحديث الذاكرة نموذج {model}: {e}")
                    continue
    except Exception as e:
        print(f"❌ خطأ عام في عملية التلخيص: {e}")

async def schedule_inactivity_summary(user_id, user_name, delay_seconds=900):
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
# 3. الاتصال بـ Gemini API
# ==========================================
async def ask_ena_brain(channel_id, user_id, prompt, user_name):
    try:
        clean_prompt = clean_text(prompt)
        if not clean_prompt:
            clean_prompt = "هلا"

        # حفظ الرسالة الحالية
        save_message_cloud(channel_id, user_id, user_name, user_name, clean_prompt)
        long_term_memory, _ = get_user_profile_cloud(user_id)

        system_instruction = (
            "أنتِ إينا، بنت عمرك 18 سنة تسولفين في ديسكورد بعفوية واقعية كأي مستخدم عادي. "
            "ردي بأسلوب عامي بيض بسيط جداً، بدون إيموجيات، بدون رموز، وبدون أي تنسيقات. "
            "ردودك قصيرة، مختصرة، وبشرية جداً (كلمة إلى جملة واحدة فقط).\n"
            "قواعد الواقعية:\n"
            "1. ممنوع كلياً ذكر اسم مدينتك (الدمام) إلا إذا سُئلتِ مباشرة عنها.\n"
            "2. ردي بأسلوب واقعي يطابق بنت عمرها 18 سنة (مثل: طفش، تصفح الجوال، طلب أكل) وبدون حركات تصنع.\n"
            "3. ركزي على الرسالة الأخيرة والسياق المباشر بأسلوب بسيط.\n"
            "4. لا تختلقي قصصاً وهمية وممنوع كلياً الاعتراف بأتك ذكاء اصطناعي أو بوت."
        )

        if long_term_memory:
            system_instruction += f"\nملاحظات تفهمينها عن الطرف الآخر ({user_name}):\n{long_term_memory}"

        # جلب المحادثات السابقة المنظمة
        contents = get_recent_history_structured(channel_id, user_id, limit=6)
        
        # التأكد من عدم إرسال مصفوفة فارغة لـ Gemini
        if not contents:
            contents = [{"role": "user", "parts": [{"text": clean_prompt}]}]

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 40,
                "temperature": 0.75,
                "topP": 0.9
            }
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
                            reply = "".join(p.get('text', '') for p in parts)
                            reply = clean_text(reply)
                            
                            if reply:
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
                            err_body = await resp.text()
                            print(f"⚠️ النموذج {model} رد بالحالة {resp.status}: {err_body}")
                except Exception as e:
                    print(f"⚠️ خطأ أثناء الاتصال بالنموذج {model}: {e}")
                    continue

        return reply_text or "هلا"

    except Exception as e:
        print(f"❌ خطأ غير متوقع في ask_ena_brain: {e}")
        return "هلا"

# ==========================================
# 4. أحداث ديسكورد
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
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip() or "هلا"
        user_name = message.author.display_name
        user_id = message.author.id

        print(f"\n📥 [رسالة من {user_name} ({user_id})]: {clean_prompt}")

        async with message.channel.typing():
            reply = await ask_ena_brain(message.channel.id, user_id, clean_prompt, user_name)
            await message.reply(reply, mention_author=False)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
