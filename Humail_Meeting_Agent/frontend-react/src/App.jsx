import React, { useState, useEffect, useRef } from 'react';
import { 
  Video, Mic, Sliders, Play, Square, IDCard, Trash2, 
  Brain, Image as ImageIcon, FileAudio, FileText, CheckCircle, 
  RefreshCw, Loader2, Volume2, ShieldAlert, ChevronDown, Check, HelpCircle
} from 'lucide-react';

export default function App() {
  // Connection & API setup
  const [backendUrl, setBackendUrl] = useState('');
  useEffect(() => {
    // Automatically determine backend host based on the window location
    const protocol = window.location.protocol;
    const host = window.location.hostname;
    setBackendUrl(`${protocol}//${host}:8000`);
  }, []);

  // Session Configuration State
  const [meetUrl, setMeetUrl] = useState('');
  const [botName, setBotName] = useState('Humail');
  const [llmProvider, setLlmProvider] = useState('gemini');
  
  // Custom Profile Persona State
  const [education, setEducation] = useState('Bachelor of Science in Artificial Intelligence & Computer Science');
  const [skills, setSkills] = useState('Python, FastAPI, WebSockets, Real-time audio pipelines, Ollama, Gemini, Voice cloning, Docker, Git');
  const [experience, setExperience] = useState('Expert AI Agent Developer and Software Engineer with experience in building autonomous systems, full-stack AI applications, meeting bots, and real-time audio/video AI pipelines.');
  
  // UI Accordion Toggle States
  const [showPersona, setShowPersona] = useState(false);
  const [showMedia, setShowMedia] = useState(false);
  const [showCamera, setShowCamera] = useState(false);

  // Camera Settings State
  const [resolution, setResolution] = useState('720p');
  const [fps, setFps] = useState('30');
  const [backdrop, setBackdrop] = useState('blur');

  // Interactive Switch States
  const [bargeIn, setBargeIn] = useState(true);
  const [noiseFilter, setNoiseFilter] = useState(true);

  // Upload States
  const [avatarUploadStatus, setAvatarUploadStatus] = useState('default headshot');
  const [voiceUploadStatus, setVoiceUploadStatus] = useState('default_cloned_voice');
  const [promptUploadStatus, setPromptUploadStatus] = useState('No custom file loaded');
  const [avatarTimestamp, setAvatarTimestamp] = useState(Date.now());

  // Agent Runtime / Call State
  const [agentStatus, setAgentStatus] = useState('idle'); // idle, starting, interviewing, stopped
  const [runtime, setRuntime] = useState('00:00:00');
  const [backendConnected, setBackendConnected] = useState(false);
  const [transcripts, setTranscripts] = useState([]);
  const [streamingText, setStreamingText] = useState('');

  // Refs for auto-scroll and sockets
  const transcriptEndRef = useRef(null);
  const ws = useRef(null);
  const timerRef = useRef(null);
  const secondsElapsed = useRef(0);

  // Auto-load meeting url if present in localstorage
  useEffect(() => {
    const savedUrl = localStorage.getItem("last_meet_url");
    if (savedUrl) {
      setMeetUrl(savedUrl);
    }
  }, []);

  // 1. Establish WebSocket connection to backend
  const connectWebSocket = () => {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.hostname || "localhost";
    const wsUrl = `${wsProtocol}//${host}:8000/ws/session/active`;

    console.log(`Connecting Status WebSocket: ${wsUrl}`);
    ws.current = new WebSocket(wsUrl);

    ws.current.onopen = () => {
      setBackendConnected(true);
    };

    ws.current.onclose = () => {
      setBackendConnected(false);
      // Auto-reconnect after 3 seconds
      setTimeout(connectWebSocket, 3000);
    };

    ws.current.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWebSocketMessage(msg);
      } catch (e) {
        console.warn("Non-JSON message:", event.data);
      }
    };
  };

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (ws.current) ws.current.close();
      clearInterval(timerRef.current);
    };
  }, []);

  // Auto-scroll transcripts
  useEffect(() => {
    if (transcriptEndRef.current) {
      transcriptEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [transcripts, streamingText]);

  // 2. Handle incoming WebSocket updates
  const handleWebSocketMessage = (msg) => {
    if (msg.event === "state_change") {
      setAgentStatus(msg.state);
    }
    else if (msg.event === "question_received") {
      setTranscripts(prev => [...prev, {
        id: Date.now(),
        speaker: "Interviewer",
        text: msg.text,
        timestamp: new Date().toLocaleTimeString()
      }]);
    }
    else if (msg.event === "brain_stream_token") {
      setStreamingText(prev => prev + msg.text);
    }
    else if (msg.event === "brain_response_done") {
      setTranscripts(prev => [...prev, {
        id: Date.now(),
        speaker: botName,
        text: msg.text,
        timestamp: new Date().toLocaleTimeString()
      }]);
      setStreamingText('');
    }
  };

  // 3. API Actions: Start Agent
  const startAgent = async () => {
    if (!meetUrl) {
      alert("Please enter a valid Google Meet or Zoom URL before joining.");
      return;
    }

    localStorage.setItem("last_meet_url", meetUrl);
    setAgentStatus('starting');
    
    const payload = {
      meeting_url: meetUrl,
      bot_name: botName,
      llm_provider: llmProvider,
      candidate_data: {
        name: botName,
        education,
        experience,
        skills: skills.split(',').map(s => s.trim())
      }
    };

    try {
      const resp = await fetch(`${backendUrl}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      
      const data = await resp.json();
      if (resp.ok) {
        setAgentStatus('interviewing');
        startTimer();
      } else {
        alert(`Failed to start: ${data.detail || "Error"}`);
        setAgentStatus('idle');
      }
    } catch (e) {
      alert("Error connecting to FastAPI backend. Please check if app.py is running on port 8000.");
      setAgentStatus('idle');
    }
  };

  // 4. API Actions: Stop Agent
  const stopAgent = async () => {
    setAgentStatus('idle');
    stopTimer();
    try {
      await fetch(`${backendUrl}/stop`, { method: "POST" });
    } catch (e) {
      console.error("Failed to stop agent cleanly:", e);
    }
  };

  // 5. API Actions: File Uploads (Portraits, Voice, Prompt Text)
  const handleFileUpload = async (e, type) => {
    const file = e.target.files[0];
    if (!file) return;

    const setStatus = type === 'avatar' ? setAvatarUploadStatus : setVoiceUploadStatus;
    setStatus('Uploading...');

    const formData = new FormData();
    formData.append("file", file);

    try {
      const endpoint = type === 'avatar' ? '/upload/avatar' : '/upload/voice';
      const resp = await fetch(`${backendUrl}${endpoint}`, {
        method: "POST",
        body: formData
      });

      if (resp.ok) {
        setStatus(`✓ Saved: ${file.name}`);
        if (type === 'avatar') {
          setAvatarTimestamp(Date.now()); // trigger preview img cache-bypass refresh
        }
      } else {
        setStatus('✗ Upload failed');
      }
    } catch {
      setStatus('✗ Connection error');
    }
  };

  // Local text file read for prompt uploads
  const handlePromptUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setPromptUploadStatus(file.name);

    const reader = new FileReader();
    reader.onload = (event) => {
      setExperience(event.target.result);
      setShowPersona(true);
    };
    reader.readAsText(file);
  };

  // Timer helpers
  const startTimer = () => {
    secondsElapsed.current = 0;
    clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      secondsElapsed.current += 1;
      const hrs = String(Math.floor(secondsElapsed.current / 3600)).padStart(2, '0');
      const mins = String(Math.floor((secondsElapsed.current % 3600) / 60)).padStart(2, '0');
      const secs = String(secondsElapsed.current % 60).padStart(2, '0');
      setRuntime(`${hrs}:${mins}:${secs}`);
    }, 1000);
  };

  const stopTimer = () => {
    clearInterval(timerRef.current);
    setRuntime('00:00:00');
  };

  // Dynamic state class mapper
  const getGlowClass = () => {
    if (agentStatus === 'listening') return 'glow-listening border-blue-500';
    if (agentStatus === 'thinking') return 'glow-thinking border-purple-500';
    if (agentStatus === 'speaking') return 'glow-speaking border-emerald-500';
    if (agentStatus === 'starting') return 'border-indigo-500 animate-pulse';
    return 'border-slate-800';
  };

  return (
    <div className="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans antialiased">
      {/* Header */}
      <header className="border-b border-slate-900 bg-slate-950/80 backdrop-blur-md px-8 py-5 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center space-x-3.5">
          <div className="bg-gradient-to-tr from-indigo-600 to-indigo-500 p-2.5 rounded-xl shadow-lg shadow-indigo-600/20 flex items-center justify-center">
            <Volume2 className="text-white w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-xl font-extrabold bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
                Humail Umar
              </h1>
              <span className="text-[9px] bg-indigo-950 text-indigo-300 font-bold px-2 py-0.5 rounded-md uppercase tracking-wider">React v1.5</span>
            </div>
            <p className="text-xs text-slate-400 font-medium">Autonomous Real-Time AI Interview Agent Dashboard</p>
          </div>
        </div>
        <div className="flex items-center space-x-2 bg-slate-900 px-3.5 py-1.5 rounded-lg border border-slate-800">
          <span className="relative flex h-2 w-2">
            <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${backendConnected ? 'bg-emerald-400' : 'bg-rose-400'}`}></span>
            <span className={`relative inline-flex rounded-full h-2 w-2 ${backendConnected ? 'bg-emerald-500' : 'bg-rose-500'}`}></span>
          </span>
          <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
            {backendConnected ? "Live Connected" : "Connecting Backend..."}
          </span>
        </div>
      </header>

      {/* Main Grid Area */}
      <main className="flex-grow p-6 grid grid-cols-1 lg:grid-cols-12 gap-6 max-w-7xl mx-auto w-full">
        {/* Left Control Column (5 columns) */}
        <section className="lg:col-span-5 flex flex-col space-y-6">
          
          {/* Main Control Card */}
          <div className="bg-slate-900/60 border border-slate-900 rounded-2xl p-6 shadow-xl backdrop-blur-md flex flex-col space-y-5">
            <div className="flex items-center justify-between border-b border-slate-800/60 pb-3">
              <div className="flex items-center space-x-2.5">
                <Sliders className="text-indigo-400 w-5 h-5" />
                <h2 className="font-extrabold text-slate-200">Session Controller</h2>
              </div>
              <span className="text-xs text-slate-400 font-mono bg-slate-950 border border-slate-800 px-2.5 py-1 rounded-md">{runtime}</span>
            </div>

            {/* Meeting Link input */}
            <div className="flex flex-col space-y-1.5">
              <label className="text-xs font-extrabold uppercase tracking-wider text-slate-400">Meeting Link (Google Meet / Zoom)</label>
              <div className="relative">
                <span className="absolute inset-y-0 left-0 flex items-center pl-3.5 pointer-events-none text-slate-500">
                  <Video className="w-4 h-4" />
                </span>
                <input type="text" value={meetUrl} onChange={(e) => setMeetUrl(e.target.value)} placeholder="https://meet.google.com/xxx-xxxx-xxx" 
                  className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded-xl py-2.5 pl-10 pr-4 text-sm text-slate-100 placeholder-slate-600 transition-all outline-none" />
              </div>
            </div>

            {/* Bot name & Provider */}
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col space-y-1.5">
                <label className="text-xs font-extrabold uppercase tracking-wider text-slate-400">Candidate Name</label>
                <input type="text" value={botName} onChange={(e) => setBotName(e.target.value)} 
                  className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded-xl py-2.5 px-4 text-sm text-slate-100 outline-none transition-all" />
              </div>
              <div className="flex flex-col space-y-1.5">
                <label className="text-xs font-extrabold uppercase tracking-wider text-slate-400">LLM Brain Provider</label>
                <select value={llmProvider} onChange={(e) => setLlmProvider(e.target.value)} 
                  className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded-xl py-2.5 px-3 text-sm text-slate-100 outline-none transition-all cursor-pointer">
                  <option value="ollama">Ollama (Local)</option>
                  <option value="gemini">Gemini AI (Cloud)</option>
                </select>
              </div>
            </div>

            {/* Media Upload Accordion */}
            <div className="border border-slate-800/80 rounded-xl bg-slate-950/30 p-4">
              <button onClick={() => setShowMedia(!showMedia)} className="flex items-center justify-between w-full text-slate-300 hover:text-indigo-400 transition-colors">
                <span className="text-xs font-extrabold uppercase tracking-wider flex items-center"><ImageIcon className="w-4 h-4 mr-2 text-indigo-400"/>Media &amp; Asset Uploads</span>
                <ChevronDown className={`w-4 h-4 transition-transform ${showMedia ? 'rotate-180' : ''}`} />
              </button>
              
              {showMedia && (
                <div className="mt-4 flex flex-col space-y-4 border-t border-slate-800/40 pt-3">
                  <div className="flex flex-col space-y-1">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Candidate Portrait (.png, .jpg)</span>
                    <div className="flex items-center space-x-3 mt-1">
                      <label className="bg-slate-900 border border-slate-800 hover:border-slate-750 hover:bg-slate-800 px-4 py-2 rounded-lg text-xs font-bold text-slate-300 cursor-pointer flex items-center space-x-2 transition-all">
                        <ImageIcon className="w-3.5 h-3.5 text-indigo-400" />
                        <span>Upload Image</span>
                        <input type="file" accept="image/*" onChange={(e) => handleFileUpload(e, 'avatar')} className="hidden" />
                      </label>
                      <span className="text-[11px] text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap max-w-[200px]">{avatarUploadStatus}</span>
                    </div>
                  </div>
                  <div className="flex flex-col space-y-1">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Voice Clone Reference (.wav, .mp3)</span>
                    <div className="flex items-center space-x-3 mt-1">
                      <label className="bg-slate-900 border border-slate-800 hover:border-slate-750 hover:bg-slate-800 px-4 py-2 rounded-lg text-xs font-bold text-slate-300 cursor-pointer flex items-center space-x-2 transition-all">
                        <FileAudio className="w-3.5 h-3.5 text-indigo-400" />
                        <span>Upload Reference</span>
                        <input type="file" accept="audio/*" onChange={(e) => handleFileUpload(e, 'voice')} className="hidden" />
                      </label>
                      <span className="text-[11px] text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap max-w-[200px]">{voiceUploadStatus}</span>
                    </div>
                  </div>
                  <div className="flex flex-col space-y-1">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">System Prompt Text File (.txt)</span>
                    <div className="flex items-center space-x-3 mt-1">
                      <label className="bg-slate-900 border border-slate-800 hover:border-slate-750 hover:bg-slate-800 px-4 py-2 rounded-lg text-xs font-bold text-slate-300 cursor-pointer flex items-center space-x-2 transition-all">
                        <FileText className="w-3.5 h-3.5 text-indigo-400" />
                        <span>Upload Prompt</span>
                        <input type="file" accept=".txt" onChange={handlePromptUpload} className="hidden" />
                      </label>
                      <span className="text-[11px] text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap max-w-[200px]">{promptUploadStatus}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Camera Accordion */}
            <div className="border border-slate-800/80 rounded-xl bg-slate-950/30 p-4">
              <button onClick={() => setShowCamera(!showCamera)} className="flex items-center justify-between w-full text-slate-300 hover:text-indigo-400 transition-colors">
                <span className="text-xs font-extrabold uppercase tracking-wider flex items-center"><Video className="w-4 h-4 mr-2 text-indigo-400"/>Camera &amp; Video Quality</span>
                <ChevronDown className={`w-4 h-4 transition-transform ${showCamera ? 'rotate-180' : ''}`} />
              </button>
              
              {showCamera && (
                <div className="mt-4 grid grid-cols-2 gap-4 border-t border-slate-800/40 pt-3 text-xs">
                  <div className="flex flex-col space-y-1">
                    <span className="text-slate-400 font-bold uppercase text-[9px] tracking-wider">Resolution</span>
                    <select value={resolution} onChange={(e) => setResolution(e.target.value)} className="bg-slate-900 border border-slate-800/80 p-2 rounded-lg text-slate-300 outline-none cursor-pointer">
                      <option value="1080p">1080p Full HD</option>
                      <option value="720p">720p HD</option>
                      <option value="480p">480p SD</option>
                    </select>
                  </div>
                  <div className="flex flex-col space-y-1">
                    <span className="text-slate-400 font-bold uppercase text-[9px] tracking-wider">Frame Rate</span>
                    <select value={fps} onChange={(e) => setFps(e.target.value)} className="bg-slate-900 border border-slate-800/80 p-2 rounded-lg text-slate-300 outline-none cursor-pointer">
                      <option value="60">60 FPS (Ultra Smooth)</option>
                      <option value="30">30 FPS (Standard)</option>
                    </select>
                  </div>
                  <div className="col-span-2 flex flex-col space-y-1">
                    <span className="text-slate-400 font-bold uppercase text-[9px] tracking-wider">Virtual Backdrop Style</span>
                    <select value={backdrop} onChange={(e) => setBackdrop(e.target.value)} className="bg-slate-900 border border-slate-800/80 p-2 rounded-lg text-slate-300 outline-none cursor-pointer">
                      <option value="neutral">Neutral Professional Studio</option>
                      <option value="office">Warm Modern Office</option>
                      <option value="darktech">Dark High-Tech Grid</option>
                      <option value="blur">Soft Blur Background</option>
                    </select>
                  </div>
                </div>
              )}
            </div>

            {/* Persona Accordion */}
            <div className="border border-slate-800/80 rounded-xl bg-slate-950/30 p-4">
              <button onClick={() => setShowPersona(!showPersona)} className="flex items-center justify-between w-full text-slate-300 hover:text-indigo-400 transition-colors">
                <span className="text-xs font-extrabold uppercase tracking-wider flex items-center"><IDCard className="w-4 h-4 mr-2 text-indigo-400"/>Candidate Background Profile</span>
                <ChevronDown className={`w-4 h-4 transition-transform ${showPersona ? 'rotate-180' : ''}`} />
              </button>
              
              {showPersona && (
                <div className="mt-4 flex flex-col space-y-3 border-t border-slate-800/40 pt-3 text-xs">
                  <div className="flex flex-col space-y-1">
                    <span className="text-slate-400 font-bold uppercase text-[9px] tracking-wider">Education</span>
                    <input type="text" value={education} onChange={(e) => setEducation(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-800 p-2 rounded-lg text-slate-300 outline-none" />
                  </div>
                  <div className="flex flex-col space-y-1">
                    <span className="text-slate-400 font-bold uppercase text-[9px] tracking-wider">Skills (Comma separated)</span>
                    <input type="text" value={skills} onChange={(e) => setSkills(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-800 p-2 rounded-lg text-slate-300 outline-none" />
                  </div>
                  <div className="flex flex-col space-y-1">
                    <span className="text-slate-400 font-bold uppercase text-[9px] tracking-wider">Work Experience Summary</span>
                    <textarea rows="3" value={experience} onChange={(e) => setExperience(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-800 p-2 rounded-lg text-slate-300 outline-none resize-none leading-relaxed" />
                  </div>
                </div>
              )}
            </div>

            {/* Switch toggles */}
            <div className="grid grid-cols-2 gap-4 bg-slate-950/30 p-3.5 border border-slate-800/40 rounded-xl">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-extrabold uppercase tracking-wider text-slate-400">Barge-in Filter</span>
                <button onClick={() => setBargeIn(!bargeIn)} className={`${bargeIn ? 'bg-indigo-600' : 'bg-slate-800'} w-9 h-5 rounded-full p-0.5 transition-colors relative flex items-center cursor-pointer`}>
                  <span className={`w-4 h-4 bg-white rounded-full transition-transform ${bargeIn ? 'translate-x-4' : 'translate-x-0'}`}></span>
                </button>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-extrabold uppercase tracking-wider text-slate-400">Noise Filter</span>
                <button onClick={() => setNoiseFilter(!noiseFilter)} className={`${noiseFilter ? 'bg-indigo-600' : 'bg-slate-800'} w-9 h-5 rounded-full p-0.5 transition-colors relative flex items-center cursor-pointer`}>
                  <span className={`w-4 h-4 bg-white rounded-full transition-transform ${noiseFilter ? 'translate-x-4' : 'translate-x-0'}`}></span>
                </button>
              </div>
            </div>

            {/* Start/Stop buttons */}
            <div className="flex flex-col space-y-3 pt-2">
              <button onClick={startAgent} disabled={agentStatus !== 'idle'}
                className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white font-extrabold py-3.5 px-4 rounded-xl shadow-lg hover:shadow-indigo-600/30 flex items-center justify-center space-x-2.5 transition-all">
                <Play className="w-4 h-4" />
                <span>Initialize &amp; Join Meeting</span>
              </button>
              <button onClick={stopAgent} disabled={agentStatus === 'idle'}
                className="w-full bg-rose-950/20 border border-rose-900/50 hover:bg-rose-900/30 active:bg-rose-950 disabled:opacity-20 text-rose-400 font-extrabold py-3.5 px-4 rounded-xl flex items-center justify-center space-x-2.5 transition-all">
                <Square className="w-4 h-4" />
                <span>Stop Agent Session</span>
              </button>
            </div>
          </div>

          {/* Animated Avatar Box */}
          <div className="bg-slate-900/40 border border-slate-900 rounded-2xl p-6 shadow-lg backdrop-blur-md flex flex-col items-center justify-center py-7">
            <div className="relative mb-4">
              <div className={`w-38 h-38 rounded-full border-4 flex items-center justify-center transition-all duration-350 ${getGlowClass()}`}>
                <div className="w-34 h-34 rounded-full overflow-hidden border-2 border-slate-950 bg-slate-900">
                  <img src={`${backendUrl}/static/placeholder-avatar.jpg?t=${avatarTimestamp}`} alt="Avatar" className="w-full h-full object-cover" />
                </div>
              </div>
              <div className="absolute -bottom-2.5 left-1/2 -translate-x-1/2 bg-slate-950 border border-slate-800 rounded-full px-4 py-1.5 text-[10px] uppercase font-bold tracking-widest text-indigo-400 flex items-center space-x-2 shadow-lg">
                <span className="flex h-2 w-2 relative">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${agentStatus === 'speaking' ? 'bg-emerald-400' : 'bg-blue-400'}`}></span>
                  <span className={`relative inline-flex rounded-full h-2 w-2 ${agentStatus === 'speaking' ? 'bg-emerald-500' : 'bg-blue-500'}`}></span>
                </span>
                <span>{agentStatus}</span>
              </div>
            </div>
            <div className="text-center mt-3">
              <h3 className="font-bold text-slate-200">{botName} Umar</h3>
              <p className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Live Lip-Synced stream</p>
            </div>
          </div>
        </section>

        {/* Right Conversation Column (7 columns) */}
        <section className="lg:col-span-7 flex flex-col bg-slate-900/60 border border-slate-900 rounded-2xl shadow-xl backdrop-blur-md min-h-[500px]">
          <div className="flex items-center justify-between border-b border-slate-800/60 px-6 py-4">
            <div className="flex items-center space-x-3">
              <div className="bg-indigo-600/10 p-2.5 rounded-lg border border-indigo-500/20 text-indigo-400 flex items-center justify-center">
                <Mic className="w-5 h-5" />
              </div>
              <div>
                <h2 className="font-bold text-slate-200">Live Interview Transcript</h2>
                <p className="text-xs text-slate-500">Real-time transcripts, AI formulated replies &amp; tokens</p>
              </div>
            </div>
            
            <button onClick={() => setTranscripts([])} className="text-xs text-slate-400 hover:text-slate-100 font-semibold flex items-center space-x-1 border border-slate-800 bg-slate-950 px-3 py-2 rounded-lg transition-all shadow-md">
              <Trash2 className="w-3.5 h-3.5" />
              <span>Clear Feed</span>
            </button>
          </div>

          {/* Transcript Scrolling Board */}
          <div className="flex-grow p-6 overflow-y-auto max-h-[640px] flex flex-col space-y-4">
            {transcripts.length === 0 && !streamingText && (
              <div className="flex items-start space-x-3">
                <div className="bg-slate-800 p-2 rounded-full text-slate-400 text-xs flex items-center justify-center w-8 h-8">
                  <HelpCircle className="w-4 h-4" />
                </div>
                <div className="bg-slate-900/80 rounded-2xl p-4 text-xs text-slate-400 max-w-[85%] border border-slate-850">
                  Welcome to the AI Candidate Interview control interface. 
                  When you enter a Google Meet link and hit <strong>Join Meeting</strong>, the live audio stream transcripts and formulated replies will be displayed here in real time.
                </div>
              </div>
            )}

            {transcripts.map((t) => {
              const isInterviewer = t.speaker === "Interviewer";
              return (
                <div key={t.id} className={`flex items-start space-x-3 ${isInterviewer ? 'justify-start' : 'justify-end'}`}>
                  {!isInterviewer && (
                    <div className="bg-indigo-600/10 border border-indigo-500/20 p-2 rounded-full w-8 h-8 flex items-center justify-center text-xs text-indigo-400 order-2 ml-3">
                      <IDCard className="w-4 h-4" />
                    </div>
                  )}
                  {isInterviewer && (
                    <div className="bg-slate-800 p-2 rounded-full w-8 h-8 flex items-center justify-center text-xs text-slate-300 shadow-inner mr-3">
                      <Mic className="w-4 h-4" />
                    </div>
                  )}
                  <div className={`p-4 rounded-2xl max-w-[85%] text-sm ${isInterviewer ? 'bg-slate-900 text-slate-100 rounded-tl-none border border-slate-800/80' : 'bg-indigo-600/90 text-white rounded-tr-none border border-indigo-500/20 shadow-md'}`}>
                    <div className="flex items-center space-x-2 mb-1">
                      <span className={`text-[10px] font-bold uppercase tracking-wider ${isInterviewer ? 'text-slate-400' : 'text-indigo-200'}`}>{t.speaker}</span>
                      <span className="text-[9px] text-slate-500">{t.timestamp}</span>
                    </div>
                    <div className="whitespace-pre-wrap leading-relaxed">{t.text}</div>
                  </div>
                </div>
              );
            })}
            
            {/* Real-time typing stream */}
            {streamingText && (
              <div className="flex items-start space-x-3 justify-end">
                <div className="bg-indigo-600/10 border border-indigo-500/20 p-2 rounded-full w-8 h-8 flex items-center justify-center text-xs text-indigo-400 order-2 ml-3">
                  <IDCard className="w-4 h-4" />
                </div>
                <div className="bg-indigo-600/90 text-white border border-indigo-500/20 p-4 rounded-2xl rounded-tr-none text-sm max-w-[85%] shadow-md">
                  <div className="flex items-center space-x-2 mb-1">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-200">Formulating {botName}...</span>
                    <span className="text-[9px] text-slate-400 animate-pulse">Streaming...</span>
                  </div>
                  <div className="whitespace-pre-wrap leading-relaxed animate-pulse">{streamingText}</div>
                </div>
              </div>
            )}
            <div ref={transcriptEndRef} />
          </div>

          {/* Typing Indicator */}
          {streamingText && (
            <div className="border-t border-slate-800/40 px-6 py-3 bg-slate-950/50 flex items-center justify-between">
              <div className="flex items-center space-x-2 text-indigo-400 text-xs font-semibold animate-pulse">
                <Brain className="w-4 h-4" />
                <span>Brain is formulating response...</span>
              </div>
              <div className="flex space-x-1">
                <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
