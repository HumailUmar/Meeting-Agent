import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from a .env file if present
load_dotenv()

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
AGENTCALL_API_KEY = os.getenv("AGENTCALL_API_KEY", "")
PIKA_DEV_KEY = os.getenv("PIKA_DEV_KEY", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Paths
BASE_DIR = Path(__file__).resolve().parent
AVATAR_IMAGE_PATH = os.getenv("AVATAR_IMAGE_PATH", str(BASE_DIR / "identity" / "videomeeting-avatar.png"))
VOICE_SAMPLE_PATH = os.getenv("VOICE_SAMPLE_PATH", str(BASE_DIR / "life" / "voice_sample.wav"))

# Meeting Link
MEETING_URL = os.getenv("MEETING_URL", "")
