import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import config

DB_PATH = Path(__file__).resolve().parent.parent / "sessions.db"


class SQLiteStateStore:
    """
    SaaS Production-Grade State Store backed by SQLite.
    Allows multiple parallel FastAPI nodes to share active session states safely.
    """
    def __init__(self):
        self._init_db()

    def _get_conn(self):
        # check_same_thread=False: the store is used across the FastAPI event loop.
        # WAL mode + a short busy timeout reduces "database is locked" contention.
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    meeting_url TEXT,
                    bot_name TEXT,
                    status TEXT,
                    avatar_path TEXT,
                    voice_path TEXT,
                    call_id TEXT,
                    pika_session_id TEXT,
                    avatar_provider TEXT,
                    provider_session_id TEXT,
                    stream_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

        with self._get_conn() as conn:
            for column_ddl in (
                "ALTER TABLE sessions ADD COLUMN avatar_provider TEXT",
                "ALTER TABLE sessions ADD COLUMN provider_session_id TEXT",
                "ALTER TABLE sessions ADD COLUMN stream_url TEXT",
            ):
                try:
                    conn.execute(column_ddl)
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def save_session(
        self, 
        session_id: str, 
        meeting_url: str, 
        bot_name: str, 
        status: str, 
        avatar_path: str, 
        voice_path: str,
        call_id: Optional[str] = None,
        pika_session_id: Optional[str] = None,
        avatar_provider: Optional[str] = None,
        provider_session_id: Optional[str] = None,
        stream_url: Optional[str] = None
    ):
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO sessions (session_id, meeting_url, bot_name, status, avatar_path, voice_path, call_id, pika_session_id, avatar_provider, provider_session_id, stream_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    meeting_url=excluded.meeting_url,
                    bot_name=excluded.bot_name,
                    avatar_path=excluded.avatar_path,
                    voice_path=excluded.voice_path,
                    status=excluded.status,
                    call_id=COALESCE(excluded.call_id, sessions.call_id),
                    pika_session_id=COALESCE(excluded.pika_session_id, sessions.pika_session_id),
                    avatar_provider=COALESCE(excluded.avatar_provider, sessions.avatar_provider),
                    provider_session_id=COALESCE(excluded.provider_session_id, sessions.provider_session_id),
                    stream_url=COALESCE(excluded.stream_url, sessions.stream_url)
            """, (session_id, meeting_url, bot_name, status, avatar_path, voice_path, call_id, pika_session_id, avatar_provider, provider_session_id, stream_url))
            conn.commit()

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row:
                return dict(row)
        return None

    def delete_session(self, session_id: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            conn.commit()

    def list_active_sessions(self) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

# Abstract Memory Fallback for testing environments
class MemoryStateStore:
    def __init__(self):
        self.sessions: Dict[str, dict] = {}

    def save_session(self, session_id, **kwargs):
        if session_id not in self.sessions:
            self.sessions[session_id] = {}
        self.sessions[session_id].update(kwargs)
        self.sessions[session_id]["session_id"] = session_id

    def get_session(self, session_id) -> Optional[dict]:
        return self.sessions.get(session_id)

    def delete_session(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def list_active_sessions(self) -> List[dict]:
        return list(self.sessions.values())

def get_state_store():
    """Factory to load persistent database store or testing memory store."""
    if config.STATE_STORE_TYPE == "sqlite":
        return SQLiteStateStore()
    return MemoryStateStore()
