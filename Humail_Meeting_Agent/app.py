import asyncio
import logging
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn

import config
from agent.orchestrator import InterviewAgentOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("API-Server")

app = FastAPI(
    title="Autonomous AI Interview Agent API",
    description="REST & WebSocket API to coordinate and track the AI Interview Candidate Agent.",
    version="1.0.0"
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
        # Also broadcast to "all" listeners
        all_targets = self.active_connections.get("all", [])
        
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

# Callback trigger to broadcast orchestrator state changes over WebSocket
async def orchestrator_status_callback(payload: dict):
    session_id = "active"
    if state.orchestrator and state.orchestrator.session:
        session_id = state.orchestrator.session.get("call_id", "active")
    
    # Broadcast event
    await manager.broadcast(session_id, payload)

async def run_orchestrator_background(orchestrator: InterviewAgentOrchestrator):
    """Asynchronous background wrapper that executes the agent's main loop."""
    try:
        state.current_status = "interviewing"
        # Overwrite orchestrator status updates to connect with WebSocket broadcasting
        # We hook into process_question and other phases to emit updates
        original_process_question = orchestrator._process_question
        
        async def hooked_process_question(question: str):
            session_id = orchestrator.session.get("call_id", "active") if orchestrator.session else "active"
            await manager.broadcast(session_id, {
                "event": "question_received",
                "text": question,
                "speaker": "Interviewer"
            })
            
            # Update state to thinking and broadcast
            await manager.broadcast(session_id, {"event": "state_change", "state": "thinking"})
            await original_process_question(question)
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

@app.post("/start", summary="Starts the AI Interview Agent")
async def start_agent(request: StartAgentRequest, background_tasks: BackgroundTasks):
    if state.orchestrator_task and not state.orchestrator_task.done():
        raise HTTPException(status_code=400, detail="Agent is already running.")

    meet_url = request.meeting_url or config.MEETING_URL
    if not meet_url:
        raise HTTPException(
            status_code=400, 
            detail="Meeting URL is required. Provide via request or environment/config.py."
        )

    state.meeting_url = meet_url
    state.bot_name = request.bot_name
    state.current_status = "starting"

    # Assemble custom persona if candidate_data is supplied
    custom_persona = None
    if request.candidate_data:
        data = request.candidate_data
        custom_persona = (
            f"Candidate Profile:\n"
            f"- Name: {data.get('name', request.bot_name)}\n"
            f"- Education: {data.get('education', 'BS AI/CS')}\n"
            f"- Experience: {data.get('experience', 'AI Agent Developer')}\n"
            f"- Skills: {', '.join(data.get('skills', [])) if isinstance(data.get('skills'), list) else data.get('skills', 'Python')}\n"
            f"- Personality: {data.get('personality', 'confident')}\n"
            f"- Speaking Style Rules: Never say 'As an AI', use natural fillers like 'well' or 'you know', be concise."
        )

    # Instantiate the Orchestrator
    orchestrator = InterviewAgentOrchestrator(
        meeting_url=meet_url,
        bot_name=request.bot_name,
        llm_provider=request.llm_provider
    )
    
    if custom_persona:
        orchestrator.brain.persona = custom_persona
        orchestrator.brain.clear_history()

    state.orchestrator = orchestrator
    
    # Spawn orchestrator in the asyncio background
    state.orchestrator_task = asyncio.create_task(run_orchestrator_background(orchestrator))

    return {
        "status": "starting",
        "message": f"Successfully launched AI agent '{request.bot_name}' to join {meet_url}",
        "bot_name": request.bot_name,
        "meeting_url": meet_url
    }

@app.post("/stop", summary="Stops the active AI Agent session")
async def stop_agent():
    if not state.orchestrator:
        return {"status": "idle", "message": "Agent is not currently running."}

    logger.info("Received request to stop the active AI Agent.")
    state.current_status = "stopped"
    
    # Trigger orchestrator shutdown
    await state.orchestrator.shutdown()
    
    # Cancel the asyncio task
    if state.orchestrator_task:
        state.orchestrator_task.cancel()
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
        "coqui_xtts_active": hasattr(state.orchestrator, "cloner") and state.orchestrator.cloner.is_initialized if state.orchestrator else False
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
