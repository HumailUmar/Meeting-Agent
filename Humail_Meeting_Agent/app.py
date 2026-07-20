import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import uvicorn

import config
from agent.orchestrator import InterviewAgentOrchestrator
from agent.store import get_state_store

# Persistent session store (SQLite by default, Memory for tests).
state_store = get_state_store()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("API-Server")

app = FastAPI(
    title="Autonomous AI Interview Agent API",
    description="REST & WebSocket API to coordinate and track the AI Interview Candidate Agent.",
    version="1.5.0"
)

# In-memory storage for active sessions and orchestrator tasks
class AppState:
    orchestrator: Optional[InterviewAgentOrchestrator] = None
    orchestrator_task: Optional[asyncio.Task] = None
    current_status: str = "idle"  # "idle", "starting", "interviewing", "stopped"
    meeting_url: Optional[str] = None
    bot_name: Optional[str] = None

state = AppState()

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        # Maps session_id (or "all") to list of active WebSockets
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)
        logger.info(f"WebSocket client connected to session: {session_id}")

    def disconnect(self, session_id: str, websocket: WebSocket):
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        logger.info(f"WebSocket client disconnected from session: {session_id}")

    async def broadcast(self, session_id: str, message: dict):
        """Broadcasts JSON updates to all websocket listeners of a session."""
        targets = self.active_connections.get(session_id, [])
        all_targets = self.active_connections.get("active", []) + self.active_connections.get("all", [])
        
        for ws in targets + all_targets:
            try:
                await ws.send_json(message)
            except Exception as e:
                # Stale connection cleanup
                logger.debug(f"Failed to send to WebSocket, skipping: {e}")

manager = ConnectionManager()

# Pydantic request models
class StartAgentRequest(BaseModel):
    meeting_url: Optional[str] = None
    bot_name: Optional[str] = "Humail"
    llm_provider: Optional[str] = "ollama"  # "ollama" or "gemini"
    candidate_data: Optional[dict] = None

async def run_orchestrator_background(orchestrator: InterviewAgentOrchestrator):
    """Asynchronous background wrapper that executes the agent's main loop and hooks WebSockets."""
    try:
        state.current_status = "interviewing"
        session_id = "active"
        
        # Intercept process_question to capture and stream tokens to WebSocket
        async def hooked_process_question(question: str):
            nonlocal session_id
            if orchestrator.session:
                session_id = orchestrator.session.get("call_id", "active")
                
            # 1. Broadcast question to WebSockets
            await manager.broadcast(session_id, {
                "event": "question_received",
                "text": question,
                "speaker": "Interviewer"
            })
            
            # 2. Transition state to 'thinking'
            await manager.broadcast(session_id, {"event": "state_change", "state": "thinking"})
            client = orchestrator.session.get("client") if isinstance(orchestrator.session, dict) else None
            if client is not None:
                await client.send_command({"type": "voice.state_update", "state": "thinking"})
            else:
                logger.warning("Hooked process_question: client unavailable; skipping voice command.")
            
            # 3. Generate answer and stream tokens to WebSocket
            response_text = ""
            try:
                async for chunk in orchestrator.brain.generate_answer(question):
                    response_text += chunk
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    # Broadcast real-time token
                    await manager.broadcast(session_id, {
                        "event": "brain_stream_token",
                        "text": chunk
                    })
            except Exception as e:
                logger.error(f"Error during hooked answer generation: {e}")
                response_text = response_text or "Sorry, I had a small hiccup there."
            sys.stdout.write("\n")
            
            # 4. Finalize brain generation and transition to 'speaking'
            await manager.broadcast(session_id, {
                "event": "brain_response_done",
                "text": response_text
            })
            await manager.broadcast(session_id, {"event": "state_change", "state": "speaking"})
            
            # 5. Play cloned voice into meeting
            if response_text.strip() and client is not None:
                await orchestrator.cloner.speak(client, response_text)
                
            # 6. Reset state to 'listening'
            await manager.broadcast(session_id, {"event": "state_change", "state": "listening"})

        orchestrator._process_question = hooked_process_question
        
        # Start the orchestrator
        await orchestrator.start()
    except Exception as e:
        logger.error(f"Error in background orchestrator task: {e}")
    finally:
        state.current_status = "idle"
        state.orchestrator = None
        state.orchestrator_task = None
        logger.info("Agent background task completed.")

# HTML Frontend Serve Route
@app.get("/", response_class=HTMLResponse, summary="Renders the AI Interview Dashboard UI")
async def serve_dashboard():
    frontend_path = Path(__file__).resolve().parent / "frontend" / "index.html"
    if frontend_path.exists():
        return HTMLResponse(content=frontend_path.read_text(), status_code=200)
    return HTMLResponse(content="<h1>Dashboard Frontend File Not Found</h1>", status_code=404)

# Static File Route for Avatar Images
@app.get("/static/placeholder-avatar.jpg", summary="Serves the default candidate avatar image")
async def serve_avatar():
    img_path = Path(config.AVATAR_IMAGE_PATH)
    if img_path.exists():
        return FileResponse(img_path)
    return FileResponse(Path(__file__).resolve().parent / "Pika-Skills" / "pikastream-video-meeting" / "assets" / "placeholder-avatar.jpg")

# Upload safety limits
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))  # 15 MB
ALLOWED_AVATAR_EXT = {".png", ".jpg", ".jpeg"}
ALLOWED_VOICE_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


def _safe_filename(filename: Optional[str]) -> Optional[str]:
    """Return a normalized lower-case extension, or None if the filename is unsafe."""
    if not filename:
        return None
    ext = os.path.splitext(filename)[1].lower()
    return ext


@app.post("/upload/avatar", summary="Upload a custom avatar image")
async def upload_avatar(file: UploadFile = File(...)):
    # Validate filename / extension
    ext = _safe_filename(file.filename)
    if ext is None:
        raise HTTPException(status_code=400, detail="Missing filename.")
    if ext not in ALLOWED_AVATAR_EXT:
        raise HTTPException(status_code=400, detail="Only PNG, JPG, or JPEG images are supported.")

    # Ensure directory exists (create parent dirs)
    os.makedirs(os.path.dirname(config.AVATAR_IMAGE_PATH), exist_ok=True)

    try:
        # Stream with a size cap to prevent disk exhaustion.
        written = 0
        with open(config.AVATAR_IMAGE_PATH, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    buffer.close()
                    try:
                        os.remove(config.AVATAR_IMAGE_PATH)
                    except OSError:
                        pass
                    raise HTTPException(status_code=413, detail="File too large.")
                buffer.write(chunk)
        logger.info(f"Custom avatar image uploaded successfully: {config.AVATAR_IMAGE_PATH}")
        return {"status": "success", "file_path": config.AVATAR_IMAGE_PATH}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save avatar image: {e}")


@app.post("/upload/voice", summary="Upload a reference voice sample for cloning")
async def upload_voice(file: UploadFile = File(...)):
    # Validate filename / extension
    ext = _safe_filename(file.filename)
    if ext is None:
        raise HTTPException(status_code=400, detail="Missing filename.")
    if ext not in ALLOWED_VOICE_EXT:
        raise HTTPException(status_code=400, detail="Unsupported audio format. Use WAV, MP3, M4A, OGG, or FLAC.")

    # Ensure directory exists (create parent dirs)
    os.makedirs(os.path.dirname(config.VOICE_SAMPLE_PATH), exist_ok=True)

    try:
        written = 0
        with open(config.VOICE_SAMPLE_PATH, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    buffer.close()
                    try:
                        os.remove(config.VOICE_SAMPLE_PATH)
                    except OSError:
                        pass
                    raise HTTPException(status_code=413, detail="File too large.")
                buffer.write(chunk)
        logger.info(f"Custom voice sample uploaded successfully: {config.VOICE_SAMPLE_PATH}")
        return {"status": "success", "file_path": config.VOICE_SAMPLE_PATH}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save voice sample: {e}")

@app.post("/start", summary="Starts the AI Interview Agent")
async def start_agent(request: StartAgentRequest):
    if state.orchestrator_task and not state.orchestrator_task.done():
        raise HTTPException(status_code=400, detail="Agent is already running.")

    meet_url = (request.meeting_url or config.MEETING_URL or "").strip()
    if not meet_url:
        raise HTTPException(
            status_code=400, 
            detail="Meeting URL is required. Provide via request or environment/config.py."
        )
    parsed = urlparse(meet_url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Meeting URL is not a valid URL.")

    bot_name = (request.bot_name or "Humail").strip() or "Humail"
    llm_provider = request.llm_provider or "ollama"
    if llm_provider not in ("ollama", "gemini"):
        raise HTTPException(status_code=400, detail="llm_provider must be 'ollama' or 'gemini'.")

    state.meeting_url = meet_url
    state.bot_name = bot_name
    state.current_status = "starting"

    # Assemble custom persona if candidate_data is supplied
    custom_persona = None
    if request.candidate_data:
        data = request.candidate_data or {}
        skills = data.get("skills", [])
        if isinstance(skills, list):
            skills_str = ", ".join(str(s) for s in skills if s) or "Python"
        else:
            skills_str = str(skills) or "Python"
        custom_persona = (
            f"Candidate Profile:\n"
            f"- Name: {data.get('name', bot_name)}\n"
            f"- Education: {data.get('education', 'BS AI/CS')}\n"
            f"- Experience: {data.get('experience', 'AI Agent Developer')}\n"
            f"- Skills: {skills_str}\n"
            f"- Personality: {data.get('personality', 'confident')}\n"
            f"- Speaking Style Rules: Never say 'As an AI', use natural fillers like 'well' or 'you know', be concise."
        )

    # Instantiate the Orchestrator
    orchestrator = InterviewAgentOrchestrator(
        meeting_url=meet_url,
        bot_name=bot_name,
        llm_provider=llm_provider
    )
    
    if custom_persona:
        orchestrator.brain.persona = custom_persona
        orchestrator.brain.clear_history()

    state.orchestrator = orchestrator
    
    # Persist the session so /status survives restarts (SaaS claim).
    try:
        state_store.save_session(
            session_id="active",
            meeting_url=meet_url,
            bot_name=bot_name,
            status="starting",
            avatar_path=config.AVATAR_IMAGE_PATH,
            voice_path=config.VOICE_SAMPLE_PATH,
        )
    except Exception as e:
        logger.warning(f"Could not persist session to store: {e}")

    # Spawn orchestrator in the asyncio background
    state.orchestrator_task = asyncio.create_task(run_orchestrator_background(orchestrator))

    return {
        "status": "starting",
        "message": f"Successfully launched AI agent '{bot_name}' to join {meet_url}",
        "bot_name": bot_name,
        "meeting_url": meet_url
    }

@app.post("/stop", summary="Stops the active AI Agent session")
async def stop_agent():
    if not state.orchestrator:
        return {"status": "idle", "message": "Agent is not currently running."}

    logger.info("Received request to stop the active AI Agent.")
    state.current_status = "stopped"
    
    # Trigger orchestrator shutdown with a safety timeout so a hung client call
    # cannot block this endpoint forever.
    try:
        await asyncio.wait_for(state.orchestrator.shutdown(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error("Orchestrator shutdown timed out after 30s; forcing task cancel.")
    
    # Cancel the asyncio task
    if state.orchestrator_task:
        state.orchestrator_task.cancel()
        try:
            await state.orchestrator_task
        except (asyncio.CancelledError, Exception):
            pass
        state.orchestrator_task = None

    state.orchestrator = None
    return {"status": "stopped", "message": "AI Agent has been successfully stopped."}

@app.get("/status", summary="Returns the current server and agent status")
async def get_status():
    call_id = None
    pika_id = None
    if state.orchestrator:
        call_id = state.orchestrator.session.get("call_id") if state.orchestrator.session else None
        pika_id = state.orchestrator.pika_session_id

    return {
        "status": state.current_status,
        "meeting_url": state.meeting_url,
        "bot_name": state.bot_name,
        "call_id": call_id,
        "pika_session_id": pika_id,
        "coqui_xtts_active": bool(state.orchestrator and getattr(state.orchestrator.cloner, "is_initialized", False)) if state.orchestrator else False
    }

@app.websocket("/ws/session/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for receiving real-time status updates (question transcripts, 
    thinking indicators, and answer playback updates) for a given session.
    """
    await manager.connect(session_id, websocket)
    try:
        while True:
            # Keep connection open and respond to any client messages if sent
            data = await websocket.receive_text()
            # Simple heartbeat response
            await websocket.send_json({"event": "heartbeat", "received": data})
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket connection error on session {session_id}: {e}")
        manager.disconnect(session_id, websocket)

if __name__ == "__main__":
    # Start FastApi server on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
