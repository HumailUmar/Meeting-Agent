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

# The reference voice sample lives under identity/ (which exists) by default.
# Previously pointed at a non-existent `life/` directory, so the clone always failed.
VOICE_SAMPLE_PATH = os.getenv("VOICE_SAMPLE_PATH", str(BASE_DIR / "identity" / "voice_sample.wav"))

# Toggle to load the heavy CPU/GPU Coqui XTTS v2 neural model. 
# Set to 'false' in low-RAM/CPU-only production servers to prevent OOM risks.
USE_COQUI_TTS = os.getenv("USE_COQUI_TTS", "false").lower() in ("true", "1", "yes")

# --- SRE SaaS Scalability Configurations ---
# State storage engine: "sqlite" (SaaS production default) or "memory" (single-node)
STATE_STORE_TYPE = os.getenv("STATE_STORE_TYPE", "sqlite").lower()

# Optional: Remote GPU accelerated voice cloning endpoint.
# If set, voice synthesis is offloaded to this external service, eliminating local CPU/OOM strain entirely.
CLONED_VOICE_API_URL = os.getenv("CLONED_VOICE_API_URL", "")

# Redis Connection string for Horizontally Scaled Multi-Instance setups (session clustering & Pub/Sub)
REDIS_URL = os.getenv("REDIS_URL", "")

# S3 or Cloud storage asset upload bucket (if empty, defaults to local folder upload)
S3_UPLOAD_BUCKET = os.getenv("S3_UPLOAD_BUCKET", "")

# Meeting Link
MEETING_URL = os.getenv("MEETING_URL", "")
