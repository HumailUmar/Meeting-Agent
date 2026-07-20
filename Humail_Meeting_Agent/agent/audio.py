import asyncio
import json
import logging
import os
import sys
from typing import AsyncGenerator, Optional
import websockets

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fallback sounddevice import for environments with no audio hardware/ALSA drivers
try:
    import sounddevice as sd
except Exception as e:
    sd = None
    logger.warning(f"sounddevice could not be imported: {e}. Audio capture via soundcard will be unavailable.")

class AudioTranscriber:
    """
    Handles real-time audio capture and streaming to Deepgram's live transcription API.
    """
    def __init__(self, api_key: Optional[str] = None, sample_rate: int = 16000, channels: int = 1, max_queue_size: int = 200):
        self.api_key = api_key or config.DEEPGRAM_API_KEY or os.environ.get("DEEPGRAM_API_KEY")
        self.sample_rate = sample_rate
        self.channels = channels
        # Bounded queue to avoid unbounded memory growth if the socket is slow.
        self.audio_queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self.dg_ws = None
        self.stream = None
        self.is_running = False
        self._sender_task: Optional[asyncio.Task] = None

    def _audio_callback(self, indata, frames, time, status):
        """Callback from sounddevice InputStream (runs in sounddevice's C thread)."""
        if status:
            logger.warning(f"Audio capture warning: {status}")
        if not self.is_running:
            return
        try:
            # Non-blocking put; drop oldest if the consumer is falling behind.
            self.audio_queue.put_nowait(indata.tobytes())
        except asyncio.QueueFull:
            logger.warning("Audio queue full; dropping oldest frame to bound memory.")
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.audio_queue.put_nowait(indata.tobytes())
            except asyncio.QueueFull:
                pass

    async def connect_deepgram(self):
        """
        Connects to Deepgram's Live WebSocket transcription API.
        """
        if not self.api_key:
            raise ValueError("Deepgram API Key is not configured. Please set DEEPGRAM_API_KEY.")

        # Deepgram Live WebSocket endpoint
        url = (
            f"wss://api.deepgram.com/v1/listen"
            f"?encoding=linear16"
            f"&sample_rate={self.sample_rate}"
            f"&channels={self.channels}"
            f"&interim_results=true"
            f"&punctuate=true"
        )
        headers = {"Authorization": f"Token {self.api_key}"}

        logger.info("Connecting to Deepgram live transcription WebSocket...")
        try:
            # NOTE: websockets.connect() uses `additional_headers` (older: `extra_headers`).
            # Support both to stay compatible across library versions.
            import inspect
            kwargs = {}
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                kwargs["additional_headers"] = headers
            else:
                kwargs["extra_headers"] = headers
            self.dg_ws = await websockets.connect(url, **kwargs)
            logger.info("Successfully connected to Deepgram WebSocket.")
        except Exception as e:
            logger.error(f"Failed to connect to Deepgram WebSocket: {e}")
            raise

    async def start_capture(self):
        """
        Starts capturing audio from default mic/audio input using sounddevice.
        """
        if sd is None:
            logger.warning("sounddevice is unavailable. Cannot capture hardware audio.")
            return False

        try:
            # Records in 16-bit PCM Mono
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                callback=self._audio_callback
            )
            self.stream.start()
            self.is_running = True
            logger.info("Real-time audio capture started via sounddevice.")
            return True
        except Exception as e:
            logger.error(f"Failed to start sounddevice capture: {e}")
            self.is_running = False
            return False

    async def stop(self):
        """
        Gracefully stops capturing and streaming.
        """
        self.is_running = False
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
            logger.info("Audio capture stream stopped.")

        if self.dg_ws:
            try:
                # Signal Deepgram stream close
                await self.dg_ws.send(json.dumps({"type": "CloseStream"}))
                await self.dg_ws.close()
            except Exception:
                pass
            self.dg_ws = None
            logger.info("Deepgram WebSocket closed.")

        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
            self._sender_task = None

    async def _stream_audio_to_dg(self):
        """
        Worker task to continuously dequeue audio chunks and push them to Deepgram WebSocket.
        """
        try:
            while self.is_running and self.dg_ws:
                chunk = await self.audio_queue.get()
                if chunk is None:
                    break
                await self.dg_ws.send(chunk)
                self.audio_queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Deepgram streaming sender task: {e}")

    async def get_transcriptions(self) -> AsyncGenerator[dict, None]:
        """
        Receives transcription results from Deepgram in real-time.
        
        Yields:
            dict: {
                "text": str,       # Transcribed text
                "is_final": bool,   # True if Deepgram finalized the utterance
                "speaker": str      # "Interviewer" or "speaker"
            }
        """
        if not self.dg_ws:
            logger.error("Deepgram WebSocket is not connected.")
            return

        self.is_running = True
        self._sender_task = asyncio.create_task(self._stream_audio_to_dg())

        try:
            async for message in self.dg_ws:
                try:
                    line = await self.dg_ws.recv()
                except websockets.ConnectionClosed:
                    break
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Skipping non-JSON Deepgram frame.")
                    continue
                channel = (data.get("channel") or {})
                alternatives = (channel.get("alternatives") or [{}])
                transcript = (alternatives[0] or {}).get("transcript", "")
                is_final = data.get("is_final", False)

                if transcript.strip():
                    yield {
                        "text": transcript,
                        "is_final": is_final,
                        "speaker": "Interviewer"
                    }
        except Exception as e:
            logger.error(f"Error receiving transcriptions from Deepgram WebSocket: {e}")
        finally:
            await self.stop()
