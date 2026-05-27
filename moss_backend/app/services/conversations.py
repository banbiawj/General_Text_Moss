from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


DEFAULT_USER_ID = "test-user"
CONVERSATION_ID_PATTERN = re.compile(r"^conv-[A-Za-z0-9_-]{8,64}$")
DEFAULT_CONVERSATION_TITLE = "Untitled conversation"


class InvalidConversationId(ValueError):
    """Raised when a conversation id does not match the public API format."""


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    note_id: str | None = None
    is_default: bool = False
    pinned_at: str | None = None
    deleted_at: str | None = None


@dataclass(frozen=True)
class ResolveConversationResult:
    record: ConversationRecord
    created: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_conversation_id() -> str:
    return f"conv-{uuid4().hex}"


def is_valid_conversation_id(conversation_id: str) -> bool:
    return bool(CONVERSATION_ID_PATTERN.fullmatch(conversation_id))


class ConversationStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def resolve(
        self,
        user_id: str,
        conversation_id: str | None,
    ) -> ResolveConversationResult:
        resolved_id = conversation_id or self._new_unique_id()
        self._validate_id(resolved_id)

        existing = self.get(resolved_id)
        if existing:
            if existing.user_id != user_id:
                raise PermissionError("conversation_id is owned by a different user")
            touched = self.touch(resolved_id)
            return ResolveConversationResult(record=touched, created=False)

        created = self._create(user_id=user_id, conversation_id=resolved_id)
        return ResolveConversationResult(record=created, created=True)

    def get(self, conversation_id: str) -> ConversationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return self._record_from_row(row) if row else None

    def touch(self, conversation_id: str) -> ConversationRecord:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
            conn.commit()

        record = self.get(conversation_id)
        if record is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        return record

    def _create(self, user_id: str, conversation_id: str) -> ConversationRecord:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (
                    conversation_id, user_id, title, created_at, updated_at,
                    note_id, is_default
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    user_id,
                    DEFAULT_CONVERSATION_TITLE,
                    now,
                    now,
                    None,
                    0,
                ),
            )
            conn.commit()

        record = self.get(conversation_id)
        if record is None:
            raise RuntimeError(f"failed to create conversation: {conversation_id}")
        return record

    def _new_unique_id(self) -> str:
        for _ in range(10):
            conversation_id = generate_conversation_id()
            if self.get(conversation_id) is None:
                return conversation_id
        raise RuntimeError("failed to generate a unique conversation id")

    def _validate_id(self, conversation_id: str) -> None:
        if not is_valid_conversation_id(conversation_id):
            raise InvalidConversationId(
                "conversation_id must match conv-[A-Za-z0-9_-]{8,64}"
            )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    note_id TEXT,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    pinned_at TEXT,
                    deleted_at TEXT
                )
                """
            )
            columns = self._table_columns(conn, "conversations")
            if "note_id" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN note_id TEXT")
            if "is_default" not in columns:
                conn.execute(
                    """
                    ALTER TABLE conversations
                    ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "pinned_at" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN pinned_at TEXT")
            if "deleted_at" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN deleted_at TEXT")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.row_factory = sqlite3.Row
        return conn

    def _record_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        columns = set(row.keys())
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            note_id=(
                None
                if "note_id" not in columns or row["note_id"] is None
                else str(row["note_id"])
            ),
            is_default=bool(row["is_default"]) if "is_default" in columns else False,
            pinned_at=None
            if "pinned_at" not in columns or row["pinned_at"] is None
            else str(row["pinned_at"]),
            deleted_at=None
            if "deleted_at" not in columns or row["deleted_at"] is None
            else str(row["deleted_at"]),
        )

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}
