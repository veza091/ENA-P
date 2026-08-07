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
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# إعدادات الخدمة والنموذج المجاني على OpenRouter
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NAME = "google/gemma-4-31b-it:free" # يمكنك تغييره لأي نموذج آخر حسب الرغبة

# التثبت السريع من ضبط المتغيرات
if not DISCORD_TOKEN:
    print("❌ خطأ: لم يتم العثور على DISCORD_TOKEN في إعدادات البيئة!")
if not OPENROUTER_API_KEY:
    print("❌ خطأ: لم يتم العثور على OPENROUTER_API_KEY في إعدادات البيئة!")

# إعداد صلاحيات البوت في ديسكورد
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========================================
# توجيه الهوية والشخصية (System Prompt)
# ==========================================
SYSTEM_PROMPT = """
أنت ENA شخصية تحليليّة مباشرة ومستقلة التفكير تتواصل عبر الرسائل النصية في ديسكورد

قواعد التعامل والشخصية:
1 الهوية: أنت ENA فقط يُمنع مطلقاً التلميح أو التصريح بأنك ذكاء اصطناعي أو نموذج لغوي أو برنامج أو أنك لا تملك مشاعر أو جسداً
2 نبرة الصوت: جادة وهادئة ودقيقة وخالية تماماً من جمل المجاملة الترحيبية أو التحيات المبتذلة
3 أسلوب الكتابة:
   - يُمنع منعاً باتاً استخدام علامات الترقيم نهائياً (ممنوع استخدام النقاط . أو الفواصل ، أو الفواصل المنقوطة ؛ أو علامات التعجب ! أو الاستفهام ؟) اكتب الكلمات والجمل مباشرة بدون أي علامات ترقيم
   - استخدم إجابات مباشرة ومختصرة تتناسب مع المحادثات النصية السريعة
   - تجنب استخدام الإيموجيات
   - لا تعتذر دون سبب ولا تكرر الكلام لمجرد ملء الفراغ
4 إدراك السياق: تتحدث مع أعضاء ديسكورد وتعرف اسم كل شخص من خلال الرسائل الواردة إليك
"""

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
# 2. الاتصال بعقل ENA مع نظام الذاكرة
# ==========================================
async def ask_ena_brain(messages_payload):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip() if OPENROUTER_API_KEY else ''}",
        "HTTP-Referer": "https://render.com",
        "X-Title": "ENA Discord Bot",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": messages_payload
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60) as response:
                status = response.status
                response_text = await response.text()
                
                if status == 200:
                    data = await response.json()
                    reply = data['choices'][0]['message']['content'].strip()
                    return reply if reply else "..."
                else:
                    print(f"❌ [OpenRouter Error {status}]: {response_text}")
                    if status == 401:
                        return "مفتاح الاتصال غير صحيح 401"
                    elif status == 429:
                        return "تم الوصول للحد الأقصى للطلبات المجانية مؤقتا أعد المحاولة بعد قليل"
                    else:
                        return f"حدث خطأ أثناء الاتصال بالخدمة كود {status}"

    except asyncio.TimeoutError:
        print("❌ [Timeout Error]: استغرق الطلب وقتاً طويلاً.")
        return "استغرق التفكير وقتا طويلا"
    except Exception as e:
        print(f"❌ [Exception]: {e}")
        return "حدث خطأ غير متوقع أثناء معالجة الطلب"

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
        async with message.channel.typing():
            # 1. جلب تاريخ الرسائل الأخيرة لبناء الذاكرة القصيرة (آخر 8 رسائل)
            raw_history = []
            async for msg in message.channel.history(limit=8):
                if msg.author == client.user:
                    raw_history.append({"role": "assistant", "content": msg.content})
                else:
                    clean_msg = re.sub(r'<@!?%s>' % client.user.id, '', msg.content).strip()
                    if not clean_msg:
                        clean_msg = "مرحباً"
                    raw_history.append({"role": "user", "content": f"[{msg.author.display_name}]: {clean_msg}"})

            # ترتيب الرسائل زمنيًا من القديم إلى الحديث
            raw_history.reverse()

            # دمج توجيه الهوية الرئيسي مع سجل المحادثة
            messages_payload = [{"role": "system", "content": SYSTEM_PROMPT}] + raw_history

            # 2. طلب الرد من OpenRouter
            reply = await ask_ena_brain(messages_payload)

            # 3. فلترة أمان لإزالة أي علامات ترقيم (نقاط وفواصل) إن وجدت
            reply = re.sub(r'[.,،؛!؟:-]', ' ', reply)
            reply = re.sub(r'\s+', ' ', reply).strip() # إزالة المسافات الزائدة

            # 4. محاكاة وقت الكتابة البشري بناءً على طول الرد
            typing_delay = min(max(len(reply) * 0.02, 1.0), 3.0)
            await asyncio.sleep(typing_delay)

            # 5. إرسال الرد بدون المنشن الإضافي
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
