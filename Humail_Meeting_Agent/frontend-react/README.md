# ⚛️ React Frontend Dashboard for AI Interview Agent

This is a modern, responsive, and highly animated **React + Vite + Tailwind CSS** frontend dashboard for the Autonomous AI Interview Agent.

---

## ✨ Features
- 📂 **Multipart Media Uploads**: Live uploading of portrait headshot pictures (`/upload/avatar`), voice clone samples (`/upload/voice`), and text prompt files (.txt).
- 🎥 **Camera Configurations**: Adjust resolution (1080p, 720p), FPS (30, 60), and backdrop style.
- 🎛️ **Audio Switches**: Toggle barge-in protection and noise filtering.
- 🗣️ **Reactive Pulse Border**: A gorgeous, status-reflective circular glow border that pulses and updates in real-time matching the candidate state (`listening`, `thinking`, `speaking`).
- 💬 **Live Transcript Feed**: Streams questions from the interviewer and answers from the candidate with a scrolling chat bubble interface.
- 🧠 **Real-Time Token Streaming**: Simulates a live writing/streaming typewriter effect as individual tokens stream from Ollama or Gemini over WebSockets.

---

## 🚀 How to Run the React Frontend

Ensure you have [Node.js](https://nodejs.org) installed on your machine.

### 1. Install Dependencies
Change into this directory and run:
```bash
npm install
```

### 2. Start the Development Server
Run the Vite development script:
```bash
npm run dev
```
*Vite will start your local React server (usually at `http://localhost:3000` or `http://localhost:5173`).*

### 3. Build for Production
To build static assets for production:
```bash
npm run build
```

---

## 📡 API Integration Notes
- This React app automatically auto-detects your browser's current IP address and connects to your FastAPI backend server at `http://<your-ip>:8000`.
- Ensure your backend server (`python3 app.py`) is running on port `8000` so that uploads and WebSocket sessions establish successfully!
