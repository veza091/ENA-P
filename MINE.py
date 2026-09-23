import asyncio
import json
import os
import random
import re
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

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

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_SSL_ENV = os.getenv("DATABASE_SSL", "").strip().lower()
DATABASE_SSL = DATABASE_SSL_ENV in {"1", "true", "yes"} or "supabase" in DATABASE_URL

if not DISCORD_TOKEN or not MAIN_GEMINI_API_KEY:
    print("❌ يلزم ضبط DISCORD_TOKEN و MAIN_GEMINI_API_KEY في متغيرات البيئة.", flush=True)
    sys.exit(1)


def sanitize_database_url(url):
    """ينظف الرابط تلقائياً من الأقواس المربعة [ ] التي يتركها المستخدمون سهواً من قالب Supabase."""
    if not url:
        return url
    url = url.strip()
    if (url.startswith('"') and url.endswith('"')) or (url.startswith("'") and url.endswith("'")):
        url = url[1:-1].strip()

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    elif not url.startswith("postgresql://"):
        return url

    rest = url[len("postgresql://"):]
    slash_idx = rest.find("/")
    authority = rest[:slash_idx] if slash_idx != -1 else rest
    path_and_query = rest[slash_idx:] if slash_idx != -1 else ""

    at_idx = authority.rfind("@")
    if at_idx != -1:
        userinfo = authority[:at_idx]
        hostinfo = authority[at_idx + 1:]

        colon_idx = userinfo.find(":")
        if colon_idx != -1:
            user = userinfo[:colon_idx]
            pwd = userinfo[colon_idx + 1:]
        else:
            user = userinfo
            pwd = ""

        # إزالة الأقواس المربعة من المستخدم أو كلمة المرور
        if pwd.startswith("[") and pwd.endswith("]"):
            pwd = pwd[1:-1]
        pwd = pwd.replace("[", "").replace("]", "")

        if user.startswith("[") and user.endswith("]"):
            user = user[1:-1]
        user = user.replace("[", "").replace("]", "")

        # إزالة الأقواس المربعة حول المضيف إن وجدت
        if hostinfo.startswith("[") and hostinfo.endswith("]"):
            hostinfo = hostinfo[1:-1]
        host_parts = hostinfo.split(":")
        if len(host_parts) == 2 and host_parts[0].startswith("[") and host_parts[0].endswith("]"):
            hostinfo = f"{host_parts[0][1:-1]}:{host_parts[1]}"

        pwd_encoded = urllib.parse.quote(urllib.parse.unquote(pwd), safe="")
        user_encoded = urllib.parse.quote(urllib.parse.unquote(user), safe=".")

        return f"postgresql://{user_encoded}:{pwd_encoded}@{hostinfo}{path_and_query}"
    return url


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
    ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
)
MEMORY_MODELS = configured_models(
    "MEMORY_GEMINI_MODELS",
    ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
)

HISTORY_LIMIT = bounded_setting("HISTORY_LIMIT", 10, 4, 30)

# ردود احتياطية عفوية إذا رجع النموذج بنتيجة فارغة (بدل تكرار «هلا» الثابتة كل مرة)
FALLBACK_REPLIES = ("هلا", "ايش؟", "قول", "اممم قل لي", "نعم؟")

db_pool = None
memory_locks = defaultdict(asyncio.Lock)
# آخر نموذج نجح لكل نوع طلب: يُجرَّب أولاً في الطلب التالي بدل حرق المهلة على نموذج معطّل
last_good_model = {}

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


# كلمات شائعة للتجاهل عند استخراج الكلمات المفتاحية
STOP_WORDS = {
    "انا", "أنا", "هو", "هي", "هم", "نحن", "انت", "أنت", "في", "من", "إلى", "على", "عن", "مع",
    "هذا", "هذه", "تم", "كان", "كنت", "ما", "لا", "لم", "لن", "هل", "وش", "ليه", "كيف", "وين",
    "اللي", "الي", "يلي", "شو", "بدك", "بس", "كل", "يا", "ان", "أن", "إن", "او", "أو", "ثم",
    "بعد", "قبل", "حتى", "إذا", "اذا", "عشان", "عشا", "لأنه", "لانه", "هيك", "كذا", "طيب"
}


def extract_keywords(text):
    """يستخرج الكلمات الدلالية الهامة من الحديث للربط التلقائي بالذاكرة."""
    if not text:
        return set()
    words = re.findall(r"[\w\u0600-\u06FF]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOP_WORDS}


def human_time_ago(occurred_at):
    """يحول تاريخ الموقف إلى صيغة بشرية عفوية (أمس، قبل يومين، الأسبوع الماضي)."""
    now = datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    diff = now - occurred_at
    days = diff.days
    seconds = diff.seconds

    if days == 0:
        if seconds < 7200:
            return "اليوم (قبل قليل)"
        return "اليوم"
    elif days == 1:
        return "أمس"
    elif days == 2:
        return "قبل يومين"
    elif 3 <= days <= 10:
        return f"قبل {days} أيام"
    elif 11 <= days <= 30:
        return "قبل أسبوعين أو ثلاثة"
    else:
        return "من فترة"


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
# PostgreSQL: الذاكرة المعرفية البشرية
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

-- ذاكرة الحقائق مع الرسوخ والنسيان التدريجي
CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL REFERENCES users(discord_user_id)
        ON DELETE CASCADE,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'profile',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.7
        CHECK (confidence >= 0 AND confidence <= 1),
    strength DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    recall_count INT NOT NULL DEFAULT 1,
    last_recalled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_value TEXT,
    source_message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (discord_user_id, memory_key)
);

-- ترقية الأعمدة للجدول إذا كان منشأ مسبقاً
ALTER TABLE memories ADD COLUMN IF NOT EXISTS strength DOUBLE PRECISION NOT NULL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS recall_count INT NOT NULL DEFAULT 1;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE memories ADD COLUMN IF NOT EXISTS previous_value TEXT;

CREATE INDEX IF NOT EXISTS memories_user_updated_idx
    ON memories (discord_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS memories_user_strength_idx
    ON memories (discord_user_id, strength DESC, last_recalled_at DESC);

-- ذاكرة المواقف والسوالف (Episodic Memory)
CREATE TABLE IF NOT EXISTS episodes (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL REFERENCES users(discord_user_id)
        ON DELETE CASCADE,
    summary TEXT NOT NULL,
    topics TEXT[] NOT NULL DEFAULT '{}',
    importance INT NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS episodes_user_occurred_idx
    ON episodes (discord_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS episodes_user_importance_idx
    ON episodes (discord_user_id, importance DESC);

-- حماية RLS لجميع الجداول
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE episodes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE users, messages, memories, episodes FROM anon;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE users, messages, memories, episodes FROM authenticated;
    END IF;
END $$;
"""


async def initialize_database():
    """يفتح اتصالاً بـ Supabase Postgres وينشئ أو يرقّي الجداول والفهارس."""
    global db_pool

    if not DATABASE_URL:
        print("❌ [Postgres] لم يتم العثور على DATABASE_URL في متغيرات البيئة! الذاكرة الدائمة لن تعمل.", flush=True)
        return

    cleaned_dsn = sanitize_database_url(DATABASE_URL)
    if cleaned_dsn != DATABASE_URL:
        print("💡 [Postgres] تم تنظيف رابط DATABASE_URL وإزالة الأقواس المربعة [ ] الزائدة تلقائياً.", flush=True)

    if "[" in DATABASE_URL or "]" in DATABASE_URL:
        print("⚠️ [Postgres تنبيه] لاحظنا وجود أقواس مربعة '[' أو ']' في DATABASE_URL الأصلي على Render!\n"
              "   يرجى مسح الأقواس المربعة حول كلمة المرور أو معرّف المشروع في Render Environment.", flush=True)

    pool_options = {
        "min_size": 1,
        "max_size": 5,
        "command_timeout": 15,
        "statement_cache_size": 0,
    }

    if DATABASE_SSL:
        pool_options["ssl"] = "require"

    print("🔌 [Postgres] جاري الاتصال بقاعدة البيانات وتجهيز نظام الذاكرة المعرفية...", flush=True)

    for attempt in range(1, 4):
        try:
            db_pool = await asyncpg.create_pool(cleaned_dsn, **pool_options)
            async with db_pool.acquire() as connection:
                await connection.execute("SELECT 1")
                await connection.execute(SCHEMA_SQL)
            print("✅ [Postgres] تم تجهيز جداول الذاكرة البشرية (الحقائق، المواقف، الرسوخ) وحمايتها بـ RLS.", flush=True)
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
                    "   تأكد من صحة DATABASE_URL وصحة كلمة المرور بدون أقواس [].",
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


# =========================================================
# محرك الاستدعاء بالترابط والنسيان البشري (Associative Retrieval)
# =========================================================
async def get_associative_context(user_id, current_prompt):
    """
    يسترجع الذكريات والمواقف المرتبطة بسياق ما يقال الآن فقط (مثل عقل الإنسان).
    يطبق معادلة النسيان الطبيعي (Decay Formula) ويعزز قوة ما يتم استرجاعه.
    """
    if db_pool is None:
        return {"memories": [], "episodes": []}

    prompt_keywords = extract_keywords(current_prompt)

    async with db_pool.acquire() as connection:
        # 1. استرجاع ذاكرة الحقائق المحسوبة بالرسوخ وتلاشي النسيان
        raw_memories = await connection.fetch(
            """
            SELECT memory_key, memory_value, category, confidence, strength,
                   recall_count, previous_value,
                   -- معادلة النسيان البشري: يقل الوزن تدريجياً إذا لم تُذكر منذ فترة
                   (confidence * (strength / (1.0 + 0.04 * GREATEST(0, EXTRACT(EPOCH FROM (NOW() - last_recalled_at)) / 86400.0)))) AS effective_score
            FROM memories
            WHERE discord_user_id = $1
            ORDER BY effective_score DESC
            LIMIT 20
            """,
            str(user_id),
        )

        selected_memories = []
        recalled_keys = []

        for row in raw_memories:
            key = row["memory_key"]
            val = row["memory_value"]
            cat = row["category"]

            # معلومات الملف الأساسية تبقى موجودة دائماً
            is_core = cat in {"profile", "identity"} or "name" in key
            # فحص إذا كانت الذكرى مرتبطة بمواضيع الرسالة الحالية
            val_words = set(re.findall(r"[\w\u0600-\u06FF]+", val.lower()))
            matches_topic = bool(prompt_keywords & val_words)

            if is_core or matches_topic or len(selected_memories) < 4:
                selected_memories.append(dict(row))
                recalled_keys.append(key)
                if len(selected_memories) >= 6:
                    break

        # تعزيز الذكريات التي تم استدعاؤها في عقل البوت الآن (Reconsolidation)
        if recalled_keys:
            await connection.execute(
                """
                UPDATE memories
                SET recall_count = recall_count + 1, last_recalled_at = NOW()
                WHERE discord_user_id = $1 AND memory_key = ANY($2)
                """,
                str(user_id),
                recalled_keys,
            )

        # 2. استرجاع ذاكرة المواقف والسوالف السابقة (Episodic Memory)
        raw_episodes = await connection.fetch(
            """
            SELECT id, summary, topics, importance, occurred_at
            FROM episodes
            WHERE discord_user_id = $1
            ORDER BY occurred_at DESC
            LIMIT 5
            """,
            str(user_id),
        )

        selected_episodes = []
        for ep in raw_episodes:
            ep_topics = set(ep["topics"] or [])
            # جلب آخر موقف حدث دائماً (لسياق المرة السابقة) أو المواقف المرتبطة بالسالفة الحالية
            if not selected_episodes or bool(prompt_keywords & ep_topics):
                selected_episodes.append({
                    "summary": ep["summary"],
                    "time_ago": human_time_ago(ep["occurred_at"]),
                    "importance": ep["importance"],
                })
                if len(selected_episodes) >= 2:
                    break

        return {
            "memories": selected_memories,
            "episodes": selected_episodes,
        }


def format_cognitive_context(user_name, cognitive_data):
    """يصيغ الذكريات والمواقف بأسلوب أفكار ذهنية بشرية طبيعية داخل البرومبت."""
    memories = cognitive_data.get("memories", [])
    episodes = cognitive_data.get("episodes", [])

    sections = []

    # 1. المواقف السابقة
    if episodes:
        ep_lines = [f"- {ep['time_ago']}: {ep['summary']}" for ep in episodes]
        sections.append(f"[مواقف وأحداث مشتركة سابقة بينكما]:\n" + "\n".join(ep_lines))

    # 2. الحقائق الحاضرة في الذهن وتنبيهات التغير والتناقض
    if memories:
        mem_lines = []
        contradictions = []
        for mem in memories:
            mem_lines.append(f"- {mem['memory_key']}: {mem['memory_value']}")
            if mem.get("previous_value"):
                contradictions.append(
                    f"(ملاحظة تغير: كان يقول سابقاً إنه '{mem['previous_value']}' والآن يقول '{mem['memory_value']}' - يمكنك التعليق باستغراب خفيف إذا ناسب السياق)"
                )

        fact_text = f"[أشياء في ذهنك عن {user_name} ترتبط بكلامه الآن]:\n" + "\n".join(mem_lines)
        if contradictions:
            fact_text += "\n" + "\n".join(contradictions)
        sections.append(fact_text)

    if not sections:
        return ""

    return "\n\n".join(sections)


async def save_memories(user_id, source_message_id, memories):
    """يحفظ ويحدث الحقائق مع رصد التناقضات وزيادة قوة الرسوخ بالتكرار."""
    if db_pool is None or not memories:
        return 0

    saved_count = 0
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            for memory in memories:
                if not valid_memory(memory):
                    continue

                # فحص القيمة القديمة لمعرفة ما إذا كان هناك تغير أو تناقض
                existing = await connection.fetchrow(
                    """
                    SELECT memory_value, strength, recall_count
                    FROM memories
                    WHERE discord_user_id = $1 AND memory_key = $2
                    """,
                    str(user_id),
                    memory["key"],
                )

                if existing:
                    old_val = existing["memory_value"]
                    if old_val != memory["value"]:
                        # تغير الرأي: تسجيل التناقض وإعادة ضبط القوة
                        await connection.execute(
                            """
                            UPDATE memories
                            SET memory_value = $3,
                                previous_value = $4,
                                category = $5,
                                confidence = $6,
                                strength = 2.0,
                                recall_count = recall_count + 1,
                                source_message_id = $7,
                                updated_at = NOW(),
                                last_recalled_at = NOW()
                            WHERE discord_user_id = $1 AND memory_key = $2
                            """,
                            str(user_id),
                            memory["key"],
                            memory["value"],
                            old_val,
                            memory["category"],
                            memory["confidence"],
                            source_message_id,
                        )
                    else:
                        # تأكيد وتكرار: زيادة الرسوخ البشري
                        await connection.execute(
                            """
                            UPDATE memories
                            SET strength = LEAST(10.0, strength + 1.0),
                                recall_count = recall_count + 1,
                                source_message_id = $3,
                                updated_at = NOW(),
                                last_recalled_at = NOW()
                            WHERE discord_user_id = $1 AND memory_key = $2
                            """,
                            str(user_id),
                            memory["key"],
                            source_message_id,
                        )
                else:
                    # ذكرى جديدة لأول مرة
                    await connection.execute(
                        """
                        INSERT INTO memories (
                            discord_user_id, memory_key, memory_value, category,
                            confidence, strength, recall_count, source_message_id
                        ) VALUES ($1, $2, $3, $4, $5, 1.0, 1, $6)
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
# Gemini: الاتصال واستخراج الذاكرة والمواقف
# =========================================================
async def request_gemini(models, api_key, payload, label):
    # ابدأ بالنموذج الذي نجح آخر مرة: يوفر 12-20 ثانية كانت تُحرق على نموذج معطّل في كل رسالة
    ordered = list(models)
    last_good = last_good_model.get(label)
    if last_good in ordered:
        ordered.remove(last_good)
        ordered.insert(0, last_good)

    async with aiohttp.ClientSession() as session:
        for model in ordered:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            # محاولتان كحد أقصى لكل نموذج: خطأ 503 (ازدحام مؤقت) يستحق إعادة محاولة بعد ثانيتين
            for attempt in (1, 2):
                try:
                    async with session.post(
                        url,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "x-goog-api-key": api_key,
                        },
                        timeout=aiohttp.ClientTimeout(total=12),
                    ) as response:
                        if response.status == 503 and attempt == 1:
                            print(f"⚠️ [{label}] {model}: ازدحام مؤقت (503) — إعادة المحاولة بعد ثانيتين...", flush=True)
                            await asyncio.sleep(2)
                            continue
                        if response.status != 200:
                            err_text = await response.text()
                            print(f"⚠️ [{label}] {model}: الحالة {response.status}: {err_text}", flush=True)
                            break
                        data = await response.json()
                        candidates = data.get("candidates") or []
                        if not candidates:
                            block_reason = (data.get("promptFeedback") or {}).get("blockReason", "غير معروف")
                            print(f"⚠️ [{label}] {model}: رجع بلا نتائج (blockReason: {block_reason})", flush=True)
                            break
                        parts = candidates[0].get("content", {}).get("parts", [])
                        result = "".join(part.get("text", "") for part in parts).strip()
                        if result:
                            last_good_model[label] = model
                            return result, model
                        finish_reason = candidates[0].get("finishReason", "غير معروف")
                        print(f"⚠️ [{label}] {model}: نتيجة فارغة (finishReason: {finish_reason})", flush=True)
                        break
                except Exception as error:
                    print(f"⚠️ [{label}] تعذر الاتصال بـ {model}: {error}", flush=True)
                    break
    return None, None


def parse_json_safely(text):
    if not text:
        return {}
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
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


async def update_memory_in_background(user_id, user_name, recent_history, source_message_id):
    """مهمة خلفية: تستخرج الحقائق الراسخة وتلخص المواقف والأحداث المشتركة (Episodic)."""
    if db_pool is None or not MEMORY_GEMINI_API_KEY:
        return

    async with memory_locks[str(user_id)]:
        try:
            conversation = "\n".join(
                f"{'المستخدم' if message['role'] == 'user' else 'إينا'}: {message['content']}"
                for message in recent_history
            )

            # 1. استخراج حقائق الذاكرة الدائمة
            fact_prompt = f"""
أنت جزء الذاكرة المعرفية في بوت Discord. استخرج فقط الحقائق الثابتة والمفيدة عن المستخدم: {user_name}.
لا تخزن التحية أو الأسئلة العابرة. إذا لم توجد حقيقة جديدة أو تصحيح لمعلومة قديمة، أعد قائمة فارغة.

أعد JSON صالحاً فقط:
{{"memories":[{{"key":"english_snake_case","value":"حقيقة قصيرة بالعربية","category":"profile_or_preference_or_interest","confidence":0.85}}]}}

المحادثة الأخيرة:
{conversation}
"""
            fact_result, fact_model = await request_gemini(
                MEMORY_MODELS,
                MEMORY_GEMINI_API_KEY,
                {
                    "contents": [{"role": "user", "parts": [{"text": fact_prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": 250,
                        "temperature": 0.1,
                        "responseMimeType": "application/json",
                    },
                },
                "Fact-Memory",
            )
            fact_data = parse_json_safely(fact_result)
            memories = [m for m in fact_data.get("memories", []) if valid_memory(m)]
            saved_count = await save_memories(user_id, source_message_id, memories)
            if saved_count:
                print(f"🧠 [Memory] تم تثبيت/تحديث {saved_count} حقيقة للمستخدم {user_name} (النموذج: {fact_model}).", flush=True)

            # 2. تلخيص المواقف والسوالف (Episodic Memory) إذا كان الحوار يتضمن حدثاً أو سالفة
            if len(recent_history) >= 4:
                episode_prompt = f"""
أنت مسؤول الذاكرة العرضية (Episodic Memory) كصديق بشري.
حلل الحوار التالي بين المستخدم ({user_name}) وإينا:
هل تضمن الحوار حدثاً، موقفاً، قصة، أو تجربة مر بها المستخدم تستحق أن تُحفظ كذكرى وسالفة مشتركة بينكما؟
(تجنب التحيات السريعة، أو الحوارات الفارغة التي لا تتضمن موضوعاً واضحاً).

أعد JSON فقط:
إذا كان هناك موقف:
{{"has_episode": true, "summary": "ملخص دافئ ومختصر في جملة واحدة للموقف كذكرى مشتركة", "topics": ["كلمة1", "كلمة2"], "importance": 7}}
إذا لم يكن هناك حدث يستحق الحفظ:
{{"has_episode": false}}

الحوار الأخير:
{conversation}
"""
                ep_result, _ep_model = await request_gemini(
                    MEMORY_MODELS,
                    MEMORY_GEMINI_API_KEY,
                    {
                        "contents": [{"role": "user", "parts": [{"text": episode_prompt}]}],
                        "generationConfig": {
                            "maxOutputTokens": 200,
                            "temperature": 0.2,
                            "responseMimeType": "application/json",
                        },
                    },
                    "Episode-Memory",
                )
                ep_data = parse_json_safely(ep_result)
                if ep_data.get("has_episode") and ep_data.get("summary"):
                    summary = ep_data["summary"].strip()
                    topics = [str(t).strip().lower() for t in ep_data.get("topics", []) if str(t).strip()]
                    importance = max(1, min(int(ep_data.get("importance", 5)), 10))

                    async with db_pool.acquire() as connection:
                        # منع تكرار نفس ملخص الموقف إذا كان مسجلاً مؤخراً
                        duplicate = await connection.fetchval(
                            """
                            SELECT 1 FROM episodes
                            WHERE discord_user_id = $1 AND summary = $2 AND occurred_at > NOW() - INTERVAL '1 hour'
                            """,
                            str(user_id),
                            summary,
                        )
                        if not duplicate:
                            await connection.execute(
                                """
                                INSERT INTO episodes (discord_user_id, summary, topics, importance, occurred_at)
                                VALUES ($1, $2, $3, $4, NOW())
                                """,
                                str(user_id),
                                summary,
                                topics,
                                importance,
                            )
                            print(f"📖 [Episode] تم تسجيل موقف جديد في الذاكرة لـ {user_name}: {summary}", flush=True)
        except Exception as error:
            print(f"⚠️ [Memory] فشل تحديث ذاكرة المستخدم {user_name}: {error}", flush=True)


# =========================================================
# Gemini: الرد الرئيسي البشري
# =========================================================
async def ask_ena_brain(clean_prompt, user_name, cognitive_context, history_messages):
    persona = (
        "أنتِ إينا، بنت عمرك 18 سنة. هادية بطبيعتك، ملاحظة وواعية باللي يحصل حواليك، "
        "وتتكلم بعفوية ودون افتعال. لسانتك خفيفة وفيها لمسة سوداوية أحياناً — "
        "ما هي ساذجة ولا تبالغ في الفرح. تتذكر الأمور بعمق وتتكلم عنها بعفوية. "
        "ردي بجملة إلى جملتين، طبيعي كأنك تكتبين لصديق قريب. "
        "طابقي لهجة المستخدم وأسلوب حديثه. "
        "إيموجي واحد كحد أقصى، وبوصفه تعبيراً عفوياً فقط — مش كزينة أو تزيين."
    )
    if cognitive_context:
        persona += f"\n\n{cognitive_context}\n\nتذكري واستحضري المواقف والأشياء السابقة بينكما كذكريات شخصية عابرة، وذكرها بعفوية دون تقول 'أنا أتذكر'."
    persona += (
        "\n\nقواعد مهمة: "
        "أجبي مباشرة على آخر رسالة قالها المستخدم ولا تتجاهليها، "
        "لا تختلقي قصصاً وهمية، "
        "ولا تكرري نفس ردك السابق حرفياً."
    )
    system_instruction = persona

    contents = [
        {
            "role": "model" if message["role"] == "model" else "user",
            "parts": [{"text": message["content"]}],
        }
        for message in history_messages
    ]
    if not contents or contents[-1]["parts"][0]["text"] != clean_prompt:
        contents.append({"role": "user", "parts": [{"text": clean_prompt}]})

    def brain_payload(temperature):
        return {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 150, "temperature": temperature, "topP": 0.9},
        }

    # آخر رد قاله البوت: لو ردّ بنفس الحرف نعيد المحاولة بدرجة حرارة أعلى حتى لا يبدو آلياً
    last_model_reply = next(
        (m["content"] for m in reversed(history_messages) if m["role"] == "model"),
        None,
    )
    result, model = None, None
    for temperature in (0.85, 1.05):
        result, model = await request_gemini(MAIN_MODELS, MAIN_GEMINI_API_KEY, brain_payload(temperature), "Brain")
        result = clean_text(result) if result else ""
        if result and result != last_model_reply:
            break

    if result:
        print(f"💬 [Brain] الرد من: {model}", flush=True)
        return result
    return random.choice(FALLBACK_REPLIES)


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

        # استرجاع الذكريات والمواقف بالترابط الدلالي البشري
        cognitive_data = await get_associative_context(user_id, clean_prompt)
        cognitive_context = format_cognitive_context(user_name, cognitive_data)

        async with message.channel.typing():
            reply = await ask_ena_brain(clean_prompt, user_name, cognitive_context, recent_history)
            await message.reply(reply, mention_author=False)

        await save_message(message.channel.id, user_id, "model", reply)

        # تثبيت الحقائق والمواقف في الخلفية
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
    await initialize_database()
    await start_web_server()
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت.", flush=True)
