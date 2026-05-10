# Conversation Threading Design

## Goal

Add first-class multi-turn chat ownership without introducing authentication yet.

The first release uses a fixed backend user:

```python
DEFAULT_USER_ID = "test-user"
```

The long-term model is still preserved:

```text
user_id = test-user
  conversation_id = conv-a
  conversation_id = conv-b
  conversation_id = conv-c

session_id = browser/session marker for logs and frontend state only
```

`conversation_id` is the LangGraph persistence key. `session_id` must not decide chat ownership or recovery.

## Current State

Relevant existing files:

- `moss_backend/app/api/schemas.py` defines `ChatRequest` with `session_id`, `user_input`, focus ids, and `canvas_snapshot`.
- `moss_backend/app/api/routes.py` forwards those fields to `stream_agent_events()`.
- `moss_backend/app/agent/graph.py` compiles the graph with `builder.compile()` and no checkpointer.
- `stream_agent_events()` builds a single-turn `initial_state` and calls `graph.astream_events(initial_state, version="v2")`.
- `index.html` stores only `moss-session-id` in `localStorage` and does not send `conversation_id`.

The existing graph has `messages` in state, but the current execution path does not yet add the user message to that history or use previous `messages` as model context. Persisting checkpoints alone will not create useful multi-turn behavior unless the graph also maintains and consumes conversation history.

## Scope

First stage:

- Add optional `conversation_id` to `POST /api/v1/chat-stream`.
- Keep `DEFAULT_USER_ID = "test-user"` on the backend.
- Create or load conversation metadata for the fixed user.
- Use a SQLite LangGraph checkpointer.
- Run LangGraph with `thread_id = conversation_id`.
- Emit an SSE `conversation` event when the backend creates a conversation.
- Store and reuse `currentConversationId` on the frontend.
- Add tests proving same conversation id reuses the same thread and different conversation ids are isolated.

Out of scope for this stage:

- Login or real user authentication.
- Conversation list UI.
- `GET /api/v1/conversations`, `POST /api/v1/conversations`, or `GET /api/v1/conversations/{conversation_id}`.
- Renaming conversations from the UI.
- Migrating historical in-memory sessions.

## API Contract

`POST /api/v1/chat-stream` accepts:

```json
{
  "session_id": "session-xxx",
  "conversation_id": "conv-xxx",
  "user_input": "continue the previous turn",
  "focus_element_id": "moss-block-1",
  "focus_block_id": "moss-block-1",
  "canvas_snapshot": "<p>...</p>"
}
```

`conversation_id` is optional.

When the request omits `conversation_id`, the backend creates one and sends this SSE event before graph progress events:

```text
event: conversation
data: {"conversation_id": "conv-xxx", "user_id": "test-user"}
```

If a request provides a valid `conversation_id` that does not yet exist for `test-user`, the backend creates metadata for it and emits the same `conversation` event. This supports a future "new chat" flow where the frontend pre-generates an id. If the id exists for a different user after authentication is added, the request must be rejected.

Validation:

- `conversation_id` uses a narrow safe format: `conv-` followed by 8 to 64 ASCII letters, digits, `_`, or `-`.
- Invalid ids return a normal API validation error before graph execution.
- Empty `user_input` keeps the existing `ChatRequest` validation behavior.

## Backend Architecture

Add a small conversation boundary before calling the graph:

```text
ChatRequest
  -> resolve user_id = DEFAULT_USER_ID
  -> resolve or create conversation metadata
  -> stream conversation event if created
  -> stream LangGraph events with thread_id = conversation_id
```

Suggested backend units:

- `moss_backend/app/api/schemas.py`
  - Add `conversation_id: str | None = None`.
- `moss_backend/app/services/conversations.py`
  - Own `DEFAULT_USER_ID`, id generation, validation, metadata create/touch/load helpers.
- `moss_backend/app/agent/graph.py`
  - Accept `conversation_id`.
  - Pass `config = {"configurable": {"thread_id": conversation_id}}` to `graph.astream_events(...)`.
  - Compile the graph with a SQLite checkpointer.
- `moss_backend/app/core/config.py`
  - Add configurable paths for checkpointer and metadata storage under `STORAGE_DIR`.

## Persistence

Use two separate layers.

### LangGraph Checkpointer

Purpose: persist graph state and resume multi-turn conversation memory.

Storage:

```text
storage/langgraph_checkpoints.sqlite3
```

Implementation notes:

- Add the LangGraph SQLite checkpoint dependency that matches the installed LangGraph version.
- Compile the graph with the SQLite checkpointer.
- Use `conversation_id`, not `session_id`, as `thread_id`.
- Manage async checkpointer lifetime through FastAPI lifespan or a graph factory so the database connection is not recreated per event chunk.

Graph invocation shape:

```python
config = {"configurable": {"thread_id": conversation_id}}
async for event in graph.astream_events(initial_state, config=config, version="v2"):
    ...
```

Multi-turn correctness requires one more graph change: each request must add the new `HumanMessage` to persisted `messages`, and the model prompt must include bounded previous conversation history. Turn-scoped fields such as `tasks`, `current_task_index`, and `pending_mutations` should be reset on each request.

### Conversation Metadata

Purpose: support ownership, future chat lists, and user-visible metadata.

Storage:

```text
storage/conversations.sqlite3
```

Initial table:

```sql
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Initial metadata:

```json
{
  "conversation_id": "conv-xxx",
  "user_id": "test-user",
  "title": "Untitled conversation",
  "created_at": "2026-05-10T00:00:00Z",
  "updated_at": "2026-05-10T00:00:00Z"
}
```

`updated_at` is touched after a chat request is accepted for that conversation.

## Frontend State

Keep:

```text
moss-session-id = browser/session marker
```

Add:

```text
moss-current-conversation-id = active chat thread
```

Request behavior:

- Include `conversation_id` when `currentConversationId` exists.
- If no `currentConversationId` exists, omit it and let the backend create one.
- On SSE `conversation`, set `currentConversationId` and persist it to `localStorage`.

Minimal first-stage UI behavior:

- Refresh continues the same conversation if `moss-current-conversation-id` exists.
- No new-chat UI is required in this stage. Clearing `moss-current-conversation-id` is enough for manual testing; a later new-chat action should clear `currentConversationId` and the local message list.
- No conversation list is required.

## Error Handling

- Metadata validation failure returns an HTTP validation error before streaming starts.
- Metadata storage failure yields an SSE `error` event if streaming has already started; otherwise it can fail the request before graph execution.
- Checkpointer initialization failure should fail fast at backend startup or graph initialization.
- Unknown but valid `conversation_id` is created for `test-user` during the no-auth stage.
- After authentication is introduced, unknown or foreign conversation ids should become `404` or `403` depending on the product decision.

## Tests

Backend tests:

- `ChatRequest` accepts optional `conversation_id` and preserves legacy `message/context` normalization.
- Missing `conversation_id` creates metadata and emits `event: conversation`.
- Provided existing `conversation_id` does not emit a new conversation event.
- Provided valid but unknown `conversation_id` creates metadata for `test-user`.
- `stream_agent_events()` passes `{"configurable": {"thread_id": conversation_id}}` into LangGraph.
- Same `conversation_id` preserves persisted `messages` across turns.
- Different `conversation_id` values do not share persisted `messages`.
- `session_id` differences do not affect thread recovery when `conversation_id` is the same.

Frontend tests or manual checks:

- First send without `moss-current-conversation-id` stores the id from the SSE `conversation` event.
- Refresh sends the stored `conversation_id`.
- Clearing the current conversation id causes the next send to create a different conversation.

## Future Extension

When login arrives:

- Replace `DEFAULT_USER_ID` with the authenticated user id.
- Keep the `conversation_id` to LangGraph `thread_id` mapping unchanged.
- Add list/detail/create APIs over the existing metadata table.
- Add title generation or first-message title extraction without changing the thread model.
