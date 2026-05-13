# Note Discussions Design

## Goal

Support multiple independent AI discussions for one note.

The model is:

```text
notes 1 ---- N conversations
```

Important boundary:

- `notes.canvas_snapshot` is the editor body source of truth.
- `conversations.conversation_id` is the LangGraph `thread_id`.
- Switching discussions changes chat history only.
- Switching discussions must not reload or overwrite the editor body.

## Current State

The project already has:

- `notes` and `conversations` tables.
- `conversations.note_id`.
- one default conversation per note.
- note-scoped message loading.
- note-scoped chat validation.

This feature adds:

- non-default conversations per note;
- `notes.last_opened_conversation_id`;
- note-scoped conversation list/create APIs;
- editor UI for creating and switching discussions.

## Data Model

Add to `notes`:

```sql
last_opened_conversation_id TEXT
```

Rules:

- It is navigation state.
- Updating it must not change `notes.updated_at`.
- If it is missing or invalid, use the note's default conversation.

The existing `conversations` table remains the AI discussion metadata store:

```text
conversation_id
user_id
title
created_at
updated_at
note_id
is_default
```

The existing unique default index remains valid because it only prevents multiple default conversations per note:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_default_per_note
ON conversations(note_id)
WHERE is_default = 1;
```

## Backend API

Add:

```text
GET  /api/v1/notes/{note_id}/conversations
POST /api/v1/notes/{note_id}/conversations
```

Extend note list/detail responses with:

```text
active_conversation_id
last_opened_conversation_id
```

`active_conversation_id` is:

```text
valid last_opened_conversation_id
else default_conversation_id
```

Message loading and note-scoped chat should mark the selected conversation as last opened. Note-scoped chat should also touch the selected conversation and may set its title from the first user message while the title is still generic.

## Editor UI

Use a title-bar trigger and a temporary ASCII tree popup.

Collapsed title-bar state:

```text
Deep Echo    3 v
```

Expanded popup shape:

```text
+-----------------------------+
| Deep Echo                + |
|                             |
| |-  Current discussion   < |
| |-  Polish version         |
| \-  Structure rewrite      |
+-----------------------------+
```

Implementation notes:

- The source file should stay encoding-safe.
- Runtime tree prefixes may render as box-drawing characters through unicode escapes:
  - `\u251C\u2500` for the middle rows.
  - `\u2514\u2500` for the last row.
- The current discussion uses a right-aligned `<` marker.
- The `+` button creates a new discussion for the current note.
- Clicking a discussion switches chat history and closes the popup.
- Clicking outside closes the popup.
- The popup is attached to the editor title, not the chat stream.
- The popup itself is fixed beside the left edge of the editor panel and vertically centered in the viewport.
- The collapsed `count + chevron` trigger has no border; hover state should make it visibly interactive.
- Do not show `note_id` in this user-facing popup.

## Library Behavior

The library still shows notes, not discussions.

Opening a note should use:

```text
note.active_conversation_id || note.default_conversation_id
```

## Acceptance Criteria

- One note can have multiple conversations.
- New conversations are non-default.
- Discussion list/create APIs work under a note.
- The editor title shows the discussion count and a dropdown trigger.
- The popup renders a tree-shaped discussion list.
- Switching a discussion changes messages only.
- Refreshing a note opens the last selected discussion.
- `Blueprint/` remains untouched.
