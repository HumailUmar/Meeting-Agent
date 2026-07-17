import asyncio
import json
import logging
import os
import sys
from typing import AsyncGenerator, Optional
import aiohttp

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default Persona Prompt including candidate name, education, work experience, skills, personality, and speaking style rules
DEFAULT_PERSONA = """
Candidate Profile:
- Name: Humail Umar
- Education: Bachelor of Science in Artificial Intelligence & Computer Science.
- Work Experience: Expert AI Agent Developer and Software Engineer with experience in building autonomous systems, full-stack AI applications, meeting bots, and real-time audio/video AI pipelines.
- Skills: Python, Node.js, FastAPI, WebSockets, Real-time WebRTC/audio, LLMs (Ollama, Gemini, OpenAI), Agentic frameworks, Voice cloning, Docker, Git.
- Personality Traits: Approachable, confident, articulate, highly professional, and direct.
- Speaking Style Rules:
  1. Speak like a real human. Never say "As an AI..." or "Based on my programming...". You are Humail Umar, a human candidate being interviewed.
  2. Speak in first-person (use "I", "me", "my").
  3. Use natural conversational fillers sparingly but realistically (e.g., "Well", "you know", "honestly", "I mean", "actually").
  4. Keep responses conversational, concise, and focused. Avoid overly structured lists or academic formatting (like bullet points or markdown bolding) in spoken answers.
  5. If asked about something you don't know, respond honestly with how you would troubleshoot or learn it.
"""

class AIBrain:
    """
    Handles conversation history, persona prompt integration, and response generation 
    using Ollama or Gemini API with token streaming.
    """
    def __init__(self, provider: str = "ollama", model: Optional[str] = None, persona: Optional[str] = None):
        self.provider = provider.lower()  # "ollama" or "gemini"
        self.persona = persona or DEFAULT_PERSONA
        self.history = []
        
        # Select default model based on the provider if not specified
        if model:
            self.model = model
        else:
            self.model = "llama3" if self.provider == "ollama" else "gemini-1.5-flash"
            
        self._init_history()

    def _init_history(self):
        """Initializes the conversation history with the system/persona prompt."""
        system_instruction = (
            f"You are participating in a live job interview.\n"
            f"Here is your background and persona:\n\n{self.persona}\n\n"
            f"CRITICAL RULES:\n"
            f"- ALWAYS stay in character. You are the candidate, NOT an AI.\n"
            f"- Your answers will be spoken out loud via text-to-speech. "
            f"Keep them relatively concise (typically 2-4 sentences or a short paragraph) "
            f"and conversational. Do not use bullet points, numbered lists, or markdown styling (like asterisks for bolding) in your output.\n"
            f"- Listen to the interviewer's question, maintain context from the previous questions and answers, and reply naturally."
        )
        
        if self.provider == "ollama":
            self.history = [{"role": "system", "content": system_instruction}]
        elif self.provider == "gemini":
            # For Gemini, we place the system prompt in the initial turn or system Instruction format
            self.history = [
                {"role": "user", "parts": [{"text": system_instruction}]},
                {"role": "model", "parts": [{"text": "Understood. I am ready to begin the interview. I will stay in character as Humail Umar, speak in a conversational human tone, avoid AI references, and keep my answers concise and natural."}]}
            ]

    def clear_history(self):
        """Resets the history while maintaining the initial persona setup."""
        self._init_history()

    async def generate_answer(self, question: str) -> AsyncGenerator[str, None]:
        """
        Appends the question to the history and streams the response token by token.
        
        Args:
            question (str): The interviewer's question.
            
        Yields:
            str: Response tokens/words as they are generated.
        """
        logger.info(f"Generating answer using {self.provider} ({self.model}) for: '{question}'")
        
        if self.provider == "ollama":
            self.history.append({"role": "user", "content": question})
            
            try:
                import ollama
                client = ollama.AsyncClient(host=config.OLLAMA_HOST)
                full_response = ""
                async for chunk in await client.chat(model=self.model, messages=self.history, stream=True):
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        full_response += content
                        yield content
                
                self.history.append({"role": "assistant", "content": full_response})
            except Exception as e:
                logger.error(f"Ollama error: {e}")
                fallback = "Well, honestly, I'm having a brief connection issue with my local LLM server, but I'd love to share my experience with that in a moment."
                yield fallback
                self.history.append({"role": "assistant", "content": fallback})
                
        elif self.provider == "gemini":
            self.history.append({"role": "user", "parts": [{"text": question}]})
            
            api_key = os.getenv("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                logger.error("GEMINI_API_KEY is not set.")
                fallback = "Honestly, I seem to be experiencing a bit of network lag and cannot access my knowledge base right now."
                yield fallback
                self.history.append({"role": "model", "parts": [{"text": fallback}]})
                return

            # Gemini streamGenerateContent URL
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent?key={api_key}"
            payload = {"contents": self.history}
            
            full_response = ""
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload) as resp:
                        if resp.status != 200:
                            err_msg = await resp.text()
                            logger.error(f"Gemini API error ({resp.status}): {err_msg}")
                            raise RuntimeError("Gemini API error")
                            
                        # Parse the JSON streaming response
                        async for line in resp.content:
                            line_str = line.decode('utf-8').strip()
                            if not line_str:
                                continue
                            
                            # Clean array elements if returned in a stream array
                            if line_str.startswith("data:"):
                                line_str = line_str[5:].strip()
                            elif line_str.startswith("[") or line_str.startswith(","):
                                line_str = line_str.strip("[], ")
                            
                            if not line_str:
                                continue
                                
                            try:
                                chunk_json = json.loads(line_str)
                                part_text = chunk_json["candidates"][0]["content"]["parts"][0]["text"]
                                if part_text:
                                    full_response += part_text
                                    yield part_text
                            except Exception:
                                pass
                                
                self.history.append({"role": "model", "parts": [{"text": full_response}]})
            except Exception as e:
                logger.error(f"Gemini API connection error: {e}")
                fallback = "Actually, there is a minor network interruption on my end. But going back to your question, I believe my skills are highly aligned."
                yield fallback
                self.history.append({"role": "model", "parts": [{"text": fallback}]})
