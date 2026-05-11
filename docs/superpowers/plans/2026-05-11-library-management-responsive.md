# Library Management Responsive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the note library into a responsive management surface with mobile drawer navigation, display-title rename, pin/unpin, and soft delete.

**Architecture:** Extend the existing `notes` metadata model in `NoteStore`, expose metadata update/delete APIs, then wire `library.html` and `index.html` to the new fields. `notes.title` remains auto-derived from `canvas_snapshot`; `notes.display_title` is library/editor display metadata only.

**Tech Stack:** FastAPI, Pydantic, SQLite, unittest, single-file Vue 3 frontends using CDN imports and Tailwind CDN.

---

## File Structure

- `moss_backend/app/services/notes.py`
  - Owns SQLite schema migration for `display_title`, `deleted_at`, `pinned_at`.
  - Owns effective title calculation.
  - Owns note metadata mutation and soft delete.
  - Ensures deleted notes are hidden from normal get/list/update flows.

- `moss_backend/app/api/schemas.py`
  - Adds response fields for display metadata.
  - Adds `UpdateNoteRequest`, `UpdateNoteResponse`, and `DeleteNoteResponse`.

- `moss_backend/app/api/routes.py`
  - Adds `PATCH /api/v1/notes/{note_id}`.
  - Adds `DELETE /api/v1/notes/{note_id}`.
  - Returns display metadata from existing note endpoints.

- `moss_backend/tests/test_notes.py`
  - Adds service-level tests for schema migration, ordering, metadata mutation, idempotent soft delete, and deleted-note visibility.

- `moss_backend/tests/test_notes_api.py`
  - Adds route-level tests for response shape, metadata patch, delete, and deleted-note status.

- `index.html`
  - Uses `effective_title` for editor title display.
  - Keeps content save path from overwriting manual display titles.

- `library.html`
  - Implements responsive drawer behavior.
  - Implements cards with more menu, rename dialog, delete confirmation, pin/unpin.
  - Uses `effective_title` and new API fields.

- `moss_backend/README.md`
  - Documents the new note metadata API.

---

### Task 1: Extend NoteStore Metadata Model

**Files:**
- Modify: `moss_backend/app/services/notes.py`
- Test: `moss_backend/tests/test_notes.py`

- [ ] **Step 1: Add failing service tests for display title, pin ordering, soft delete, and deleted get behavior**

Append these tests inside `NoteStoreTests` in `moss_backend/tests/test_notes.py`, before the `if __name__ == "__main__":` block:

```python
    def test_note_management_fields_are_added_to_existing_notes_table(self) -> None:
        db_path = self.make_temp_dir() / "metadata.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.execute(
            """
            CREATE TABLE notes (
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
            CREATE TABLE conversations (
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
        conn.commit()
        conn.close()

        NoteStore(db_path)

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
        conn.close()
        self.assertIn("display_title", columns)
        self.assertIn("deleted_at", columns)
        self.assertIn("pinned_at", columns)

    def test_update_note_display_title_does_not_modify_snapshot_or_updated_at(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        saved = store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Body title</h1><p>Body text</p>",
        )

        updated = store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Library name",
        )

        self.assertEqual(updated.display_title, "Library name")
        self.assertEqual(updated.effective_title, "Library name")
        self.assertEqual(updated.title, "Body title")
        self.assertEqual(updated.updated_at, saved.updated_at)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.canvas_snapshot, "<h1>Body title</h1><p>Body text</p>")

    def test_clearing_display_title_restores_effective_auto_title(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Auto title</h1><p>Body text</p>",
        )
        store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Manual title",
        )

        updated = store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="   ",
        )

        self.assertIsNone(updated.display_title)
        self.assertEqual(updated.effective_title, "Auto title")

    def test_pinned_notes_sort_before_unpinned_without_touching_updated_at(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        first = store.create_note(DEFAULT_USER_ID)
        time.sleep(0.01)
        second = store.create_note(DEFAULT_USER_ID)
        first_saved = store.save_snapshot(
            DEFAULT_USER_ID,
            first.note.note_id,
            "<h1>First</h1>",
        )
        time.sleep(0.01)
        second_saved = store.save_snapshot(
            DEFAULT_USER_ID,
            second.note.note_id,
            "<h1>Second</h1>",
        )

        pinned = store.update_note(DEFAULT_USER_ID, first.note.note_id, pinned=True)
        notes = store.list_notes(DEFAULT_USER_ID)

        self.assertEqual([note.note_id for note in notes], [first.note.note_id, second.note.note_id])
        self.assertIsNotNone(pinned.pinned_at)
        self.assertEqual(pinned.updated_at, first_saved.updated_at)
        self.assertGreater(second_saved.updated_at, first_saved.updated_at)

    def test_unpin_clears_pinned_at(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        store.update_note(DEFAULT_USER_ID, created.note.note_id, pinned=True)

        updated = store.update_note(DEFAULT_USER_ID, created.note.note_id, pinned=False)

        self.assertIsNone(updated.pinned_at)

    def test_soft_delete_hides_note_from_list_and_get(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)

        deleted = store.delete_note(DEFAULT_USER_ID, created.note.note_id)

        self.assertEqual(deleted.note_id, created.note.note_id)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(store.list_notes(DEFAULT_USER_ID), [])
        with self.assertRaises(KeyError):
            store.get_note(DEFAULT_USER_ID, created.note.note_id)

    def test_soft_delete_is_idempotent(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        deleted = store.delete_note(DEFAULT_USER_ID, created.note.note_id)

        repeated = store.delete_note(DEFAULT_USER_ID, created.note.note_id)

        self.assertEqual(repeated.deleted_at, deleted.deleted_at)
```

- [ ] **Step 2: Run service tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_notes -v
```

Expected: FAIL with missing methods/attributes such as `update_note`, `delete_note`, `display_title`, `effective_title`, or missing columns.

- [ ] **Step 3: Add note management dataclasses and helper**

Modify `moss_backend/app/services/notes.py` dataclasses near the existing note dataclasses.

Replace `NoteRecord` with:

```python
@dataclass(frozen=True)
class NoteRecord:
    note_id: str
    user_id: str
    title: str
    display_title: str | None
    effective_title: str
    preview_text: str
    canvas_snapshot: str
    created_at: str
    updated_at: str
    pinned_at: str | None
    deleted_at: str | None
```

Replace `NoteSummary` with:

```python
@dataclass(frozen=True)
class NoteSummary:
    note_id: str
    user_id: str
    default_conversation_id: str
    title: str
    display_title: str | None
    effective_title: str
    preview_text: str
    pinned_at: str | None
    created_at: str
    updated_at: str
```

Replace `LoadedNote` with:

```python
@dataclass(frozen=True)
class LoadedNote:
    note_id: str
    user_id: str
    title: str
    display_title: str | None
    effective_title: str
    preview_text: str
    canvas_snapshot: str
    created_at: str
    updated_at: str
    pinned_at: str | None
    default_conversation_id: str
```

Add after `SavedSnapshot`:

```python
@dataclass(frozen=True)
class UpdatedNote:
    note_id: str
    title: str
    display_title: str | None
    effective_title: str
    preview_text: str
    pinned_at: str | None
    updated_at: str


@dataclass(frozen=True)
class DeletedNote:
    note_id: str
    deleted_at: str
```

Add near `truncate_preview()`:

```python
def normalize_display_title(display_title: str | None) -> str | None:
    if display_title is None:
        return None
    normalized = normalize_text(display_title)
    return normalized or None


def effective_note_title(display_title: str | None, title: str) -> str:
    return normalize_display_title(display_title) or title or DEFAULT_NOTE_TITLE
```

- [ ] **Step 4: Extend schema migration**

In `_init_schema()`, after `_ensure_conversation_columns(conn)`, call a new `_ensure_note_columns(conn)`:

```python
self._ensure_note_columns(conn)
```

Add method:

```python
    def _ensure_note_columns(self, conn: sqlite3.Connection) -> None:
        columns = self._table_columns(conn, "notes")
        if "display_title" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN display_title TEXT")
        if "deleted_at" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN deleted_at TEXT")
        if "pinned_at" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN pinned_at TEXT")
```

Update the `CREATE TABLE IF NOT EXISTS notes` SQL to include:

```sql
display_title TEXT,
pinned_at TEXT,
deleted_at TEXT,
```

Place these after `title TEXT NOT NULL`.

- [ ] **Step 5: Update create/list/get row handling**

In `create_note()`, change the insert column list and values:

```sql
INSERT INTO notes (
    note_id, user_id, title, display_title, preview_text, canvas_snapshot,
    created_at, updated_at, pinned_at, deleted_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Values:

```python
(
    note_id,
    user_id,
    DEFAULT_NOTE_TITLE,
    None,
    "",
    "",
    now,
    now,
    None,
    None,
)
```

In `list_notes()`, select and order:

```sql
SELECT
    n.note_id,
    n.user_id,
    c.conversation_id AS default_conversation_id,
    n.title,
    n.display_title,
    n.preview_text,
    n.pinned_at,
    n.created_at,
    n.updated_at
FROM notes n
JOIN conversations c
    ON c.note_id = n.note_id AND c.is_default = 1
WHERE n.user_id = ? AND n.deleted_at IS NULL
ORDER BY
    n.pinned_at IS NULL ASC,
    n.pinned_at DESC,
    n.updated_at DESC
```

Map rows with:

```python
display_title = None if row["display_title"] is None else str(row["display_title"])
title = str(row["title"])
```

and set:

```python
display_title=display_title,
effective_title=effective_note_title(display_title, title),
pinned_at=None if row["pinned_at"] is None else str(row["pinned_at"]),
```

In `get_note()`, `save_snapshot()`, `verify_conversation_for_note()`, and `_get_note_record()`, deleted notes must be treated as missing by selecting only `deleted_at IS NULL` in `_get_note_record()`.

Update `_get_note_record()` query:

```sql
SELECT *
FROM notes
WHERE user_id = ? AND note_id = ? AND deleted_at IS NULL
```

Update `_note_from_row()`:

```python
    def _note_from_row(self, row: sqlite3.Row) -> NoteRecord:
        display_title = None if row["display_title"] is None else str(row["display_title"])
        title = str(row["title"])
        return NoteRecord(
            note_id=str(row["note_id"]),
            user_id=str(row["user_id"]),
            title=title,
            display_title=display_title,
            effective_title=effective_note_title(display_title, title),
            preview_text=str(row["preview_text"]),
            canvas_snapshot=str(row["canvas_snapshot"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            pinned_at=None if row["pinned_at"] is None else str(row["pinned_at"]),
            deleted_at=None if row["deleted_at"] is None else str(row["deleted_at"]),
        )
```

Update `LoadedNote(...)` construction in `get_note()` so it passes:

```python
display_title=note.display_title,
effective_title=note.effective_title,
pinned_at=note.pinned_at,
```

Keep `SavedSnapshot` unchanged; snapshot save responses do not include display metadata.

- [ ] **Step 6: Update legacy migration inserts**

In `_migrate_legacy_conversations()` and any tests' pre-existing legacy schemas handled by code, update note insert SQL:

```sql
INSERT INTO notes (
    note_id, user_id, title, display_title, preview_text, canvas_snapshot,
    created_at, updated_at, pinned_at, deleted_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Values:

```python
(
    note_id,
    str(row["user_id"]),
    title,
    None,
    "",
    "",
    str(row["created_at"]),
    str(row["updated_at"]),
    None,
    None,
)
```

- [ ] **Step 7: Add update_note and delete_note**

Add to `NoteStore`:

```python
    def update_note(
        self,
        user_id: str,
        note_id: str,
        *,
        display_title: object = _UNSET,
        pinned: object = _UNSET,
    ) -> UpdatedNote:
        self._validate_note_id(note_id)
        current_note = self._get_note_record(user_id, note_id)
        if current_note is None:
            raise KeyError(f"note not found: {note_id}")

        next_display_title = current_note.display_title
        if display_title is not _UNSET:
            next_display_title = normalize_display_title(
                None if display_title is None else str(display_title)
            )

        next_pinned_at = current_note.pinned_at
        if pinned is not _UNSET:
            next_pinned_at = utc_now_iso() if bool(pinned) else None

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notes
                SET display_title = ?, pinned_at = ?
                WHERE user_id = ? AND note_id = ? AND deleted_at IS NULL
                """,
                (next_display_title, next_pinned_at, user_id, note_id),
            )
            conn.commit()

        updated = self._get_note_record(user_id, note_id)
        if updated is None:
            raise KeyError(f"note not found: {note_id}")
        return UpdatedNote(
            note_id=updated.note_id,
            title=updated.title,
            display_title=updated.display_title,
            effective_title=updated.effective_title,
            preview_text=updated.preview_text,
            pinned_at=updated.pinned_at,
            updated_at=updated.updated_at,
        )

    def delete_note(self, user_id: str, note_id: str) -> DeletedNote:
        self._validate_note_id(note_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT deleted_at
                FROM notes
                WHERE user_id = ? AND note_id = ?
                """,
                (user_id, note_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"note not found: {note_id}")
            deleted_at = row["deleted_at"]
            if deleted_at is None:
                deleted_at = utc_now_iso()
                conn.execute(
                    """
                    UPDATE notes
                    SET deleted_at = ?
                    WHERE user_id = ? AND note_id = ?
                    """,
                    (deleted_at, user_id, note_id),
                )
                conn.commit()
        return DeletedNote(note_id=note_id, deleted_at=str(deleted_at))
```

Add module sentinel near constants:

```python
_UNSET = object()
```

- [ ] **Step 8: Run service tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_notes -v
```

Expected: all `tests.test_notes` pass.

---

### Task 2: Add Note Metadata API

**Files:**
- Modify: `moss_backend/app/api/schemas.py`
- Modify: `moss_backend/app/api/routes.py`
- Test: `moss_backend/tests/test_notes_api.py`

- [ ] **Step 1: Add failing API tests**

Append these tests inside `NotesApiTests` before `test_get_unknown_note_returns_404`:

```python
    def test_list_notes_returns_display_and_pin_fields(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        self.store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Body title</h1><p>Body</p>",
        )
        self.store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Library title",
            pinned=True,
        )

        response = self.request("GET", "/api/v1/notes")

        self.assertEqual(response.status_code, 200, response.text)
        note = response.json()["notes"][0]
        self.assertEqual(note["title"], "Body title")
        self.assertEqual(note["display_title"], "Library title")
        self.assertEqual(note["effective_title"], "Library title")
        self.assertIsNotNone(note["pinned_at"])

    def test_get_note_returns_effective_title_fields(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        self.store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Body title</h1><p>Body</p>",
        )
        self.store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Library title",
        )

        response = self.request("GET", f"/api/v1/notes/{created.note.note_id}")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["display_title"], "Library title")
        self.assertEqual(payload["effective_title"], "Library title")
        self.assertEqual(payload["title"], "Body title")

    def test_patch_note_updates_display_title_and_pin(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.request(
            "PATCH",
            f"/api/v1/notes/{created.note.note_id}",
            json={"display_title": "Renamed note", "pinned": True},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["display_title"], "Renamed note")
        self.assertEqual(payload["effective_title"], "Renamed note")
        self.assertIsNotNone(payload["pinned_at"])

    def test_patch_note_can_clear_display_title_and_unpin(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        self.store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Auto title</h1>",
        )
        self.store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Manual",
            pinned=True,
        )

        response = self.request(
            "PATCH",
            f"/api/v1/notes/{created.note.note_id}",
            json={"display_title": "", "pinned": False},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsNone(payload["display_title"])
        self.assertEqual(payload["effective_title"], "Auto title")
        self.assertIsNone(payload["pinned_at"])

    def test_delete_note_soft_deletes_and_hides_from_list(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.request("DELETE", f"/api/v1/notes/{created.note.note_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()["deleted_at"])
        list_response = self.request("GET", "/api/v1/notes")
        self.assertEqual(list_response.json()["notes"], [])
        get_response = self.request("GET", f"/api/v1/notes/{created.note.note_id}")
        self.assertEqual(get_response.status_code, 404)

    def test_repeated_delete_note_is_ok(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        first = self.request("DELETE", f"/api/v1/notes/{created.note.note_id}")

        second = self.request("DELETE", f"/api/v1/notes/{created.note.note_id}")

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["deleted_at"], first.json()["deleted_at"])
```

- [ ] **Step 2: Run API tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_notes_api -v
```

Expected: FAIL because response schemas and routes do not include metadata fields, PATCH, or DELETE yet.

- [ ] **Step 3: Add schemas**

In `moss_backend/app/api/schemas.py`, update `NoteSummaryResponse`:

```python
class NoteSummaryResponse(BaseModel):
    note_id: str
    default_conversation_id: str
    title: str
    display_title: str | None = None
    effective_title: str
    preview_text: str
    pinned_at: str | None = None
    created_at: str
    updated_at: str
```

Update `NoteDetailResponse`:

```python
class NoteDetailResponse(BaseModel):
    note_id: str
    default_conversation_id: str
    title: str
    display_title: str | None = None
    effective_title: str
    canvas_snapshot: str
    preview_text: str
    pinned_at: str | None = None
    created_at: str
    updated_at: str
```

Add after `SaveNoteSnapshotResponse`:

```python
class UpdateNoteRequest(BaseModel):
    display_title: str | None = None
    pinned: bool | None = None


class UpdateNoteResponse(BaseModel):
    note_id: str
    title: str
    display_title: str | None = None
    effective_title: str
    preview_text: str
    pinned_at: str | None = None
    updated_at: str


class DeleteNoteResponse(BaseModel):
    note_id: str
    deleted_at: str
```

- [ ] **Step 4: Update list/get response mapping**

In `moss_backend/app/api/routes.py`, import:

```python
DeleteNoteResponse,
UpdateNoteRequest,
UpdateNoteResponse,
```

Also update the notes service import:

```python
from app.services.notes import InvalidNoteId, NoteStore, _UNSET
```

In `list_notes()`, map:

```python
display_title=note.display_title,
effective_title=note.effective_title,
pinned_at=note.pinned_at,
```

In `get_note()`, map:

```python
display_title=note.display_title,
effective_title=note.effective_title,
pinned_at=note.pinned_at,
```

- [ ] **Step 5: Add PATCH and DELETE routes**

Add after `save_note_snapshot()` and before conversation messages route:

```python
@api_router.patch("/notes/{note_id}", response_model=UpdateNoteResponse)
async def update_note(
    note_id: str,
    payload: UpdateNoteRequest,
) -> UpdateNoteResponse:
    try:
        display_title = (
            payload.display_title if "display_title" in payload.model_fields_set else _UNSET
        )
        pinned = payload.pinned if "pinned" in payload.model_fields_set else _UNSET
        updated = get_note_store().update_note(
            DEFAULT_USER_ID,
            note_id,
            display_title=display_title,
            pinned=pinned,
        )
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return UpdateNoteResponse(
        note_id=updated.note_id,
        title=updated.title,
        display_title=updated.display_title,
        effective_title=updated.effective_title,
        preview_text=updated.preview_text,
        pinned_at=updated.pinned_at,
        updated_at=updated.updated_at,
    )


@api_router.delete("/notes/{note_id}", response_model=DeleteNoteResponse)
async def delete_note(note_id: str) -> DeleteNoteResponse:
    try:
        deleted = get_note_store().delete_note(DEFAULT_USER_ID, note_id)
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeleteNoteResponse(
        note_id=deleted.note_id,
        deleted_at=deleted.deleted_at,
    )
```

- [ ] **Step 6: Run API tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_notes_api -v
```

Expected: all `tests.test_notes_api` pass.

---

### Task 3: Integrate Effective Title In Editor

**Files:**
- Modify: `index.html`
- Test: existing API tests plus manual browser smoke

- [ ] **Step 1: Update note title assignment in editor**

In `index.html` inside `loadCurrentNote()`, replace:

```javascript
documentTitle.value = note.title || 'Untitled note';
```

with:

```javascript
documentTitle.value = note.effective_title || note.display_title || note.title || 'Untitled note';
```

- [ ] **Step 2: Ensure content save does not clear display title**

No frontend code should send `display_title` in the snapshot save request. Confirm `persistNoteSnapshot()` still sends only:

```javascript
body: JSON.stringify({ canvas_snapshot: contentHTML.value })
```

- [ ] **Step 3: Run backend tests affected by note response schema**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_notes tests.test_notes_api tests.test_chat_stream_notes -v
```

Expected: all listed tests pass.

---

### Task 4: Rebuild Library Responsive Shell

**Files:**
- Modify: `library.html`
- Test: manual browser smoke

- [ ] **Step 1: Replace Vue imports and state setup**

Change Vue import:

```javascript
import { createApp, ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
```

Add state inside `setup()` after `viewMode`:

```javascript
const isSidebarOpen = ref(true);
const isMobile = ref(false);
const actionError = ref('');

const checkScreenSize = () => {
    const mobile = window.innerWidth < 768;
    isMobile.value = mobile;
    isSidebarOpen.value = !mobile;
};

const toggleSidebar = () => {
    isSidebarOpen.value = !isSidebarOpen.value;
};

const closeSidebarOnMobile = () => {
    if (isMobile.value) isSidebarOpen.value = false;
};
```

Update `onMounted(loadNotes);` to:

```javascript
onMounted(() => {
    checkScreenSize();
    window.addEventListener('resize', checkScreenSize);
    loadNotes();
});

onUnmounted(() => {
    window.removeEventListener('resize', checkScreenSize);
});
```

Return the new fields:

```javascript
isSidebarOpen,
isMobile,
actionError,
toggleSidebar,
closeSidebarOnMobile,
```

- [ ] **Step 2: Add mobile overlay and drawer classes**

Immediately inside `<div id="app"...>`, before `<aside>`, add:

```html
<div v-if="isSidebarOpen && isMobile"
     class="fixed inset-0 bg-black/20 backdrop-blur-[2px] z-40 md:hidden"
     @click="toggleSidebar"></div>
```

Replace `<aside class="w-64 ...">` with:

```html
<aside :class="[
    'fixed md:relative inset-y-0 left-0 z-50 w-64 bg-[#fafafa] border-r border-gray-200 flex-shrink-0 flex flex-col h-full transition-transform duration-300 ease-out',
    isSidebarOpen ? 'translate-x-0 shadow-2xl md:shadow-none' : '-translate-x-full md:translate-x-0'
]">
```

Inside the brand row, add close button after the brand span:

```html
<button v-if="isMobile" @click="toggleSidebar" class="w-8 h-8 flex items-center justify-center rounded-md hover:bg-gray-200 text-gray-500 transition">
    <i class="fa-solid fa-xmark text-sm"></i>
</button>
```

- [ ] **Step 3: Add hamburger to header**

In the header left controls, before current view label, add:

```html
<button @click="toggleSidebar"
        class="md:hidden w-9 h-9 flex items-center justify-center rounded-lg hover:bg-gray-100 text-gray-500 hover:text-black transition-colors shrink-0"
        title="打开侧边栏">
    <i class="fa-solid fa-bars-staggered text-sm"></i>
</button>
```

This button is mobile-only. Desktop keeps the sidebar visible and does not provide desktop sidebar collapse in this phase.

- [ ] **Step 4: Verify Library loads manually**

Run or reuse the local server:

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:8000/library' -UseBasicParsing
```

Expected: status `200`.

Manual browser checks:

- desktop width shows sidebar and notes
- narrow mobile width hides sidebar
- hamburger opens drawer
- overlay closes drawer
- close button closes drawer

---

### Task 5: Add Library Management UI State And API Helpers

**Files:**
- Modify: `library.html`
- Test: manual browser smoke

- [ ] **Step 1: Use effective title in search and display**

Add helper in `setup()`:

```javascript
const noteTitle = (note) => note.effective_title || note.display_title || note.title || 'Untitled note';
```

Change `filteredNotes` search string to:

```javascript
return `${noteTitle(note)} ${note.preview_text || ''}`.toLowerCase().includes(query);
```

Change card title interpolation:

```html
{{ noteTitle(note) }}
```

Return `noteTitle`.

- [ ] **Step 2: Add metadata API helper and local update helpers**

Add inside `setup()`:

```javascript
const mergeNote = (updated) => {
    notes.value = notes.value.map((note) => (
        note.note_id === updated.note_id ? { ...note, ...updated } : note
    ));
};

const patchNote = async (note, payload) => {
    actionError.value = '';
    const response = await fetch(apiUrl(`/api/v1/notes/${encodeURIComponent(note.note_id)}`), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(await response.text());
    const updated = await response.json();
    mergeNote(updated);
    return updated;
};

const removeNoteFromList = (noteId) => {
    notes.value = notes.value.filter((note) => note.note_id !== noteId);
};
```

Return `patchNote` only if used from template directly. Otherwise keep internal.

- [ ] **Step 3: Add menu state**

Add:

```javascript
const openMenuNoteId = ref('');

const toggleNoteMenu = (note, event) => {
    event?.stopPropagation?.();
    openMenuNoteId.value = openMenuNoteId.value === note.note_id ? '' : note.note_id;
};

const closeNoteMenu = () => {
    openMenuNoteId.value = '';
};
```

Return:

```javascript
openMenuNoteId,
toggleNoteMenu,
closeNoteMenu,
```

- [ ] **Step 4: Add action error banner**

In the main content area, above loading state, add:

```html
<div v-if="actionError" class="mb-4 rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700 flex items-center justify-between gap-3">
    <span>{{ actionError }}</span>
    <button @click="actionError = ''" class="text-red-500 hover:text-red-700">
        <i class="fa-solid fa-xmark"></i>
    </button>
</div>
```

---

### Task 6: Add Pin, Rename, And Delete Interactions

**Files:**
- Modify: `library.html`
- Test: manual browser smoke

- [ ] **Step 1: Add card management controls**

Inside each note card, before `<h3>`, add:

```html
<div class="absolute top-3 right-3 md:top-4 md:right-4 flex gap-1.5 bg-white/90 backdrop-blur-sm px-1.5 py-1 rounded-md shadow-sm border border-gray-100 z-10 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity"
     @click.stop>
    <button @click.stop="togglePinned(note)"
            class="text-gray-400 hover:text-black transition w-7 h-7 flex items-center justify-center rounded hover:bg-gray-50"
            :title="note.pinned_at ? '取消置顶' : '置顶'">
        <i :class="note.pinned_at ? 'fa-solid fa-thumbtack text-black' : 'fa-solid fa-thumbtack'" class="text-xs"></i>
    </button>
    <button @click.stop="toggleNoteMenu(note, $event)"
            class="text-gray-400 hover:text-black transition w-7 h-7 flex items-center justify-center rounded hover:bg-gray-50"
            title="更多">
        <i class="fa-solid fa-ellipsis text-xs"></i>
    </button>
    <div v-if="openMenuNoteId === note.note_id"
         class="absolute right-0 top-9 w-36 rounded-lg border border-gray-100 bg-white shadow-lg py-1 text-sm text-gray-700">
        <button @click.stop="startRename(note)" class="w-full px-3 py-2 text-left hover:bg-gray-50 flex items-center gap-2">
            <i class="fa-regular fa-pen-to-square text-xs text-gray-400"></i> 重命名
        </button>
        <button @click.stop="togglePinned(note)" class="w-full px-3 py-2 text-left hover:bg-gray-50 flex items-center gap-2">
            <i class="fa-solid fa-thumbtack text-xs text-gray-400"></i> {{ note.pinned_at ? '取消置顶' : '置顶' }}
        </button>
        <button @click.stop="startDelete(note)" class="w-full px-3 py-2 text-left hover:bg-red-50 text-red-600 flex items-center gap-2">
            <i class="fa-regular fa-trash-can text-xs"></i> 删除
        </button>
    </div>
</div>
```

Add `pr-14` to the title class so title text does not overlap controls:

```html
class="font-medium text-gray-900 text-[15px] md:text-base mb-2 md:mb-3 leading-snug pr-14"
```

- [ ] **Step 2: Add pin function**

Add in `setup()`:

```javascript
const togglePinned = async (note) => {
    closeNoteMenu();
    try {
        await patchNote(note, { pinned: !note.pinned_at });
        notes.value = [...notes.value].sort(compareNotes);
    } catch (error) {
        actionError.value = error.message || '置顶操作失败';
    }
};

const compareNotes = (a, b) => {
    if (a.pinned_at && !b.pinned_at) return -1;
    if (!a.pinned_at && b.pinned_at) return 1;
    if (a.pinned_at && b.pinned_at) return String(b.pinned_at).localeCompare(String(a.pinned_at));
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
};
```

When setting `notes.value = payload.notes || []` in `loadNotes()`, sort:

```javascript
notes.value = (payload.notes || []).sort(compareNotes);
```

Return `togglePinned`.

- [ ] **Step 3: Add rename state and dialog**

Add state:

```javascript
const renameNote = ref(null);
const renameValue = ref('');

const startRename = (note) => {
    closeNoteMenu();
    renameNote.value = note;
    renameValue.value = note.display_title || note.title || '';
};

const cancelRename = () => {
    renameNote.value = null;
    renameValue.value = '';
};

const submitRename = async () => {
    if (!renameNote.value) return;
    try {
        await patchNote(renameNote.value, { display_title: renameValue.value });
        cancelRename();
    } catch (error) {
        actionError.value = error.message || '重命名失败';
    }
};
```

Return:

```javascript
renameNote,
renameValue,
startRename,
cancelRename,
submitRename,
```

Add before closing `</div>` of `#app`, after `</main>`:

```html
<div v-if="renameNote" class="fixed inset-0 z-[70] flex items-center justify-center bg-black/20 px-4" @click.self="cancelRename">
    <div class="w-full max-w-sm rounded-lg bg-white border border-gray-100 shadow-xl p-4">
        <div class="text-sm font-semibold text-gray-900 mb-3">重命名笔记</div>
        <input v-model="renameValue"
               @keyup.enter="submitRename"
               @keyup.esc="cancelRename"
               class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-gray-400"
               placeholder="留空则使用正文标题">
        <div class="mt-4 flex justify-end gap-2">
            <button @click="cancelRename" class="px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100">取消</button>
            <button @click="submitRename" class="px-3 py-2 rounded-lg text-sm bg-black text-white hover:bg-gray-800">保存</button>
        </div>
    </div>
</div>
```

- [ ] **Step 4: Add delete state and confirmation**

Add state:

```javascript
const deleteNoteTarget = ref(null);

const startDelete = (note) => {
    closeNoteMenu();
    deleteNoteTarget.value = note;
};

const cancelDelete = () => {
    deleteNoteTarget.value = null;
};

const confirmDelete = async () => {
    if (!deleteNoteTarget.value) return;
    const note = deleteNoteTarget.value;
    try {
        const response = await fetch(apiUrl(`/api/v1/notes/${encodeURIComponent(note.note_id)}`), {
            method: 'DELETE'
        });
        if (!response.ok) throw new Error(await response.text());
        removeNoteFromList(note.note_id);
        cancelDelete();
    } catch (error) {
        actionError.value = error.message || '删除失败';
    }
};
```

Return:

```javascript
deleteNoteTarget,
startDelete,
cancelDelete,
confirmDelete,
```

Add after rename dialog:

```html
<div v-if="deleteNoteTarget" class="fixed inset-0 z-[70] flex items-center justify-center bg-black/20 px-4" @click.self="cancelDelete">
    <div class="w-full max-w-sm rounded-lg bg-white border border-gray-100 shadow-xl p-4">
        <div class="text-sm font-semibold text-gray-900">删除笔记</div>
        <p class="mt-2 text-sm text-gray-600 leading-relaxed">
            “{{ noteTitle(deleteNoteTarget) }}” 将从笔记库中移除。当前版本会保留数据，不会立即永久删除。
        </p>
        <div class="mt-4 flex justify-end gap-2">
            <button @click="cancelDelete" class="px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100">取消</button>
            <button @click="confirmDelete" class="px-3 py-2 rounded-lg text-sm bg-red-600 text-white hover:bg-red-700">删除</button>
        </div>
    </div>
</div>
```

- [ ] **Step 5: Ensure card click still opens editor**

Keep card `@click="openNote(note)"`.

All menu buttons must include `.stop` or call `event.stopPropagation()` as shown above.

Manual check:

- clicking card body opens editor
- clicking pin does not open editor
- clicking more menu does not open editor
- clicking rename/delete menu item does not open editor

---

### Task 7: Documentation And Verification

**Files:**
- Modify: `moss_backend/README.md`
- Verify: backend tests and smoke checks

- [ ] **Step 1: Document metadata APIs**

In `moss_backend/README.md`, under `### Notes`, add:

```markdown
- `PATCH /api/v1/notes/{note_id}`: update note display metadata. Supports `display_title` and `pinned`; it does not alter `canvas_snapshot` or content `updated_at`.
- `DELETE /api/v1/notes/{note_id}`: soft delete a note by setting `deleted_at`. The note is hidden from normal library list/get responses; conversations and checkpoints are preserved.
```

- [ ] **Step 2: Run focused backend suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_notes tests.test_notes_api tests.test_chat_stream_notes tests.test_chat_stream_conversations tests.test_conversations tests.test_graph_threading -v
```

Expected: all listed tests pass.

- [ ] **Step 3: Run whitespace check**

Run from repo root:

```powershell
git diff --check
```

Expected: exit code `0`. CRLF warnings are acceptable if no whitespace errors are reported.

- [ ] **Step 4: Restart local backend if needed**

If `http://127.0.0.1:8000` is already running old code, stop the listener and restart:

```powershell
$listeners = netstat -ano | Select-String ':8000'
$listeners
$pidLine = $listeners | Select-String 'LISTENING' | Select-Object -First 1
if ($pidLine) {
    $serverPid = [int](($pidLine.ToString().Trim() -split '\s+')[-1])
    Stop-Process -Id $serverPid
}
Start-Process -FilePath 'E:\project-VScode-Moss\General_Text_Moss\moss_backend\.venv\Scripts\python.exe' -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory 'E:\project-VScode-Moss\General_Text_Moss\moss_backend' -WindowStyle Hidden
```

- [ ] **Step 5: Smoke API and pages**

Run:

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:8000/library' -UseBasicParsing
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/notes' -Method Get
```

Expected:

- `/library` returns `200`
- `/api/v1/notes` returns notes with `effective_title`, `display_title`, and `pinned_at`

- [ ] **Step 6: Manual browser verification**

Verify in browser:

- desktop `http://127.0.0.1:8000/library` shows sidebar and cards
- mobile viewport hides sidebar
- hamburger opens sidebar drawer
- overlay and close button close drawer
- card body opens editor
- rename updates card title after refresh
- clearing rename restores automatic title
- pin moves card to top after refresh
- unpin returns card to normal ordering
- delete removes card and it stays hidden after refresh
- deleted note URL returns editor load error instead of opening content

---

## Known Non-Goals For This Plan

- Do not edit `Blueprint/library.html`.
- Do not implement starred notes.
- Do not implement notebooks/folders.
- Do not implement recycle bin or restore UI.
- Do not hard-delete checkpoints or conversations.
- Do not add batch selection.
