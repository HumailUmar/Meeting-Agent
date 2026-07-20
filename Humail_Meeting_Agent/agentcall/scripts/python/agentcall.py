"""AgentCall API client."""

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator, Optional

import aiohttp
import websockets

CONFIG_PATH = Path.home() / ".agentcall" / "config.json"

# Default network timeout for all HTTP calls (seconds).
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=float(os.getenv("AGENTCALL_HTTP_TIMEOUT", "30")))


def load_api_key() -> str:
    """Load API key: env var first, then ~/.agentcall/config.json."""
    key = os.environ.get("AGENTCALL_API_KEY", "")
    if key:
        return key
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("api_key", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def load_api_url() -> str:
    """Load API URL: env var first, then ~/.agentcall/config.json, then default."""
    url = os.environ.get("AGENTCALL_API_URL", "")
    if url:
        return url
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("api_url", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""


class AgentCallClient:
    """HTTP + WebSocket client for AgentCall API."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = ""):
        self.api_key = api_key or load_api_key()
        self.base_url = base_url or load_api_url() or "https://api.agentcall.dev"
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=DEFAULT_TIMEOUT,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # --- Call Management ---

    async def create_call(self, **kwargs) -> dict:
        """Create a new call. Returns call_id, ws_url, tunnel_url, status."""
        session = await self._get_session()
        async with session.post(f"{self.base_url}/v1/calls", json=kwargs) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if not isinstance(data, dict):
                raise ValueError(f"create_call returned unexpected payload: {type(data)}")
            return data

    async def get_call(self, call_id: str) -> dict:
        """Get call details."""
        session = await self._get_session()
        async with session.get(f"{self.base_url}/v1/calls/{call_id}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def end_call(self, call_id: str) -> dict:
        """End a call."""
        session = await self._get_session()
        async with session.delete(f"{self.base_url}/v1/calls/{call_id}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def list_calls(self) -> list:
        """List active calls."""
        session = await self._get_session()
        async with session.get(f"{self.base_url}/v1/calls") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_transcript(self, call_id: str, fmt: str = "json") -> dict:
        """Download call transcript."""
        session = await self._get_session()
        async with session.get(
            f"{self.base_url}/v1/calls/{call_id}/transcript",
            params={"format": fmt},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    # --- WebSocket ---

    async def connect_ws(self, call_id: str) -> AsyncIterator[dict]:
        """Connect to a call's WebSocket and yield events."""
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        uri = f"{ws_url}/v1/calls/{call_id}/ws?api_key={self.api_key}"

        async with websockets.connect(uri) as ws:
            self._ws = ws
            async for message in ws:
                try:
                    event = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    # Skip malformed frames instead of killing the consumer.
                    continue
                if isinstance(event, dict):
                    yield event

    async def send_command(self, command: dict):
        """
        Send a command via the WebSocket with automatic retry on transient errors.
        Retries up to 3 times with exponential backoff. Returns True on success,
        raises RuntimeError after persistent failure so callers can react.
        """
        import sys
        last_err = None
        for attempt in range(3):
            try:
                if hasattr(self, "_ws") and self._ws:
                    await self._ws.send(json.dumps(command))
                    return True
            except Exception as e:
                last_err = e
                print(f"[agentcall] send failed (attempt {attempt + 1}/3): {e}",
                      file=sys.stderr, flush=True)
                await asyncio.sleep(0.5 * (attempt + 1))
        err = last_err or RuntimeError("WebSocket not connected")
        raise RuntimeError(
            f"[agentcall] dropped command after 3 failures: {command.get('type', '?')} ({err})"
        )

    # --- TTS ---

    async def tts_generate(self, text: str, voice: str = "af_heart", speed: float = 1.0) -> dict:
        """Generate TTS audio."""
        session = await self._get_session()
        async with session.post(
            f"{self.base_url}/v1/tts/generate",
            json={"text": text, "voice": voice, "speed": speed},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def tts_voices(self) -> list:
        """List available TTS voices."""
        session = await self._get_session()
        async with session.get(f"{self.base_url}/v1/tts/voices") as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("voices", [])
