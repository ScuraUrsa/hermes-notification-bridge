"""SQLite models for the Notification Bridge."""

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from .config import config


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and row factory."""
    path = db_path or config.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None):
    """Initialize the database schema."""
    close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'notification',
                source TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                title TEXT,
                body TEXT NOT NULL,
                action TEXT,
                voice INTEGER DEFAULT 0,
                tts_text TEXT,
                metadata TEXT,
                created_at INTEGER NOT NULL,
                read_at INTEGER,
                delivered_to TEXT
            );

            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT NOT NULL DEFAULT 'unknown',
                last_seen INTEGER,
                user_agent TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_created
                ON messages(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_unread
                ON messages(read_at) WHERE read_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_messages_voice
                ON messages(voice) WHERE voice = 1;
        """)
        conn.commit()
    finally:
        if close:
            conn.close()


class Message:
    """Represents a push notification message."""

    def __init__(self, id: str, type: str, source: str, priority: str,
                 title: Optional[str], body: str, action: Optional[dict],
                 voice: bool, tts_text: Optional[str],
                 metadata: Optional[dict], created_at: int,
                 read_at: Optional[int] = None,
                 delivered_to: Optional[list] = None):
        self.id = id
        self.type = type
        self.source = source
        self.priority = priority
        self.title = title
        self.body = body
        self.action = action
        self.voice = voice
        self.tts_text = tts_text
        self.metadata = metadata or {}
        self.created_at = created_at
        self.read_at = read_at
        self.delivered_to = delivered_to or []

    @classmethod
    def create(cls, source: str, title: Optional[str], body: str,
               priority: str = "normal", type: str = "notification",
               action: Optional[dict] = None, voice: bool = False,
               tts_text: Optional[str] = None,
               metadata: Optional[dict] = None) -> "Message":
        """Create a new message with auto-generated ID and timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            type=type,
            source=source,
            priority=priority,
            title=title,
            body=body,
            action=action,
            voice=voice,
            tts_text=tts_text,
            metadata=metadata,
            created_at=int(time.time()),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "priority": self.priority,
            "title": self.title,
            "body": self.body,
            "action": self.action,
            "voice": self.voice,
            "tts_text": self.tts_text,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "read_at": self.read_at,
            "delivered_to": self.delivered_to,
        }

    def to_push_event(self) -> dict:
        """Format for WebSocket/SSE delivery to clients."""
        return {
            "event": "message",
            "data": {
                "id": self.id,
                "type": self.type,
                "source": self.source,
                "priority": self.priority,
                "title": self.title,
                "body": self.body,
                "action": self.action,
                "voice": self.voice,
                "tts_text": self.tts_text,
                "metadata": self.metadata,
                "created_at": self.created_at,
            },
        }


class MessageStore:
    """SQLite-backed message storage."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn or get_connection()
        init_db(self.conn)

    def insert(self, msg: Message) -> Message:
        self.conn.execute(
            """INSERT INTO messages
               (id, type, source, priority, title, body, action,
                voice, tts_text, metadata, created_at, delivered_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg.id, msg.type, msg.source, msg.priority, msg.title,
             msg.body, json.dumps(msg.action) if msg.action else None,
             1 if msg.voice else 0, msg.tts_text,
             json.dumps(msg.metadata) if msg.metadata else None,
             msg.created_at,
             json.dumps(msg.delivered_to) if msg.delivered_to else None),
        )
        self.conn.commit()
        return msg

    def get(self, msg_id: str) -> Optional[Message]:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        return self._row_to_message(row) if row else None

    def get_recent(self, limit: int = 50, offset: int = 0,
                   voice_only: bool = False) -> list[Message]:
        if voice_only:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE voice = 1 ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM messages ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get_unread(self) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE read_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def mark_read(self, msg_id: str) -> bool:
        now = int(time.time())
        cur = self.conn.execute(
            "UPDATE messages SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (now, msg_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def record_delivery(self, msg_id: str, client_id: str):
        row = self.conn.execute(
            "SELECT delivered_to FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        if row:
            delivered = json.loads(row["delivered_to"]) if row["delivered_to"] else []
            if client_id not in delivered:
                delivered.append(client_id)
                self.conn.execute(
                    "UPDATE messages SET delivered_to = ? WHERE id = ?",
                    (json.dumps(delivered), msg_id),
                )
                self.conn.commit()

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        unread = self.conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE read_at IS NULL"
        ).fetchone()["c"]
        voice = self.conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE voice = 1"
        ).fetchone()["c"]
        return {"total": total, "unread": unread, "voice": voice}

    def cleanup_old(self, max_messages: int = 1000):
        """Remove oldest messages beyond the limit."""
        count = self.conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        if count > max_messages:
            delete_count = count - max_messages
            self.conn.execute(
                """DELETE FROM messages WHERE id IN
                   (SELECT id FROM messages ORDER BY created_at ASC LIMIT ?)""",
                (delete_count,),
            )
            self.conn.commit()

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            type=row["type"],
            source=row["source"],
            priority=row["priority"],
            title=row["title"],
            body=row["body"],
            action=json.loads(row["action"]) if row["action"] else None,
            voice=bool(row["voice"]),
            tts_text=row["tts_text"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            created_at=row["created_at"],
            read_at=row["read_at"],
            delivered_to=json.loads(row["delivered_to"]) if row["delivered_to"] else [],
        )


class ClientStore:
    """Tracks connected clients."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn or get_connection()
        init_db(self.conn)

    def register(self, client_id: str, name: str = "",
                 client_type: str = "unknown", user_agent: str = ""):
        now = int(time.time())
        self.conn.execute(
            """INSERT INTO clients (id, name, type, last_seen, user_agent)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   last_seen = excluded.last_seen,
                   user_agent = excluded.user_agent""",
            (client_id, name, client_type, now, user_agent),
        )
        self.conn.commit()

    def get_connected_count(self) -> int:
        # Within last 60 seconds
        cutoff = int(time.time()) - 60
        row = self.conn.execute(
            "SELECT COUNT(*) as c FROM clients WHERE last_seen > ?", (cutoff,)
        ).fetchone()
        return row["c"] if row else 0
