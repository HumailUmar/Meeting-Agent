import asyncio
import json
import logging
import os
import sys
from typing import AsyncGenerator, List, Optional
import aiohttp

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Maximum conversation turns (user+assistant) to retain after the system prompt.
# Prevents unbounded history growth (OOM / context overflow) on long interviews.
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "20"))

# aiohttp client timeout for external LLM calls (seconds).
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=float(os.getenv("LLM_HTTP_TIMEOUT", "60")))

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

    def _trim_history(self):
        """Keep the initial system/persona turn(s) and cap recent turns to avoid OOM/context overflow."""
        if self.provider == "gemini":
            # history[0] = user (system instruction), history[1] = model (ack)
            keep = 2
        else:
            keep = 1  # ollama/system prompt
        recent = self.history[keep:]
        max_recent = MAX_HISTORY_TURNS * 2
        if len(recent) > max_recent:
            recent = recent[-max_recent:]
        self.history = self.history[:keep] + recent

    async def generate_answer(self, question: str) -> AsyncGenerator[str, None]:
        """
        Appends the question to the history and streams the response token by token.
        
        Args:
            question (str): The interviewer's question.
            
        Yields:
            str: Response tokens/words as they are generated.
        """
        question = (question or "").strip()
        if not question:
            logger.warning("generate_answer called with empty question; ignoring.")
            return

        logger.info(f"Generating answer using {self.provider} ({self.model}) for: '{question[:80]}'")

        fallback_ollama = "Well, honestly, I'm having a brief connection issue with my local LLM server, but I'd love to share my experience with that in a moment."
        fallback_gemini = "Actually, there is a minor network interruption on my end. But going back to your question, I believe my skills are highly aligned."

        if self.provider == "ollama":
            self.history.append({"role": "user", "content": question})

            try:
                import ollama
                client = ollama.AsyncClient(host=config.OLLAMA_HOST)
                full_response = ""
                # NOTE: client.chat() is an async generator; do NOT await it.
                async for chunk in client.chat(model=self.model, messages=self.history, stream=True):
                    content = (chunk.get("message", {}) or {}).get("content", "")
                    if content:
                        full_response += content
                        yield content

                self.history.append({"role": "assistant", "content": full_response})
                self._trim_history()
            except Exception as e:
                logger.error(f"Ollama error: {e}")
                yield fallback_ollama
                self.history.append({"role": "assistant", "content": fallback_ollama})
                self._trim_history()

        elif self.provider == "gemini":
            self.history.append({"role": "user", "parts": [{"text": question}]})

            api_key = os.getenv("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                logger.error("GEMINI_API_KEY is not set.")
                yield fallback_gemini
                self.history.append({"role": "model", "parts": [{"text": fallback_gemini}]})
                return

            # Gemini streamGenerateContent URL
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent?key={api_key}"
            payload = {"contents": self.history}

            full_response = ""
            try:
                async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                    async with session.post(url, json=payload) as resp:
                        if resp.status != 200:
                            err_msg = await resp.text()
                            logger.error(f"Gemini API error ({resp.status}): {err_msg[:500]}")
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
                                candidates = (chunk_json.get("candidates") or [])
                                if not candidates:
                                    continue
                                parts = (candidates[0].get("content") or {}).get("parts") or []
                                if not parts:
                                    continue
                                part_text = parts[0].get("text", "")
                                if part_text:
                                    full_response += part_text
                                    yield part_text
                            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                                logger.debug(f"Skipping malformed Gemini chunk: {e}")

                self.history.append({"role": "model", "parts": [{"text": full_response}]})
                self._trim_history()
            except Exception as e:
                logger.error(f"Gemini API connection error: {e}")
                yield fallback_gemini
                self.history.append({"role": "model", "parts": [{"text": fallback_gemini}]})
                self._trim_history()
