import aiohttp
import asyncio
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from aiohttp import ClientResponseError
from .base import AvatarProvider, AvatarProviderQuotaError
import logging

logger = logging.getLogger(__name__)

class DIdProvider(AvatarProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.d-id.com"
    
    def _log_time(self, method_name, start_time):
        elapsed = time.time() - start_time
        logger.info(f"D-ID {method_name} completed in {elapsed:.3f}s")
    
    @retry(
        retry=retry_if_exception_type((ClientResponseError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        reraise=True,
    )
    async def _post_with_retry(self, session: aiohttp.ClientSession, endpoint: str, **kwargs):
        async with session.post(f"{self.base_url}{endpoint}", **kwargs) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def create_avatar(self, photo_path: str, resume: str, name: str) -> str:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            with open(photo_path, "rb") as photo_file:
                data = aiohttp.FormData()
                data.add_field("photo", photo_file, filename=photo_path)
                data.add_field("resume", resume)
                data.add_field("name", name)
                async with session.post(
                    f"{self.base_url}/avatars", data=data, headers={"Authorization": f"Bearer {self.api_key}"}
                ) as resp:
                    if resp.status == 401 or resp.status == 402 or resp.status == 429:
                        raise AvatarProviderQuotaError(await resp.text())
                    resp.raise_for_status()
                    result = await resp.json()
                    self._log_time("create_avatar", start_time)
                    return result["avatar_id"]
    
    async def clone_voice(self, voice_sample_path: str) -> str:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            with open(voice_sample_path, "rb") as voice_file:
                data = aiohttp.FormData()
                data.add_field("audio", voice_file, filename=voice_sample_path)
                async with session.post(
                    f"{self.base_url}/voices", data=data, headers={"Authorization": f"Bearer {self.api_key}"}
                ) as resp:
                    if resp.status == 401 or resp.status == 402 or resp.status == 429:
                        raise AvatarProviderQuotaError(await resp.text())
                    resp.raise_for_status()
                    result = await resp.json()
                    self._log_time("clone_voice", start_time)
                    return result["voice_id"]
    
    async def start_stream(self, avatar_id: str, voice_id: str) -> dict:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            async def _create_session():
                async with session.post(
                    f"{self.base_url}/streams",
                    json={"avatar_id": avatar_id, "voice_id": voice_id},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as resp:
                    if resp.status == 401 or resp.status == 402 or resp.status == 429:
                        raise AvatarProviderQuotaError(await resp.text())
                    resp.raise_for_status()
                    result = await resp.json()
                    return result
            
            result = await _create_session()
            if result.get("status") != "pending":
                self._log_time("start_stream", start_time)
                return result
            
            for attempt in range(1, 4):
                await asyncio.sleep(2)
                try:
                    result = await _create_session()
                    if result.get("status") != "pending":
                        self._log_time("start_stream", start_time)
                        return result
                except ClientResponseError as e:
                    if e.status == 401 or e.status == 402 or e.status == 429:
                        raise AvatarProviderQuotaError(await e.response.content.read())
                    raise
            
            self._log_time("start_stream", start_time)
            raise RuntimeError("Failed to start stream after 3 attempts")
    
    async def stop_stream(self, session_id: str) -> None:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.delete(
                    f"{self.base_url}/streams/{session_id}", headers={"Authorization": f"Bearer {self.api_key}"}
                ) as resp:
                    if resp.status == 404:
                        self._log_time("stop_stream", start_time)
                        return
                    resp.raise_for_status()
            except ClientResponseError as e:
                if e.status not in (401, 402, 429):
                    raise
            self._log_time("stop_stream", start_time)
    
    async def send_audio(self, session_id: str, audio_bytes: bytes) -> None:
        raise NotImplementedError("D-ID does not support sending audio chunks during an active stream session.")
