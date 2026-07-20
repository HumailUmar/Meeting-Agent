import sys
import os
from pathlib import Path

# Setup paths to ensure we can import from agentcall scripts
BASE_DIR = Path(__file__).resolve().parent.parent
AGENTCALL_PYTHON_DIR = BASE_DIR / "agentcall" / "scripts" / "python"

if str(AGENTCALL_PYTHON_DIR) not in sys.path:
    sys.path.append(str(AGENTCALL_PYTHON_DIR))

# Import the AgentCall client
try:
    from agentcall import AgentCallClient
except ImportError:
    # Fallback if agentcall structure is different or path is not resolved yet
    sys.path.append(str(BASE_DIR))
    from agentcall.scripts.python.agentcall import AgentCallClient

import config

async def join_meeting(meeting_url: str, bot_name: str, avatar_image: str = None) -> dict:
    """
    Joins the meeting as a bot participant using the AgentCall SDK.
    
    Args:
        meeting_url (str): The Google Meet or Zoom meeting URL.
        bot_name (str): The display name of the bot.
        avatar_image (str): Optional path to an avatar image.
        
    Returns:
        dict: A dictionary containing the call information (session reference), 
              including the client instance.
    """
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

    try:
        # Create the call via AgentCall SDK
        result = await client.create_call(**create_params)
        
        # Attach client to the result to serve as a reference to the active session
        result["client"] = client
        return result
    except Exception as e:
        await client.close()
        raise RuntimeError(f"Failed to join meeting: {e}")
