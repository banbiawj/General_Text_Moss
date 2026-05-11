# Note Library Design

## Goal

Build a separate note library page that lists and opens notes, while keeping the editor page focused on writing and AI-assisted editing.

The design separates note content from AI conversation memory:

- `note` is the document body shown in the editor and library.
- `conversation` is an AI dialogue thread attached to a note.
- LangGraph checkpoints continue to use `conversation_id` as `thread_id`.

The first implementation uses one default conversation per note, but the data model supports multiple conversations per note later.

## Current Context

The project already has:

- `index.html` as the editor page.
- `Blueprint/library.html` as a visual reference for the future library page. It must not be edited.
- `moss_backend/app/services/conversations.py` with a `conversations` metadata table.
- LangGraph SQLite checkpointing keyed by `conversation_id`.
- `canvas_snapshot` in `moss_backend/app/agent/state.py`, passed from the editor to the agent.

The missing piece is persistent note content independent of LangGraph checkpoints. A user can edit text without sending an AI message, so note content cannot be recovered only from checkpoint state.

## Product Model

There are two independent pages:

- `/` serves `index.html`, the note editor and single-note reading page.
- `/library` and `/library.html` serve `library.html`, the note library page.

Library flow:

```text
/library
  -> GET /api/v1/notes
  -> render note cards
  -> click a card
  -> navigate to /?note_id=<note_id>&conversation_id=<default_conversation_id>
```

Editor flow:

```text
/
  -> read note_id and conversation_id from URL
  -> GET /api/v1/notes/{note_id}
  -> load notes.canvas_snapshot into Tiptap
  -> use conversation_id for AI chat-stream calls
```

If the editor opens without `note_id`, it creates a new note and default conversation, then uses `history.replaceState()` to put both ids in the URL.

There is no preview/confirmation modal in the library first version. The editor owns autosave and leave-before-save behavior.

## Data Model

Use the existing metadata SQLite database resolved by `CONVERSATION_METADATA_DB`, currently `storage/conversations.sqlite3`, and add a `notes` table.

### `notes`

`notes` stores the document body and list metadata. It is the source of truth for the library and editor content.

```sql
CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    canvas_snapshot TEXT NOT NULL,
    preview_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Field responsibilities:

- `note_id`: unique document id, formatted as `note-` plus a UUID hex string.
- `user_id`: owner id. First version uses fixed `test-user`.
- `title`: card/editor title extracted from the current HTML. Use the first heading text, then the first non-empty text block, then `Untitled note`.
- `canvas_snapshot`: full editor HTML. This is the note body source of truth.
- `preview_text`: plain text extracted from `canvas_snapshot`, normalized and truncated for library display/search.
- `created_at`: UTC ISO creation time.
- `updated_at`: UTC ISO time when note content metadata last changed.

Future fields, not in the first implementation:

- `folder_id`
- `is_starred`
- `deleted_at`

### `conversations`

`conversations` stores AI thread metadata. It does not store note body HTML.

```sql
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    note_id TEXT,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(note_id)
);
```

The existing `conversations` table currently lacks `note_id` and `is_default`. Migration adds both columns.

Field responsibilities:

- `conversation_id`: AI thread id, already validated as `conv-[A-Za-z0-9_-]{8,64}`.
- `note_id`: owning note. New records must set it. Legacy rows may be migrated into generated notes.
- `user_id`: owner id. It must match the owning note.
- `title`: AI thread title. First version uses `Default conversation`.
- `is_default`: marks the note's default AI thread. First version creates one default conversation per note.
- `created_at`: UTC ISO creation time.
- `updated_at`: UTC ISO time of last AI activity or conversation metadata update.

Add a partial unique index to prevent multiple default threads per note:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_default_per_note
ON conversations(note_id)
WHERE is_default = 1;
```

Because SQLite allows multiple `NULL` values in a unique index, legacy `note_id IS NULL` rows do not violate this index during migration.

## Migration Strategy

The current database already has `conversations` rows without `note_id`. Migration should preserve them.

On `ConversationStore` initialization, or in a renamed note/conversation metadata store:

1. Create `notes` if it does not exist.
2. Add `note_id` to `conversations` if missing.
3. Add `is_default` to `conversations` if missing.
4. Create `idx_conversations_default_per_note`.
5. For each conversation row with `note_id IS NULL`, create a legacy note:

```text
note_id = note-<uuid>
user_id = conversation.user_id
title = conversation.title if not "Untitled conversation" else "Untitled note"
canvas_snapshot = ""
preview_text = ""
created_at = conversation.created_at
updated_at = conversation.updated_at
```

6. Update the legacy conversation to point to that note and set `is_default = 1`.
7. If `LANGGRAPH_CHECKPOINT_DB` has a latest non-empty `canvas_snapshot` write for that conversation id, hydrate only empty notes from that snapshot and recalculate title/preview. This is a best-effort compatibility backfill; `notes.canvas_snapshot` remains the source of truth after migration.

This keeps existing conversations accessible and recovers note bodies that were already present in LangGraph checkpoint writes. Conversations with no checkpoint snapshot remain empty `Untitled note` records until edited.

## Backend Services

Introduce a note-oriented service, for example `moss_backend/app/services/notes.py`, or expand `conversations.py` into a clearer metadata service. The preferred boundary is:

- `NoteStore`: owns note CRUD, snapshot saving, title/preview extraction, and default conversation creation.
- `ConversationStore`: owns conversation id validation, lookup, ownership checks, and touch behavior.

If keeping one file is simpler for the current codebase, keep the public methods separated clearly.

Required behavior:

- Creating a note creates one default conversation in the same transaction.
- Loading a note returns its default conversation id.
- Saving a note snapshot recalculates title and preview.
- Chat requests verify that `conversation_id` belongs to `note_id`.
- Unknown valid `conversation_id` should no longer silently create a standalone conversation when `note_id` is present. A chat request with mismatched or missing note ownership returns an error.

## API Design

### `GET /api/v1/notes`

Returns note list ordered by `notes.updated_at DESC`.

Response:

```json
{
  "notes": [
    {
      "note_id": "note-abc",
      "default_conversation_id": "conv-abc",
      "title": "毕业论文引言",
      "preview_text": "正在使用 codex CLI 协助我...",
      "created_at": "2026-05-11T08:00:00+00:00",
      "updated_at": "2026-05-11T09:00:00+00:00"
    }
  ]
}
```

This endpoint does not return `canvas_snapshot`.

### `POST /api/v1/notes`

Creates an empty note and its default conversation.

Request body can be empty in the first version.

Response:

```json
{
  "note_id": "note-abc",
  "default_conversation_id": "conv-abc"
}
```

The created note uses:

- `title = "Untitled note"`
- `canvas_snapshot = ""`
- `preview_text = ""`

### `GET /api/v1/notes/{note_id}`

Loads a full note for the editor.

Response:

```json
{
  "note_id": "note-abc",
  "default_conversation_id": "conv-abc",
  "title": "毕业论文引言",
  "canvas_snapshot": "<h1>毕业论文引言</h1><p>...</p>",
  "preview_text": "正在使用 codex CLI 协助我...",
  "created_at": "2026-05-11T08:00:00+00:00",
  "updated_at": "2026-05-11T09:00:00+00:00"
}
```

### `PUT /api/v1/notes/{note_id}/snapshot`

Saves the editor HTML.

Request:

```json
{
  "canvas_snapshot": "<h1>毕业论文引言</h1><p>...</p>"
}
```

Response:

```json
{
  "note_id": "note-abc",
  "title": "毕业论文引言",
  "preview_text": "正在使用 codex CLI 协助我...",
  "updated_at": "2026-05-11T09:05:00+00:00"
}
```

### `POST /api/v1/chat-stream`

Add `note_id` to `ChatRequest`.

Request:

```json
{
  "note_id": "note-abc",
  "conversation_id": "conv-abc",
  "session_id": "session-abc",
  "user_input": "帮我润色这一段",
  "focus_element_id": "moss-block-abc",
  "focus_block_id": "moss-block-abc",
  "canvas_snapshot": "<h1>...</h1>"
}
```

Validation:

- `note_id` must match `note-[A-Za-z0-9_-]{8,64}`.
- `note_id` must exist for `test-user`.
- `conversation_id` must exist for `test-user`.
- `conversation_id` must belong to `note_id`.

The route should still stream the existing SSE events. It no longer needs to emit `conversation` for normal editor requests because note creation already returns ids. A compatibility path may still create a note when both `note_id` and `conversation_id` are omitted, but the editor should use `POST /api/v1/notes`.

## Frontend Design

### `library.html`

Create a root-level `library.html` based on the visual style of `Blueprint/library.html`. Do not edit files under `Blueprint/`.

First-version library capabilities:

- Load notes from `GET /api/v1/notes`.
- Render cards with title, preview text, updated date, and optional empty state.
- Search/filter locally by `title` and `preview_text`.
- Keep grid/list view toggle if inexpensive, matching the Blueprint behavior.
- Create note button calls `POST /api/v1/notes`, then navigates to `/?note_id=...&conversation_id=...`.
- Clicking an existing card directly navigates to `/?note_id=...&conversation_id=...`.

Out of scope for first version:

- Folder creation.
- Starred notes.
- Deleting notes.
- Multi-conversation branch UI.

### `index.html`

Add note identity state:

```text
currentNoteId
currentConversationId
```

Startup:

1. Read `note_id` and `conversation_id` from `URLSearchParams`.
2. If `note_id` exists, load `GET /api/v1/notes/{note_id}`.
3. Use URL `conversation_id` if present; otherwise use `default_conversation_id`.
4. If `note_id` is missing, call `POST /api/v1/notes`, then `history.replaceState()` to write both ids into the URL.
5. Load `canvas_snapshot` into Tiptap. If empty, load a minimal blank document such as `<p></p>` so a new user note is not saved as the welcome template.

Saving:

- Debounce Tiptap updates and call `PUT /api/v1/notes/{note_id}/snapshot`.
- Existing save button immediately flushes the same endpoint.
- `beforeunload` should attempt a final save. If a normal async request is unreliable during unload, use `navigator.sendBeacon()` with a small endpoint or perform best-effort synchronous state persistence. The implementation plan should choose the least invasive option.
- After `dom_mutation` updates Tiptap, autosave should persist the changed snapshot.

AI requests:

- Include `note_id`.
- Include `conversation_id`.
- Continue sending `canvas_snapshot`, focus ids, and session id.
- Do not rely on SSE `conversation` events for normal flow.

Navigation:

- Add a lightweight link/button from editor to `/library`.
- Before navigating to `/library`, flush any pending autosave.

## Error Handling

Backend:

- `GET /api/v1/notes/{note_id}` returns 404 for unknown notes.
- Snapshot save returns 404 for unknown notes.
- Chat stream returns 422 for malformed ids.
- Chat stream returns 404 for unknown note or conversation.
- Chat stream returns 409 for a valid conversation that does not belong to the requested note.
- Permission errors return 403 after real authentication exists; during the fixed-user stage, ownership mismatch can use 403.

Frontend:

- Library empty state offers a new-note action.
- Library load failure shows a compact error state with retry.
- Editor note-load failure shows a blocking error message and a link back to `/library`.
- Autosave failure should not interrupt typing; show a small status near the save control.
- Manual save failure should add a visible message because the user explicitly requested saving.

## Testing Plan

Backend tests:

- Note creation creates both `notes` and a default `conversations` row.
- Listing notes returns summary fields without `canvas_snapshot`.
- Loading a note returns `canvas_snapshot` and default conversation id.
- Saving snapshot updates `canvas_snapshot`, `title`, `preview_text`, and `updated_at`.
- Title extraction prefers headings, then text, then `Untitled note`.
- Existing legacy conversations are migrated to notes.
- Empty migrated notes are hydrated from latest checkpoint snapshots when available.
- Chat stream accepts matching `note_id` and `conversation_id`.
- Chat stream saves the request `canvas_snapshot` to the note before streaming.
- Chat stream rejects mismatched note/conversation ids.
- Chat stream still passes `conversation_id` as LangGraph `thread_id`.

Frontend/manual verification:

- `/library` loads notes and shows cards.
- New note from library opens editor with URL ids.
- Existing note card opens editor with URL ids.
- Refreshing editor reloads the same note content.
- Manual save updates the library card.
- Pure editing without AI updates the library card after autosave.
- AI `dom_mutation` updates the editor and persists to the note snapshot.

## Scope Boundaries

In scope:

- Separate `library.html`.
- Notes table.
- Conversation-to-note relationship.
- Default conversation per note.
- Editor loading and saving by `note_id`.
- Chat requests validated against note ownership.

Out of scope:

- Folder management.
- Starred notes.
- Delete/archive/recycle bin.
- Multiple conversation UI per note.
- Full text search backed by SQLite FTS.
- Checkpoint blob migration into note bodies.

## Acceptance Criteria

- A user can open `/library`, see notes, create a note, and open a note in `/`.
- The editor URL contains both `note_id` and `conversation_id`.
- A user can edit text without sending AI messages and see the updated title/preview in the library.
- AI conversation memory still uses the same `conversation_id` after reload.
- Notes and conversations are stored separately and linked by `note_id`.
