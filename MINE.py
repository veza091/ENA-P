import asyncio
import json
import os
import re
import ssl
import sys
from collections import defaultdict

# إجبار بايثون على إفراغ السجلات لحظياً على Render دون انتظار (Unbuffered output)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

import aiohttp
import asyncpg
import discord
from aiohttp import web
from dotenv import load_dotenv


# =========================================================
# الإعدادات
# =========================================================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
MAIN_GEMINI_API_KEY = (
    os.getenv("MAIN_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
).strip()
MEMORY_GEMINI_API_KEY = (
    os.getenv("MEMORY_GEMINI_API_KEY") or MAIN_GEMINI_API_KEY
).strip()

# رابط PostgreSQL من Supabase Session Pooler (port 5432)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# تفعيل SSL (افتراضياً مفعل إذا كان الرابط Supabase)
DATABASE_SSL_ENV = os.getenv("DATABASE_SSL", "").strip().lower()
DATABASE_SSL = DATABASE_SSL_ENV in {"1", "true", "yes"} or "supabase" in DATABASE_URL

if not DISCORD_TOKEN or not MAIN_GEMINI_API_KEY:
    print("❌ يلزم ضبط DISCORD_TOKEN و MAIN_GEMINI_API_KEY في متغيرات البيئة.", flush=True)
    sys.exit(1)


def configured_models(variable_name, default_models):
    """يسمح بتبديل النماذج من Render من دون تعديل الكود."""
    configured = os.getenv(variable_name, "")
    return [name.strip() for name in configured.split(",") if name.strip()] or default_models


def bounded_setting(variable_name, default, minimum, maximum):
    try:
        value = int(os.getenv(variable_name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


MAIN_MODELS = configured_models(
    "MAIN_GEMINI_MODELS",
    ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"],
)
MEMORY_MODELS = configured_models(
    "MEMORY_GEMINI_MODELS",
    ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
)

HISTORY_LIMIT = bounded_setting("HISTORY_LIMIT", 12, 4, 30)
MEMORY_LIMIT = bounded_setting("MEMORY_LIMIT", 25, 5, 100)

db_pool = None
memory_locks = defaultdict(asyncio.Lock)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"<@!?\d+>", "", text)
    text = re.sub(r"[\*\-_•#@\$%\^&\(\)]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def memories_to_prompt(memories):
    if not memories:
        return "لا توجد معلومات ثابتة محفوظة عنه بعد."
    return "\n".join(
        f"- {memory['memory_key']}: {memory['memory_value']}"
        for memory in memories
    )


# =========================================================
# خادم الويب (Render Port Binding & Health Check)
# =========================================================
async def handle_ping(request):
    database_state = "ready" if db_pool is not None else "unavailable"
    return web.json_response({
        "status": "ok" if db_pool is not None else "degraded",
        "database": database_state,
        "service": "ENA Discord Bot"
    })


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🌐 [Web Server] يعمل على المنفذ: {port}", flush=True)


# =========================================================
# PostgreSQL: رسائل + ذاكرة منظمة مع Supabase
# =========================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    discord_user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    channel_id TEXT NOT NULL,
    discord_user_id TEXT NOT NULL REFERENCES users(discord_user_id)
        ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'model')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS messages_user_channel_created_idx
    ON messages (discord_user_id, channel_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL REFERENCES users(discord_user_id)
        ON DELETE CASCADE,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'profile',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.7
        CHECK (confidence >= 0 AND confidence <= 1),
    source_message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (discord_user_id, memory_key)
);
CREATE INDEX IF NOT EXISTS memories_user_updated_idx
    ON memories (discord_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS memories_user_conf_idx
    ON memories (discord_user_id, confidence DESC, updated_at DESC);

-- تفعيل حماية RLS لمنع وصول واجهات Supabase العامة (REST API) لذاكرة المستخدمين
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;

-- حظر الوصول لأدوار anon و authenticated في Supabase إذا كانت موجودة
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE users, messages, memories FROM anon;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE users, messages, memories FROM authenticated;
    END IF;
END $$;
"""


async def initialize_database():
    """يفتح اتصالاً بـ Supabase Postgres وينشئ الجداول والفهارس عند الإقلاع."""
    global db_pool

    if not DATABASE_URL:
        print("❌ [Postgres] لم يتم العثور على DATABASE_URL في متغيرات البيئة! الذاكرة الدائمة لن تعمل.", flush=True)
        return

    # فحص نوع الرابط للتنبيه
    if "pooler.supabase.com" not in DATABASE_URL and "supabase" in DATABASE_URL:
        print(
            "⚠️ [Postgres تحذير] الرابط المستخدم يبدو أنه Direct connection وليس Session Pooler!\n"
            "   خدمة Render تدعم IPv4 فقط وقد يفشل الاتصال. يرجى استخدام رابط Session Pooler على المنفذ 5432.",
            flush=True,
        )

    # ضبط إعدادات الاتصال
    # مهم: statement_cache_size=0 ضروري عند التعامل مع PgBouncer / Supabase Pooler
    pool_options = {
        "min_size": 1,
        "max_size": 5,
        "command_timeout": 15,
        "statement_cache_size": 0,
    }

    if DATABASE_SSL:
        pool_options["ssl"] = "require"

    # تصحيح بداية الرابط إذا بدأ بـ postgres://
    dsn = DATABASE_URL
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]

    print("🔌 [Postgres] جاري الاتصال بقاعدة البيانات وإنشاء الجداول...", flush=True)

    # محاولة الاتصال مع إعادة المحاولة في حال تأخر الاستجابة
    for attempt in range(1, 4):
        try:
            db_pool = await asyncpg.create_pool(dsn, **pool_options)
            async with db_pool.acquire() as connection:
                await connection.execute("SELECT 1")
                await connection.execute(SCHEMA_SQL)
            print("✅ [Postgres] تم الاتصال بنجاح وتجهيز جداول (users, messages, memories) وتفعيل حماية RLS.", flush=True)
            return
        except Exception as error:
            print(f"⚠️ [Postgres] المحاولة {attempt}/3 فشلت: {error}", flush=True)
            if db_pool:
                await db_pool.close()
                db_pool = None
            if attempt < 3:
                await asyncio.sleep(2)
            else:
                print(
                    "❌ [Postgres] تعذر الاتصال نهائياً بقاعدة البيانات!\n"
                    "   تأكد من صحة DATABASE_URL (Session Pooler: pooler.supabase.com:5432) وصحة كلمة المرور.",
                    flush=True,
                )


async def upsert_user(user_id, user_name):
    if db_pool is None:
        return
    async with db_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users (discord_user_id, display_name)
            VALUES ($1, $2)
            ON CONFLICT (discord_user_id) DO UPDATE
            SET display_name = EXCLUDED.display_name, updated_at = NOW()
            """,
            str(user_id),
            user_name,
        )


async def save_message(channel_id, user_id, role, content):
    if db_pool is None:
        return None
    async with db_pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO messages (channel_id, discord_user_id, role, content)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            str(channel_id),
            str(user_id),
            role,
            content,
        )


async def get_recent_history(channel_id, user_id, limit=HISTORY_LIMIT):
    if db_pool is None:
        return []
    async with db_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT role, content
            FROM messages
            WHERE channel_id = $1 AND discord_user_id = $2
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """,
            str(channel_id),
            str(user_id),
            limit,
        )
    return list(reversed(rows))


async def get_memories(user_id, limit=MEMORY_LIMIT):
    if db_pool is None:
        return []
    async with db_pool.acquire() as connection:
        return await connection.fetch(
            """
            SELECT memory_key, memory_value, category, confidence
            FROM memories
            WHERE discord_user_id = $1
            ORDER BY confidence DESC, updated_at DESC
            LIMIT $2
            """,
            str(user_id),
            limit,
        )


async def save_memories(user_id, source_message_id, memories):
    """يحدث حقيقة واحدة فقط، ولا يعيد كتابة ذاكرة المستخدم كاملة."""
    if db_pool is None or not memories:
        return 0

    saved_count = 0
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            for memory in memories:
                if not valid_memory(memory):
                    continue
                await connection.execute(
                    """
                    INSERT INTO memories (
                        discord_user_id, memory_key, memory_value, category,
                        confidence, source_message_id
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (discord_user_id, memory_key) DO UPDATE
                    SET memory_value = EXCLUDED.memory_value,
                        category = EXCLUDED.category,
                        confidence = EXCLUDED.confidence,
                        source_message_id = EXCLUDED.source_message_id,
                        updated_at = NOW()
                    """,
                    str(user_id),
                    memory["key"],
                    memory["value"],
                    memory["category"],
                    memory["confidence"],
                    source_message_id,
                )
                saved_count += 1
    return saved_count


def valid_memory(memory):
    if not isinstance(memory, dict):
        return False
    key = memory.get("key")
    value = memory.get("value")
    category = memory.get("category", "profile")
    confidence = memory.get("confidence", 0.7)
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", key):
        return False
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return False
    if not isinstance(category, str) or not re.fullmatch(r"[a-z_]{1,30}", category):
        return False
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return False
    return True


# =========================================================
# Gemini: الاتصال واستخراج الذاكرة المنظمة
# =========================================================
async def request_gemini(models, api_key, payload, label):
    async with aiohttp.ClientSession() as session:
        for model in models:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            try:
                async with session.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key,
                    },
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    if response.status != 200:
                        err_text = await response.text()
                        print(f"⚠️ [{label}] {model}: الحالة {response.status}: {err_text}", flush=True)
                        continue
                    data = await response.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    result = "".join(part.get("text", "") for part in parts).strip()
                    if result:
                        return result
            except Exception as error:
                print(f"⚠️ [{label}] تعذر الاتصال بـ {model}: {error}", flush=True)
    return None


def parse_memory_json(text):
    if not text:
        return []
    cleaned = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)
    else:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    memories = payload.get("memories", []) if isinstance(payload, dict) else []
    return [memory for memory in memories if valid_memory(memory)]


async def update_memory_in_background(user_id, user_name, recent_history, source_message_id):
    """مهمة خلفية لاستخراج الحقائق من الحديث وحفظها في Supabase لكل مستخدم."""
    if db_pool is None or not MEMORY_GEMINI_API_KEY:
        return

    async with memory_locks[str(user_id)]:
        conversation = "\n".join(
            f"{'المستخدم' if message['role'] == 'user' else 'إينا'}: {message['content']}"
            for message in recent_history
        )
        prompt = f"""
أنت جزء الذاكرة في بوت Discord. استخرج فقط الحقائق الثابتة والمفيدة عن المستخدم: {user_name}.
لا تخزن التحية، الأسئلة العابرة، التوقعات، المعلومات الحساسة، أو أي شيء غير مؤكد.
إذا لم توجد حقيقة جديدة أو تصحيح واضح، أعد قائمة فارغة.

أعد JSON صالحاً فقط بهذا الشكل:
{{"memories":[{{"key":"english_snake_case","value":"حقيقة قصيرة بالعربية","category":"profile_or_preference_or_interest","confidence":0.0}}]}}

القواعد:
- يجب أن يكون key ثابتاً ومحدداً، مثل preferred_language أو main_interest.
- لا تضع أكثر من 3 حقائق.
- confidence رقم بين 0 و1، ولا تقل عن 0.7.
- عند تعارض معلومة جديدة مع قديمة ذات المفتاح نفسه، أعد القيمة الجديدة فقط.

المحادثة الأخيرة:
{conversation}
"""
        result = await request_gemini(
            MEMORY_MODELS,
            MEMORY_GEMINI_API_KEY,
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 250,
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            },
            "Memory",
        )
        memories = parse_memory_json(result)
        saved_count = await save_memories(user_id, source_message_id, memories)
        if saved_count:
            print(f"🧠 [Memory] تم حفظ {saved_count} حقيقة للمستخدم {user_name}.", flush=True)


# =========================================================
# Gemini: الرد الرئيسي
# =========================================================
async def ask_ena_brain(clean_prompt, user_name, memories, history_messages):
    system_instruction = (
        "أنتِ إينا، بنت عمرك 18 سنة تسولفين في ديسكورد بعفوية واقعية كأي مستخدم عادي. "
        "ردي بأسلوب عامي بيض بسيط جداً، بدون إيموجيات، بدون رموز، وبدون أي تنسيقات. "
        "ردودك قصيرة، مختصرة، وبشرية جداً (كلمة إلى جملة واحدة فقط).\n\n"
        f"[معلومات ثابتة عن {user_name}]:\n{memories_to_prompt(memories)}\n\n"
        "استخدمي المعلومات فقط عندما تكون ذات صلة، ولا تدّعي معرفة شيء غير مذكور.\n"
        "لا تختلقي قصصاً وهمية."
    )

    contents = [
        {
            "role": "model" if message["role"] == "model" else "user",
            "parts": [{"text": message["content"]}],
        }
        for message in history_messages
    ]
    if not contents or contents[-1]["parts"][0]["text"] != clean_prompt:
        contents.append({"role": "user", "parts": [{"text": clean_prompt}]})

    result = await request_gemini(
        MAIN_MODELS,
        MAIN_GEMINI_API_KEY,
        {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 60, "temperature": 0.75, "topP": 0.9},
        },
        "Brain",
    )
    return clean_text(result) or "هلا"


# =========================================================
# أحداث Discord
# =========================================================
@client.event
async def on_ready():
    db_status = "متصلة ✅" if db_pool is not None else "غير متصلة ❌"
    print(f"✅ تم الاتصال بنجاح بحساب ديسكورد: {client.user}", flush=True)
    print(f"📊 حالة قاعدة البيانات: {db_status}", flush=True)


@client.event
async def on_message(message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    if client.user not in message.mentions and not is_dm:
        return

    user_id = str(message.author.id)
    user_name = message.author.display_name
    clean_prompt = re.sub(r"<@!?%s>" % client.user.id, "", message.content).strip() or "هلا"

    try:
        await upsert_user(user_id, user_name)
        user_message_id = await save_message(message.channel.id, user_id, "user", clean_prompt)
        recent_history = await get_recent_history(message.channel.id, user_id)
        memories = await get_memories(user_id)

        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name, memories, recent_history)
            await message.reply(reply, mention_author=False)

        await save_message(message.channel.id, user_id, "model", reply)

        # تبدأ في الخلفية بعد الرد لعدم تأخير استجابة ديسكورد
        if user_message_id is not None:
            asyncio.create_task(
                update_memory_in_background(user_id, user_name, recent_history, user_message_id)
            )
    except Exception as error:
        print(f"❌ [Message Error] {error}", flush=True)
        await message.reply("صار عندي تعليق بسيط، جرب مرة ثانية.", mention_author=False)


# =========================================================
# نقطة الدخول الرئيسية
# =========================================================
async def main():
    # 1. تهيئة قاعدة البيانات أولاً قبل تشغيل ديسكورد
    await initialize_database()
    # 2. تشغيل خادم الويب للربط مع منفذ Render
    await start_web_server()
    # 3. تشغيل عميل ديسكورد
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.", flush=True)

