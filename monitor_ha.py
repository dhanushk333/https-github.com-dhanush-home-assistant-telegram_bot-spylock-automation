import asyncio
import aiohttp
import json
import os
import sys
from dotenv import load_dotenv
from llm_handler import LLMHandler
from telegram_bot import TelegramBot

# Load environment variables
load_dotenv()

# --- Configuration ---
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "HASentinel")
HASS_TOKEN = os.getenv("HASS_TOKEN")

# Home Assistant URL (Auto-Fallback logic is inside connection loop, this is default)
HASS_URL = os.getenv("HASS_URL", "ws://localhost:8123/api/websocket")
HASS_API_URL = os.getenv("HASS_API_URL", HASS_URL.replace("ws://", "http://").replace("wss://", "https://").replace("/websocket", ""))

LOG_FILE = os.getenv("HASS_LOG_FILE", "/config/home-assistant.log")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PID_FILE = "hasentinel.pid"

# Log Filtering
keys_env = os.getenv("LOG_KEYWORDS", "ERROR,WARNING,CRITICAL")
LOG_KEYWORDS = [k.strip() for k in keys_env.split(",")]

# Agents
llm_agent = LLMHandler()
bot = None 

# --- Functions ---

async def fetch_ha_states():
    """Fetches ALL current states from Home Assistant via REST API."""
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{HASS_API_URL}/states"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    states = await response.json()
                    simplified_states = []
                    for s in states:
                        name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
                        simplified_states.append({
                            "entity_id": s["entity_id"],
                            "state": s["state"],
                            "name": name
                        })
                    return json.dumps(simplified_states)
                else:
                    return f"Error: HA API returned {response.status}"
    except Exception as e:
        return f"Error fetching states: {e}"

def validate_config():
    """Checks for critical variables."""
    print("[-] Validating configuration...")
    if not HASS_TOKEN:
        print("[!] FATAL: HASS_TOKEN is missing.")
        sys.exit(1)
    if not TELEGRAM_TOKEN:
        print("[!] WARNING: TELEGRAM_TOKEN is missing. Bot will likely fail.")
    print("[+] Configuration valid.")

async def trigger_action(source, message):
    """Handles Alerts."""
    print(f"\n[TRIGGER] Source: {source}")
    if bot:
        # Ask LLM for insight
        context = f"[{INSTANCE_NAME}] Source: {source}\nMessage: {message}"
        analysis = await llm_agent.analyze_log(context)
        
        # Send formatted alert
        await bot.send_alert(f"🏠 **{INSTANCE_NAME}**\n{analysis}")

async def monitor_logs():
    """Tails the HA log file."""
    print(f"[*] Starting Log Monitor on {LOG_FILE}...")
    while not os.path.exists(LOG_FILE):
        await asyncio.sleep(5)

    try:
        with open(LOG_FILE, "r") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(1)
                    continue

                if any(keyword in line for keyword in LOG_KEYWORDS):
                    # Ignore own Telegram errors to prevent loops
                    if "telegram" in line.lower() or "conflict" in line.lower():
                        continue
                    await trigger_action("LOG", line.strip())
    except Exception as e:
        print(f"[!] Log Monitor died: {e}")

async def monitor_states():
    """Connects to HA WebSocket with Auto-Failover."""
    print(f"[*] Starting State Monitor...")
    urls = [HASS_URL, "ws://localhost:8123/api/websocket", "ws://supervisor/core/websocket"]
    idx = 0

    async with aiohttp.ClientSession() as session:
        while True:
            target = urls[idx % len(urls)]
            try:
                async with session.ws_connect(target) as ws:
                    print(f"[+] Connected to {target}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data["type"] == "auth_required":
                                await ws.send_json({"type": "auth", "access_token": HASS_TOKEN})
                            elif data["type"] == "auth_ok":
                                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                            elif data["type"] == "event":
                                event = data["event"]
                                eid = event["data"]["entity_id"]
                                # Filter: Alerts only on Lights/Switches/Sensors for now (customizable)
                                if eid.startswith(("light.", "switch.", "binary_sensor.")):
                                     # Simple logic: Just notify on change? 
                                     # To reduce spam, maybe we rely on LLM or just Log monitor?
                                     # User asked for "Event State Changes". Let's enable it.
                                     new_s = event["data"]["new_state"]
                                     old_s = event["data"]["old_state"]
                                     if new_s and old_s and new_s["state"] != old_s["state"]:
                                         await trigger_action("STATE", f"{eid}: {old_s['state']} -> {new_s['state']}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except:
                print(f"[!] Connection failed to {target}. Retrying...")
                idx += 1
                await asyncio.sleep(5)

async def main():
    global bot
    
    # PID Lock
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print("FATAL: Already running.")
            sys.exit(1)
        except: pass
    with open(PID_FILE, 'w') as f: f.write(str(os.getpid()))

    validate_config()

    tasks = [monitor_logs(), monitor_states()]

    if TELEGRAM_TOKEN:
        print("[*] Initializing Telegram Bot...")
        bot = TelegramBot(TELEGRAM_TOKEN, llm_handler=llm_agent, state_fetcher=fetch_ha_states)
        bot.instance_name = INSTANCE_NAME # Property Injection
        worker = await bot.run()
        if worker: tasks.append(worker)

    try:
        await asyncio.gather(*tasks)
    finally:
        if bot: await bot.stop()
        if os.path.exists(PID_FILE): os.remove(PID_FILE)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye.")