# -*- coding: utf-8 -*-
"""
==========================================================================
 ENA — بوت ديسكورد ذكي | النسخة 2.2 (إصلاح تكرار Gemma النهائي)
==========================================================================
 • حل مشكلة التكرار (Echoing) الخاصة بنماذج Gemma نهائياً
 • حماية برمجية ترفض الرد إذا كان مطابقاً لرسالة المستخدم
 • ذاكرة سياق منزلقة لكل قناة (collections.deque)
 • خادم aiohttp مدمج للإبقاء على البوت حياً 24/7
==========================================================================
"""

import os
import re
import sys
import time
import random
import asyncio
import unicodedata
from collections import deque, defaultdict

import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# ==========================================================================
# 1) الإعدادات العامة
# ==========================================================================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("OPENROUTER_API_KEY") or "").strip()

MODEL_NAME = os.getenv("MODEL_NAME", "gemma-4-31b-it")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

MAX_OUTPUT_TOKENS = 80     # حد مناسب لردود قصيرة
REPLY_WORD_LIMIT  = 15     # سقف الكلمات
CONTEXT_MESSAGES  = 6      # آخر 6 رسائل لكل قناة
REQUEST_TIMEOUT   = 25     # مهلة نداء API بالثواني
USER_COOLDOWN     = 3      # فترة حماية بين الطلبات

if not DISCORD_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ قاتل: DISCORD_TOKEN أو GEMINI_API_KEY غير موجودة في .env !")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

http_session: aiohttp.ClientSession | None = None

# ==========================================================================
# 2) ذاكرة السياق
# ==========================================================================
conversation_memory: dict[int, deque] = defaultdict(lambda: deque(maxlen=CONTEXT_MESSAGES))
_last_request_times: dict[int, float] = {}

# ==========================================================================
# 3) هوية إينا + الأمثلة التدريبية
# ==========================================================================
# تم صياغة البرومبت بدون ذكر أرقام شروط كي لا يهلوس بها النموذج
SYSTEM_PROMPT = (
    "أنتِ إينا، بنت تسولف في ديسكورد بعفوية كأنك قاعدة مع ربعك. "
    "تتكلمين بالعامية العربية فقط، وردودك قصيرة ومباشرة وعفوية. "
    "ممنوع تكرار كلام الشخص الذي يكلمك، وممنوع كتابة أي أرقام أو قواعد."
)

FIRST_MODEL_ACK = "تمام، أنا إينا وجاهزة أسولف معكم بعفوية بدون ما أكرر كلام أحد!"

FEW_SHOT_EXAMPLES = [
    ("كيف حالك؟", "بخير الحمد لله! أنت كيف يومك؟"),
    ("الو", "أهلين! سم وش عندك؟"),
    ("وش تسوين؟", "قاعدة أسولف معكم بالديسكورد ومروقة ☕"),
    ("اشرح لي الكاميرا بأسلوب مرقم", "وش دراني بالشرح؟ أنا بنت أسولف مو أكتب تقارير 😂"),
]

FALLBACK_REPLIES = [
    "النت فصل عندي لحظة، عيد كلامك 🥺",
    "شرود لحظة سامحني، وش كنت تقول؟",
    "ضاعت السالفة بيني وبينك، كررها 🙈",
]

def _fallback() -> str:
    return random.choice(FALLBACK_REPLIES)

# ==========================================================================
# 4) خط التنظيف (Sanitization Pipeline)
# ==========================================================================
_EMOJI_RANGES = (
    r"\U0001F300-\U0001F5FF" r"\U0001F600-\U0001F64F" r"\U0001F680-\U0001F6FF"
    r"\U0001F900-\U0001F9FF" r"\U0001FA70-\U0001FAFF" r"\u2600-\u27BF"
)

_ALLOWED = re.compile(
    r"[^\u0621-\u064A\u064B-\u0652\u0670\u0660-\u0669"
    r"\u060C\u061B\u061F!.\s" + _EMOJI_RANGES + r"]"
)

_URL_RE      = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|net|org|gg|io|xyz|tv|me)\S*", re.IGNORECASE)
_MENTION_RE  = re.compile(r"<@!?&?\d+>|<#\d+>")
_CODE_RE     = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)
_LEAK_PREFIX = re.compile(r"^\s*[A-Za-z\u0621-\u064A0-9٠-٩_ ]{1,25}\s*[:：\-–]\s*")
_ENGLISH_RE  = re.compile(r"[A-Za-z]+")
_DIGIT_TRANS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def sanitize_reply(raw: str) -> str:
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)
    text = _CODE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _MENTION_RE.sub(" ", text)

    for _ in range(2):
        cleaned = _LEAK_PREFIX.sub("", text)
        if cleaned == text:
            break
        text = cleaned

    text = text.replace("?", "؟").replace(",", "،").replace(";", "؛")
    text = text.translate(_DIGIT_TRANS)
    text = _ENGLISH_RE.sub("", text)
    text = _ALLOWED.sub("", text)

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([،؛؟!.])", r"\1", text)
    text = re.sub(r"([!؟])\1{2,}", r"\1\1", text)
    return text.strip()


def trim_reply(text: str, max_words: int = REPLY_WORD_LIMIT) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip()

# ==========================================================================
# 5) بناء الـ Payload (متوافق 100% مع Gemma)
# ==========================================================================
def _append_turn(contents: list, role: str, text: str) -> None:
    if not text:
        return
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"][0]["text"] += "\n" + text
    else:
        contents.append({"role": role, "parts": [{"text": text}]})


def build_contents(user_turn: str, history: deque) -> list:
    # إدراج التعليمات والهوية داخل contents مباشرة لأن Gemma لا يقرأ systemInstruction
    contents: list = [
        {"role": "user",  "parts": [{"text": SYSTEM_PROMPT}]},
        {"role": "model", "parts": [{"text": FIRST_MODEL_ACK}]},
    ]
    
    # إدراج الأمثلة التدريبية
    for question, answer in FEW_SHOT_EXAMPLES:
        contents.append({"role": "user",  "parts": [{"text": question}]})
        contents.append({"role": "model", "parts": [{"text": answer}]})
        
    # إدراج المحادثة السابقة
    for role, text in history:
        _append_turn(contents, role, text)
        
    # إدراج رسالة المستخدم الحالية مع توجيه صريح لمنع التكرار
    _append_turn(contents, "user", user_turn)
    return contents


def _extract_text(data: dict) -> tuple[str, str]:
    candidates = data.get("candidates") or []
    if not candidates:
        return "", ""
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    return text, candidate.get("finishReason", "")

# ==========================================================================
# 6) الاستعلام من النموذج
# ==========================================================================
async def ask_ena_brain(user_name: str, user_text: str, history: deque) -> str:
    payload = {
        "contents": build_contents(user_text, history),
        "generationConfig": {
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "temperature": 0.7,
            "topP": 0.9,
            "topK": 40,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    for attempt in range(2):
        try:
            async with http_session.post(API_URL, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    raw, finish_reason = _extract_text(data)
                    if not raw:
                        return _fallback()
                    
                    reply = trim_reply(sanitize_reply(raw))
                    
                    # فحص حماية: إذا حاول النموذج نسخ كلام المستخدم حرفياً، يتم رفضه واستخدام الـ fallback
                    clean_user_input = re.sub(r"[^\w\s]", "", user_text).strip()
                    clean_reply_output = re.sub(r"[^\w\s]", "", reply).strip()
                    
                    if clean_reply_output == clean_user_input or not reply:
                        return _fallback()
                        
                    return reply

                body = await response.text()
                print(f"❌ [API {response.status}]: {body[:300]}")
                if response.status in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                return _fallback()

        except Exception as e:
            print(f"❌ [Error]: {e!r}")
            return _fallback()

        if attempt == 0:
            await asyncio.sleep(1)

    return _fallback()

# ==========================================================================
# 7) خادم الويب للإبقاء على البوت حياً
# ==========================================================================
async def handle_ping(request):
    return web.Response(text="ENA Discord Bot is Online & Running!", status=200)


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 [Web Server] الخادم يعمل على المنفذ: {port}")

# ==========================================================================
# 8) أحداث ديسكورد
# ==========================================================================
@client.event
async def on_ready():
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
    try:
        await client.change_presence(activity=discord.Game(name="تسمع السوالف 👀"))
    except Exception:
        pass
    print("=" * 50)
    print("✅ تم الاتصال بديسكورد بنجاح!")
    print(f"🤖 البوت: {client.user} (ID: {client.user.id})")
    print(f"🧠 النموذج: {MODEL_NAME}")
    print("=" * 50)


@client.event
async def on_message(message: discord.Message):
    try:
        if message.author.bot:
            return

        mentioned = client.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        if not (mentioned or is_dm):
            return

        now = time.monotonic()
        if now - _last_request_times.get(message.author.id, 0.0) < USER_COOLDOWN:
            return
        _last_request_times[message.author.id] = now

        prompt = _MENTION_RE.sub("", message.content).strip()[:400] or "هلا"
        user_name = message.author.display_name
        history = conversation_memory[message.channel.id]

        async with message.channel.typing():
            reply = await ask_ena_brain(user_name, prompt, history)

        await message.reply(reply, mention_author=False)

        history.append(("user", prompt))
        history.append(("model", reply))

    except Exception as e:
        print(f"❌ [on_message]: {e!r}")
        try:
            await message.reply(_fallback(), mention_author=False)
        except Exception:
            pass

# ==========================================================================
# 9) التشغيل الرئيسي
# ==========================================================================
async def main():
    global http_session
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
    await start_web_server()
    try:
        await client.start(DISCORD_TOKEN)
    finally:
        if http_session and not http_session.closed:
            await http_session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت يدوياً.")
