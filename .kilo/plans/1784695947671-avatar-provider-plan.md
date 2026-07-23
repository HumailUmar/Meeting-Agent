# Viva Bot Re-engineering: Replace PikaStream with D-ID Real-Time Avatar Provider
## Decision
Keep AgentCall for meeting transport. Replace PikaStream with **D-ID Realtime V4** (sole primary) for avatar rendering. Preserve the existing Python/FastAPI orchestrator, React frontend, state store, AI brain, Deepgram transcription, and Coqui XTTS voice pipeline.

## Verified Facts from Codebase
- `agentcall/references/api.md` confirms AgentCall supports `webpage_url` and `screenshare_url` in call creation, plus `audio.inject` for raw PCM injection.
- `Pika-Skills/pikastream-video-meeting/SKILL.md` confirms Pika joins via a subprocess CLI and uses a static image (`--image`) with an "animated avatar" template — no photorealistic lip-sync rendering.
- `agent/avatar.py` is tightly coupled to Pika's subprocess CLI, confirming it as the right replacement target.
- `agent/orchestrator.py` already isolates avatar integration behind `AvatarManager`, so swapping the implementation is low blast-radius.

## Independent Research Findings (2026-07-22)

### D-ID Realtime V4 (d-id.com) — SOLE PRIMARY
- Expressives (V4): LiveKit-based streaming, sub-0.5-second latency, launched March 2026
- Official LiveKit plugin exists for V4 agents
- **Higher integration complexity** — requires LiveKit peer connection or D-ID Agents SDK management
- **Photorealistic quality confirmed**
- Only provider that satisfies BOTH sub-second latency AND photorealism after Flexatar quality testing failed

### Flexatar (flexatar-sdk.com) — REJECTED
- Browser-native 3D avatar SDK running locally via WebGL/WebAssembly
- `connectMediaStream()` attaches a `MediaStream` with audio track and returns a lip-synced video stream for playback
- Markets output as "photorealistic speaking avatar generated from a single photo"
- Subscription model (~$5/month); SDK free for testing
- **Sub-second latency, native `MediaStream` return, lowest bridge complexity**
- **REJECTED:** Real-world testing confirmed avatar quality is below acceptable threshold for interview use case

### HeyGen — DEPRIORITIZED
- Avatar Realtime (`/v3/avatar-realtime`): HLS-based, ~2-5s latency
- LiveAvatar: LiveKit WebRTC, separate product/subscription
- Deprioritized because HLS latency violates the sub-second hard requirement

## Latency Requirement
**Sub-second latency is a hard requirement.** No human presence during the actual meeting is acceptable. This disqualifies HLS-based providers as primary.

## Provider Priority
| Priority | Provider | Latency | Transport | Bridge Complexity | Notes |
|----------|----------|---------|-----------|------------------|-------|
| **1 (Sole Primary)** | **D-ID Realtime V4** | **<0.5s** | LiveKit WebRTC | High — peer/SFU or Agents SDK management | Only option satisfying sub-second + photorealism. |
| **2 (Deprioritized)** | **HeyGen Avatar Realtime** | ~2-5s | HLS polling + text append | Low — play HLS URL in `<video>` | Available only if latency requirement is relaxed. |

## System Structure
### Architecture (text)

User (React frontend)
  │  HTTPS (REST + WebSocket)
  ▼
FastAPI Backend (`app.py`)
  │
  ├─► AgentCall SDK (`agent/meeting.py`) — meeting transport
  │     • join Google Meet / Zoom
  │     • inject audio (`audio.inject`)
  │     • transcript WebSocket events
  │     • state commands (`voice.state_update`)
  │
  ├─► Avatar Provider Layer (NEW)
  │     └─► D-ID Realtime V4 — LiveKit WebRTC, <0.5s latency
  │
  ├─► AI Brain (`agent/brain.py`) — Gemini / Ollama streaming answers
  ├─► Deepgram (`agent/audio.py`) — optional real-time audio transcription
  ├─► Voice Cloner (`agent/voice.py`) — Coqui XTTS v2 local OR cloud TTS fallback
  └─► State Store (`agent/store.py`) — SQLite session persistence

### Critical Integration Point
AgentCall `webpage-av` mode creates a tunnel that serves a webpage. Your custom `webpage_url` must render the D-ID Realtime V4 avatar stream and expose it as a captured media stream for AgentCall to inject into the meeting.

**Path A (preferred):** Custom webpage initializes D-ID Agents SDK / LiveKit peer connection, renders the avatar stream to a `<video>` element, and captures it via `video.captureStream()` or directly uses the peer's video track as a `MediaStream`. Audio is injected separately using AgentCall's `audio.inject`.

**Path B (fallback):** AgentCall `webpage-av-screenshare` mode, where the bot appears as a screen share rather than a camera participant.

**Prerequisite:** Confirm with AgentCall that their tunnel page infrastructure permits an externally hosted `webpage_url` to use `captureStream()` on an embedded video stream. If not, Path B becomes the default and the UX trade-off must be accepted.

## Functional Requirements (FRs)
FR1. The system MUST allow the candidate to choose D-ID as the avatar rendering provider at session start.
FR2. The system MUST use D-ID to generate a talking-head video stream from an uploaded photo and resume / system prompt.
FR3. The system MUST synthesize speech for the candidate's answers using D-ID's voice clone or a fallback TTS.
FR4. The system MUST inject the synthesized audio into the AgentCall meeting session.
FR5. The system MUST render the D-ID video stream as the bot's visible presence inside the meeting.
FR6. The system MUST preserve existing Deepgram, Gemini/Ollama, and SQLite state-management functionality unchanged.
FR7. The system MUST preserve AgentCall as the meeting transport layer without migrating to another provider.
FR8. The system MUST preserve the React frontend start/stop/status/transcript flow with minimal UX changes.
FR9. The system MUST fail gracefully to AgentCall native TTS or a static avatar image if D-ID synthesis fails.
FR10. The system MUST support re-cloning the voice per session when the existing reference sample is unavailable or expired.

## Use Cases
UC1. D-ID single-interview session
- Candidate uploads photo, voice sample, resume.
- Selects D-ID provider.
- Enters Google Meet URL and starts the agent.
- Agent joins via AgentCall, renders D-ID avatar, answers questions in cloned voice.
- Candidate stops the agent; transcript is persisted.

UC2. Provider fallback
- D-ID returns 5xx, rate limit, or exceeds quota.
- System logs the failure, falls back to AgentCall native TTS for audio and either no avatar or a static image, and continues the interview without crashing.

UC3. Real-time monitoring
- Frontend connects via WebSocket to `/ws/session/active`.
- Displays live state: `starting`, `interviewing`, `listening`, `thinking`, `speaking`, `stopped`.
- Shows interviewer questions and AI responses with streaming tokens.

UC4. Session replay / admin audit
- Admin queries SQLite state store.
- Views past sessions including provider used, call ID, transcript, timestamps.

## Limitations
L1. AgentCall is paid after free trial credits. Zero marginal cost is not achievable with the current transport layer.
L2. D-ID does not natively join Google Meet/Zoom. The bot appears inside the call only because AgentCall transports the stream. If AgentCall transport is removed, this architecture fails.
L3. Added latency is unavoidable but targeted for sub-second. Goal is <0.5s to first audio chunk with D-ID Realtime V4.
L4. D-ID has independent rate limits, concurrency limits, and costs that are additional to AgentCall billing.
L5. D-ID Realtime V4 requires LiveKit peer connection management or use of the D-ID Agents SDK. This is significant integration complexity.
L6. Existing frontend camera controls (resolution, fps, backdrop) are meaningless for cloud-rendered avatar streams. These controls must be hidden when D-ID is selected.
L7. Coqui XTTS v2 voice cloning is CPU-bound and slow without GPU. On CPU-only hosts it falls back or is skipped. This limitation is unchanged but must be documented.
L8. The React frontend currently connects to a single active session (`/ws/session/active`). Multi-instance multi-session support is out of scope for this re-engineering.
L9. D-ID processes avatar photos, voice samples, and resumes on remote servers. Privacy and data-residency implications are the team's responsibility.
L10. Google Meet/Zoom bot-detection heuristics remain a risk regardless of avatar provider. This re-engineering does not reduce or eliminate that risk.

## Phases & Deliverables

### Phase 0: Prerequisite Gate
**Decision required before implementation begins.**
- Confirm with AgentCall support whether a custom `webpage_url` can use `video.captureStream()` on an embedded video stream to act as the bot camera source.
- If not possible, select Path B (screenshare-as-avatar) as the accepted UX trade-off and update L2/L6 accordingly.

---

### Phase 1: Abstract Avatar Provider Interface
Goal: define the contract and refactor existing code to support D-ID as the sole implementation.

**Deliverables:**
- `agent/avatar/base.py`: `AvatarProvider` abstract base class with `create_avatar`, `clone_voice`, `start_stream`, `send_audio`, `stop_stream`. Include `AvatarProviderQuotaError`.
- `agent/avatar.py`: Refactor `AvatarManager` to delegate to an injected `AvatarProvider` instance. Remove Pika-specific subprocess code.
- `config.py`: Add `AVATAR_PROVIDER` (`did` | `pika`) and `DID_API_KEY`.
- `agent/orchestrator.py`: Accept optional `avatar_provider` in `__init__` and pass it to `AvatarManager`.
- Tests in `test_agent.py`: contract test for `AvatarProvider`, delegation test for `AvatarManager`, state-store persistence for new columns.

**Acceptance criteria:**
- `python -m unittest test_agent.py` passes.
- No imports of PikaStreamingVideomeeting remain in `agent/avatar.py`.
- `python -c "from agent.avatar.base import AvatarProvider; print('ok')"` succeeds.

---

### Phase 2: D-ID Provider Implementation
Goal: complete working D-ID integration for avatar creation, voice clone, streaming, and audio injection.

**Deliverables:**
- `agent/avatar/did_provider.py`: Complete `DIdProvider` implementing all `AvatarProvider` methods.
  - `create_avatar`: POST to D-ID `/avatars` with multipart form.
  - `clone_voice`: POST to D-ID `/voices` with audio file.
  - `start_stream`: Call D-ID Agents Streams API, poll for readiness, return `session_id` and `stream_url`.
  - `stop_stream`: DELETE D-ID stream session; swallow 404.
  - `send_audio`: Implement if D-ID supports audio chunking; otherwise raise `NotImplementedError`.
- Error handling: map 401/402/429 to `AvatarProviderQuotaError`.
- Retry: exponential backoff for 429/5xx on `start_stream` (tenacity).
- Tests: `TestDIdProviderLifecycle`, `TestDIdProviderQuota`, `TestDIdProviderStopStream404`.

**Acceptance criteria:**
- `python -m unittest test_agent.py` passes with new D-ID tests.
- All five interface methods are implemented; none raise `NotImplementedError`.

---

### Phase 3: Avatar Stream Renderer HTML Helper Page
Goal: define, implement, and validate the custom HTML page that bridges the D-ID stream into AgentCall's meeting transport. Hard prerequisite for Phase 4.

**Deliverables:**
- `frontend/avatar-page.html`: Self-contained static HTML page.
  - Receives `session_id` via URL path at `/avatar-page/{session_id}`.
  - Loads config (`stream_url`, `provider=did`, `session_id`) from backend-injected JSON or status API.
  - Renders D-ID stream in `<video id="avatar-video">` via HLS/WebRTC or D-ID Agents SDK if provided.
  - Captures stream via `video.captureStream()` on `playing` event.
  - Shows visible error overlay if capture fails; polls `/api/avatar-page/status/{session_id}` every 5s.
  - Sends `POST /api/avatar-page/stop` on `window.onbeforeunload`.
- Backend routes in `app.py`:
  - `GET /avatar-page/{session_id}`: render HTML with injected config.
  - `GET /api/avatar-page/status/{session_id}`: return session status JSON.
  - `POST /api/avatar-page/stop`: signal orchestrator shutdown.
- `agent/store.py`: add `provider_session_id`, `stream_url`, `avatar_provider` columns.
- `agent/orchestrator.py`: accept stop signal from avatar page to break event loop.

**Acceptance criteria:**
- `python -m unittest test_agent.py` passes.
- Visiting `/avatar-page/test-session` renders the HTML page with injected config.
- Browser console shows track count when stream initializes.

---

### Phase 4: Orchestrator Wiring
Goal: wire D-ID provider into the running orchestrator without breaking existing AgentCall flow.

**Deliverables:**
- `agent/orchestrator.py`:
  - Store `avatar_provider` instance.
  - After `join_meeting`, call `provider.create_avatar`, `provider.clone_voice`, and `provider.start_stream`.
  - Persist `provider`, `provider_session_id`, `stream_url` to state store.
  - Build avatar page URL and pass it as `webpage_url` to AgentCall.
  - Fallback to AgentCall native TTS on provider failure.
- `agent/orchestrator.py` `_process_question`:
  - If D-ID active, attempt `provider.send_audio` or fallback to `client.send_command({"type": "tts.speak", ...})`.
- `agent/orchestrator.py` `shutdown`:
  - Call `provider.stop_stream` if active; clear persisted stream data.
- `config.py`: add `BACKEND_BASE_URL`.

**Acceptance criteria:**
- `python -m unittest test_agent.py` passes with mocks.
- Orchestrator calls `create_avatar`, `clone_voice`, and `start_stream` exactly once when provider is set and join succeeds.
- Shutdown calls `provider.stop_stream` when `provider_session_id` is set.

---

### Phase 5: Voice Pipeline Extension
Goal: support cloud voice clone as alternative to local Coqui.

**Deliverables:**
- `agent/voice.py`:
  - `VoiceCloner.__init__` accepts `voice_clone_provider` (`None` | `local` | `did`).
  - Skip Coqui initialization when provider is `did`.
  - `speak()` bypasses local synthesis when `voice_clone_provider == "did"` and sends text via AgentCall TTS.
- `config.py`: add `VOICE_CLONE_PROVIDER` defaulting to `local`.
- `agent/orchestrator.py`: pass `VOICE_CLONE_PROVIDER` into `VoiceCloner`.
- Test: verify `VoiceCloner.speak` does not call `generate_cloned_audio` when `VOICE_CLONE_PROVIDER=did`.

**Acceptance criteria:**
- `python -m unittest test_agent.py` passes.
- With `VOICE_CLONE_PROVIDER=did` and `COQUI_AVAILABLE=False`, `VoiceCloner.speak` routes through AgentCall TTS without touching Coqui.

---

### Phase 6: Frontend Extension
Goal: expose provider selection to the user with minimal surface area.

**Deliverables:**
- `frontend-react/src/App.jsx`:
  - Add D-ID radio selector defaulting to `did`.
  - Hide Camera & Video Quality accordion when D-ID is selected.
  - Persist `avatarProvider` in `localStorage`.
- `frontend-react/src/services/api.js`: pass `avatar_provider` in payload.
- `app.py`: add `avatar_provider: Optional[str] = None` to `StartAgentRequest` and persist to state store.

**Acceptance criteria:**
- React app loads without console errors.
- `POST /start` accepts `avatar_provider` without 422.
- State store records `avatar_provider` for the session.

---

### Phase 7: Integration Testing
Goal: validate the full end-to-end flow with mocks and a manual smoke test.

**Deliverables:**
- `test_agent.py` additions:
  - `TestDIdProviderLifecycle`, `TestDIdProviderQuota`, `TestDIdProviderStopStream404`.
  - `TestOrchestratorProviderFallback`: verify graceful fallback to AgentCall TTS.
  - Manual integration checklist as comments.
- Manual smoke test:
  - Start `uvicorn app:app --port 8000`.
  - Start `npm run dev`.
  - Select D-ID, upload dummy files, enter Meet URL, click Initialize.
  - Verify bot joins, avatar page renders, answers one question, stops cleanly.

**Acceptance criteria:**
- `python -m unittest test_agent.py` passes.
- Fallback path is covered by a test.

---

## Key Risks
- **Gate risk:** AgentCall tunnel + external stream capture is unconfirmed. Implementation of Phase 3 is blocked until this is validated.
- **Cost risk:** D-ID costs are additional to AgentCall billing. Exact pricing must be confirmed during implementation.
- **Latency risk:** Every additional provider hop adds round-trip time. Mitigation: D-ID Realtime V4 is sub-0.5s at the rendering layer; orchestrator overhead must be kept minimal.
- **CORS/Helmet risk:** AgentCall may restrict embedding external streaming origins in tunnel pages. Mitigation: serve the stream renderer from your own origin, or use Path B (screenshare).
- **LiveKit complexity risk:** D-ID Realtime V4 requires LiveKit peer/SFU or Agents SDK management. This is the highest-risk integration in the plan. Assign your most experienced WebRTC engineer.
