import os
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# Using a robust logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)

class TelegramBot:
    def __init__(self, token, llm_handler=None, state_fetcher=None):
        self.token = token
        self.application = ApplicationBuilder().token(token).build()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.msg_queue = asyncio.Queue()
        self.is_running = True
        self.llm = llm_handler
        self.state_fetcher = state_fetcher
        
        # Default Identity (Will be overwritten by monitor_ha.py)
        self.instance_name = "HASentinel"
        
        # Register handlers
        self.application.add_handler(CommandHandler('start', self.start))
        self.application.add_handler(CommandHandler('status', self.status))
        self.application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_message))
        self.application.add_error_handler(self.error_handler)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and handle Telegram conflicts."""
        e = context.error
        if "Conflict" in str(e) or "terminated by other getUpdates" in str(e):
            if self.is_running:
                print("\n🛑 TELEGRAM CONFLICT DETECTED! Stopping polling.")
                print("✅ Alerts (Push) will still work. Chat (Pull) is disabled.")
                self.is_running = False 
                await self.application.updater.stop()
        else:
            print(f"[Bot] Error: {e}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🛡️ **{self.instance_name} Online**\nI'm monitoring your Home Assistant.\nUse /status to check health."
        )
        if not self.chat_id:
            self.chat_id = update.effective_chat.id
            print(f"[Bot] Chat ID set to: {self.chat_id}")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ **System Normal**\nIdentity: {self.instance_name}\nLogs: Active\nStates: Active"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_text = update.message.text
        chat_type = update.effective_chat.type
        
        # --- Multi-Site Filtering ---
        # In a group chat, ignore messages unless they mention our Name
        if chat_type in ['group', 'supergroup']:
            if self.instance_name.lower() not in user_text.lower():
                # Not explicitly for us. But maybe LLM can decide?
                # For safety/noise reduction, we default to ignore unless name is spoken.
                pass 
                
        # Send a "typing..." status
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        response_text = "I'm sorry, I cannot process your request right now."
        
        if self.llm and self.state_fetcher:
             # 1. Fetch current HA State
            ha_state = await self.state_fetcher()
            
            # 2. Add Context
            full_context = (
                f"System Identity: {self.instance_name}\n"
                f"Chat Type: {chat_type}\n"
                f"Device States (JSON): {ha_state}\n\n"
                f"INSTRUCTIONS:\n"
                f"1. Check if the user is asking about THIS specific home ('{self.instance_name}').\n"
                f"2. If the user asks about a different home, reply with 'IGNORE'.\n"
                f"3. Answer concisely."
            )
            
            # 3. Ask LLM
            response_text = await self.llm.chat(user_text, system_context=full_context)
            
            if "IGNORE" in response_text or not response_text.strip():
                return # Stay silent
        else:
            response_text = "⚠️ **Config Error**: Brain missing."
            
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🏠 **{self.instance_name}**: {response_text}",
            parse_mode='Markdown'
        )

    async def send_alert(self, message):
        """Adds message to the queue for safe sending."""
        if not self.chat_id:
            return
        await self.msg_queue.put(message)

    async def _queue_worker(self):
        """Background task to process messages one by one."""
        print("[Bot] Queue worker started.")
        while True: # Keep worker alive even if polling stops
            try:
                try:
                    message = await asyncio.wait_for(self.msg_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if not self.is_running and self.msg_queue.empty():
                        # If polling stopped AND queue empty, we can maybe chill, but let's keep checking for alerts
                        pass
                    continue

                if self.chat_id:
                    if len(message) > 4000:
                        message = message[:4000] + "\n...(truncated)"
                    try:
                        await self.application.bot.send_message(chat_id=self.chat_id, text=message, parse_mode='Markdown')
                    except Exception as e:
                        print(f"[Bot] Send failed: {e}")
                        
                self.msg_queue.task_done()
                await asyncio.sleep(1.0) # Rate limit
                
            except Exception as e:
                print(f"[Bot] Worker error: {e}")
                await asyncio.sleep(1)

    async def run(self):
        """Runs the bot polling and the queue worker."""
        print("[Bot] Starting Telegram Polling...")
        await self.application.initialize()
        await self.application.start()
        
        worker_task = asyncio.create_task(self._queue_worker())
        
        try:
            await self.application.updater.start_polling()
        except Exception as e:
            if "Conflict" in str(e):
                print("🛑 TELEGRAM CONFLICT. Chat disabled.")
                await self.application.updater.stop()
        
        return worker_task
        
    async def stop(self):
        self.is_running = False
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
