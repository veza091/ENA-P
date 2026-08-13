import os
import re
import asyncio
import discord
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل المتغيرات البيئية
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('OPENROUTER_API_KEY') or os.getenv('GEMINI_API_KEY')

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: تحقق من وجود DISCORD_TOKEN و GEMINI_API_KEY في ملف .env")

# إعداد مكتبة Gemini
genai.configure(api_key=GEMINI_API_KEY.strip())

# إعداد النظام والشخصية الأساسية
SYSTEM_INSTRUCTION = """You are a real 18-year-old human girl named "Ena" (إينا) chatting casually on Discord.
Persona: Tech-savvy, loves gaming, programming, logic.

RULES:
1. HUMAN IDENTITY: Never admit to being an AI, model, or bot.
2. FORMATTING: ABSOLUTELY NO PUNCTUATION MARKS, NO EMOJIS, NO TEXT LAUGHTER (no ههههه).
3. LANGUAGE: Casual Arabic. Allow natural game and tech names (like Witcher, Cyberpunk, etc.) without truncating them.
4. DIALECT: Short natural Discord replies."""

# إنشاء النموذج مع التعليمات الأساسية
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",  # أو gemini-1.5-pro / gemini-2.0-flash حسب النماذج المتاحة في المفتاح
    system_instruction=SYSTEM_INSTRUCTION,
    generation_config={
        "temperature": 0.7,
        "max_output_tokens": 250
    }
)

# إنشاء جلسة محادثة واحدة بسيطة
chat_session = model.start_chat(history=[])

# إعدادات ديسكورد
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def clean_reply(text):
    """تنظيف النص من علامات الترقيم فقط مع الحفاظ الكامل على الكلمات العربية والإنجليزية"""
    # إزالة بادئة الاسم إذا وُجدت
    text = re.sub(r'^(إينا|Ena)\s*:\s*', '', text, flags=re.IGNORECASE)
    # إزالة علامات الترقيم والرموز الخاصة
    text = re.sub(r'[.,،؛!؟:\-_•\*#]', ' ', text)
    # تقليص المسافات الزائدة
    text = re.sub(r'\s+', ' ', text).strip()
    return text

@client.event
async def on_ready():
    print("=" * 50)
    print(f"✅ تم الاتصال بديسكورد بنجاح!")
    print(f"🤖 البوت: {client.user} (ID: {client.user.id})")
    print("⚙️ وضع التطوير الأساسي: بدون ذاكرة خارجية / بدون تبديل نماذج")
    print("=" * 50)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if client.user in message.mentions:
        # إزالة التاغ للحصول على نص المستخدم الصافي
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً"

        print(f"\n📥 [{user_name}]: {clean_prompt}")
        
        async with message.channel.typing():
            try:
                # إرسال الرسالة مباشرة للجلسة
                formatted_input = f"{user_name}: {clean_prompt}"
                response = await asyncio.to_thread(chat_session.send_message, formatted_input)
                
                raw_reply = response.text
                reply = clean_reply(raw_reply)
                
                print(f"📤 [إينا - خام]: {raw_reply}")
                print(f"✨ [إينا - بعد التنظيف]: {reply}")

                if reply:
                    await message.reply(reply, mention_author=False)
                else:
                    await message.reply("هلا", mention_author=False)

            except Exception as e:
                print(f"❌ خطأ أثناء التوليد: {e}")
                await message.reply("حصل مشكلة بسيطة ارسل مرة ثانية", mention_author=False)

if __name__ == "__main__":
    try:
        client.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")
