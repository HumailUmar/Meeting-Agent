import aiohttp
import asyncio
import json
import os
import sys
import unittest
from abc import ABC
from unittest.mock import AsyncMock, MagicMock, patch

# Setup paths to ensure we can import our modules correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from agent.meeting import join_meeting
from agent.audio import AudioTranscriber
from agent.brain import AIBrain, DEFAULT_PERSONA
from agent.voice import VoiceCloner
from agent.avatar import AvatarManager
from agent.avatar.base import AvatarProvider, AvatarProviderQuotaError
from agent.avatar.did_provider import DIdProvider
from agent.orchestrator import InterviewAgentOrchestrator


class TestAIInterviewAgent(unittest.IsolatedAsyncioTestCase):
    """
    Test suite for checking the functionality, configurations, and API 
    integrations of the Autonomous AI Interview Agent.
    """

    def setUp(self):
        # Set dummy env keys for mock testing if not present
        os.environ["DEEPGRAM_API_KEY"] = os.getenv("DEEPGRAM_API_KEY") or "test_deepgram_key"
        os.environ["AGENTCALL_API_KEY"] = os.getenv("AGENTCALL_API_KEY") or "test_agentcall_key"
        os.environ["PIKA_DEV_KEY"] = os.getenv("PIKA_DEV_KEY") or "test_pika_key"

    def test_config_loading(self):
        """Verifies that configuration variables load from environment/dotenv."""
        print("\n[TEST] Verifying configuration loading...")
        self.assertIsNotNone(config.OLLAMA_HOST)
        self.assertIsNotNone(config.AVATAR_IMAGE_PATH)
        print("[SUCCESS] Configurations successfully validated!")

    @patch("agent.meeting.AgentCallClient")
    async def test_meeting_joining(self, mock_client_class):
        """Verifies the bot joins Google Meet with the correct direct webpage-av template."""
        print("\n[TEST] Verifying meeting joining module...")

        # Mock AgentCall API Response
        mock_client_instance = mock_client_class.return_value
        mock_client_instance.create_call = AsyncMock(return_value={
            "call_id": "call-12345",
            "ws_url": "wss://api.agentcall.dev/v1/calls/call-12345/ws",
            "status": "created"
        })

        test_url = "https://meet.google.com/abc-defg-hij"
        bot_name = "Humail"

        session = await join_meeting(test_url, bot_name)

        # Assert correct calling parameters to the SDK
        mock_client_instance.create_call.assert_called_once_with(
            meet_url=test_url,
            bot_name=bot_name,
            mode="webpage-av",
            voice_strategy="direct",
            transcription=True,
            ui_template="avatar"
        )
        self.assertEqual(session["call_id"], "call-12345")
        self.assertEqual(session["ws_url"], "wss://api.agentcall.dev/v1/calls/call-12345/ws")
        print("[SUCCESS] Meeting join parameters and Active Session reference verified!")

    async def test_brain_answer_generation(self):
        """Verifies that AI Brain handles conversational history and generates answers."""
        print("\n[TEST] Verifying AI Brain (Answer Generation)...")
        brain = AIBrain(provider="ollama", model="llama3")

        # Confirm persona/system prompt sets the in-character rule (negation of 'As an AI').
        self.assertIn("Never say", brain.history[0]["content"])
        self.assertIn("Humail Umar", brain.persona)

        # Mock the Ollama Async client to test answer streaming
        mock_chunk_1 = {"message": {"content": "Well, "}}
        mock_chunk_2 = {"message": {"content": "honestly, "}}
        mock_chunk_3 = {"message": {"content": "I have extensive experience."}}

        async def mock_chat_generator(*args, **kwargs):
            for chunk in [mock_chunk_1, mock_chunk_2, mock_chunk_3]:
                yield chunk

        # Ensure the `ollama` module exists so it can be patched even if the
        # optional dependency isn't installed in the test environment.
        import types
        if "ollama" not in sys.modules:
            mod = types.ModuleType("ollama")
            mod.AsyncClient = object  # placeholder; patch() overrides it
            sys.modules["ollama"] = mod

        with patch("ollama.AsyncClient") as mock_ollama_client:
            mock_client_instance = mock_ollama_client.return_value
            mock_client_instance.chat = mock_chat_generator

            generated_tokens = []
            async for token in brain.generate_answer("Tell me about your skills."):
                generated_tokens.append(token)

            full_response = "".join(generated_tokens)
            self.assertEqual(full_response, "Well, honestly, I have extensive experience.")
            # Check conversation history update
            self.assertEqual(brain.history[-1]["role"], "assistant")
            self.assertEqual(brain.history[-1]["content"], full_response)

        print("[SUCCESS] Brain Persona rules and token streaming generation verified!")

    def test_audio_transcription_connect(self):
        """Verifies the audio transcription module configures correct Deepgram websocket URLs."""
        print("\n[TEST] Verifying Audio Capture & Transcription configuration...")
        transcriber = AudioTranscriber(api_key="mock_dg_key")
        self.assertEqual(transcriber.api_key, "mock_dg_key")
        self.assertEqual(transcriber.sample_rate, 16000)
        self.assertEqual(transcriber.channels, 1)
        print("[SUCCESS] Audio capture channels and sample rates successfully verified!")

    @patch("agent.voice.COQUI_AVAILABLE", True)
    @patch("agent.voice.TTS", create=True)
    @patch("subprocess.run")
    async def test_voice_cloning_and_tts(self, mock_subprocess, mock_tts_class):
        """Verifies Coqui XTTS speech conversion, resampling, and audio injection logic."""
        print("\n[TEST] Verifying Voice Cloning & TTS pipeline...")
        cloner = VoiceCloner(voice_sample_path="dummy_sample.wav", voice_clone_provider="local")
        cloner.is_initialized = True

        mock_tts_instance = mock_tts_class.return_value
        cloner.tts = mock_tts_instance

        # Mock voice cloner file synthesis
        cloner.generate_cloned_audio = MagicMock(return_value=True)

        # Mock successful ffmpeg PCM resampling subprocess
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        # Mock AgentCall client send command
        mock_client = AsyncMock()

        # Patch 'open' and 'os.path.exists' to simulate reading the raw converted PCM file
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=b"rawpcmbytes123")):
            await cloner.speak(mock_client, "Hello, I am Humail.")

            # Verify avatar was set to speaking, audio injected, and state returned to listening
            mock_client.send_command.assert_any_call({"type": "voice.state_update", "state": "speaking"})
            mock_client.send_command.assert_any_call({"type": "audio.inject", "data": "cmF3cGNtYnl0ZXMxMjM="})  # b64 of rawpcmbytes123
            mock_client.send_command.assert_any_call({"type": "voice.state_update", "state": "listening"})

        print("[SUCCESS] Voice cloning generation, ffmpeg resampling, and audio injection commands verified!")


    @patch("asyncio.create_subprocess_exec")
    async def test_avatar_provider_contract(self, mock_subprocess_exec):
        """Verifies AvatarProvider abstract contract and AvatarManager delegation."""
        print("\n[TEST] Verifying Avatar Provider Contract and Delegation...")

        class ConcreteProvider(AvatarProvider):
            async def create_avatar(self, photo_path: str, resume: str, name: str) -> str:
                return "avatar-123"
            async def clone_voice(self, voice_sample_path: str) -> str:
                return "voice-456"
            async def start_stream(self, avatar_id: str, voice_id: str) -> dict:
                return {"session_id": "sess-1", "stream_url": "http://example.com/stream"}
            async def send_audio(self, session_id: str, audio_bytes: bytes) -> None:
                pass
            async def stop_stream(self, session_id: str) -> None:
                pass

        provider = ConcreteProvider()
        manager = AvatarManager(provider=provider)

        self.assertEqual(await manager.create_avatar("img.png", "resume", "Bot"), "avatar-123")
        self.assertEqual(await manager.clone_voice("voice.wav"), "voice-456")
        result = await manager.start_stream("avatar-123", "voice-456")
        self.assertEqual(result["session_id"], "sess-1")
        await manager.send_audio("sess-1", b"audio")
        await manager.stop_stream("sess-1")

        noop_manager = AvatarManager(provider=None)
        self.assertEqual(await noop_manager.create_avatar("img.png", "resume", "Bot"), "")
        self.assertEqual(await noop_manager.clone_voice("voice.wav"), "")
        self.assertEqual(await noop_manager.start_stream("a", "v"), {})
        await noop_manager.send_audio("s", b"a")
        await noop_manager.stop_stream("s")

        print("[SUCCESS] Avatar provider contract and delegation verified!")


class TestAvatarProviderContract(unittest.IsolatedAsyncioTestCase):
    """Verifies that AvatarProvider cannot be instantiated directly
    and that its abstract methods raise NotImplementedError when invoked
    from the base class."""

    def test_instantiation_raises_type_error(self):
        with self.assertRaises(TypeError):
            AvatarProvider()

    async def test_each_method_raises_not_implemented_error(self):
        with self.subTest(method="create_avatar"):
            with self.assertRaises(NotImplementedError):
                await AvatarProvider.create_avatar(None, "photo", "resume", "name")
        with self.subTest(method="clone_voice"):
            with self.assertRaises(NotImplementedError):
                await AvatarProvider.clone_voice(None, "voice.wav")
        with self.subTest(method="start_stream"):
            with self.assertRaises(NotImplementedError):
                await AvatarProvider.start_stream(None, "a", "v")
        with self.subTest(method="send_audio"):
            with self.assertRaises(NotImplementedError):
                await AvatarProvider.send_audio(None, "sess", b"data")
        with self.subTest(method="stop_stream"):
            with self.assertRaises(NotImplementedError):
                await AvatarProvider.stop_stream(None, "sess")


class TestAvatarManagerDelegation(unittest.IsolatedAsyncioTestCase):
    """Verifies AvatarManager delegates calls to its AvatarProvider."""

    async def test_create_avatar_delegates_with_same_args(self):
        mock_provider = MagicMock(spec=AvatarProvider)
        mock_provider.create_avatar = AsyncMock(return_value="avatar-123")
        manager = AvatarManager(provider=mock_provider)

        result = await manager.create_avatar("img.png", "resume", "Bot")
        mock_provider.create_avatar.assert_called_once_with(
            "img.png", "resume", "Bot"
        )
        self.assertEqual(result, "avatar-123")


class TestDIdProviderLifecycle(unittest.IsolatedAsyncioTestCase):
    """Test the full lifecycle of D-ID provider operations."""

    async def test_full_lifecycle(self):
        """Mock aiohttp.ClientSession.post to return fake avatar_id, voice_id, stream_url.
        Assert full lifecycle: create_avatar -> clone_voice -> start_stream -> stop_stream.
        Assert start_stream returns a dict containing stream_url."""

        with patch("agent.avatar.didi_provider.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            mock_resp_avatar = MagicMock()
            mock_resp_avatar.status = 200
            mock_resp_avatar.raise_for_status = MagicMock()
            mock_resp_avatar.json = AsyncMock(return_value={"avatar_id": "avatar-123"})
            mock_resp_avatar.__aenter__ = AsyncMock(return_value=mock_resp_avatar)
            mock_resp_avatar.__aexit__ = AsyncMock(return_value=False)

            mock_resp_voice = MagicMock()
            mock_resp_voice.status = 200
            mock_resp_voice.raise_for_status = MagicMock()
            mock_resp_voice.json = AsyncMock(return_value={"voice_id": "voice-456"})
            mock_resp_voice.__aenter__ = AsyncMock(return_value=mock_resp_voice)
            mock_resp_voice.__aexit__ = AsyncMock(return_value=False)

            mock_resp_stream = MagicMock()
            mock_resp_stream.status = 200
            mock_resp_stream.raise_for_status = MagicMock()
            mock_resp_stream.json = AsyncMock(return_value={"session_id": "sess-1", "stream_url": "http://example.com/stream", "status": "ready"})
            mock_resp_stream.__aenter__ = AsyncMock(return_value=mock_resp_stream)
            mock_resp_stream.__aexit__ = AsyncMock(return_value=False)

            mock_resp_delete = MagicMock()
            mock_resp_delete.status = 200
            mock_resp_delete.raise_for_status = MagicMock()
            mock_resp_delete.__aenter__ = AsyncMock(return_value=mock_resp_delete)
            mock_resp_delete.__aexit__ = AsyncMock(return_value=False)

            def post_side_effect(url, **kwargs):
                if "/avatars" in url:
                    return mock_resp_avatar
                elif "/voices" in url:
                    return mock_resp_voice
                elif "/streams" in url:
                    return mock_resp_stream
                raise AssertionError(f"Unexpected POST URL: {url}")

            mock_session.post = MagicMock(side_effect=post_side_effect)
            mock_session.delete = MagicMock(return_value=mock_resp_delete)

            with patch("builtins.open", unittest.mock.mock_open(read_data=b"fake_image_bytes")):
                provider = DIdProvider(api_key="test_key")
                avatar_id = await provider.create_avatar("dummy_photo.jpg", "dummy_resume", "TestBot")
                self.assertEqual(avatar_id, "avatar-123")

                voice_id = await provider.clone_voice("dummy_voice.wav")
                self.assertEqual(voice_id, "voice-456")

                result = await provider.start_stream(avatar_id, voice_id)
                self.assertIn("stream_url", result)
                self.assertEqual(result["stream_url"], "http://example.com/stream")

                await provider.stop_stream("sess-1")


class TestDIdProviderQuota(unittest.IsolatedAsyncioTestCase):
    """Test that AvatarProviderQuotaError is raised on 401, 402, or 429 responses."""

    async def test_quota_error_on_429(self):
        """Mock a 429 response from D-ID. Assert AvatarProviderQuotaError is raised."""
        with patch("agent.avatar.didi_provider.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            mock_resp = MagicMock()
            mock_resp.status = 429
            mock_resp.raise_for_status = MagicMock()
            mock_resp.text = AsyncMock(return_value="Rate limit exceeded")
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session.post = MagicMock(return_value=mock_resp)

            with patch("builtins.open", unittest.mock.mock_open(read_data=b"fake_image_bytes")):
                provider = DIdProvider(api_key="test_key")
                with self.assertRaises(AvatarProviderQuotaError) as cm:
                    await provider.create_avatar("dummy_photo.jpg", "dummy_resume", "TestBot")
                self.assertIn("Rate limit exceeded", str(cm.exception))


class TestDIdProviderStopStream404(unittest.IsolatedAsyncioTestCase):
    """Test that stop_stream handles 404 gracefully without raising."""
    async def test_stop_stream_404(self):
        """Mock 404 on DELETE and assert stop_stream does not raise."""
        with patch("agent.avatar.didi_provider.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            mock_resp = MagicMock()
            mock_resp.status = 404
            mock_resp.raise_for_status = MagicMock()
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session.delete = MagicMock(return_value=mock_resp)

            provider = DIdProvider(api_key="test_key")
            await provider.stop_stream("sess-1")

class TestOrchestratorProviderFallback(unittest.IsolatedAsyncioTestCase):
    """Test orchestrator falls back to AgentCall TTS when avatar provider fails."""

    async def test_fallback_when_start_stream_raises(self):
        """Mock a DIdProvider whose start_stream raises on first call. 
        Verify orchestrator catches the error, falls back to AgentCall TTS, and continues."""

        failing_provider = MagicMock(spec=DIdProvider)
        failing_provider.create_avatar = AsyncMock(return_value="avatar-123")
        failing_provider.clone_voice = AsyncMock(return_value="voice-456")
        failing_provider.start_stream = AsyncMock(side_effect=Exception("D-ID stream failed"))
        failing_provider.send_audio = AsyncMock()
        failing_provider.stop_stream = AsyncMock()

        with patch("agent.orchestrator.join_meeting", new_callable=AsyncMock) as mock_join, \
             patch("agent.orchestrator.AudioTranscriber") as mock_transcriber_cls, \
             patch("agent.orchestrator.AIBrain", new_callable=MagicMock) as mock_brain_cls, \
             patch("agent.orchestrator.VoiceCloner", new_callable=MagicMock) as mock_cloner_cls, \
             patch("agent.orchestrator.AvatarManager", new_callable=MagicMock) as mock_avatar_mgr_cls, \
             patch("agent.orchestrator.state_store") as mock_state_store:

            mock_client = MagicMock()
            mock_client.send_command = AsyncMock()
            mock_client.end_call = AsyncMock()
            mock_client.close = AsyncMock()

            async def empty_ws():
                if False:
                    yield

            mock_client.connect_ws = MagicMock(return_value=empty_ws())

            mock_join.return_value = {
                "call_id": "call-12345",
                "client": mock_client,
                "ws_url": "wss://example.com/ws"
            }

            mock_transcriber = mock_transcriber_cls.return_value
            mock_transcriber.connect_deepgram = AsyncMock()
            mock_transcriber.start_capture = AsyncMock(return_value=False)
            mock_transcriber.stop = AsyncMock()
            mock_transcriber.is_running = False

            mock_avatar_mgr = mock_avatar_mgr_cls.return_value
            mock_avatar_mgr.leave_avatar_meeting = AsyncMock()

            orchestrator = InterviewAgentOrchestrator(
                meeting_url="https://meet.google.com/abc",
                bot_name="TestBot",
                avatar_provider=failing_provider
            )

            await orchestrator.start()

            self.assertIsNone(orchestrator.avatar_provider)
            failing_provider.create_avatar.assert_called_once()
            failing_provider.clone_voice.assert_called_once()
            failing_provider.start_stream.assert_called_once_with("avatar-123", "voice-456")


# Manual Integration Checklist
# -----------------------------
# 1. Start the backend server:      uvicorn app:app --port 8000
# 2. Start the frontend dev server: npm run dev
# 3. Open the browser and select the D-ID provider
# 4. Upload a dummy photo and voice sample
# 5. Enter a valid Google Meet / Zoom URL
# 6. Click Initialize
# 7. Verify the bot joins the meeting
# 8. Verify the avatar page renders in the meeting UI
# 9. Ask the bot one question and verify it answers
# 10. End the meeting and verify clean shutdown


if __name__ == "__main__":
    unittest.main()


class TestDIdProviderSendAudio(unittest.IsolatedAsyncioTestCase):
    async def test_send_audio_raises_not_implemented(self):
        """Test that send_audio raises NotImplementedError as expected."""
        with patch("agent.avatar.didi_provider.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            provider = DIdProvider(api_key="test_key")
            # Setup: create avatar and voice first
            await provider.create_avatar("dummy_photo.jpg", "dummy_resume", "TestBot")
            await provider.clone_voice("dummy_voice.wav")
            await provider.start_stream("avatar-123", "voice-456")
            with self.assertRaises(NotImplementedError):
                await provider.send_audio("sess-1", b"audio")