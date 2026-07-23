import logging
from .base import AvatarProvider

logger = logging.getLogger(__name__)

class AvatarManager:
    """
    Thin wrapper that delegates avatar lifecycle calls to the configured
    AvatarProvider implementation.
    """
    def __init__(self, provider: AvatarProvider):
        self.provider = provider

    async def create_avatar(self, photo_path: str, resume: str, name: str) -> str:
        if self.provider is None:
            return ""
        return await self.provider.create_avatar(photo_path, resume, name)

    async def clone_voice(self, voice_sample_path: str) -> str:
        if self.provider is None:
            return ""
        return await self.provider.clone_voice(voice_sample_path)

    async def start_stream(self, avatar_id: str, voice_id: str) -> dict:
        if self.provider is None:
            return {}
        return await self.provider.start_stream(avatar_id, voice_id)

    async def send_audio(self, session_id: str, audio_bytes: bytes) -> None:
        if self.provider is None:
            return
        return await self.provider.send_audio(session_id, audio_bytes)

    async def stop_stream(self, session_id: str) -> None:
        if self.provider is None:
            return
        return await self.provider.stop_stream(session_id)