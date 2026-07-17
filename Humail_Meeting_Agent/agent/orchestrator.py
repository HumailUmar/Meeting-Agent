import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# Setup paths to import correctly
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

import config
from agent.meeting import join_meeting
from agent.audio import AudioTranscriber
from agent.brain import AIBrain
from agent.voice import VoiceCloner
from agent.avatar import AvatarManager

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
    def __init__(self, meeting_url: str, bot_name: str, llm_provider: str = "ollama"):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.llm_provider = llm_provider
        
        # Instantiate modules
        self.brain = AIBrain(provider=llm_provider)
        self.cloner = VoiceCloner(voice_sample_path=config.VOICE_SAMPLE_PATH)
        self.avatar_manager = AvatarManager()
        self.transcriber = AudioTranscriber()
        
        self.session = None
        self.pika_session_id = None
        self.is_running = False
        self._main_task = None

    async def start(self):
        """Starts the autonomous agent orchestrator."""
        self.is_running = True
        logger.info("Initializing Autonomous AI Interview Agent...")

        # Step 1: Join the meeting via AgentCall
        logger.info(f"Joining Google Meet session: {self.meeting_url} as '{self.bot_name}'")
        try:
            self.session = await join_meeting(
                meeting_url=self.meeting_url,
                bot_name=self.bot_name,
                avatar_image=config.AVATAR_IMAGE_PATH
            )
            logger.info(f"Joined meeting successfully! Call ID: {self.session['call_id']}")
        except Exception as e:
            logger.error(f"Failed to join meeting: {e}")
            await self.shutdown()
            return

        # Step 2: Display Avatar via Pika's Video Meeting Skill
        logger.info("Connecting Pika video meeting avatar skill...")
        try:
            # We can run the avatar integration in parallel to avoid blocking the main thread
            self.pika_session_id = await self.avatar_manager.join_avatar_meeting(
                meet_url=self.meeting_url,
                bot_name=self.bot_name,
                image_path=config.AVATAR_IMAGE_PATH,
                system_prompt="AI Interview Candidate Persona"
            )
            if self.pika_session_id:
                logger.info(f"Pika Avatar connected. Session ID: {self.pika_session_id}")
            else:
                logger.warning("Pika Avatar could not be initialized. Continuing with audio/native-avatar stream.")
        except Exception as e:
            logger.warning(f"Error starting Pika Avatar: {e}. Continuing without Pika stream.")

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

        # Step 4: Run the main Event Loop
        self._main_task = asyncio.create_task(self._run_event_loop())
        await self._main_task

    async def _run_event_loop(self):
        """
        Listens to real-time events on AgentCall's meeting WebSocket (transcripts, participant changes, call ended).
        """
        client = self.session["client"]
        call_id = self.session["call_id"]

        logger.info("Listening for interview questions...")
        try:
            async for event in client.connect_ws(call_id):
                if not self.is_running:
                    break

                event_type = event.get("event") or event.get("type", "")

                # Handle call ended
                if event_type == "call.ended":
                    logger.info("Meeting has ended. Shutting down...")
                    break

                # Fallback to AgentCall's native transcription stream if Deepgram capture is inactive
                elif event_type == "transcript.final" and not self.transcriber.is_running:
                    speaker = event.get("speaker", "Unknown")
                    text = event.get("text", "")
                    
                    # Ignore own speech to prevent echo response loops
                    if speaker.lower() != self.bot_name.lower():
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
                
                text = data.get("text", "")
                is_final = data.get("is_final", False)
                
                if is_final and text.strip():
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
        client = self.session["client"]
        
        # 1. Update state to 'thinking' (activates thinking indicator on Pika / AgentCall template)
        logger.info("Thinking of an answer...")
        await client.send_command({"type": "voice.state_update", "state": "thinking"})

        # 2. Generate answer via AI Brain with token streaming
        response_text = ""
        async for chunk in self.brain.generate_answer(question):
            response_text += chunk
            # Print to stdout in real-time
            sys.stdout.write(chunk)
            sys.stdout.flush()
        sys.stdout.write("\n")

        # 3. Speak the answer (converts text to speech and injects raw PCM/TTS into call)
        if response_text.strip():
            logger.info("Speaking the answer...")
            await self.cloner.speak(client, response_text)

    async def shutdown(self):
        """Gracefully releases resources and leaves the meeting call."""
        if not self.is_running:
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
        if self.session and "client" in self.session:
            client = self.session["client"]
            call_id = self.session["call_id"]
            try:
                logger.info(f"Ending AgentCall meeting session: {call_id}")
                await client.end_call(call_id)
                await client.close()
            except Exception as e:
                logger.error(f"Error cleaning up AgentCall session: {e}")

        # Cancel main task if running
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()

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
