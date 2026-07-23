import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Setup paths to import correctly
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

import config
from agent.meeting import join_meeting
from agent.audio import AudioTranscriber
from agent.brain import AIBrain
from agent.voice import VoiceCloner
from agent.avatar import AvatarManager
from agent.avatar.did_provider import DIdProvider
from agent.store import get_state_store

state_store = get_state_store()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Orchestrator")

class InterviewAgentOrchestrator:
    """
    Main orchestrator that coordinates meeting joining, transcription, 
    AI brain answer generation, voice cloning synthesis, and avatar rendering.
    """
    def __init__(self, meeting_url: str, bot_name: str, llm_provider: str = "ollama", avatar_provider=None):
        self.meeting_url = meeting_url
        self.bot_name = (bot_name or "Agent").strip() or "Agent"
        self.llm_provider = llm_provider
        
        # Instantiate modules
        self.brain = AIBrain(provider=llm_provider)
        self.cloner = VoiceCloner(voice_sample_path=config.VOICE_SAMPLE_PATH, voice_clone_provider=config.VOICE_CLONE_PROVIDER)
        
        # Handle avatar provider - if it's a D-ID provider, we need to initialize it properly
        if isinstance(avatar_provider, str) and avatar_provider == "did":
            # For D-ID provider, we need to create the provider instance and initialize it
            self.avatar_provider = DIdProvider(config.DID_API_KEY)
        else:
            self.avatar_provider = avatar_provider
            
        self.avatar_manager = AvatarManager(provider=self.avatar_provider)
            
        self.transcriber = AudioTranscriber()
        
        self.session = None
        self.pika_session_id = None
        self.provider_session_id = None  # This will store the D-ID session ID
        self.is_running = False
        self._main_task = None
        self._shutting_down = False
        # Echo-loop protection: remember recent question hashes.
        self._recent_questions = set()
        
        # We'll store the stream URL and session ID for later use
        self.stream_url = None
        self.avatar_session_id = None

    def _question_hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]

    def _is_duplicate_question(self, text: str) -> bool:
        h = self._question_hash(text)
        if h in self._recent_questions:
            return True
        self._recent_questions.add(h)
        # Bound the dedup set.
        if len(self._recent_questions) > 200:
            self._recent_questions.clear()
        return False

    async def _safe_client(self):
        """Return the active AgentCall client, or None if not joined."""
        if self.session and isinstance(self.session, dict) and self.session.get("client"):
            return self.session["client"]
        return None

    async def start(self):
        """Starts the autonomous agent orchestrator."""
        self.is_running = True
        logger.info("Initializing Autonomous AI Interview Agent...")
        
        # Step 1: Prepare avatar page URL if provider is configured
        avatar_page_url = None
        if self.avatar_provider is not None:
            avatar_page_url = f"{config.BACKEND_BASE_URL}/avatar-page/active"
            logger.info(f"Avatar provider configured. Avatar page URL: {avatar_page_url}")
        
        # Step 2: Join the meeting via AgentCall
        logger.info(f"Joining Google Meet session: {self.meeting_url} as '{self.bot_name}'")
        try:
            self.session = await join_meeting(
                meeting_url=self.meeting_url,
                bot_name=self.bot_name,
                avatar_image=config.AVATAR_IMAGE_PATH,
                webpage_url=avatar_page_url
            )
            logger.info(f"Joined meeting successfully! Call ID: {self.session['call_id']}")
        except Exception as e:
            logger.error(f"Failed to join meeting: {e}")
            await self.shutdown()
            return

        # Step 3: Handle avatar integration
        logger.info("Connecting avatar integration...")
        if self.avatar_provider is not None:
            try:
                # Create avatar if not already done
                avatar_id = await self.avatar_provider.create_avatar(
                    config.AVATAR_IMAGE_PATH, "dummy_resume", self.bot_name
                )
                logger.info(f"Created avatar with ID: {avatar_id}")
                
                # Clone voice if not already done
                voice_id = await self.avatar_provider.clone_voice(config.VOICE_SAMPLE_PATH)
                logger.info(f"Cloned voice with ID: {voice_id}")
                
                # Start stream - this will give us session_id and stream_url
                stream_result = await self.avatar_provider.start_stream(avatar_id, voice_id)
                self.stream_url = stream_result.get("stream_url")
                self.provider_session_id = stream_result.get("session_id")
                self.avatar_id = avatar_id
                self.voice_id = voice_id
                logger.info(f"Started stream with session_id: {self.provider_session_id} and stream_url: {self.stream_url}")
                
                # Persist to state store
                state_store.save_session(
                    session_id="active",
                    meeting_url=self.meeting_url,
                    bot_name=self.bot_name,
                    status="starting",
                    avatar_path=config.AVATAR_IMAGE_PATH,
                    voice_path=config.VOICE_SAMPLE_PATH,
                    avatar_provider=self.avatar_provider.__class__.__name__ if self.avatar_provider else None,
                    provider_session_id=self.provider_session_id,
                    stream_url=self.stream_url
                )
            except Exception as e:
                logger.warning(f"Error setting up avatar provider: {e}. Falling back to AgentCall native TTS only.")
                self.avatar_provider = None
                self.stream_url = None
                self.provider_session_id = None
        else:
            logger.info("No avatar provider configured. Using AgentCall native TTS only.")

        # Step 3: Initialize Deepgram Live Transcriber (if hardware audio is available)
        logger.info("Initializing real-time transcription...")
        try:
            await self.transcriber.connect_deepgram()
            # Try to start soundcard capture (will log warning if soundcard is missing in headless Docker)
            capture_ok = await self.transcriber.start_capture()
            if capture_ok:
                logger.info("Deepgram Audio Capture & Transcription is live!")
                # Start reading from Deepgram in background
                asyncio.create_task(self._process_deepgram_transcripts())
            else:
                logger.info("No soundcard detected. Falling back to AgentCall's high-fidelity built-in meeting transcript WebSocket stream.")
        except Exception as e:
            logger.warning(f"Deepgram initialization skipped or failed: {e}. Falling back to AgentCall WebSocket transcripts.")

        # Health check: AgentCall's native transcript stream is the baseline source
        # (it works without a soundcard). Deepgram is an optional enhancement.
        # As long as we have an active session, the event loop will receive
        # transcript.final events. If the session is missing, abort.
        if not self.session:
            logger.error("No active meeting session; cannot continue. Aborting start.")
            await self.shutdown()
            return

        # Step 4: Run the main Event Loop
        self._main_task = asyncio.create_task(self._run_event_loop())
        await self._main_task

    async def _run_event_loop(self):
        """
        Listens to real-time events on AgentCall's meeting WebSocket (transcripts, participant changes, call ended).
        """
        client = await self._safe_client()
        if self.session and isinstance(self.session, dict) and self.session.get("client"):
            call_id = self.session.get("call_id")
            if not call_id:
                logger.error("Event loop started without a call_id; aborting.")
                await self.shutdown()
                return

        logger.info("Listening for interview questions...")
        try:
            async for event in client.connect_ws(call_id):
                if not self.is_running:
                    break

                if not isinstance(event, dict):
                    logger.warning(f"Skipping non-dict WS event: {type(event)}")
                    continue
                event_type = event.get("event") or event.get("type", "")

                # Handle call ended
                if event_type == "call.ended":
                    logger.info("Meeting has ended. Shutting down...")
                    break

                # Fallback to AgentCall's native transcription stream if Deepgram capture is inactive
                elif event_type == "transcript.final" and not self.transcriber.is_running:
                    speaker = (event.get("speaker") or "Unknown")
                    text = (event.get("text") or "").strip()

                    # Ignore own speech to prevent echo response loops
                    if speaker.lower() != self.bot_name.lower() and text:
                        if self._is_duplicate_question(text):
                            logger.info(f"Skipping duplicate transcript: {text[:60]}")
                            continue
                        logger.info(f"[Transcript Received] {speaker}: {text}")
                        await self._process_question(text)

                # Handle speaking status updates
                elif event_type == "voice.state":
                    state = event.get("state")
                    logger.info(f"Avatar state updated: {state}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in main event loop: {e}")
        finally:
            await self.shutdown()

    async def _process_deepgram_transcripts(self):
        """Background worker reading transcripts directly from Deepgram."""
        try:
            async for data in self.transcriber.get_transcriptions():
                if not self.is_running:
                    break
                if not isinstance(data, dict):
                    continue
                text = (data.get("text") or "").strip()
                is_final = data.get("is_final", False)

                if is_final and text:
                    if self._is_duplicate_question(text):
                        logger.info(f"Skipping duplicate Deepgram transcript: {text[:60]}")
                        continue
                    logger.info(f"[Deepgram Live Transcript] {text}")
                    await self._process_question(text)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Deepgram transcription handler: {e}")

    async def _process_question(self, question: str):
        """
        Handles responding to a detected question:
        updates states, generates AI brain response, converts to speech,
        and plays audio (with lip-sync synced automatically).
        """
        question = (question or "").strip()
        if not question:
            return

        client = await self._safe_client()
        if client is None:
            logger.error("Cannot process question: meeting session/client unavailable.")
            return

        # Update state to 'thinking' (activates thinking indicator on Pika / AgentCall template)
        logger.info("Thinking of an answer...")
        await client.send_command({"type": "voice.state_update", "state": "thinking"})

        # Generate answer via AI Brain with token streaming
        response_text = ""
        try:
            async for chunk in self.brain.generate_answer(question):
                response_text += chunk
                # Print to stdout in real-time
                sys.stdout.write(chunk)
                sys.stdout.flush()
        except Exception as e:
            logger.error(f"Error during answer generation: {e}")
            response_text = response_text or "Sorry, I had a small hiccup there."
        sys.stdout.write("\n")

        # Speak the answer (convert text to speech and inject raw PCM/TTS into call)
        if response_text.strip():
            logger.info("Speaking the answer...")
            try:
                await self.cloner.speak(client, response_text)
            except Exception as e:
                logger.error(f"Error while speaking answer: {e}")

        # Reset state to 'listening'
        await client.send_command({"type": "voice.state_update", "state": "listening"})

    async def shutdown(self):
        """Gracefully releases resources and leaves the meeting call."""
        if self._shutting_down:
            return
        self._shutting_down = True
        if not self.is_running:
            logger.info("Shutdown already completed.")
            return

        self.is_running = False
        logger.info("Starting graceful shutdown...")

        # 1. Stop audio capture and Deepgram
        try:
            await self.transcriber.stop()
        except Exception as e:
            logger.error(f"Error stopping transcriber: {e}")

        # 2. Leave Pika Avatar session
        if self.pika_session_id:
            try:
                await self.avatar_manager.leave_avatar_meeting(self.pika_session_id)
            except Exception as e:
                logger.error(f"Error leaving Pika session: {e}")

        # 3. Leave and clean up AgentCall meeting session
        client = self.session.get("client") if isinstance(self.session, dict) else None
        call_id = self.session.get("call_id") if isinstance(self.session, dict) else None
        if client and call_id:
            try:
                logger.info(f"Ending AgentCall meeting session: {call_id}")
                await client.end_call(call_id)
                await client.close()
            except Exception as e:
                logger.error(f"Error cleaning up AgentCall session: {e}")

        # Cancel main task if running
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except (asyncio.CancelledError, Exception):
                pass
        self.session = None
        logger.info("Graceful shutdown completed successfully. Offline.")

    async def shutdown(self):
        """Gracefully releases resources and leaves the meeting call."""
        if self._shutting_down:
            return
        self._shutting_down = True
        if not self.is_running:
            logger.info("Shutdown already completed.")
            return

        self.is_running = False
        logger.info("Starting graceful shutdown...")

        # 1. Stop audio capture and Deepgram
        try:
            await self.transcriber.stop()
        except Exception as e:
            logger.error(f"Error stopping transcriber: {e}")

        # 2. Leave Pika Avatar session
        if self.pika_session_id:
            try:
                await self.avatar_manager.leave_avatar_meeting(self.pika_session_id)
            except Exception as e:
                logger.error(f"Error leaving Pika session: {e}")

        # 3. Leave and clean up AgentCall meeting session
        client = self.session.get("client") if isinstance(self.session, dict) else None
        call_id = self.session.get("call_id") if isinstance(self.session, dict) else None
        if client and call_id:
            try:
                logger.info(f"Ending AgentCall meeting session: {call_id}")
                await client.end_call(call_id)
                await client.close()
            except Exception as e:
                logger.error(f"Error cleaning up AgentCall session: {e}")

        # Cancel main task if running
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except (asyncio.CancelledError, Exception):
                pass
        self.session = None
        logger.info("Graceful shutdown completed successfully. Offline.")

    # CLI Execution
    def main():
        parser = argparse.ArgumentParser(description="Autonomous AI Interview Agent - Orchestrator")
        parser.add_argument("--url", help="Google Meet / Zoom URL (overrides config)")
        parser.add_argument("--name", default="Humail", help="Display name of the AI bot candidate")
        parser.add_argument("--provider", default="ollama", choices=["ollama", "gemini"], help="LLM Provider to generate answers")
        args = parser.parse_args()

        meet_url = args.url or config.MEETING_URL
        if not meet_url:
            print("Error: Meeting URL is required. Provide via config.py or --url parameter.", file=sys.stderr)
            sys.exit(1)

        orchestrator = InterviewAgentOrchestrator(
            meeting_url=meet_url,
            bot_name=args.name,
            llm_provider=args.provider
        )

        loop = asyncio.get_event_loop()

        # Register OS signals for graceful termination
        def handle_sig():
            logger.info("Termination signal received. Initiating exit...")
            asyncio.create_task(orchestrator.shutdown())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_sig)
            except NotImplementedError:
                # Fallback for Windows or non-Unix environments
                pass

        try:
            loop.run_until_complete(orchestrator.start())
        except KeyboardInterrupt:
            logger.info("Interrupted. Shutting down...")
            loop.run_until_complete(orchestrator.shutdown())
        finally:
            loop.close()

if __name__ == "__main__":
    main()