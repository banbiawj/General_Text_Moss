# Library Management And Responsive Design

## Goal

Upgrade `library.html` from a note entry list into a usable note management surface.

This phase focuses on:

- responsive layout
- mobile drawer sidebar
- note rename
- note pin/unpin
- soft delete

This phase intentionally does not include:

- starred notes
- notebooks/folders
- bulk selection
- recycle bin UI
- multi-conversation management per note

## Current State

The first library version already supports the main note flow:

```text
/library
  -> GET /api/v1/notes
  -> render note cards
  -> create note
  -> open /?note_id=...&conversation_id=...
```

`index.html` owns note editing and autosave. `library.html` owns note discovery and management.

`Blueprint/library.html` is the visual reference only. It must not be modified.

## Product Rules

### Rename

Rename only changes the library/editor display name. It must not modify the note body HTML.

Add `notes.display_title`.

Title display order:

```text
display_title -> title -> "Untitled note"
```

Where:

- `display_title` is manually set by the user in the library.
- `title` is still extracted from `canvas_snapshot`.
- Clearing `display_title` restores automatic title display.

Both `library.html` and `index.html` should use the same display title rule.

### Delete

Delete is soft delete.

Add `notes.deleted_at`.

Rules:

- `deleted_at IS NULL`: normal note.
- `deleted_at IS NOT NULL`: deleted note.
- Default library list excludes deleted notes.
- `DELETE /api/v1/notes/{note_id}` sets `deleted_at`.
- It does not delete conversations.
- It does not delete LangGraph checkpoints.
- First version has no recycle bin UI.
- Direct `GET /api/v1/notes/{note_id}` for a deleted note returns `404`.

Rationale: deleting should be safe and reversible at the data level, even if restore UI is not included yet.

### Pin

Pin uses a timestamp, not a boolean.

Add `notes.pinned_at`.

Rules:

- `pinned_at IS NULL`: unpinned.
- `pinned_at IS NOT NULL`: pinned.
- Pinning sets `pinned_at` to current UTC ISO timestamp.
- Unpinning clears `pinned_at`.
- Pin/unpin does not change `updated_at`.
- Pin/unpin does not change `canvas_snapshot`.

List ordering:

```sql
ORDER BY
  pinned_at IS NULL ASC,
  pinned_at DESC,
  updated_at DESC
```

This puts pinned notes first, newest pinned first, then normal notes by last content update.

## Data Model

Extend `notes`:

```sql
ALTER TABLE notes ADD COLUMN display_title TEXT;
ALTER TABLE notes ADD COLUMN deleted_at TEXT;
ALTER TABLE notes ADD COLUMN pinned_at TEXT;
```

Existing rows keep all three fields as `NULL`.

`notes.title` remains required and auto-derived from the body.

`notes.display_title` is optional and may be `NULL` or an empty string. The service should normalize an empty or whitespace-only display title to `NULL`.

## API Design

### List Notes

Existing:

```text
GET /api/v1/notes
```

Changes:

- Exclude `deleted_at IS NOT NULL`.
- Return display fields.
- Preserve existing summary fields.

Response item:

```json
{
  "note_id": "note-abc",
  "default_conversation_id": "conv-abc",
  "title": "Automatic body title",
  "display_title": "Manual library title",
  "effective_title": "Manual library title",
  "preview_text": "Body preview...",
  "pinned_at": "2026-05-11T00:00:00+00:00",
  "created_at": "2026-05-11T00:00:00+00:00",
  "updated_at": "2026-05-11T00:01:00+00:00"
}
```

`effective_title` is included to keep frontend logic simple and consistent with the backend.

### Get Note

Existing:

```text
GET /api/v1/notes/{note_id}
```

Changes:

- Return `display_title`, `effective_title`, and `pinned_at`.
- Return `404` if the note is soft deleted.

### Update Note Metadata

New:

```text
PATCH /api/v1/notes/{note_id}
```

Request:

```json
{
  "display_title": "New display name",
  "pinned": true
}
```

Both fields are optional.

Rules:

- If `display_title` is omitted, do not change it.
- If `display_title` is `null` or whitespace-only, clear it.
- If `pinned` is omitted, do not change pin state.
- If `pinned` is `true`, set `pinned_at`.
- If `pinned` is `false`, clear `pinned_at`.
- Do not change `updated_at`.
- Do not change `canvas_snapshot`.

Response:

```json
{
  "note_id": "note-abc",
  "title": "Automatic body title",
  "display_title": "New display name",
  "effective_title": "New display name",
  "preview_text": "Body preview...",
  "pinned_at": "2026-05-11T00:00:00+00:00",
  "updated_at": "2026-05-11T00:01:00+00:00"
}
```

### Delete Note

New:

```text
DELETE /api/v1/notes/{note_id}
```

Rules:

- Set `deleted_at` to current UTC ISO timestamp.
- Do not remove the row.
- Do not remove conversations.
- Do not remove checkpoints.
- Repeated delete is idempotent.

Response:

```json
{
  "note_id": "note-abc",
  "deleted_at": "2026-05-11T00:00:00+00:00"
}
```

## Frontend Design

### Layout

`library.html` keeps the Blueprint visual language:

- quiet work-focused interface
- left navigation
- white main surface
- grid/list note views
- compact icon controls

Desktop:

- sidebar is visible by default
- main content fills remaining width
- cards use masonry-like grid in grid mode
- list mode constrains to a readable centered column

Mobile:

- sidebar is hidden by default
- top-left hamburger opens drawer
- overlay closes drawer
- close button inside drawer
- selecting a view closes drawer
- main content remains scrollable

### Sidebar

This phase keeps sidebar navigation minimal:

- brand row
- create note button
- all notes view

Starred and notebook entries are not shown yet. This avoids presenting controls with no backing model.

### Header

Header contains:

- sidebar toggle
- current view label
- search box
- grid/list toggle

Sort controls are omitted in this phase. The backend ordering is fixed: pinned notes first, then content update time.

### Note Card

Each card shows:

- effective title
- preview text or empty note label
- update timestamp
- pinned state
- more menu button

Card click opens the editor:

```text
/?note_id=<note_id>&conversation_id=<default_conversation_id>
```

Menu click must stop propagation so it does not open the editor.

### More Menu

Menu actions:

- Rename
- Pin or Unpin
- Delete

Rules:

- Rename opens a compact dialog.
- Pin/unpin applies immediately.
- Delete opens a confirmation dialog.
- Action failures show a lightweight error message.
- The menu closes after a successful action.

### Rename Dialog

Dialog fields:

- text input initialized with `display_title || title`
- save button
- cancel button
- clear display name control, or allow empty value to clear

Saving:

```text
PATCH /api/v1/notes/{note_id}
{ "display_title": "<input>" }
```

After success:

- update the note item in local state
- update visible title
- close dialog

### Delete Confirmation

Confirmation text should make clear that the note will be removed from the library, not permanently destroyed in this phase.

On confirm:

```text
DELETE /api/v1/notes/{note_id}
```

After success:

- remove note from local list
- close dialog

### Search

Search is client-side in this phase.

Search fields:

```text
effective_title + preview_text
```

Soft-deleted notes are never present in the list, so they are not searchable.

## Editor Integration

`index.html` should display `effective_title` where it currently displays `title`.

Loading a note should preserve:

- `display_title`
- `effective_title`
- `canvas_snapshot`
- `default_conversation_id`

Saving note content should still update only the auto title/preview generated from `canvas_snapshot`. It must not clear or overwrite `display_title`.

If a user opens an old URL for a soft-deleted note, the backend returns `404`; the editor should show the existing note-load error and offer a path back to `/library`.

## Error Handling

Backend:

- invalid note id returns `422`
- unknown note returns `404`
- soft-deleted note returns `404` from normal get/update/delete paths, except repeated delete may return the existing `deleted_at`
- mismatched note/conversation behavior remains unchanged

Frontend:

- list load failure shows retry
- metadata action failure shows compact message
- delete failure keeps the card visible
- rename failure keeps the dialog open
- pin failure restores the previous local state or reloads notes

## Testing Plan

Backend tests:

- schema migration adds `display_title`, `deleted_at`, and `pinned_at`
- list excludes soft-deleted notes
- list orders pinned notes first
- patch `display_title` changes display metadata but not `canvas_snapshot`
- patch `display_title` does not change `updated_at`
- patch `pinned=true` sets `pinned_at` but not `updated_at`
- patch `pinned=false` clears `pinned_at`
- delete sets `deleted_at`
- deleted notes disappear from list
- direct get of deleted note returns `404`
- repeated delete is safe

Frontend/manual verification:

- desktop layout shows sidebar and notes
- mobile layout opens/closes drawer with hamburger, close button, and overlay
- card click opens editor
- menu click does not open editor
- rename updates card title after refresh
- rename does not change editor body HTML
- clearing rename restores automatic title
- pin moves card to top after refresh
- unpin returns card to normal ordering
- delete removes card and it remains hidden after refresh

## Scope Boundaries

In scope:

- responsive library layout
- mobile drawer
- rename display title
- pin/unpin
- soft delete
- menu and dialogs
- editor display title integration

Out of scope:

- starred notes
- notebooks/folders
- recycle bin UI
- restore deleted note UI
- hard delete
- bulk operations
- server-side full-text search
- user accounts/auth

## Open Implementation Notes

- Preserve `Blueprint/library.html` untouched.
- Prefer `NoteStore` methods for all note metadata operations.
- Keep management operations idempotent where practical.
- Avoid changing `updated_at` for metadata-only operations, because `updated_at` is currently the content recency signal.
- Use `pinned_at` as the primary pin ordering signal.
