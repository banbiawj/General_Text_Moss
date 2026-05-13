# Note Discussions Implementation Plan

> REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal

Implement multiple AI discussions per note and expose them through a title-triggered ASCII tree popup in `index.html`.

## Architecture

- `notes.canvas_snapshot` remains the editor body source of truth.
- `conversations.conversation_id` remains the LangGraph `thread_id`.
- `notes.last_opened_conversation_id` stores the last selected discussion.
- Switching discussions changes messages only.

## Files

- `moss_backend/app/services/notes.py`
- `moss_backend/app/api/schemas.py`
- `moss_backend/app/api/routes.py`
- `index.html`
- `library.html`
- `moss_backend/README.md`
- note and frontend tests under `moss_backend/tests/`

Do not edit `Blueprint/`.

## Task 1: Backend Data And Services

- [x] Add `last_opened_conversation_id` migration for `notes`.
- [x] Add active conversation resolution.
- [x] Add `list_note_conversations()`.
- [x] Add `create_conversation_for_note()`.
- [x] Add `mark_conversation_opened()`.
- [x] Extend `touch_conversation()` with a title hint.
- [x] Add tests for non-default discussions and last-opened state.

## Task 2: Backend API

- [x] Add note conversation response schemas.
- [x] Add `GET /api/v1/notes/{note_id}/conversations`.
- [x] Add `POST /api/v1/notes/{note_id}/conversations`.
- [x] Extend note list/detail responses with `active_conversation_id`.
- [x] Mark conversations opened during message load.
- [x] Touch/title conversations during note-scoped chat.
- [x] Add route tests.

## Task 3: Frontend Title Popup

- [x] Load current note conversations in `index.html`.
- [x] Keep create/switch flows from replacing editor content.
- [x] Remove the previous chat-stream-top discussion strip.
- [x] Add a title-bar discussion count trigger.
- [x] Add a temporary tree popup attached to the title.
- [x] Move the popup to the editor panel's left side, vertically centered in the viewport.
- [x] Remove the trigger border and rely on hover emphasis.
- [x] Add outside-click close behavior.
- [x] Use source-safe unicode escapes for runtime tree prefixes.
- [x] Use `<` as the active conversation marker.
- [x] Make `library.html` open `active_conversation_id` first.

Collapsed title trigger:

```text
document title    3 v
```

Popup shape:

```text
+-----------------------------+
| document title           + |
|                             |
| |-  discussion name     <  |
| \-  discussion name        |
+-----------------------------+
```

## Task 4: Verification

- [x] Focused note/discussion backend tests.
- [x] Frontend static tests.
- [ ] Full discovery is expected to keep pre-existing failures unrelated to this feature:
  - missing `app.agent.skill_runtime`;
  - `test_document_content.test_rejects_unsupported_task_type`.
- [x] `git diff --check`.
- [x] `git diff -- Blueprint`.

## Manual Checks

- Open `/library`.
- Open a note.
- Click the count/dropdown next to the editor title.
- Create a new discussion with `+`.
- Switch between discussions.
- Confirm chat history changes while editor body stays unchanged.
- Refresh and confirm the last selected discussion is selected.
