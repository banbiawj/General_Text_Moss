# Note Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate note library backed by a `notes` table, while preserving LangGraph conversation memory through `conversation_id`.

**Architecture:** Store editor document bodies in `notes.canvas_snapshot` and store AI thread metadata in `conversations`. The library reads note summaries from the notes API; the editor loads/saves notes by `note_id` and sends both `note_id` and `conversation_id` to chat-stream. Existing LangGraph checkpointing remains keyed by `conversation_id`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, stdlib `sqlite3`, LangGraph, Vue 3 single-file frontend, Tiptap, Tailwind CDN, `unittest`.

---

## File Structure

- Create `moss_backend/app/services/notes.py`
  - Owns note id validation/generation, notes schema, conversation schema migration, default conversation creation, title/preview extraction, note list/load/save, and note/conversation ownership verification.
- Create `moss_backend/tests/test_notes.py`
  - Unit tests for `NoteStore`, migration, extraction, and note/conversation validation.
- Create `moss_backend/tests/test_notes_api.py`
  - Route tests for `/api/v1/notes`, `/api/v1/notes/{note_id}`, and `/api/v1/notes/{note_id}/snapshot`.
- Modify `moss_backend/app/services/conversations.py`
  - Keep compatibility exports for `DEFAULT_USER_ID`, conversation id generation/validation, and existing tests. Add optional `note_id`/`is_default` support to `ConversationRecord`.
- Modify `moss_backend/app/api/schemas.py`
  - Add note request/response schemas and `note_id` to `ChatRequest`.
- Modify `moss_backend/app/api/routes.py`
  - Add note endpoints. Replace chat-stream free conversation creation with note/conversation verification when `note_id` is supplied.
- Modify `moss_backend/app/main.py`
  - Serve `/library` and `/library.html` from root `library.html`.
- Create `moss_backend/tests/test_chat_stream_notes.py`
  - Route tests for chat-stream validation against note/conversation ownership.
- Create root `library.html`
  - Implement the standalone note library UI based on `Blueprint/library.html` style, wired to the notes API.
- Modify root `index.html`
  - Add `currentNoteId`, startup note loading/creation, autosave to notes API, library navigation, and `note_id` in chat requests.
- Modify `moss_backend/README.md`
  - Document the new note endpoints after tests pass.

Existing unrelated changes:

- The worktree currently shows `D library.html` and `?? Blueprint/library.html`; treat these as user changes. Do not revert them. Creating root `library.html` is intentional for this feature.
- `.superpowers/` may exist from visual brainstorming and should not be committed.

---

### Task 1: Note Store Core

**Files:**
- Create: `moss_backend/tests/test_notes.py`
- Create: `moss_backend/app/services/notes.py`
- Modify: `moss_backend/app/services/conversations.py`

- [ ] **Step 1: Write the failing note store tests**

Create `moss_backend/tests/test_notes.py`:

```python
from __future__ import annotations

import shutil
import sqlite3
import time
import unittest
from pathlib import Path
from uuid import uuid4

from app.services.conversations import DEFAULT_USER_ID
from app.services.notes import (
    DEFAULT_NOTE_TITLE,
    DEFAULT_THREAD_TITLE,
    InvalidNoteId,
    NoteStore,
    extract_note_metadata,
    is_valid_note_id,
)


class NoteStoreTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_dir = Path.cwd() / ".tmp" / "tests" / f"notes-{uuid4().hex}"
        temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return temp_dir

    def test_create_note_creates_default_conversation(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")

        created = store.create_note(DEFAULT_USER_ID)

        self.assertTrue(created.note.note_id.startswith("note-"))
        self.assertTrue(created.default_conversation.conversation_id.startswith("conv-"))
        self.assertEqual(created.note.title, DEFAULT_NOTE_TITLE)
        self.assertEqual(created.note.canvas_snapshot, "")
        self.assertEqual(created.note.preview_text, "")
        self.assertEqual(created.default_conversation.note_id, created.note.note_id)
        self.assertEqual(created.default_conversation.title, DEFAULT_THREAD_TITLE)
        self.assertTrue(created.default_conversation.is_default)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.default_conversation_id, created.default_conversation.conversation_id)

    def test_list_notes_returns_summaries_without_snapshot_ordered_by_updated_at(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        first = store.create_note(DEFAULT_USER_ID)
        time.sleep(0.01)
        second = store.create_note(DEFAULT_USER_ID)
        store.save_snapshot(DEFAULT_USER_ID, first.note.note_id, "<h1>First title</h1><p>alpha</p>")
        time.sleep(0.01)
        store.save_snapshot(DEFAULT_USER_ID, second.note.note_id, "<h1>Second title</h1><p>beta</p>")

        notes = store.list_notes(DEFAULT_USER_ID)

        self.assertEqual([note.note_id for note in notes], [second.note.note_id, first.note.note_id])
        self.assertEqual(notes[0].title, "Second title")
        self.assertEqual(notes[0].preview_text, "Second title beta")
        self.assertFalse(hasattr(notes[0], "canvas_snapshot"))

    def test_save_snapshot_updates_title_preview_and_timestamp(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        original_updated_at = created.note.updated_at
        time.sleep(0.01)

        saved = store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            '<h2 id="a">Draft title</h2><p>Hello <strong>world</strong>.</p>',
        )

        self.assertEqual(saved.title, "Draft title")
        self.assertEqual(saved.preview_text, "Draft title Hello world.")
        self.assertGreater(saved.updated_at, original_updated_at)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(
            loaded.canvas_snapshot,
            '<h2 id="a">Draft title</h2><p>Hello <strong>world</strong>.</p>',
        )

    def test_extract_note_metadata_prefers_heading_then_text_then_default(self) -> None:
        self.assertEqual(
            extract_note_metadata("<p>Lead paragraph</p><h1>Later</h1>").title,
            "Later",
        )
        self.assertEqual(
            extract_note_metadata("<p>Only paragraph text here</p>").title,
            "Only paragraph text here",
        )
        self.assertEqual(extract_note_metadata("<p> </p>").title, DEFAULT_NOTE_TITLE)

    def test_invalid_note_id_is_rejected(self) -> None:
        invalid_ids = ["abc", "note-", "note-short", "note-has space", "note-中文"]
        for note_id in invalid_ids:
            with self.subTest(note_id=note_id):
                self.assertFalse(is_valid_note_id(note_id))

        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        with self.assertRaises(InvalidNoteId):
            store.get_note(DEFAULT_USER_ID, "note-has space")

    def test_verify_conversation_for_note_rejects_mismatch(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        first = store.create_note(DEFAULT_USER_ID)
        second = store.create_note(DEFAULT_USER_ID)

        verified = store.verify_conversation_for_note(
            DEFAULT_USER_ID,
            first.note.note_id,
            first.default_conversation.conversation_id,
        )
        self.assertEqual(verified.note_id, first.note.note_id)

        with self.assertRaises(ValueError):
            store.verify_conversation_for_note(
                DEFAULT_USER_ID,
                first.note.note_id,
                second.default_conversation.conversation_id,
            )

    def test_legacy_conversations_are_migrated_to_notes(self) -> None:
        db_path = self.make_temp_dir() / "metadata.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO conversations (
                conversation_id, user_id, title, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "conv-legacy123",
                DEFAULT_USER_ID,
                "Legacy conversation",
                "2026-05-11T00:00:00+00:00",
                "2026-05-11T00:01:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        store = NoteStore(db_path)
        notes = store.list_notes(DEFAULT_USER_ID)

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Legacy conversation")
        loaded = store.get_note(DEFAULT_USER_ID, notes[0].note_id)
        self.assertEqual(loaded.default_conversation_id, "conv-legacy123")
        conversation = store.get_conversation("conv-legacy123")
        self.assertIsNotNone(conversation)
        self.assertEqual(conversation.note_id, notes[0].note_id)
        self.assertTrue(conversation.is_default)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the note store tests to verify RED**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.notes'`.

- [ ] **Step 3: Implement `NoteStore` and compatibility conversation records**

Create `moss_backend/app/services/notes.py`:

```python
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from app.services.conversations import (
    CONVERSATION_ID_PATTERN,
    DEFAULT_USER_ID,
    generate_conversation_id,
    utc_now_iso,
)


NOTE_ID_PATTERN = re.compile(r"^note-[A-Za-z0-9_-]{8,64}$")
DEFAULT_NOTE_TITLE = "Untitled note"
DEFAULT_THREAD_TITLE = "Default conversation"
PREVIEW_LIMIT = 240


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
    canvas_snapshot: str
    preview_text: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class NoteSummary:
    note_id: str
    default_conversation_id: str
    title: str
    preview_text: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LoadedNote:
    note_id: str
    default_conversation_id: str
    title: str
    canvas_snapshot: str
    preview_text: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    note_id: str | None
    user_id: str
    title: str
    is_default: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CreatedNote:
    note: NoteRecord
    default_conversation: ConversationRecord


@dataclass(frozen=True)
class SavedSnapshot:
    note_id: str
    title: str
    preview_text: str
    updated_at: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading_depth:
            self._heading_depth -= 1

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        self._text_parts.append(text)
        if self._heading_depth:
            self._heading_parts.append(text)

    @property
    def heading_text(self) -> str:
        return normalize_text(" ".join(self._heading_parts))

    @property
    def plain_text(self) -> str:
        return normalize_text(" ".join(self._text_parts))


def generate_note_id() -> str:
    return f"note-{uuid4().hex}"


def is_valid_note_id(note_id: str) -> bool:
    return bool(NOTE_ID_PATTERN.fullmatch(note_id))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def truncate_preview(value: str) -> str:
    if len(value) <= PREVIEW_LIMIT:
        return value
    return value[: PREVIEW_LIMIT - 1].rstrip() + "…"


def extract_note_metadata(canvas_snapshot: str) -> NoteMetadata:
    parser = _TextExtractor()
    parser.feed(canvas_snapshot or "")
    plain_text = parser.plain_text
    title = parser.heading_text or plain_text or DEFAULT_NOTE_TITLE
    return NoteMetadata(title=title[:120], preview_text=truncate_preview(plain_text))


class NoteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def create_note(self, user_id: str = DEFAULT_USER_ID) -> CreatedNote:
        note_id = self._new_unique_note_id()
        conversation_id = self._new_unique_conversation_id()
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    note_id, user_id, title, canvas_snapshot,
                    preview_text, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (note_id, user_id, DEFAULT_NOTE_TITLE, "", "", now, now),
            )
            conn.execute(
                """
                INSERT INTO conversations (
                    conversation_id, note_id, user_id, title,
                    is_default, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (conversation_id, note_id, user_id, DEFAULT_THREAD_TITLE, now, now),
            )
            conn.commit()

        loaded = self.get_note(user_id, note_id)
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise RuntimeError(f"failed to create default conversation: {conversation_id}")
        return CreatedNote(
            note=NoteRecord(
                note_id=loaded.note_id,
                user_id=user_id,
                title=loaded.title,
                canvas_snapshot=loaded.canvas_snapshot,
                preview_text=loaded.preview_text,
                created_at=loaded.created_at,
                updated_at=loaded.updated_at,
            ),
            default_conversation=conversation,
        )

    def list_notes(self, user_id: str = DEFAULT_USER_ID) -> list[NoteSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    n.note_id,
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
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    n.note_id,
                    c.conversation_id AS default_conversation_id,
                    n.title,
                    n.canvas_snapshot,
                    n.preview_text,
                    n.created_at,
                    n.updated_at
                FROM notes n
                JOIN conversations c
                    ON c.note_id = n.note_id AND c.is_default = 1
                WHERE n.user_id = ? AND n.note_id = ?
                """,
                (user_id, note_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"note not found: {note_id}")
        return LoadedNote(
            note_id=str(row["note_id"]),
            default_conversation_id=str(row["default_conversation_id"]),
            title=str(row["title"]),
            canvas_snapshot=str(row["canvas_snapshot"]),
            preview_text=str(row["preview_text"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def save_snapshot(
        self,
        user_id: str,
        note_id: str,
        canvas_snapshot: str,
    ) -> SavedSnapshot:
        self._validate_note_id(note_id)
        metadata = extract_note_metadata(canvas_snapshot)
        now = utc_now_iso()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE notes
                SET title = ?, canvas_snapshot = ?, preview_text = ?, updated_at = ?
                WHERE user_id = ? AND note_id = ?
                """,
                (
                    metadata.title,
                    canvas_snapshot,
                    metadata.preview_text,
                    now,
                    user_id,
                    note_id,
                ),
            )
            conn.commit()
        if result.rowcount == 0:
            raise KeyError(f"note not found: {note_id}")
        return SavedSnapshot(
            note_id=note_id,
            title=metadata.title,
            preview_text=metadata.preview_text,
            updated_at=now,
        )

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT conversation_id, note_id, user_id, title,
                       is_default, created_at, updated_at
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return self._conversation_from_row(row)

    def verify_conversation_for_note(
        self,
        user_id: str,
        note_id: str,
        conversation_id: str,
    ) -> ConversationRecord:
        self._validate_note_id(note_id)
        if not CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            raise ValueError("conversation_id must match conv-[A-Za-z0-9_-]{8,64}")
        self.get_note(user_id, note_id)
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        if conversation.user_id != user_id:
            raise PermissionError("conversation_id is owned by a different user")
        if conversation.note_id != note_id:
            raise ValueError("conversation_id does not belong to note_id")
        return self.touch_conversation(conversation_id)

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
                    canvas_snapshot TEXT NOT NULL,
                    preview_text TEXT NOT NULL,
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
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "conversations", "note_id", "TEXT")
            self._ensure_column(
                conn,
                "conversations",
                "is_default",
                "INTEGER NOT NULL DEFAULT 0",
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_default_per_note
                ON conversations(note_id)
                WHERE is_default = 1
                """
            )
            self._migrate_legacy_conversations(conn)
            conn.commit()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def _migrate_legacy_conversations(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT conversation_id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE note_id IS NULL
            ORDER BY created_at ASC
            """
        ).fetchall()
        for row in rows:
            note_id = self._new_unique_note_id(conn)
            title = str(row["title"])
            if title == "Untitled conversation":
                title = DEFAULT_NOTE_TITLE
            conn.execute(
                """
                INSERT INTO notes (
                    note_id, user_id, title, canvas_snapshot,
                    preview_text, created_at, updated_at
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

    def _new_unique_note_id(self, conn: sqlite3.Connection | None = None) -> str:
        owns_connection = conn is None
        active_conn = conn or self._connect()
        try:
            for _ in range(10):
                note_id = generate_note_id()
                row = active_conn.execute(
                    "SELECT 1 FROM notes WHERE note_id = ?",
                    (note_id,),
                ).fetchone()
                if row is None:
                    return note_id
        finally:
            if owns_connection:
                active_conn.close()
        raise RuntimeError("failed to generate a unique note id")

    def _new_unique_conversation_id(self) -> str:
        for _ in range(10):
            conversation_id = generate_conversation_id()
            if self.get_conversation(conversation_id) is None:
                return conversation_id
        raise RuntimeError("failed to generate a unique conversation id")

    def _validate_note_id(self, note_id: str) -> None:
        if not is_valid_note_id(note_id):
            raise InvalidNoteId("note_id must match note-[A-Za-z0-9_-]{8,64}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.row_factory = sqlite3.Row
        return conn

    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        raw_note_id = row["note_id"]
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            note_id=str(raw_note_id) if raw_note_id is not None else None,
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            is_default=bool(row["is_default"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
```

Modify `moss_backend/app/services/conversations.py`:

```python
@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    note_id: str | None = None
    is_default: bool = False
```

Update `_init_schema()` so existing tests still work with new columns:

```python
    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "note_id" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN note_id TEXT")
            if "is_default" not in columns:
                conn.execute(
                    "ALTER TABLE conversations ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0"
                )
            conn.commit()
```

Update `_record_from_row()` to read optional new columns defensively:

```python
    def _record_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        keys = set(row.keys())
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            note_id=str(row["note_id"]) if "note_id" in keys and row["note_id"] is not None else None,
            is_default=bool(row["is_default"]) if "is_default" in keys else False,
        )
```

- [ ] **Step 4: Run the note store tests to verify GREEN**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes -v
```

Expected: PASS.

- [ ] **Step 5: Run existing conversation tests**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_conversations -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```powershell
git add moss_backend/app/services/notes.py moss_backend/app/services/conversations.py moss_backend/tests/test_notes.py
git commit -m "feat: add note metadata store"
```

If Git still reports `.git/index.lock` permission errors, do not force-delete anything. Record the failure and continue with the next task only if the user approves uncommitted execution.

---

### Task 2: Note API Routes

**Files:**
- Create: `moss_backend/tests/test_notes_api.py`
- Modify: `moss_backend/app/api/schemas.py`
- Modify: `moss_backend/app/api/routes.py`

- [ ] **Step 1: Write failing API tests**

Create `moss_backend/tests/test_notes_api.py`:

```python
from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.services.conversations import DEFAULT_USER_ID
from app.services.notes import NoteStore


class NotesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path.cwd() / ".tmp" / "tests" / f"notes-api-{uuid4().hex}"
        self.temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.store = NoteStore(self.temp_dir / "metadata.sqlite3")
        self.original_note_store_getter = getattr(routes, "get_note_store", None)
        routes.get_note_store = lambda: self.store

    def tearDown(self) -> None:
        if self.original_note_store_getter is None:
            delattr(routes, "get_note_store")
        else:
            routes.get_note_store = self.original_note_store_getter

    def request(self, method: str, path: str, **kwargs: Any):
        with TestClient(app) as client:
            return client.request(method, path, **kwargs)

    def test_create_note_returns_note_and_default_conversation_ids(self) -> None:
        response = self.request("POST", "/api/v1/notes")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["note_id"].startswith("note-"))
        self.assertTrue(payload["default_conversation_id"].startswith("conv-"))
        loaded = self.store.get_note(DEFAULT_USER_ID, payload["note_id"])
        self.assertEqual(
            loaded.default_conversation_id,
            payload["default_conversation_id"],
        )

    def test_list_notes_excludes_canvas_snapshot(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        self.store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Library title</h1><p>Library body</p>",
        )

        response = self.request("GET", "/api/v1/notes")

        self.assertEqual(response.status_code, 200, response.text)
        notes = response.json()["notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note_id"], created.note.note_id)
        self.assertEqual(notes[0]["title"], "Library title")
        self.assertNotIn("canvas_snapshot", notes[0])

    def test_get_note_returns_full_snapshot(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        self.store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Loaded title</h1><p>Loaded body</p>",
        )

        response = self.request("GET", f"/api/v1/notes/{created.note.note_id}")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["note_id"], created.note.note_id)
        self.assertEqual(
            payload["default_conversation_id"],
            created.default_conversation.conversation_id,
        )
        self.assertEqual(
            payload["canvas_snapshot"],
            "<h1>Loaded title</h1><p>Loaded body</p>",
        )

    def test_save_snapshot_updates_note(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.request(
            "PUT",
            f"/api/v1/notes/{created.note.note_id}/snapshot",
            json={"canvas_snapshot": "<h1>Saved title</h1><p>Saved body</p>"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["title"], "Saved title")
        loaded = self.store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(
            loaded.canvas_snapshot,
            "<h1>Saved title</h1><p>Saved body</p>",
        )

    def test_get_unknown_note_returns_404(self) -> None:
        response = self.request("GET", "/api/v1/notes/note-missing123")

        self.assertEqual(response.status_code, 404)

    def test_invalid_note_id_returns_422(self) -> None:
        response = self.request("GET", "/api/v1/notes/bad id")

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the API tests to verify RED**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes_api -v
```

Expected: FAIL with 404 responses for `/api/v1/notes` or `AttributeError` for `get_note_store`.

- [ ] **Step 3: Add note API schemas**

Modify `moss_backend/app/api/schemas.py` after `ChatRequest`:

```python
class NoteSummaryResponse(BaseModel):
    note_id: str
    default_conversation_id: str
    title: str
    preview_text: str
    created_at: str
    updated_at: str


class NoteListResponse(BaseModel):
    notes: list[NoteSummaryResponse]


class CreateNoteResponse(BaseModel):
    note_id: str
    default_conversation_id: str


class NoteDetailResponse(BaseModel):
    note_id: str
    default_conversation_id: str
    title: str
    canvas_snapshot: str
    preview_text: str
    created_at: str
    updated_at: str


class SaveNoteSnapshotRequest(BaseModel):
    canvas_snapshot: str = ""


class SaveNoteSnapshotResponse(BaseModel):
    note_id: str
    title: str
    preview_text: str
    updated_at: str
```

- [ ] **Step 4: Add note API routes**

Modify imports in `moss_backend/app/api/routes.py`:

```python
from app.api.schemas import (
    ChatRequest,
    CreateNoteResponse,
    DocumentUploadResponse,
    ExportDocumentRequest,
    HealthResponse,
    NoteDetailResponse,
    NoteListResponse,
    NoteSummaryResponse,
    SaveDocumentRequest,
    SaveDocumentResponse,
    SaveNoteSnapshotRequest,
    SaveNoteSnapshotResponse,
    UploadResponse,
)
from app.services.notes import InvalidNoteId, NoteStore
```

Add a store getter after `get_conversation_store()`:

```python
def get_note_store() -> NoteStore:
    settings = get_settings()
    return NoteStore(settings.conversation_metadata_path)
```

Add helpers near `_sse()`:

```python
def _note_not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))
```

Add routes after `health()`:

```python
@api_router.get("/notes", response_model=NoteListResponse)
async def list_notes() -> NoteListResponse:
    notes = get_note_store().list_notes(DEFAULT_USER_ID)
    return NoteListResponse(
        notes=[
            NoteSummaryResponse(
                note_id=note.note_id,
                default_conversation_id=note.default_conversation_id,
                title=note.title,
                preview_text=note.preview_text,
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
            for note in notes
        ]
    )


@api_router.post("/notes", response_model=CreateNoteResponse)
async def create_note() -> CreateNoteResponse:
    created = get_note_store().create_note(DEFAULT_USER_ID)
    return CreateNoteResponse(
        note_id=created.note.note_id,
        default_conversation_id=created.default_conversation.conversation_id,
    )


@api_router.get("/notes/{note_id}", response_model=NoteDetailResponse)
async def get_note(note_id: str) -> NoteDetailResponse:
    try:
        note = get_note_store().get_note(DEFAULT_USER_ID, note_id)
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise _note_not_found(exc) from exc
    return NoteDetailResponse(
        note_id=note.note_id,
        default_conversation_id=note.default_conversation_id,
        title=note.title,
        canvas_snapshot=note.canvas_snapshot,
        preview_text=note.preview_text,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@api_router.put("/notes/{note_id}/snapshot", response_model=SaveNoteSnapshotResponse)
async def save_note_snapshot(
    note_id: str,
    payload: SaveNoteSnapshotRequest,
) -> SaveNoteSnapshotResponse:
    try:
        saved = get_note_store().save_snapshot(
            DEFAULT_USER_ID,
            note_id,
            payload.canvas_snapshot,
        )
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise _note_not_found(exc) from exc
    return SaveNoteSnapshotResponse(
        note_id=saved.note_id,
        title=saved.title,
        preview_text=saved.preview_text,
        updated_at=saved.updated_at,
    )
```

- [ ] **Step 5: Run the note API tests to verify GREEN**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes_api -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```powershell
git add moss_backend/app/api/schemas.py moss_backend/app/api/routes.py moss_backend/tests/test_notes_api.py
git commit -m "feat: add notes api"
```

---

### Task 3: Chat Stream Note Ownership

**Files:**
- Create: `moss_backend/tests/test_chat_stream_notes.py`
- Modify: `moss_backend/app/api/schemas.py`
- Modify: `moss_backend/app/api/routes.py`

- [ ] **Step 1: Write failing chat ownership tests**

Create `moss_backend/tests/test_chat_stream_notes.py`:

```python
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.services.conversations import DEFAULT_USER_ID
from app.services.notes import NoteStore


async def fake_stream_agent_events(
    *,
    session_id: str,
    conversation_id: str,
    user_input: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
    compiled_graph: Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    yield {
        "event": "chat_chunk",
        "data": {
            "content": f"{conversation_id}:{session_id}:{user_input}:{canvas_snapshot}",
            "done": True,
        },
    }


def event_names(body: str) -> list[str]:
    return [
        line.removeprefix("event: ")
        for line in body.splitlines()
        if line.startswith("event: ")
    ]


def event_payloads(body: str, event_name: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    current_event: str | None = None
    for line in body.splitlines():
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ")
        elif line.startswith("data: ") and current_event == event_name:
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


class ChatStreamNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path.cwd() / ".tmp" / "tests" / f"chat-notes-{uuid4().hex}"
        self.temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.store = NoteStore(self.temp_dir / "metadata.sqlite3")
        self.original_note_store_getter = getattr(routes, "get_note_store", None)
        self.original_stream = routes.stream_agent_events
        routes.get_note_store = lambda: self.store
        routes.stream_agent_events = fake_stream_agent_events

    def tearDown(self) -> None:
        if self.original_note_store_getter is None:
            delattr(routes, "get_note_store")
        else:
            routes.get_note_store = self.original_note_store_getter
        routes.stream_agent_events = self.original_stream

    def post_chat(self, payload: dict[str, Any]):
        with TestClient(app) as client:
            return client.post("/api/v1/chat-stream", json=payload)

    def test_matching_note_and_conversation_streams_without_conversation_event(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.post_chat(
            {
                "session_id": "session-a",
                "note_id": created.note.note_id,
                "conversation_id": created.default_conversation.conversation_id,
                "user_input": "hello",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("conversation", event_names(response.text))
        chunks = event_payloads(response.text, "chat_chunk")
        self.assertIn(created.default_conversation.conversation_id, chunks[0]["content"])

    def test_mismatched_conversation_returns_409(self) -> None:
        first = self.store.create_note(DEFAULT_USER_ID)
        second = self.store.create_note(DEFAULT_USER_ID)

        response = self.post_chat(
            {
                "session_id": "session-b",
                "note_id": first.note.note_id,
                "conversation_id": second.default_conversation.conversation_id,
                "user_input": "hello",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertEqual(response.status_code, 409)

    def test_unknown_note_returns_404(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.post_chat(
            {
                "session_id": "session-c",
                "note_id": "note-missing123",
                "conversation_id": created.default_conversation.conversation_id,
                "user_input": "hello",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertEqual(response.status_code, 404)

    def test_invalid_note_id_returns_422(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.post_chat(
            {
                "session_id": "session-d",
                "note_id": "bad id",
                "conversation_id": created.default_conversation.conversation_id,
                "user_input": "hello",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run chat ownership tests to verify RED**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_chat_stream_notes -v
```

Expected: FAIL because `ChatRequest` does not accept/validate `note_id` ownership and the route still resolves conversations freely.

- [ ] **Step 3: Add `note_id` to `ChatRequest`**

Modify `moss_backend/app/api/schemas.py` imports:

```python
from app.services.notes import is_valid_note_id
```

Add field to `ChatRequest`:

```python
    note_id: str | None = None
```

Add validation in `normalize_legacy_payload()` before the user_input check:

```python
        if self.note_id is not None and not is_valid_note_id(self.note_id):
            raise ValueError("note_id must match note-[A-Za-z0-9_-]{8,64}")
```

- [ ] **Step 4: Update `chat_stream` route note validation**

Modify `moss_backend/app/api/routes.py` `chat_stream()`:

```python
@api_router.post("/chat-stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    try:
        if payload.note_id and payload.conversation_id:
            conversation = get_note_store().verify_conversation_for_note(
                DEFAULT_USER_ID,
                payload.note_id,
                payload.conversation_id,
            )
            resolved_conversation_id = conversation.conversation_id
            emit_conversation_event = False
            resolved_user_id = conversation.user_id
        else:
            resolved = get_conversation_store().resolve(
                user_id=DEFAULT_USER_ID,
                conversation_id=payload.conversation_id,
            )
            resolved_conversation_id = resolved.record.conversation_id
            emit_conversation_event = resolved.created
            resolved_user_id = resolved.record.user_id
    except InvalidConversationId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def generator():
        try:
            if emit_conversation_event:
                yield _sse(
                    "conversation",
                    {
                        "conversation_id": resolved_conversation_id,
                        "user_id": resolved_user_id,
                    },
                )

            async for event in stream_agent_events(
                session_id=payload.session_id,
                conversation_id=resolved_conversation_id,
                user_input=payload.user_input,
                focus_element_id=payload.focus_element_id,
                focus_block_id=payload.focus_block_id,
                canvas_snapshot=payload.canvas_snapshot,
                compiled_graph=getattr(request.app.state, "agent_graph", None),
            ):
                yield _sse(event["event"], event.get("data", {}))
            yield _sse("done", {"status": "ok"})
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
```

Ensure `InvalidNoteId` is imported from `app.services.notes`.

- [ ] **Step 5: Run chat ownership tests to verify GREEN**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_chat_stream_notes -v
```

Expected: PASS.

- [ ] **Step 6: Run existing chat conversation tests**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_chat_stream_conversations -v
```

Expected: PASS. Existing compatibility path without `note_id` must still emit `conversation` when creating a conversation.

- [ ] **Step 7: Commit Task 3**

Run:

```powershell
git add moss_backend/app/api/schemas.py moss_backend/app/api/routes.py moss_backend/tests/test_chat_stream_notes.py
git commit -m "feat: validate chat streams against notes"
```

---

### Task 4: Library Page Route and UI

**Files:**
- Modify: `moss_backend/app/main.py`
- Create/Modify: `library.html`

- [ ] **Step 1: Write the failing route smoke test**

Add this test to `moss_backend/tests/test_notes_api.py`:

```python
    def test_library_routes_serve_html(self) -> None:
        with TestClient(app) as client:
            response = client.get("/library")
            response_html = client.get("/library.html")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response_html.status_code, 200)
        self.assertIn("text/html", response_html.headers["content-type"])
```

- [ ] **Step 2: Run route smoke test to verify RED**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes_api.NotesApiTests.test_library_routes_serve_html -v
```

Expected: FAIL with 404 for `/library`.

- [ ] **Step 3: Serve `library.html`**

Modify `moss_backend/app/main.py` after `frontend_entry()`:

```python
@app.get("/library", include_in_schema=False)
@app.get("/library.html", include_in_schema=False)
async def library_entry():
    library_path = Path(__file__).resolve().parents[2] / "library.html"
    if library_path.exists():
        return FileResponse(library_path)
    return JSONResponse({"status": "not_found", "message": "library.html is missing"}, status_code=404)
```

- [ ] **Step 4: Create root `library.html`**

Create root `library.html` using `Blueprint/library.html` as visual reference, but wire it to the API. Keep first version scoped: all notes, local search, grid/list toggle, new note, direct card navigation.

Use this implementation skeleton:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>moss - 笔记库</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <script type="importmap">
    {
      "imports": {
        "vue": "https://unpkg.com/vue@3/dist/vue.esm-browser.js"
      }
    }
    </script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: { sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'] },
                }
            }
        }
    </script>
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #fafafa; color: #171717; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .masonry-grid { column-count: 1; column-gap: 1.25rem; }
        @media (min-width: 640px) { .masonry-grid { column-count: 2; } }
        @media (min-width: 1024px) { .masonry-grid { column-count: 3; } }
        @media (min-width: 1536px) { .masonry-grid { column-count: 4; } }
        .list-layout { display: flex; flex-direction: column; max-width: 48rem; margin: 0 auto; }
        .note-item { break-inside: avoid; page-break-inside: avoid; margin-bottom: 1.25rem; transition: all 0.25s ease; }
        .note-item:hover { border-color: #d4d4d4; box-shadow: 0 4px 20px rgba(0,0,0,.04); }
        .line-clamp-custom { display: -webkit-box; -webkit-line-clamp: 8; -webkit-box-orient: vertical; overflow: hidden; }
    </style>
</head>
<body class="h-screen w-screen overflow-hidden selection:bg-black selection:text-white flex text-[15px]">
<div id="app" class="h-full w-full flex bg-[#fafafa] relative">
    <aside class="w-64 bg-[#fafafa] border-r border-gray-200 flex-shrink-0 flex flex-col h-full">
        <div class="p-5 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-7 h-7 rounded-md bg-black flex items-center justify-center shadow-sm">
                    <i class="fa-solid fa-asterisk text-white text-[10px]"></i>
                </div>
                <span class="font-semibold text-sm tracking-wide text-gray-800">moss notes</span>
            </div>
        </div>
        <div class="px-3 pb-4">
            <button @click="createNote"
                    :disabled="isCreating"
                    class="w-full bg-white border border-gray-200 hover:bg-gray-50 disabled:opacity-60 text-gray-700 text-sm py-2 rounded-lg transition shadow-sm flex items-center justify-center gap-2 active:scale-95">
                <i class="fa-solid fa-pen-nib text-xs"></i>
                新建笔记
            </button>
        </div>
        <div class="flex-1 overflow-y-auto no-scrollbar px-3 py-2">
            <div class="text-xs font-semibold text-gray-400 mb-2 px-2 uppercase tracking-wider">视图</div>
            <ul class="space-y-0.5 mb-6">
                <li>
                    <a href="#" class="flex items-center gap-3 px-2 py-1.5 rounded-md font-medium text-sm bg-black/5 text-black">
                        <i class="fa-regular fa-file-lines text-gray-500 w-4 text-center"></i> 全部笔记
                    </a>
                </li>
            </ul>
        </div>
    </aside>

    <main class="flex-1 flex flex-col h-full relative overflow-hidden bg-white md:rounded-l-2xl shadow-[-5px_0_30px_rgba(0,0,0,0.02)] border-l border-gray-100 z-10 min-w-0">
        <header class="h-16 px-4 md:px-8 flex items-center justify-between border-b border-gray-50 bg-white/80 backdrop-blur-md sticky top-0 z-20 gap-4">
            <div class="flex items-center gap-4 flex-1 min-w-0">
                <div class="flex items-center gap-2 text-sm shrink-0 whitespace-nowrap">
                    <span class="text-gray-300 hidden sm:inline">/</span>
                    <span class="font-semibold text-gray-800 tracking-wide flex items-center gap-2">
                        <i class="fa-regular fa-file-lines text-gray-400 hidden sm:inline"></i>
                        全部笔记
                    </span>
                </div>
                <div class="flex items-center gap-3 flex-1 max-w-md bg-gray-50/60 px-3 py-1.5 rounded-lg border border-transparent focus-within:bg-white focus-within:border-gray-200 focus-within:shadow-sm transition-all group">
                    <i class="fa-solid fa-magnifying-glass text-gray-400 text-sm shrink-0 group-focus-within:text-blue-500 transition-colors"></i>
                    <input v-model="searchQuery" type="text" placeholder="搜索..."
                           class="w-full bg-transparent border-none outline-none text-[14px] text-gray-800 placeholder-gray-400 font-light min-w-0">
                </div>
            </div>
            <button @click="toggleViewMode"
                    class="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-gray-100 hover:text-black transition-colors text-gray-400"
                    :title="viewMode === 'grid' ? '切换为列表视图' : '切换为网格视图'">
                <i :class="viewMode === 'grid' ? 'fa-solid fa-list-ul' : 'fa-solid fa-border-all'" class="text-sm"></i>
            </button>
        </header>

        <div class="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8 no-scrollbar scroll-smooth bg-[#fcfcfc]">
            <div class="max-w-7xl mx-auto h-full">
                <div v-if="isLoading" class="h-full flex items-center justify-center text-gray-400">
                    <i class="fa-solid fa-circle-notch fa-spin mr-2"></i> 正在加载
                </div>

                <div v-else-if="loadError" class="h-full flex flex-col items-center justify-center text-gray-500">
                    <p class="text-[15px] font-medium text-gray-700">笔记库加载失败</p>
                    <p class="text-sm mt-1">{{ loadError }}</p>
                    <button @click="loadNotes" class="mt-5 px-4 py-2 bg-black text-white rounded-lg text-sm">重试</button>
                </div>

                <div v-else-if="filteredNotes.length > 0" :class="viewMode === 'grid' ? 'masonry-grid' : 'list-layout'">
                    <div v-for="note in filteredNotes" :key="note.note_id"
                         @click="openNote(note)"
                         class="note-item bg-white border border-gray-200/80 rounded-xl p-4 md:p-5 cursor-pointer group relative">
                        <h3 class="font-medium text-gray-900 text-[15px] md:text-base mb-2 md:mb-3 leading-snug">
                            {{ note.title || 'Untitled note' }}
                        </h3>
                        <div class="text-gray-600 text-[13px] md:text-[14px] leading-relaxed line-clamp-custom font-light whitespace-pre-wrap">
                            {{ note.preview_text || '空白笔记' }}
                        </div>
                        <div class="mt-4 pt-3 text-[10px] md:text-[11px] text-gray-400 font-medium tracking-wide flex justify-between items-center border-t border-gray-50/50">
                            <span>{{ formatDate(note.updated_at) }}</span>
                            <span class="bg-gray-50 px-2 py-1 rounded text-gray-500">默认线程</span>
                        </div>
                    </div>
                </div>

                <div v-else class="h-full flex flex-col items-center justify-center text-gray-400">
                    <div class="w-20 h-20 bg-gray-50 rounded-full flex items-center justify-center border border-gray-100 mb-4">
                        <i class="fa-regular fa-folder-open text-2xl text-gray-300"></i>
                    </div>
                    <p class="text-[15px] font-medium text-gray-600">还没有笔记</p>
                    <p class="text-sm mt-1">新建一篇笔记开始写作</p>
                    <button @click="createNote" class="mt-6 px-5 py-2 bg-black text-white rounded-lg text-sm font-medium hover:bg-gray-800 transition shadow-sm flex items-center gap-2">
                        <i class="fa-solid fa-pen-nib text-xs"></i> 新建笔记
                    </button>
                </div>
            </div>
        </div>
    </main>
</div>

<script type="module">
    import { createApp, ref, computed, onMounted } from 'vue';

    const DEFAULT_API_BASE = 'http://127.0.0.1:8000';
    const API_BASE = window.MOSS_API_BASE || (window.location.protocol.startsWith('http') ? window.location.origin : DEFAULT_API_BASE);
    const apiUrl = (path) => `${API_BASE.replace(/\/$/, '')}${path}`;

    createApp({
        setup() {
            const notes = ref([]);
            const isLoading = ref(false);
            const isCreating = ref(false);
            const loadError = ref('');
            const searchQuery = ref('');
            const viewMode = ref(localStorage.getItem('moss-library-view-mode') || 'grid');

            const filteredNotes = computed(() => {
                const query = searchQuery.value.trim().toLowerCase();
                if (!query) return notes.value;
                return notes.value.filter((note) => {
                    return `${note.title || ''} ${note.preview_text || ''}`.toLowerCase().includes(query);
                });
            });

            const loadNotes = async () => {
                isLoading.value = true;
                loadError.value = '';
                try {
                    const response = await fetch(apiUrl('/api/v1/notes'));
                    if (!response.ok) throw new Error(await response.text());
                    const payload = await response.json();
                    notes.value = payload.notes || [];
                } catch (error) {
                    loadError.value = error.message || 'unknown error';
                } finally {
                    isLoading.value = false;
                }
            };

            const createNote = async () => {
                if (isCreating.value) return;
                isCreating.value = true;
                try {
                    const response = await fetch(apiUrl('/api/v1/notes'), { method: 'POST' });
                    if (!response.ok) throw new Error(await response.text());
                    const payload = await response.json();
                    openNote({
                        note_id: payload.note_id,
                        default_conversation_id: payload.default_conversation_id
                    });
                } catch (error) {
                    loadError.value = error.message || 'create failed';
                } finally {
                    isCreating.value = false;
                }
            };

            const openNote = (note) => {
                const params = new URLSearchParams({
                    note_id: note.note_id,
                    conversation_id: note.default_conversation_id
                });
                window.location.href = `/?${params.toString()}`;
            };

            const toggleViewMode = () => {
                viewMode.value = viewMode.value === 'grid' ? 'list' : 'grid';
                localStorage.setItem('moss-library-view-mode', viewMode.value);
            };

            const formatDate = (value) => {
                if (!value) return '';
                try {
                    return new Intl.DateTimeFormat('zh-CN', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                    }).format(new Date(value));
                } catch {
                    return value;
                }
            };

            onMounted(loadNotes);

            return {
                notes,
                filteredNotes,
                isLoading,
                isCreating,
                loadError,
                searchQuery,
                viewMode,
                loadNotes,
                createNote,
                openNote,
                toggleViewMode,
                formatDate
            };
        }
    }).mount('#app');
</script>
</body>
</html>
```

- [ ] **Step 5: Run route smoke test to verify GREEN**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes_api.NotesApiTests.test_library_routes_serve_html -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```powershell
git add moss_backend/app/main.py moss_backend/tests/test_notes_api.py library.html
git commit -m "feat: add note library page"
```

---

### Task 5: Editor Note Loading and Autosave

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add frontend state and helper design notes**

There is no existing frontend unit test harness. Before editing, manually identify these anchors in `index.html`:

- Around line 246: `currentConversationId`.
- Around line 592: `saveContent`.
- Around line 881: `sendMessage`.
- Around line 1040: `onMounted`.
- Around line 1090: returned setup bindings.

Expected: Anchors exist.

- [ ] **Step 2: Add note identity state and URL helpers**

Modify the setup block near `currentConversationId`:

```javascript
            const urlParams = new URLSearchParams(window.location.search);
            const currentNoteId = ref(urlParams.get('note_id') || localStorage.getItem('moss-current-note-id') || '');
            const currentConversationId = ref(urlParams.get('conversation_id') || localStorage.getItem('moss-current-conversation-id') || '');

            const setCurrentNoteId = (noteId) => {
                if (!noteId) return;
                currentNoteId.value = noteId;
                localStorage.setItem('moss-current-note-id', noteId);
            };
```

Keep `setCurrentConversationId`, but make sure URL-provided ids take precedence over localStorage.

Add helper functions after `setCurrentConversationId`:

```javascript
            const syncNoteUrl = () => {
                if (!currentNoteId.value || !currentConversationId.value) return;
                const params = new URLSearchParams(window.location.search);
                params.set('note_id', currentNoteId.value);
                params.set('conversation_id', currentConversationId.value);
                window.history.replaceState({}, '', `/?${params.toString()}`);
            };
```

- [ ] **Step 3: Add note API functions and autosave state**

Add state near `isSaving`:

```javascript
            const saveStatus = ref('idle');
            const noteLoadError = ref('');
            let autosaveTimer = null;
            let lastSavedSnapshot = '';
```

Add functions before `saveContent`:

```javascript
            const createNote = async () => {
                const response = await fetch(apiUrl('/api/v1/notes'), { method: 'POST' });
                if (!response.ok) throw new Error(await response.text());
                const payload = await response.json();
                setCurrentNoteId(payload.note_id);
                setCurrentConversationId(payload.default_conversation_id);
                syncNoteUrl();
                return payload;
            };

            const loadCurrentNote = async () => {
                if (!currentNoteId.value) {
                    await createNote();
                    contentHTML.value = '<p></p>';
                    return;
                }

                const response = await fetch(apiUrl(`/api/v1/notes/${encodeURIComponent(currentNoteId.value)}`));
                if (!response.ok) throw new Error(await response.text());
                const note = await response.json();
                setCurrentNoteId(note.note_id);
                setCurrentConversationId(currentConversationId.value || note.default_conversation_id);
                syncNoteUrl();
                documentTitle.value = note.title || 'Untitled note';
                contentHTML.value = note.canvas_snapshot || '<p></p>';
                lastSavedSnapshot = note.canvas_snapshot || '';
            };

            const persistNoteSnapshot = async ({ immediate = false } = {}) => {
                if (!currentNoteId.value || !tiptapEditor) return;
                contentHTML.value = tiptapEditor.getHTML();
                if (!immediate && contentHTML.value === lastSavedSnapshot) return;
                saveStatus.value = 'saving';
                const response = await fetch(apiUrl(`/api/v1/notes/${encodeURIComponent(currentNoteId.value)}/snapshot`), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ canvas_snapshot: contentHTML.value })
                });
                if (!response.ok) {
                    saveStatus.value = 'error';
                    throw new Error(await response.text());
                }
                const saved = await response.json();
                lastSavedSnapshot = contentHTML.value;
                documentTitle.value = saved.title || documentTitle.value;
                saveStatus.value = 'saved';
            };

            const queueAutosave = () => {
                if (autosaveTimer) window.clearTimeout(autosaveTimer);
                autosaveTimer = window.setTimeout(() => {
                    persistNoteSnapshot().catch(() => {});
                }, 1200);
            };

            const flushAutosave = async () => {
                if (autosaveTimer) {
                    window.clearTimeout(autosaveTimer);
                    autosaveTimer = null;
                }
                await persistNoteSnapshot({ immediate: true });
            };
```

- [ ] **Step 4: Update save button behavior**

Modify `saveContent` to call `flushAutosave()` before the existing `/api/document/save`, or replace document-save with note snapshot save for this button.

Preferred first-version replacement:

```javascript
            const saveContent = async () => {
                if (isSaving.value) return;
                isSaving.value = true;
                try {
                    await flushAutosave();
                } catch (error) {
                    messages.value.push({ role: 'ai', content: `保存失败：${error.message}` });
                    await nextTick();
                    scrollToBottom();
                } finally {
                    setTimeout(() => { isSaving.value = false; }, 900);
                }
            };
```

This keeps `/api/document/save` out of the note autosave path.

- [ ] **Step 5: Include note id in chat requests**

Modify request body inside `sendMessage`:

```javascript
                        body: JSON.stringify({
                            session_id: sessionId,
                            note_id: currentNoteId.value,
                            conversation_id: currentConversationId.value,
                            user_input: currentInput,
                            focus_element_id: requestAnchors.focusElementId,
                            focus_block_id: requestAnchors.focusBlockId,
                            canvas_snapshot: requestAnchors.canvasSnapshot
                        })
```

Before building the request, ensure note exists:

```javascript
                    if (!currentNoteId.value || !currentConversationId.value) {
                        await createNote();
                    }
```

Keep SSE `conversation` handling for compatibility, but normal note flow should not depend on it.

- [ ] **Step 6: Autosave after editor updates and DOM mutations**

Modify Tiptap `onUpdate` around line 1064:

```javascript
                    onUpdate: ({ editor }) => {
                        contentHTML.value = editor.getHTML();
                        if (!isMaintainingDocumentIds) queueAutosave();
                        if (isMaintainingDocumentIds) return;
                        const signature = topLevelStructureSignature();
                        if (signature !== lastTopLevelSignature) {
                            lastTopLevelSignature = signature;
                            queueTopLevelIdMaintenance();
                        }
                    }
```

Modify `applyDomMutation` after `ensureTopLevelBlockIds();`:

```javascript
                await persistNoteSnapshot({ immediate: true }).catch(() => {});
```

- [ ] **Step 7: Load note before editor initialization**

Change `onMounted(() => { ... })` into `onMounted(async () => { ... })`.

At the beginning of `onMounted`, before creating `new Editor`, run:

```javascript
                try {
                    await loadCurrentNote();
                } catch (error) {
                    noteLoadError.value = error.message || '加载笔记失败';
                    messages.value.push({ role: 'ai', content: `加载笔记失败：${noteLoadError.value}` });
                    contentHTML.value = '<p></p>';
                }
```

Then create the editor using `content: contentHTML.value`.

- [ ] **Step 8: Add library navigation and pending-save cleanup**

Add a Library button in the editor header near Upload/Export:

```html
                <button @click="openLibrary"
                        class="text-xs bg-white border border-gray-200 text-gray-700 px-3 md:px-4 py-2.5 rounded-lg hover:bg-gray-50 transition-colors shadow-sm font-medium flex items-center gap-2"
                        title="打开笔记库">
                    <i class="fa-regular fa-folder-open text-[11px]"></i>
                    <span class="hidden sm:inline">Library</span>
                </button>
```

Add function:

```javascript
            const openLibrary = async () => {
                try {
                    await flushAutosave();
                } catch {}
                window.location.href = '/library';
            };
```

Add cleanup in `onUnmounted`:

```javascript
                if (autosaveTimer) window.clearTimeout(autosaveTimer);
```

Do not add a `sendBeacon()` path in this version. The snapshot save endpoint is `PUT`, while `sendBeacon()` sends `POST`; adding a second beacon-only endpoint would expand API scope. The first version relies on debounced autosave during editing and an explicit `flushAutosave()` before the built-in Library navigation.

- [ ] **Step 9: Return new bindings**

Add to returned object:

```javascript
                currentNoteId,
                setCurrentNoteId,
                saveStatus,
                noteLoadError,
                openLibrary
```

- [ ] **Step 10: Manual frontend verification**

Start the server:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Expected:

- URL becomes `/?note_id=note-...&conversation_id=conv-...`.
- Editor loads blank content for a new note.
- Typing and waiting 1-2 seconds saves without visible interruption.
- Clicking Library navigates to `/library`.
- The library shows the note card with updated title/preview.
- Clicking the card returns to the same note URL and loads the saved content.

- [ ] **Step 11: Commit Task 5**

Run:

```powershell
git add index.html
git commit -m "feat: load and autosave editor notes"
```

---

### Task 6: Regression Verification and README

**Files:**
- Modify: `moss_backend/README.md`

- [ ] **Step 1: Run focused backend tests**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest tests.test_notes tests.test_notes_api tests.test_chat_stream_notes tests.test_chat_stream_conversations tests.test_conversations tests.test_graph_threading -v
```

Expected: PASS.

- [ ] **Step 2: Run known broader test discovery**

Run:

```powershell
cd moss_backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: Existing known failures may remain in `test_skill_runtime.py`, `test_agent_refactor.py`, and `test_document_content.py::test_rejects_unsupported_task_type` as documented in `moss_backend/README.md`. New note-related tests must pass.

- [ ] **Step 3: Check formatting and accidental Blueprint edits**

Run:

```powershell
git diff --check
git status --short
git diff -- Blueprint
```

Expected:

- `git diff --check` passes.
- `Blueprint` diff is empty unless it already existed before implementation; do not include Blueprint changes in commits.
- `.superpowers/` is not staged.

- [ ] **Step 4: Update README endpoint list**

Add these bullets to `moss_backend/README.md` API Surface:

```markdown
### Notes

- `GET /api/v1/notes`: list note summaries for the library.
- `POST /api/v1/notes`: create an empty note and default AI conversation.
- `GET /api/v1/notes/{note_id}`: load a full note snapshot for the editor.
- `PUT /api/v1/notes/{note_id}/snapshot`: save the editor HTML snapshot and update title/preview metadata.
```

- [ ] **Step 5: Final manual verification**

Run the server and verify:

```text
http://127.0.0.1:8000/library
```

Expected:

- Library loads with cards.
- New note opens editor.
- Editor saves pure typing.
- Library reflects changed title/preview.
- Chat request still streams a response in mock mode.

- [ ] **Step 6: Commit Task 6**

Run:

```powershell
git add moss_backend/README.md
git commit -m "docs: document note library api"
```

---

## Self-Review

Spec coverage:

- Notes table: Task 1.
- Conversation-to-note relationship and migration: Task 1.
- Notes API: Task 2.
- Chat-stream `note_id` validation: Task 3.
- `/library` and `/library.html`: Task 4.
- Editor loading, autosave, and chat payload: Task 5.
- Verification and docs: Task 6.

Placeholder scan:

- No `TBD`, `TODO`, or open-ended implementation placeholders are intentionally left in the plan.

Type consistency:

- Backend uses snake_case response fields matching the design: `note_id`, `default_conversation_id`, `canvas_snapshot`, `preview_text`.
- Frontend uses the same JSON field names directly.
- `NoteStore.verify_conversation_for_note()` is the single ownership check used by chat-stream.
