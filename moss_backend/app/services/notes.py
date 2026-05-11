from __future__ import annotations

import html
import re
import sqlite3
import warnings
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from app.services.conversations import (
    DEFAULT_CONVERSATION_TITLE,
    DEFAULT_USER_ID,
    ConversationRecord,
    generate_conversation_id,
    utc_now_iso,
)


NOTE_ID_PATTERN = re.compile(r"^note-[A-Za-z0-9_-]{8,64}$")
DEFAULT_NOTE_TITLE = "Untitled note"
DEFAULT_THREAD_TITLE = "Default conversation"


class InvalidNoteId(ValueError):
    """Raised when a note id does not match the public API format."""


@dataclass(frozen=True)
class NoteMetadata:
    title: str
    preview_text: str


@dataclass(frozen=True)
class NoteRecord:
    note_id: str
    user_id: str
    title: str
    preview_text: str
    canvas_snapshot: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class NoteSummary:
    note_id: str
    user_id: str
    default_conversation_id: str
    title: str
    preview_text: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LoadedNote:
    note_id: str
    user_id: str
    title: str
    preview_text: str
    canvas_snapshot: str
    created_at: str
    updated_at: str
    default_conversation_id: str


@dataclass(frozen=True)
class CreatedNote:
    note: NoteRecord
    default_conversation: ConversationRecord


@dataclass(frozen=True)
class SavedSnapshot:
    note_id: str
    title: str
    preview_text: str
    canvas_snapshot: str
    updated_at: str


def generate_note_id() -> str:
    return f"note-{uuid4().hex}"


def is_valid_note_id(note_id: str) -> bool:
    return bool(NOTE_ID_PATTERN.fullmatch(note_id))


def normalize_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", normalized)


def truncate_preview(text: str) -> str:
    normalized = normalize_text(text)
    return normalized[:240]


def extract_note_metadata(canvas_snapshot: str) -> NoteMetadata:
    parser = _NoteHtmlParser()
    parser.feed(canvas_snapshot)
    parser.close()

    plain_text = normalize_text(" ".join(parser.text_parts))
    heading_text = normalize_text(" ".join(parser.heading_parts))
    title = heading_text or plain_text or DEFAULT_NOTE_TITLE
    return NoteMetadata(title=title, preview_text=truncate_preview(plain_text))


class NoteStore:
    def __init__(
        self,
        db_path: Path,
        checkpoint_db_path: Path | None = None,
    ) -> None:
        self.db_path = db_path
        self.checkpoint_db_path = checkpoint_db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def create_note(self, user_id: str = DEFAULT_USER_ID) -> CreatedNote:
        now = utc_now_iso()
        note_id = self._new_unique_note_id()
        conversation_id = self._new_unique_conversation_id()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    note_id, user_id, title, preview_text, canvas_snapshot,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    user_id,
                    DEFAULT_NOTE_TITLE,
                    "",
                    "",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO conversations (
                    conversation_id, user_id, title, created_at, updated_at,
                    note_id, is_default
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    conversation_id,
                    user_id,
                    DEFAULT_THREAD_TITLE,
                    now,
                    now,
                    note_id,
                ),
            )
            conn.commit()

        note = self._get_note_record(user_id, note_id)
        conversation = self.get_conversation(conversation_id)
        if note is None or conversation is None:
            raise RuntimeError("failed to create note")
        return CreatedNote(note=note, default_conversation=conversation)

    def list_notes(self, user_id: str = DEFAULT_USER_ID) -> list[NoteSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    n.note_id,
                    n.user_id,
                    c.conversation_id AS default_conversation_id,
                    n.title,
                    n.preview_text,
                    n.created_at,
                    n.updated_at
                FROM notes n
                JOIN conversations c
                    ON c.note_id = n.note_id AND c.is_default = 1
                WHERE n.user_id = ?
                ORDER BY n.updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [
            NoteSummary(
                note_id=str(row["note_id"]),
                user_id=str(row["user_id"]),
                default_conversation_id=str(row["default_conversation_id"]),
                title=str(row["title"]),
                preview_text=str(row["preview_text"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def get_note(self, user_id: str, note_id: str) -> LoadedNote:
        self._validate_note_id(note_id)
        note = self._get_note_record(user_id, note_id)
        if note is None:
            raise KeyError(f"note not found: {note_id}")
        default_conversation = self._get_default_conversation(note_id)
        if default_conversation is None:
            raise KeyError(f"default conversation not found for note: {note_id}")
        return LoadedNote(
            note_id=note.note_id,
            user_id=note.user_id,
            title=note.title,
            preview_text=note.preview_text,
            canvas_snapshot=note.canvas_snapshot,
            created_at=note.created_at,
            updated_at=note.updated_at,
            default_conversation_id=default_conversation.conversation_id,
        )

    def save_snapshot(
        self,
        user_id: str,
        note_id: str,
        canvas_snapshot: str,
    ) -> SavedSnapshot:
        self._validate_note_id(note_id)
        current_note = self._get_note_record(user_id, note_id)
        if current_note is None:
            raise KeyError(f"note not found: {note_id}")
        if current_note.canvas_snapshot == canvas_snapshot:
            return SavedSnapshot(
                note_id=note_id,
                title=current_note.title,
                preview_text=current_note.preview_text,
                canvas_snapshot=current_note.canvas_snapshot,
                updated_at=current_note.updated_at,
            )
        metadata = extract_note_metadata(canvas_snapshot)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notes
                SET title = ?, preview_text = ?, canvas_snapshot = ?, updated_at = ?
                WHERE user_id = ? AND note_id = ?
                """,
                (
                    metadata.title,
                    metadata.preview_text,
                    canvas_snapshot,
                    now,
                    user_id,
                    note_id,
                ),
            )
            conn.commit()
        return SavedSnapshot(
            note_id=note_id,
            title=metadata.title,
            preview_text=metadata.preview_text,
            canvas_snapshot=canvas_snapshot,
            updated_at=now,
        )

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return self._conversation_from_row(row) if row else None

    def verify_conversation_for_note(
        self,
        user_id: str,
        note_id: str,
        conversation_id: str,
    ) -> ConversationRecord:
        self._validate_note_id(note_id)
        if self._get_note_record(user_id, note_id) is None:
            raise KeyError(f"note not found: {note_id}")
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        if conversation.user_id != user_id:
            raise PermissionError("conversation_id is owned by a different user")
        if conversation.note_id != note_id:
            raise ValueError("conversation does not belong to note")
        return conversation

    def touch_conversation(self, conversation_id: str) -> ConversationRecord:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
            conn.commit()
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        return conversation

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    note_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    preview_text TEXT NOT NULL,
                    canvas_snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    note_id TEXT,
                    is_default INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._ensure_conversation_columns(conn)
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_default_per_note
                ON conversations(note_id)
                WHERE is_default = 1
                """
            )
            self._migrate_legacy_conversations(conn)
            self._hydrate_empty_notes_from_checkpoints(conn)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_conversation_columns(self, conn: sqlite3.Connection) -> None:
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

    def _migrate_legacy_conversations(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT *
            FROM conversations
            WHERE note_id IS NULL
            """
        ).fetchall()
        for row in rows:
            note_id = self._new_unique_note_id(conn)
            title = str(row["title"])
            if title == DEFAULT_CONVERSATION_TITLE:
                title = DEFAULT_NOTE_TITLE
            conn.execute(
                """
                INSERT INTO notes (
                    note_id, user_id, title, preview_text, canvas_snapshot,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    str(row["user_id"]),
                    title,
                    "",
                    "",
                    str(row["created_at"]),
                    str(row["updated_at"]),
                ),
            )
            conn.execute(
                """
                UPDATE conversations
                SET note_id = ?, is_default = 1
                WHERE conversation_id = ?
                """,
                (note_id, str(row["conversation_id"])),
            )

    def _hydrate_empty_notes_from_checkpoints(self, conn: sqlite3.Connection) -> None:
        snapshots_by_thread = self._latest_checkpoint_snapshots_by_thread()
        if not snapshots_by_thread:
            return

        rows = conn.execute(
            """
            SELECT
                n.note_id,
                n.user_id,
                n.updated_at,
                c.conversation_id
            FROM notes n
            JOIN conversations c
                ON c.note_id = n.note_id AND c.is_default = 1
            WHERE n.canvas_snapshot = ''
            """
        ).fetchall()

        for row in rows:
            snapshot = snapshots_by_thread.get(str(row["conversation_id"]))
            if not snapshot:
                continue
            metadata = extract_note_metadata(snapshot)
            if metadata.title == DEFAULT_NOTE_TITLE and not metadata.preview_text:
                continue
            conn.execute(
                """
                UPDATE notes
                SET title = ?, preview_text = ?, canvas_snapshot = ?
                WHERE user_id = ? AND note_id = ? AND canvas_snapshot = ''
                """,
                (
                    metadata.title,
                    metadata.preview_text,
                    snapshot,
                    str(row["user_id"]),
                    str(row["note_id"]),
                ),
            )

    def _latest_checkpoint_snapshots_by_thread(self) -> dict[str, str]:
        if self.checkpoint_db_path is None or not self.checkpoint_db_path.exists():
            return {}

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

                serializer = JsonPlusSerializer()
            with sqlite3.connect(self.checkpoint_db_path) as conn:
                conn.row_factory = sqlite3.Row
                if "writes" not in self._table_names(conn):
                    return {}
                rows = conn.execute(
                    """
                    SELECT thread_id, checkpoint_id, task_id, idx, type, value
                    FROM writes
                    WHERE channel = 'canvas_snapshot' AND value IS NOT NULL
                    ORDER BY thread_id ASC, checkpoint_id ASC, task_id ASC, idx ASC
                    """
                ).fetchall()
        except sqlite3.Error:
            return {}

        snapshots: dict[str, str] = {}
        for row in rows:
            try:
                snapshot = serializer.loads_typed((str(row["type"]), row["value"]))
            except Exception:
                continue
            if isinstance(snapshot, str) and snapshot.strip():
                snapshots[str(row["thread_id"])] = snapshot
        return snapshots

    def _get_note_record(self, user_id: str, note_id: str) -> NoteRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM notes
                WHERE user_id = ? AND note_id = ?
                """,
                (user_id, note_id),
            ).fetchone()
        return self._note_from_row(row) if row else None

    def _get_default_conversation(self, note_id: str) -> ConversationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE note_id = ? AND is_default = 1
                """,
                (note_id,),
            ).fetchone()
        return self._conversation_from_row(row) if row else None

    def _new_unique_note_id(self, conn: sqlite3.Connection | None = None) -> str:
        for _ in range(10):
            note_id = generate_note_id()
            if self._note_id_exists(note_id, conn) is False:
                return note_id
        raise RuntimeError("failed to generate a unique note id")

    def _new_unique_conversation_id(self) -> str:
        for _ in range(10):
            conversation_id = generate_conversation_id()
            if self.get_conversation(conversation_id) is None:
                return conversation_id
        raise RuntimeError("failed to generate a unique conversation id")

    def _note_id_exists(
        self,
        note_id: str,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        if conn is not None:
            row = conn.execute(
                "SELECT 1 FROM notes WHERE note_id = ?",
                (note_id,),
            ).fetchone()
            return row is not None
        with self._connect() as owned_conn:
            row = owned_conn.execute(
                "SELECT 1 FROM notes WHERE note_id = ?",
                (note_id,),
            ).fetchone()
            return row is not None

    def _validate_note_id(self, note_id: str) -> None:
        if not is_valid_note_id(note_id):
            raise InvalidNoteId("note_id must match note-[A-Za-z0-9_-]{8,64}")

    def _note_from_row(self, row: sqlite3.Row) -> NoteRecord:
        return NoteRecord(
            note_id=str(row["note_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            preview_text=str(row["preview_text"]),
            canvas_snapshot=str(row["canvas_snapshot"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            note_id=None if row["note_id"] is None else str(row["note_id"]),
            is_default=bool(row["is_default"]),
        )

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _table_names(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}


class _NoteHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.heading_parts: list[str] = []
        self._heading_depth = 0
        self._capturing_first_heading = False
        self._has_completed_first_heading = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if re.fullmatch(r"h[1-6]", tag.lower()):
            self._heading_depth += 1
            if not self._has_completed_first_heading:
                self._capturing_first_heading = True

    def handle_endtag(self, tag: str) -> None:
        if re.fullmatch(r"h[1-6]", tag.lower()) and self._heading_depth > 0:
            self._heading_depth -= 1
            if self._capturing_first_heading and self._heading_depth == 0:
                self._capturing_first_heading = False
                self._has_completed_first_heading = True

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._capturing_first_heading:
            self.heading_parts.append(data)
