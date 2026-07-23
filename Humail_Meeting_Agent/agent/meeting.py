import sys
import os
import re
from pathlib import Path
from urllib.parse import urlparse

# Setup paths to ensure we can import from agentcall scripts
BASE_DIR = Path(__file__).resolve().parent.parent
AGENTCALL_PYTHON_DIR = BASE_DIR / "agentcall" / "scripts" / "python"

if str(AGENTCALL_PYTHON_DIR) not in sys.path:
    sys.path.append(str(AGENTCALL_PYTHON_DIR))

# Robust import: try package-style, fall back to direct module path.
try:
    from agentcall import AgentCallClient
except ImportError:
    sys.path.append(str(BASE_DIR))
    try:
        from agentcall.scripts.python.agentcall import AgentCallClient
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "agentcall", AGENTCALL_PYTHON_DIR / "agentcall.py"
        )
        agentcall_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agentcall_mod)
        AgentCallClient = agentcall_mod.AgentCallClient

import config

# Minimal URL sanity check for Meet/Zoom links.
_URL_RE = re.compile(r"^https?://(meet\.google\.com|.*zoom\.(us|com))/?", re.IGNORECASE)


def _is_valid_meet_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme in ("http", "https") and parsed.netloc)
    except Exception:
        return False


async def join_meeting(meeting_url: str, bot_name: str, avatar_image: str = None, webpage_url: str = None) -> dict:
    """
    Joins the meeting as a bot participant using the AgentCall SDK.
    
    Args:
        meeting_url (str): The Google Meet or Zoom meeting URL.
        bot_name (str): The display name of the bot.
        avatar_image (str): Optional path to an avatar image.
        webpage_url (str): Optional public URL for bot's video feed (avatar page).
        
    Returns:
        dict: A dictionary containing the call information (session reference), 
              including the client instance.
    """
    if not _is_valid_meet_url(meeting_url):
        raise ValueError(f"Invalid meeting URL supplied: {meeting_url!r}")

    bot_name = (bot_name or "Agent").strip() or "Agent"

    api_key = config.AGENTCALL_API_KEY or os.environ.get("AGENTCALL_API_KEY")
    if not api_key:
        raise ValueError("AGENTCALL_API_KEY is not configured in config.py or environment.")

    # Initialize the AgentCallClient with configured API key
    client = AgentCallClient(api_key=api_key)

    # We join with 'webpage-av' mode for voice + visual presence
    # and use the direct strategy so the agent controls speech and triggers manually.
    create_params = {
        "meet_url": meeting_url,
        "bot_name": bot_name,
        "mode": "webpage-av",
        "voice_strategy": "direct",
        "transcription": True,
        "ui_template": "avatar"
    }

    if webpage_url:
        create_params["webpage_url"] = webpage_url

    try:
        # Create the call via AgentCall SDK
        result = await client.create_call(**create_params)

        if not isinstance(result, dict):
            await client.close()
            raise RuntimeError(f"AgentCall create_call returned unexpected type: {type(result)}")

        call_id = result.get("call_id")
        if not call_id:
            await client.close()
            raise RuntimeError(f"AgentCall create_call response missing 'call_id': {result}")

        # Attach client to the result to serve as a reference to the active session
        result["client"] = client
        return result
    except Exception as e:
        await client.close()
        raise RuntimeError(f"Failed to join meeting: {e}")
