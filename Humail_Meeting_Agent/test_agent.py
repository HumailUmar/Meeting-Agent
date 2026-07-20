import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Setup paths to ensure we can import our modules correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from agent.meeting import join_meeting
from agent.audio import AudioTranscriber
from agent.brain import AIBrain, DEFAULT_PERSONA
from agent.voice import VoiceCloner
from agent.avatar import AvatarManager

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
        cloner = VoiceCloner(voice_sample_path="dummy_sample.wav")
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
            mock_client.send_command.assert_any_call({"type": "audio.inject", "data": "cmF3cGNtYnl0ZXMxMjM="}) # b64 of rawpcmbytes123
            mock_client.send_command.assert_any_call({"type": "voice.state_update", "state": "listening"})

        print("[SUCCESS] Voice cloning generation, ffmpeg resampling, and audio injection commands verified!")

    @patch("asyncio.create_subprocess_exec")
    async def test_avatar_integration(self, mock_subprocess_exec):
        """Verifies PikaStream avatar session initialization and leave command."""
        print("\n[TEST] Verifying Avatar Integration (Pika)...")
        avatar = AvatarManager(pika_dev_key="mock_pika_key")

        # Mock stdout reader of the join subprocess to simulate PikaStream returning a ready status
        mock_process = AsyncMock()
        mock_process.stdout.readline = AsyncMock(side_effect=[
            b'{"session_id": "pika-sess-777", "status": "created"}\n',
            b'{"session_id": "pika-sess-777", "status": "ready", "video": true, "bot": true}\n',
            b'' # EOF
        ])
        mock_process.stderr.read = AsyncMock(return_value=b'')
        # Subprocess terminate/kill/wait are sync in real code; use plain mocks
        # to avoid unawaited-coroutine ResourceWarnings under AsyncMock.
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = MagicMock(return_value=0)
        mock_subprocess_exec.return_value = mock_process

        # Patch os.path.exists to return True so it doesn't fail on missing placeholder avatar image
        with patch("os.path.exists", return_value=True):
            session_id = await avatar.join_avatar_meeting(
                meet_url="https://meet.google.com/xyz-abc-123",
                bot_name="Humail",
                image_path=config.AVATAR_IMAGE_PATH
            )

        self.assertEqual(session_id, "pika-sess-777")
        self.assertEqual(avatar.active_session_id, "pika-sess-777")
        
        # Test leave
        mock_leave_process = AsyncMock()
        mock_leave_process.returncode = 0
        mock_leave_process.communicate = AsyncMock(return_value=(b"Success", b""))
        mock_subprocess_exec.return_value = mock_leave_process

        left = await avatar.leave_avatar_meeting("pika-sess-777")
        self.assertTrue(left)
        print("[SUCCESS] PikaStream subprocess spawn and event streaming verified!")

if __name__ == "__main__":
    unittest.main()
