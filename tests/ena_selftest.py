# -*- coding: utf-8 -*-
"""اختبار ذاتي لوظائف ENA النقية — يعمل بلا ديسكورد ولا قاعدة بيانات.

الاستخدام:  python ena_selftest.py [مسار ملف البوت]
الافتراضي:  ../ena.py (المصدر الأساسي) — يمكن تمرير مسار نسخة أخرى كوسيط
يستخرج الدوال النقية من الملف عبر AST (يختبر الكود الفعلي لا نسخة منه)
ويفشل برمز خروج غير صفري إذا انكسر أي فحص — شغّله بعد أي تعديل مستقبلي.
"""
import ast
import json
import os
import re
import struct
import sys
from datetime import datetime, timedelta, timezone

import nacl.secret

# المسار الافتراضي مستقلٌّ عن مجلد التشغيل: يخاطب ../ena.py نسبة لهذا الملف.
BOT_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "ena.py")
)
SRC = open(BOT_PATH, encoding="utf-8").read()

FUNCTIONS = {
    "normalize_arabic", "extract_keywords", "valid_memory", "valid_emotion",
    "mood_zone", "update_mood", "apply_mood_decay", "human_time_ago",
    "human_gap_ago", "parse_json_safely", "valid_anticipation", "merge_previous_chain",
    "is_key_quota_exhausted", "pick_memory_key", "fact_supported",
}
# دوال الصوت الحي (نسخة 34 فما فوق) — اختيارية: غيابها لا يفشل الفحص على نسخ أقدم
LIVE_FUNCTIONS = {"_parse_rtp", "_to_16k_mono", "_rms_audio", "_decode_ssrc_event",
                  "_transport_decrypt", "_redact", "_sanitize_log_line",
                  "_reply_similarity", "_is_repeat",
                  # صندوق صدمات الوسيط (v35): تصنيف الأعطال والاحتياطي التلقائي
                  "_relay_fault", "_relay_is_up", "_mark_relay_down"}
# ثابتات v35 الاختيارية — تُحمّل إن وُجدت ولا تُشترط على النسخ الأقدم
LIVE_CONSTANTS = {"RELAY_FAULT_SIGNATURES"}
CONSTANTS = {
    "ARABIC_LETTER_MAP", "ARABIC_DIACRITICS_RE", "ARTICLE_PREFIXES",
    "RAW_STOP_WORDS", "STOP_WORDS", "SUPPORT_STOP", "MOOD_BASELINE_V", "MOOD_BASELINE_A",
    "MOOD_EMA_ALPHA", "MOOD_DECAY_PER_HOUR", "SAUDI_TZ", "ARABIC_WEEKDAYS",
}


def load_functions(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    chunks, found, found_live = [], set(), set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            chunks.append(ast.get_source_segment(src, node))
            found.add(node.name)
        elif isinstance(node, ast.FunctionDef) and node.name in LIVE_FUNCTIONS:
            chunks.append(ast.get_source_segment(src, node))
            found_live.add(node.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in CONSTANTS:
                chunks.append(ast.get_source_segment(src, node))
                found.add(name)
            elif name in LIVE_CONSTANTS:
                chunks.append(ast.get_source_segment(src, node))   # يُحمَّل إن وُجد، بلا اشتراط
    missing = (FUNCTIONS | CONSTANTS) - found
    if missing:
        print(f"❌ عناصر مفقودة من {path}: {missing}")
        sys.exit(1)
    ns = {"re": __import__("re"), "json": json, "struct": struct, "nacl": __import__("nacl"),
          "datetime": datetime, "timezone": timezone, "timedelta": timedelta}
    exec("\n\n".join(chunks), ns)
    for name in LIVE_FUNCTIONS - found_live:
        ns.pop(name, None)
    return ns


ns = load_functions(BOT_PATH)
now = datetime.now(timezone.utc)
failures = []


def check(label, ok):
    print(("✅" if ok else "❌"), label)
    if not ok:
        failures.append(label)


# --- التطبيع العربي ---
norm = ns["normalize_arabic"]
nk = ns["extract_keywords"]
check("توحيد الحروف: أحمد إبراهيم", norm("أحمد إبراهيم") == "احمد ابراهيم")
check("ة←ه و ى←ي", norm("مدرسة كبيرة") == "مدرسه كبيره")
check("حذف التشكيل والتطويل", norm("أَحْمَــد") == "احمد")
check("الجامعة ↔ بالجامعة", nk("يدرس في الجامعة") & nk("وش صار بالجامعة") == {"جامعه"})
check("أحمد ↔ احمد", nk("أحمد") & nk("احمد") == {"احمد"})
check("كلمات التوقف تُصفّى", nk("انا في البيت") == {"بيت"})
check("حارس الجذع القصير", {"علم", "نور"} <= nk("العلم نور"))

# --- التحقق من الحقائق ---
vm = ns["valid_memory"]
check("حقيقة سليمه تُقبل", bool(vm({"key": "name", "value": "احمد", "category": "profile", "confidence": 0.9})))
check("مفتاح فاسد يُرفض", not vm({"key": "9bad", "value": "شيء"}))
check("قيمة فارغة تُرفض", not vm({"key": "name", "value": "   "}))
check("ثقة خارج النطاق تُرفض", not vm({"key": "name", "value": "شيء", "confidence": 1.5}))

# --- التحقق من المشاعر ---
vem = ns["valid_emotion"]
check("شعور سليم يُقبل", bool(vem({"emotion": "حزين", "intensity": 0.8, "valence": -0.6, "arousal": 0.3, "note": "زعلان"})))
check("قيمة خارج النطاق تُرفض", vem({"valence": 5.0, "arousal": 0.5}) is None)
check("غير رقمي يُرفض", vem({"valence": "كثير", "arousal": 0.5}) is None)
check("ليس قاموساً يُرفض", vem("نص") is None)

# --- المزاج: المناطق والرياضيات ---
zone = ns["mood_zone"]
ema = ns["update_mood"]
decay = ns["apply_mood_decay"]
check("مناطق المناطق الست", [zone(v, a) for v, a in [(0.6, 0.8), (0.6, 0.3), (-0.6, 0.8), (-0.6, 0.2), (0.1, 0.9), (0.1, 0.3)]]
      == ["مبسوط ومتكهرب", "مرتاح ودافئ", "متضايق ومنفعل", "مطفوط", "متكهرب مشدود", "هادي طبيعي"])
v, a = ema(0.0, 0.2, 1.0, 1.0)
check("EMA من الأساس", abs(v - 0.25) < 1e-9 and abs(a - 0.4) < 1e-9)
v2, a2 = ema(0.95, 0.95, 1.0, 1.0)
check("قفل الحدود عند القمة", v2 <= 1.0 and a2 <= 1.0)
dv, _ = decay(0.6, 0.5, now - timedelta(hours=1))
check("نصف عمر السكون (ساعة)", abs(dv - 0.3) < 1e-4)

# --- الوقت ---
hga = ns["human_gap_ago"]
check("فجوة ساعتين", hga(timedelta(hours=2)) == "قبل ساعتين")
check("فجوة 3 أيام", hga(timedelta(days=3)) == "قبل 3 أيام")
check("فجوة دقيقة", hga(timedelta(seconds=60)) == "قبل دقيقة")
hta = ns["human_time_ago"]
check("أمس", hta(now - timedelta(days=1)) == "أمس")
check("قبل يومين", hta(now - timedelta(days=2)) == "قبل يومين")

# --- JSON ---
pjs = ns["parse_json_safely"]
check("JSON داخل أسوار", pjs('```json\n{"a": 1}\n```') == {"a": 1})
check("JSON محاط بنص", pjs('هذا: {"b": 2} نهاية') == {"b": 2})
check("تالف يعطي فارغ", pjs("ليس json") == {})

# --- الذكريات المقبلة وسلاسل التغيير ---
va = ns["valid_anticipation"]
r_ok = va({"content": "امتحان الرياضة", "due": "2026-10-01"})
check("توقع سليم بتاريخ ISO", bool(r_ok) and r_ok["due_at"] is not None and r_ok["due_at"].strftime("%Y-%m-%d") == "2026-10-01")
check("توقع بلا تاريخ يُقبل", bool(va({"content": "سفر قريب"})) )
r_bad = va({"content": "شيء ما", "due": "بكرة"})
check("تاريخ غير قابل للقراءة → بلا موعد", bool(r_bad) and r_bad["due_at"] is None)
check("محتوى فارغ أو طويل يُرفض", va({"content": ""}) is None and va({"content": "x" * 201}) is None)
check("ليس قاموساً يُرفض", va("نص") is None)
mp = ns["merge_previous_chain"]
check("جيل واحد فقط (أحدث سابق)", json.loads(mp("عمر", "احمد")) == ["عمر"])
check("لا سلسلة تنمو بلا حد", json.loads(mp("ساري", mp("عمر", "احمد"))) == ["ساري"])
fs = ns["fact_supported"]
check("حقيقة مدعومة بكلامه", fs("يحب الشطرنج", "ممكن نلعب شطرنج اليوم"))
check("حقيقة من كلام غيره تُرفض", not fs("يقيم في الدمام", "وش رايك باللعبة"))
check("قيمة بلا كلمات قابلة للفحص تمرّ", fs("!!!", "أي كلام"))

# --- تناوب مفاتيح الذاكرة (نفاد أحدها شفاف) ---
pmk = ns["pick_memory_key"]
ns["memory_api_keys"] = ["مفتاح_قديم", "مفتاح_جديد"]
ns["quota_exhausted_until"] = {"مفتاح_قديم": datetime.now(timezone.utc) + timedelta(hours=2)}
check("التناوب يتخطى المنفد", pmk() == "مفتاح_جديد")
ns["quota_exhausted_until"] = {}
check("يرجع للأول عند تجدده", pmk() == "مفتاح_قديم")
ns["quota_exhausted_until"] = {"مفتاح_قديم": now + timedelta(hours=2), "مفتاح_جديد": now + timedelta(hours=2)}
check("كلاهما منفد يرجع الأول ويجرب حظه", pmk() == "مفتاح_قديم")

# --- سلامة الاستعلامات البنيوية (درس معاملات الـINSERT المكرر) ---
m_ins = re.search(r'INSERT INTO episodes \(([^)]+)\)\s*VALUES\s*\(([^)]*)\)', SRC, re.DOTALL)
check("INSERT المواقف موجود", bool(m_ins))
if m_ins:
    cols = [c.strip() for c in m_ins.group(1).split(',')]
    vals = [v.strip() for v in m_ins.group(2).split(',') if v.strip()]
    check("أعمدة المواقف = القيم (لا تكرار)", len(cols) == len(vals))
    check("كل القيم معاملات أو NOW", all(v.startswith('$') or v.startswith('NOW') for v in vals))

# --- الإصلاحات الأمنية البنيوية (تدقيق 01-10) — تنطبق على النسخة 34 فما فوق فقط ---
if "WIPE_TRIGGER" in SRC:
    check("المحو الذاتي: حذف users بمعامل واحد (J95 مُغلق)",
          bool(re.search(r'DELETE FROM users WHERE discord_user_id = \$1', SRC)))
    check("طابور السماع بسقف (J94 مُغلق)", "asyncio.Queue(maxsize=400)" in SRC)
    check("طابور التشغيل بسقف (J94 مُغلق)", "queue.Queue(maxsize=150)" in SRC)
    check("المصادقة بالهيدر أول درجة في السلم (B16 مُغلق)",
          bool(re.search(r'variants = \[\("كامل بمفتاح الهيدر"', SRC)))
    check("حجب المفتاح مطبق في طباعة فشل الإعداد (D40 مُغلق)",
          "_redact(repr(error), MAIN_GEMINI_API_KEY)" in SRC)
    check("تعقيم السجل مطبق داخل _live_log (A9 مُغلق)",
          bool(re.search(r'safe = _redact\(_sanitize_log_line\(message\)', SRC)))

# --- حزمة الطبيعية (v34.6) — تنطبق على نسختها فما فوق فقط ---
if "أوقف نفسك بنفسك" in SRC:
    check("سقف الرد 150 مع الإيقاف الذاتي", '"maxOutputTokens": 150' in SRC)
    check("قاعدة الإيقاف الذاتي موجودة", "أوقف نفسك بنفسك" in SRC)
    check("أمثلة الشخصية ≥ 8", SRC.count("أنتِ: ") >= 8)
    check("عداد البتر (MAX_TOKENS) موجود", "رد مبتور (MAX_TOKENS)" in SRC)
    check("عدّاد الابتراق التراكمي مدمج", "_truncation_counter += 1" in SRC)
    check("لا سقف 60 متبقٍ (منع عودة الابتراق)", '"maxOutputTokens": 60' not in SRC)
    check("حارس التكرار البارافرازي مدمج في الدماغ", "_is_repeat(reply_clean, previous_replies)" in SRC)
    check("قاعدة الصدق الصوتي (لا صوت بلا أعذار)", "الصوت مو متوفر حالياً" in SRC)

# --- وسيط Cloudflare (v34.7): تجاوز حجب عنوان الاستضافة بتمرير النداءات عبر شبكة مدعومة ---
if "GEMINI_RELAY_URL" in SRC:
    check("دعم وسيط Cloudflare موجود", '"x-relay-token"' in SRC)
    check("توجيه طلب الوسيط (model + payload)", '"model": model, "payload": payload' in SRC)
    check("معالجة 401/403 كحجب لا نفاد حصة", "resp.status in (401, 403)" in SRC)

# --- صندوق صدمات الوسيط (v35): تصنيف الأعطال ثم الاحتياطي التلقائي ---
if all(n in ns for n in ("_relay_fault", "_relay_is_up", "_mark_relay_down")):
    class _Resp:
        def __init__(self, status, ctype="application/json"):
            self.status = status
            self._ctype = ctype

        @property
        def headers(self):
            return {"Content-Type": self._ctype}

    fault, route = ns["_relay_fault"], {"url": "https://r.test", "token": "T"}
    check("عطل الوسيط: 401 برسالة الوسيط → استبدال",
          fault(route, _Resp(401), '{"error":{"code":401,"message":"unauthorized relay client"}}') is not None)
    check("رد غير JSON من الوسيط → استبدال", fault(route, _Resp(404, "text/html"), "<html>404</html>") is not None)
    check("عطل بوابة الوسيط 502 بتوقيع الوسيط الحقيقي → استبدال",
          fault(route, _Resp(502),
                '{"error":{"code":502,"message":"relay upstream fetch failed: Error: connect timeout"}}') is not None)
    check("5xx من Google (503 ازدحام) ليس عطل وسيط — درس حادثة 16-10-2026",
          fault(route, _Resp(503),
                '{"error":{"code":503,"message":"This model is currently experiencing high demand."}}') is None)
    check("500 داخلي من Google ليس عطل وسيط",
          fault(route, _Resp(500), '{"error":{"code":500,"message":"Internal error encountered."}}') is None)
    check("حجب Google بصفحة HTML ليس عطل وسيط",
          fault(route, _Resp(403, "text/html"),
                "<title>Google</title> Your client does not have permission to get URL") is None)
    check("429 من Google ليس عطل وسيط", fault(route, _Resp(429), '{"error":{"code":429}}') is None)
    check("200 ليس عطل وسيط", fault(route, _Resp(200), '{"candidates":[]}') is None)
    check("بلا وسيط: لا تصنيف أصلاً", fault(None, _Resp(502), "anything") is None)

    is_up, mark = ns["_relay_is_up"], ns["_mark_relay_down"]
    ns.setdefault("_relay_down_until", {})
    ns.setdefault("_relay_alive_state", {})
    ns.setdefault("RELAY_COOLDOWN_SECONDS", 600)
    ns["_relay_down_until"].clear()
    ns["_relay_alive_state"].clear()
    check("وسيط سليم قبل أي تعطّل", is_up(route) is True)
    _quota_before = dict(ns.get("quota_exhausted_until", {}))
    mark(route, "فحص")
    check("وسيط معطّل يُستبعد فوراً", is_up(route) is False)
    check("تعطّل الوسيط لا يلوّث حالة الكوتة", ns.get("quota_exhausted_until", {}) == _quota_before)
    ns["_relay_down_until"]["https://r.test"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    check("انتهاء التهدئة يعيد الوسيط للخدمة", is_up(route) is True)
    check("تبديل المسار عند التعطّل مدمج", "_relay_is_up(r)" in SRC and "_mark_relay_down(route, fault)" in SRC)
    check("كلب حراسة الصحّة موجود", "async def relay_watchdog" in SRC and "asyncio.create_task(relay_watchdog())" in SRC)
    check("فحص الصحّة صفر كوتة", "__health_probe__" in SRC)
    check("فحص الصحّة يُرسل مفتاحاً وهمياً (بدونه يرفض الوسيط الطلب فيبدو ميتاً وهو حيّ)",
          '"x-goog-api-key": "health-probe-invalid-key"' in SRC)
    check("العنوان الافتراري مُثبَّت في الكود", "DEFAULT_GEMINI_RELAY_URL" in SRC)

# --- الصوت الحي (نسخة 34): RTP والتحويلات وفك التشفير ---
if all(name in ns for name in LIVE_FUNCTIONS):
    prtp = ns["_parse_rtp"]
    base = struct.pack(">BBHI", 0x80, 0x78, 1, 2) + struct.pack(">I", 555) + b"\x01\x02"
    ssrc, hlen = prtp(base)
    check("RTP أساسي: ssrc وطول الترويسة", ssrc == 555 and hlen == 12)
    check("ليس RTP يُرفض", prtp(b"\x00" * 12)[0] is None and prtp(b"short")[0] is None)
    ext = struct.pack(">BBHI", 0x90, 0x78, 1, 2) + struct.pack(">I", 555)
    ext += struct.pack(">HH", 0xBEDE, 1) + b"\xAA\xBB\xCC\xDD" + b"\xff" * 4
    ssrc2, hlen2 = prtp(ext)
    check("RTP بامتداد: الترويسة تتوسع", ssrc2 == 555 and hlen2 == 20)

    conv = ns["_to_16k_mono"]
    pcm48 = struct.pack("<6h", 3, 9, 6, 18, 9, 27)  # استريو: قناة يسرى 3,6,9 عند 48k
    check("48k استريو ← 16k أحادي", conv(pcm48) == struct.pack("<1h", 3))

    rms = ns["_rms_audio"]
    check("RMS للصمت صفر", rms(b"\x00\x00\x00\x00") == 0.0)
    check("RMS للقيم المتساوية", rms(struct.pack("<2h", 5, 5)) == 5.0)

    ev = ns["_decode_ssrc_event"]
    check("حدث دخول يضيف", ev(12, {"user_id": "5", "audio_ssrc": 77}) == ("add", "5", 77))
    check("حدث تكلم يضيف", ev(5, {"user_id": "5", "ssrc": 77}) == ("add", "5", 77))
    check("حدث خروج يحذف", ev(13, {"user_id": "5"}) == ("remove", "5", 0))
    check("حدث غريب يُهمَل", ev(99, {}) is None)

    # دورة كاملة: تشفير بمرآة مسار الإرسال ← فك بدالتنا
    nacl_mod = ns["nacl"]
    tdec = ns["_transport_decrypt"]
    key = bytes(range(32))
    header = bytes(range(12))
    secret_msg = b"opus-frame-bytes"
    box = nacl_mod.secret.Aead(key)
    nonce = bytearray(24)
    nonce[:4] = (7).to_bytes(4, "big")
    enc = box.encrypt(secret_msg, header, bytes(nonce)).ciphertext + (7).to_bytes(4, "big")
    check("فك تشفير النقل يعيد الأصل (دورة كاملة)", tdec(enc, header, key) == secret_msg)

    # الإصلاحات الأمنية (تدقيق 01-10): حجب المفتاح وتعقيم السجل
    red = ns["_redact"]
    check("حجب المفتاح من النصوص", red("wss://x?key=SECRET123 ونايف", "SECRET123") == "wss://x?key=*** ونايف")
    check("نص بلا مفتاح يمر كما هو", red("نص عادي", "SECRET123") == "نص عادي")
    sln = ns["_sanitize_log_line"]
    check("تعقيم السجل من حقن الأسطر", "\n" not in sln("سطر1\nسطر2\r\t\x01") and sln("سطر1\nسطر2\r\t\x01").count(" ") >= 2)

    # حزمة الطبيعية (v34.6): حارس التكرار البارافرازي
    sim = ns["_reply_similarity"]
    rep = ns["_is_repeat"]
    check("تشابه ردين متطابقين = 1", sim("شكلك نسيبت انا كنت قبل قدرة", "شكلك نسيبت انا كنت قبل قدرة") == 1.0)
    check("تشابه ردين مختلفين منخفض", sim("الدوام ينهك اي انسان", "هلا والله وش اخبارك") < 0.3)
    check("حارس التكرار يكشف المشابه", rep("يعني شكلك جالس تدورلي سالفة من المال",
                                           ["يعني شكلك قاعد تدورلي سالفة من المال"]) is True)
    check("حارس التكرار يمر الجديد", rep("مال خلق خليها بعدين", ["هلا والله وش اخبارك"]) is False)
else:
    print("ℹ️ دوال الصوت الحي غير موجودة — فحوصاتها مُتخطاة (نسخة أقدم من 34).")

print()
if failures:
    print(f"فشل {len(failures)} فحص: {failures}")
    sys.exit(1)
print(f"كل الفحوصات نجحت ✅ ({BOT_PATH})")
