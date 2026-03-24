import os
import asyncio
from groq import Groq

# Try importing Google Generative AI as fallback
try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

class LLMHandler:
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.gemini_api_key = os.getenv("LLM_API_KEY")
        
        self.provider = None
        self.client = None

        if self.groq_api_key:
            self.provider = "GROQ"
            self.client = Groq(api_key=self.groq_api_key)
            print("[+] LLM Provider: Groq (Llama 3.3)")
        elif self.gemini_api_key and HAS_GENAI:
            self.provider = "GEMINI"
            genai.configure(api_key=self.gemini_api_key)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
            print("[+] LLM Provider: Google Gemini")
        else:
            print("[-] Warning: No valid LLM API Key found (GROQ_API_KEY or LLM_API_KEY). AI features disabled.")

    async def analyze_log(self, log_entry: str) -> str:
        """
        Analyzes a log error and suggests a fix.
        """
        if not self.provider:
            return f"⚠️ **AI Missing**: No API Key.\n`{log_entry[:100]}...`"

        prompt = f"Analyze this Home Assistant log error. Be concise. Explain what is broken and suggest a fix.\n\nLog: {log_entry}"

        try:
            if self.provider == "GROQ":
                chat_completion = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    messages=[
                        {"role": "system", "content": "You are a Home Assistant expert."},
                        {"role": "user", "content": prompt}
                    ],
                    model="llama-3.3-70b-versatile",
                )
                return f"⚡ **Analysis**:\n{chat_completion.choices[0].message.content}"

            elif self.provider == "GEMINI":
                response = await asyncio.to_thread(self.model.generate_content, prompt)
                return f"🤖 **Analysis**:\n{response.text}"

        except Exception as e:
            print(f"[!] LLM Error: {e}")
            return f"⚠️ **AI Error**: Failed to analyze log."

    async def chat(self, user_message: str, system_context: str = None) -> str:
        """
        Handles natural language queries about the home state.
        """
        if not self.provider:
            return "AI is not configured. Please add an API key."

        context_prompt = ""
        if system_context:
            context_prompt = (
                f"You are a Home Assistant assistant. "
                f"Here is the CURRENT STATE of the house (JSON format):\n{system_context}\n\n"
                f"User Question: {user_message}\n"
                f"Answer the user's question based on the state above. Be concise. "
                f"If the user asks to turn something on/off, explain that you can't verify actions yet, only report status."
            )
        else:
            context_prompt = user_message

        try:
            if self.provider == "GROQ":
                chat_completion = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    messages=[
                        {"role": "system", "content": "You are a helpful Home Assistant assistant. Always assume the role of the properties' guardian/monitor."},
                        {"role": "user", "content": context_prompt}
                    ],
                    model="llama-3.3-70b-versatile",
                )
                return chat_completion.choices[0].message.content
            
            elif self.provider == "GEMINI":
                chat = self.model.start_chat(history=[])
                response = await asyncio.to_thread(chat.send_message, context_prompt)
                return response.text
                
        except Exception as e:
            return f"Error with AI Provider: {e}"
