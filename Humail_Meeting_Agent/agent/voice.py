import asyncio
import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lazy check for Coqui TTS to handle environment constraints gracefully
COQUI_AVAILABLE = False
try:
    from TTS.api import TTS
    COQUI_AVAILABLE = True
except Exception as e:
    logger.warning(f"Coqui TTS (coqui-xtts) is not available: {e}. Falling back to AgentCall native TTS.")

class VoiceCloner:
    """
    Clones voices from a reference audio file using Coqui XTTS v2, 
    resamples to the meeting format, and plays it into the session.
    """
    def __init__(self, voice_sample_path: Optional[str] = None, voice_clone_provider: str = None):
        self.voice_sample_path = voice_sample_path or config.VOICE_SAMPLE_PATH
        self.tts = None
        self.is_initialized = False
        self.voice_clone_provider = voice_clone_provider
        
        if COQUI_AVAILABLE and self.voice_clone_provider == "local":
            self._init_coqui()
    
    def _init_coqui(self):
        """Initializes the Coqui XTTS v2 model."""
        try:
            logger.info("Initializing Coqui XTTS v2 model (multilingual multi-dataset)...")
            model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
            
            # Detect device
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Using device: {device} for XTTS model.")
            
            self.tts = TTS(model_name).to(device)
            self.is_initialized = True
            logger.info("Coqui XTTS v2 model successfully initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Coqui XTTS: {e}")
            self.is_initialized = False
    
    def generate_cloned_audio(self, text: str, output_path: str) -> bool:
        """
        Generates WAV audio of the given text with the cloned voice of the reference file.
        """
        if not COQUI_AVAILABLE or not self.is_initialized or not self.tts:
            logger.warning("Coqui XTTS v2 is not active/available. Skipping synthesis.")
            return False
        
        if not os.path.exists(self.voice_sample_path):
            logger.error(f"Voice reference sample file missing at: {self.voice_sample_path}. Using native TTS fallback.")
            return False
        
        try:
            logger.info(f"Cloning voice reference '{self.voice_sample_path}' for speech: '{text[:60]}...'")
            self.tts.tts_to_file(
                text=text,
                speaker_wav=self.voice_sample_path,
                language="en",
                file_path=output_path
            )
            return True
        except Exception as e:
            logger.error(f"Error during Coqui XTTS cloned voice generation: {e}")
            return False
    
    async def speak(self, client, text: str, send_provider_text: bool = False):
        """
        Speaks the given text in the meeting using the cloned voice or native fallback.
        
        Args:
            client (AgentCallClient): Reference to the active AgentCall session client.
            text (str): The text content to speak.
            send_provider_text: If True or if voice_clone_provider is "did", 
                bypass Coqui synthesis and send text directly via tts.speak.
        """
        if client is None:
            logger.error("VoiceCloner.speak called without a valid client; skipping.")
            return
    
        # Determine if we should use Coqui synthesis
        use_coqui = (
            COQUI_AVAILABLE 
            and self.is_initialized 
            and self.voice_clone_provider == "local" 
            and not send_provider_text
        )

        if use_coqui:
            # Existing Coqui synthesis path (corrected)
            with tempfile.TemporaryDirectory() as tmpdir:
                wav_path = os.path.join(tmpdir, "cloned.wav")
                pcm_path = os.path.join(tmpdir, "output.raw")
                
                # Generate the cloned voice WAV file
                success = self.generate_cloned_audio(text, wav_path)
                
                if success and os.path.exists(wav_path):
                    # Convert to raw PCM 16kHz 16-bit mono using ffmpeg (AgentCall spec)
                    try:
                        # Ensure ffmpeg is actually available before attempting conversion.
                        subprocess.run(
                            ["ffmpeg", "-version"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                        )
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        logger.error("ffmpeg not found; falling back to native TTS.")
                        success = False

                    if success:
                        try:
                            logger.info("Resampling cloned voice to PCM 16kHz 16-bit mono...")
                            cmd = [
                                "ffmpeg", "-i", wav_path,
                                "-acodec", "pcm_s16le",
                                "-ac", "1",
                                "-ar", "16000",
                                pcm_path, "-y"
                            ]
                            process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            
                            if process.returncode == 0 and os.path.exists(pcm_path):
                                with open(pcm_path, "rb") as f:
                                    pcm_bytes = f.read()
                                
                                # Base64 encode raw PCM bytes
                                b64_data = base64.b64encode(pcm_bytes).decode('utf-8')
                                
                                # Set voice state to speaking and inject audio
                                logger.info("Injecting raw cloned voice PCM into the meeting call...")
                                await client.send_command({"type": "voice.state_update", "state": "speaking"})
                                await client.send_command({"type": "audio.inject", "data": b64_data})
                                
                                # Simulate speech duration (PCM Mono 16kHz 16-bit = 32000 bytes/sec)
                                duration = len(pcm_bytes) / 32000.0
                                await asyncio.sleep(duration)
                                
                                await client.send_command({"type": "voice.state_update", "state": "listening"})
                                return
                            else:
                                logger.error(f"FFmpeg failed with: {process.stderr.decode()}")
                        except Exception as e:
                            logger.error(f"Exception during FFmpeg conversion: {e}")
        else:
            # Direct TTS fallback (no Coqui, no ffmpeg)
            logger.info(f"Using AgentCall's native TTS to speak: '{text}'")
        
            # Send speaking state update
            await client.send_command({"type": "voice.state_update", "state": "speaking"})
            await client.send_command({"type": "tts.speak", "text": text, "voice": "af_heart"})
            
            # Approximate speech duration based on speaking rate
            words = len(text.split())
            est_duration = max(2.0, words * 0.45)  # ~133 words per minute
            await asyncio.sleep(est_duration)
            
            # Reset state back to listening
            await client.send_command({"type": "voice.state_update", "state": "listening"})