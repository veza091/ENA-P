import asyncio
import json
import os
import re
import sys
from collections import defaultdict

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

# رابط PostgreSQL من Supabase أو Render. لا يوضع في GitHub.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# فعّله عند استخدام Supabase أو أي رابط Postgres خارجي يتطلب TLS.
DATABASE_SSL = os.getenv("DATABASE_SSL", "false").strip().lower() in {"1", "true", "yes"}

if not DISCORD_TOKEN or not MAIN_GEMINI_API_KEY:
    print("❌ يلزم ضبط DISCORD_TOKEN و MAIN_GEMINI_API_KEY في متغيرات البيئة.")
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
# خادم الويب
# =========================================================
async def handle_ping(request):
    database_state = "ready" if db_pool is not None else "unavailable"
    return web.json_response({"status": "ok", "database": database_state})


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🌐 [Web Server] يعمل على المنفذ: {port}")


# =========================================================
# PostgreSQL: رسائل + ذاكرة منظمة
# =========================================================
async def initialize_database():
    """يفتح اتصالاً حقيقياً وينشئ الجداول والفهارس عند أول تشغيل."""
    global db_pool
    if not DATABASE_URL:
        print("⚠️ لم يتم ضبط DATABASE_URL؛ البوت سيعمل، لكن لن يحتفظ بذاكرة دائمة.")
        return

    try:
        pool_options = {
            "min_size": 1,
            "max_size": 5,
            "command_timeout": 10,
        }
        if DATABASE_SSL:
            pool_options["ssl"] = True
        db_pool = await asyncpg.create_pool(DATABASE_URL, **pool_options)
        async with db_pool.acquire() as connection:
            await connection.execute("SELECT 1")
            await connection.execute(
                """
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
                """
            )
        print("✅ [Postgres] تم اختبار الاتصال وتجهيز جداول الذاكرة.")
    except Exception as error:
        db_pool = None
        print(f"❌ [Postgres] فشل الاتصال؛ الذاكرة الدائمة متوقفة: {error}")


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
# Gemini: استخراج ذاكرة منظمة
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
                        print(f"⚠️ [{label}] {model}: الحالة {response.status}: {await response.text()}")
                        continue
                    data = await response.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    result = "".join(part.get("text", "") for part in parts).strip()
                    if result:
                        return result
            except Exception as error:
                print(f"⚠️ [{label}] تعذر الاتصال بـ {model}: {error}")
    return None


def parse_memory_json(text):
    if not text:
        return []
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    memories = payload.get("memories", []) if isinstance(payload, dict) else []
    return [memory for memory in memories if valid_memory(memory)]


async def update_memory_in_background(user_id, user_name, recent_history, source_message_id):
    """مهمة منفصلة ومتسلسلة لكل مستخدم، حتى لا تكتب رسالة قديمة فوق الأحدث."""
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
            print(f"🧠 [Memory] تم حفظ {saved_count} حقيقة للمستخدم {user_name}.")


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
    print(f"✅ تم الاتصال بنجاح: {client.user}")


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

        # تبدأ بعد الرد كي لا تبطئ Discord، وتستخدم قفلاً يمنع سباق التحديثات.
        if user_message_id is not None:
            asyncio.create_task(
                update_memory_in_background(user_id, user_name, recent_history, user_message_id)
            )
    except Exception as error:
        print(f"❌ [Message Error] {error}")
        await message.reply("صار عندي تعليق بسيط، جرب مرة ثانية.", mention_author=False)


async def main():
    await initialize_database()
    await start_web_server()
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.")


