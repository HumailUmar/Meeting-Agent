import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
PIKA_SCRIPT_PATH = BASE_DIR / "Pika-Skills" / "pikastream-video-meeting" / "scripts" / "pikastreaming_videomeeting.py"

class AvatarManager:
    """
    Manages the integration with Pika's real-time lip-synced video meeting avatar skill.
    Uses the underlying pikastreaming_videomeeting.py script to control the avatar.
    """
    def __init__(self, pika_dev_key: Optional[str] = None):
        self.pika_dev_key = pika_dev_key or config.PIKA_DEV_KEY or os.environ.get("PIKA_DEV_KEY")
        self.active_session_id = None
        self.process = None # Subprocess instance tracked to prevent leaks/zombies

    async def join_avatar_meeting(
        self, 
        meet_url: str, 
        bot_name: str, 
        image_path: str, 
        voice_id: Optional[str] = None, 
        system_prompt: Optional[str] = None
    ) -> Optional[str]:
        """
        Launches Pika's join command to load the avatar into the Google Meet or Zoom meeting.
        Reads status logs from stdout and captures the session_id when connected and ready.
        
        Args:
            meet_url (str): Google Meet or Zoom link.
            bot_name (str): The meeting display name for the bot.
            image_path (str): Path to the avatar headshot image.
            voice_id (str, calendar, optional): The Pika voice ID to use.
            system_prompt (str, optional): The system prompt for conversational guidance.
            
        Returns:
            str: The session_id of the active meeting session if successful, else None.
        """
        if not self.pika_dev_key:
            logger.error("PIKA_DEV_KEY is missing. Unable to join Pika avatar meeting.")
            return None

        # Resolve image path safely
        if not os.path.exists(image_path):
            logger.error(f"Avatar headshot image not found at: {image_path}")
            return None

        # Assemble CLI command
        cmd = [
            sys.executable, str(PIKA_SCRIPT_PATH), "join",
            "--meet-url", meet_url,
            "--bot-name", bot_name,
            "--image", image_path
        ]

        if voice_id:
            cmd.extend(["--voice-id", voice_id])
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        # Prepare environment
        env = os.environ.copy()
        env["PIKA_DEV_KEY"] = self.pika_dev_key

        logger.info(f"Spawning Pika meeting joining subprocess...")
        
        try:
            # Terminate any existing running process before starting a new one
            await self._kill_process()

            # Start asynchronous subprocess
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            session_id = None
            
            while True:
                line_bytes = await self.process.stdout.readline()
                if not line_bytes:
                    break
                
                line = line_bytes.decode('utf-8').strip()
                if not line:
                    continue
                
                # Parse JSON output from the script
                try:
                    data = json.loads(line)
                    logger.info(f"[PikaStream status] {data}")
                    
                    if "session_id" in data:
                        session_id = data["session_id"]
                        self.active_session_id = session_id
                    
                    status = data.get("status")
                    video = data.get("video")
                    bot = data.get("bot")
                    
                    if status == "ready" or (video and bot):
                        logger.info(f"Pika Avatar is ready in the meeting! Session ID: {session_id}")
                        return session_id
                        
                    if status in ("error", "closed"):
                        logger.error(f"Pika meeting session terminated with error: {data}")
                        break
                        
                except json.JSONDecodeError:
                    logger.info(f"[PikaStream Raw] {line}")
            
            # Read stderr if process failed early
            stderr_bytes = await self.process.stderr.read()
            if stderr_bytes:
                logger.error(f"[PikaStream Stderr Error] {stderr_bytes.decode('utf-8')}")

            # Cleanup process if it failed to get ready
            await self._kill_process()
            return session_id

        except Exception as e:
            logger.error(f"Error while joining Pika avatar meeting: {e}")
            await self._kill_process()
            return None

    async def leave_avatar_meeting(self, session_id: Optional[str] = None) -> bool:
        """
        Triggers Pika's leave command to shut down the avatar session and kills local processes.
        """
        sid = session_id or self.active_session_id
        
        # Kill the local active running process
        await self._kill_process()

        if not sid:
            logger.warning("No active session ID found to trigger leave command.")
            return False

        if not self.pika_dev_key:
            logger.error("PIKA_DEV_KEY is not set.")
            return False

        cmd = [
            sys.executable, str(PIKA_SCRIPT_PATH), "leave",
            "--session-id", sid
        ]

        env = os.environ.copy()
        env["PIKA_DEV_KEY"] = self.pika_dev_key

        logger.info(f"Requesting leave for session ID: {sid}...")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            
            if process.returncode == 0:
                logger.info(f"Pika session {sid} terminated successfully.")
                if sid == self.active_session_id:
                    self.active_session_id = None
                return True
            else:
                logger.error(f"Failed to close Pika session: {stderr_bytes.decode('utf-8')}")
                return False
        except Exception as e:
            logger.error(f"Exception during Pika leave session: {e}")
            return False

    async def _kill_process(self):
        """Safely terminates and reaps the spawned subprocess to prevent zombie leaks."""
        if self.process:
            logger.info("Reaping Pika subprocess to prevent memory leak...")
            try:
                self.process.terminate()
                # Wait up to 5s for clean shutdown, then force kill
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Pika subprocess didn't exit cleanly. Sending SIGKILL...")
                    self.process.kill()
                    await self.process.wait()
            except Exception as e:
                logger.debug(f"Error while reaping process: {e}")
            finally:
                self.process = None

    async def generate_avatar_image(self, output_path: str, prompt: Optional[str] = None) -> bool:
        """
        Autonomously generates an avatar headshot using Pika's image generation capability.
        Saves output directly to the specified output_path.
        """
        if not self.pika_dev_key:
            logger.error("PIKA_DEV_KEY is required to generate avatar.")
            return False

        cmd = [
            sys.executable, str(PIKA_SCRIPT_PATH), "generate-avatar",
            "--output", output_path
        ]
        if prompt:
            cmd.extend(["--prompt", prompt])

        env = os.environ.copy()
        env["PIKA_DEV_KEY"] = self.pika_dev_key

        logger.info(f"Generating new avatar headshot at {output_path}...")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            
            if process.returncode == 0:
                logger.info("Avatar generated and saved successfully!")
                return True
            else:
                logger.error(f"Avatar generation failed: {stderr_bytes.decode('utf-8')}")
                return False
        except Exception as e:
            logger.error(f"Error generating avatar: {e}")
            return False
