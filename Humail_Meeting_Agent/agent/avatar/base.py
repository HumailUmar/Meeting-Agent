from abc import ABC, abstractmethod

class AvatarProviderQuotaError(Exception):
    pass

class AvatarProvider(ABC):
    @abstractmethod
    async def create_avatar(self, photo_path: str, resume: str, name: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def clone_voice(self, voice_sample_path: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def start_stream(self, avatar_id: str, voice_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def send_audio(self, session_id: str, audio_bytes: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop_stream(self, session_id: str) -> None:
        raise NotImplementedError
