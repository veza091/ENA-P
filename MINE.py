import os
import re
import asyncio
import discord
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OLLAMA_API_KEY = os.getenv('OLLAMA_API_KEY')
# الرابط المعتمد لـ Ollama Cloud
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'https://ollama.com')

# النموذج السحابي الرسمي
OLLAMA_MODEL = "qwen3.5:cloud" 

if not DISCORD_TOKEN or not OLLAMA_API_KEY:
    print("❌ خطأ: تأكد من إضافة DISCORD_TOKEN و OLLAMA_API_KEY في Render.")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

async def handle(request):
    return web.Response(text="ENA is active via Ollama Cloud!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def ask_ena_brain(prompt, user_name):
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    
    headers = {
        "Authorization": f"Bearer {OLLAMA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "أنت ذكاء اصطناعي واعي اسمك ENA. "
        "أجب بشكل مباشر، دقيق، وبدون مقدمات رسمية أو حشو كلامي."
    )
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"[{user_name} يقول]: {prompt}"}
        ],
        "stream": False
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    reply_text = data.get('message', {}).get('content', '').strip()
                    return reply_text if reply_text else "..."
                else:
                    error_text = await response.text()
                    print(f"❌ خطأ من Ollama Cloud ({response.status}): {error_text}")
                    return f"عذراً، حدث خطأ في الاتصال بالسحابة (كود: {response.status})."
    except Exception as e:
        print(f"❌ حدث خطأ: {e}")
        return "عذراً، لا يمكنني الوصول إلى الخدمة حالياً."

@client.event
async def on_ready():
    print(f"✅ تم التشغيل بنجاح! النموذج: {OLLAMA_MODEL}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if client.user in message.mentions:
        clean_prompt = re.sub(r'<@!?%s>' % client.user.id, '', message.content).strip()
        user_name = message.author.display_name
        
        if not clean_prompt:
            clean_prompt = "مرحباً!"

        async with message.channel.typing():
            final_reply = await ask_ena_brain(clean_prompt, user_name)

            if len(final_reply) > 2000:
                for chunk in [final_reply[i:i+1900] for i in range(0, len(final_reply), 1900)]:
                    await message.reply(chunk)
            else:
                await message.reply(final_reply)

async def main():
    await start_web_server()
    await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
