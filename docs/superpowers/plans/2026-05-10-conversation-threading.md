# Conversation Threading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add C-lite multi-turn conversation ownership: fixed backend user, many conversations, SQLite LangGraph checkpoints, and frontend reuse of the active conversation id.

**Architecture:** Add a focused conversation metadata service backed by SQLite, then resolve a conversation before streaming each chat request. Compile LangGraph with a SQLite checkpointer and pass `conversation_id` as `thread_id`; `session_id` remains a browser/log marker only. The frontend stores `moss-current-conversation-id` and sends it on later chat requests.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, stdlib `sqlite3`, LangGraph, `langgraph-checkpoint-sqlite`, Vue 3 single-file frontend, `unittest`.

---

## File Structure

- Create `moss_backend/app/services/conversations.py`
  - Owns `DEFAULT_USER_ID`, conversation id validation/generation, SQLite metadata schema, create/load/touch behavior.
- Create `moss_backend/tests/test_conversations.py`
  - Unit tests for metadata creation, validation, fixed user ownership, and updated timestamps.
- Create `moss_backend/tests/test_chat_stream_conversations.py`
  - Route-level tests for `conversation` SSE events and passing `conversation_id` through to the graph entrypoint.
- Create `moss_backend/tests/test_graph_threading.py`
  - Graph-level tests for `thread_id` config, persisted `messages`, and conversation isolation.
- Modify `moss_backend/app/api/schemas.py`
  - Add optional `conversation_id` and validate the `conv-...` format.
- Modify `moss_backend/app/api/routes.py`
  - Resolve/create metadata before graph streaming, emit `conversation` SSE events, and pass `conversation_id` to `stream_agent_events`.
- Modify `moss_backend/app/agent/state.py`
  - Add `conversation_id` to state.
- Modify `moss_backend/app/agent/graph.py`
  - Add graph compile factory, accept `conversation_id`, pass LangGraph config, persist current `HumanMessage`, and include bounded history in execute prompts.
- Modify `moss_backend/app/main.py`
  - Add FastAPI lifespan that opens an async SQLite checkpointer and stores the compiled graph in `app.state.agent_graph`.
- Modify `moss_backend/app/core/config.py`
  - Add `CONVERSATION_METADATA_DB` and `LANGGRAPH_CHECKPOINT_DB` settings resolved under `STORAGE_DIR` by default.
- Modify `moss_backend/requirements.txt`
  - Add `langgraph-checkpoint-sqlite>=2.0,<3.0`.
- Modify `moss_backend/README.md`
  - Document the new payload field, SSE event, storage files, and config values.
- Modify `index.html`
  - Persist and send `moss-current-conversation-id`; handle the SSE `conversation` event.

Existing dirty files at plan time:

- `README.md`
- `Blueprint/SYSTEM_DESIGN.md`

Do not include those two files in C-lite commits unless the user explicitly asks.

---

### Task 1: Add Conversation Metadata Store

**Files:**
- Create: `moss_backend/app/services/conversations.py`
- Create: `moss_backend/tests/test_conversations.py`

- [ ] **Step 1: Write failing metadata tests**

Create `moss_backend/tests/test_conversations.py`:

```python
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.services.conversations import (
    DEFAULT_USER_ID,
    ConversationStore,
    InvalidConversationId,
    is_valid_conversation_id,
)


class ConversationStoreTests(unittest.TestCase):
    def test_missing_conversation_id_creates_record_for_default_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir) / "conversations.sqlite3")

            result = store.resolve(user_id=DEFAULT_USER_ID, conversation_id=None)

            self.assertTrue(result.created)
            self.assertTrue(result.record.conversation_id.startswith("conv-"))
            self.assertEqual(result.record.user_id, "test-user")
            self.assertEqual(result.record.title, "Untitled conversation")
            self.assertIsNotNone(store.get(result.record.conversation_id))

    def test_existing_conversation_id_is_reused_and_touched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir) / "conversations.sqlite3")
            created = store.resolve(DEFAULT_USER_ID, "conv-existing_123")
            original_updated_at = created.record.updated_at
            time.sleep(0.01)

            reused = store.resolve(DEFAULT_USER_ID, "conv-existing_123")

            self.assertFalse(reused.created)
            self.assertEqual(reused.record.conversation_id, "conv-existing_123")
            self.assertGreater(reused.record.updated_at, original_updated_at)

    def test_valid_unknown_conversation_id_creates_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir) / "conversations.sqlite3")

            result = store.resolve(DEFAULT_USER_ID, "conv-clientCreated123")

            self.assertTrue(result.created)
            self.assertEqual(result.record.conversation_id, "conv-clientCreated123")
            self.assertEqual(result.record.user_id, DEFAULT_USER_ID)

    def test_invalid_conversation_id_is_rejected(self) -> None:
        invalid_ids = ["abc", "conv-", "conv-中文", "conv-short", "conv-has space"]

        for conversation_id in invalid_ids:
            with self.subTest(conversation_id=conversation_id):
                self.assertFalse(is_valid_conversation_id(conversation_id))

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir) / "conversations.sqlite3")

            with self.assertRaises(InvalidConversationId):
                store.resolve(DEFAULT_USER_ID, "conv-has space")

    def test_foreign_user_cannot_reuse_existing_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir) / "conversations.sqlite3")
            store.resolve("test-user", "conv-ownedByUser1")

            with self.assertRaises(PermissionError):
                store.resolve("another-user", "conv-ownedByUser1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_conversations -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.conversations'`.

- [ ] **Step 3: Implement metadata store**

Create `moss_backend/app/services/conversations.py`:

```python
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

    def resolve(self, user_id: str, conversation_id: str | None) -> ResolveConversationResult:
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
                SELECT conversation_id, user_id, title, created_at, updated_at
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
                    conversation_id, user_id, title, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    user_id,
                    DEFAULT_CONVERSATION_TITLE,
                    now,
                    now,
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
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _record_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_conversations -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add moss_backend/app/services/conversations.py moss_backend/tests/test_conversations.py
git commit -m "feat: add conversation metadata store"
```

Expected: commit succeeds. If `.git/index.lock` permission is denied in the current environment, record that exact error and continue without altering unrelated files.

---

### Task 2: Add Chat Payload And SSE Conversation Resolution

**Files:**
- Modify: `moss_backend/app/api/schemas.py`
- Modify: `moss_backend/app/api/routes.py`
- Modify: `moss_backend/app/core/config.py`
- Create: `moss_backend/tests/test_chat_stream_conversations.py`

- [ ] **Step 1: Write failing route tests**

Create `moss_backend/tests/test_chat_stream_conversations.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.services.conversations import DEFAULT_USER_ID, ConversationStore


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
            "content": f"{conversation_id}:{session_id}:{user_input}",
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


class ChatStreamConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.temp_dir.name) / "conversations.sqlite3")
        self.original_store_getter = routes.get_conversation_store
        self.original_stream = routes.stream_agent_events
        routes.get_conversation_store = lambda: self.store
        routes.stream_agent_events = fake_stream_agent_events

    def tearDown(self) -> None:
        routes.get_conversation_store = self.original_store_getter
        routes.stream_agent_events = self.original_stream
        self.temp_dir.cleanup()

    def post_chat(self, payload: dict[str, Any]) -> str:
        with TestClient(app) as client:
            response = client.post("/api/v1/chat-stream", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.text

    def test_missing_conversation_id_creates_and_emits_conversation_event(self) -> None:
        body = self.post_chat(
            {
                "session_id": "session-a",
                "user_input": "hello",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertEqual(event_names(body)[:2], ["conversation", "chat_chunk"])
        conversation = event_payloads(body, "conversation")[0]
        self.assertEqual(conversation["user_id"], DEFAULT_USER_ID)
        self.assertTrue(conversation["conversation_id"].startswith("conv-"))
        self.assertIsNotNone(self.store.get(conversation["conversation_id"]))

    def test_existing_conversation_id_does_not_emit_conversation_event(self) -> None:
        created = self.store.resolve(DEFAULT_USER_ID, None)

        body = self.post_chat(
            {
                "session_id": "session-b",
                "conversation_id": created.record.conversation_id,
                "user_input": "continue",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertNotIn("conversation", event_names(body))
        self.assertIn(
            f"{created.record.conversation_id}:session-b:continue",
            body,
        )

    def test_valid_unknown_conversation_id_creates_and_emits_event(self) -> None:
        body = self.post_chat(
            {
                "session_id": "session-c",
                "conversation_id": "conv-clientGenerated123",
                "user_input": "start",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertIn("conversation", event_names(body))
        conversation = event_payloads(body, "conversation")[0]
        self.assertEqual(conversation["conversation_id"], "conv-clientGenerated123")
        self.assertIsNotNone(self.store.get("conv-clientGenerated123"))

    def test_invalid_conversation_id_returns_validation_error(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat-stream",
                json={
                    "session_id": "session-d",
                    "conversation_id": "bad id",
                    "user_input": "hello",
                    "canvas_snapshot": "<p>doc</p>",
                },
            )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_chat_stream_conversations -v
```

Expected: FAIL because `routes.get_conversation_store`, schema validation, and the new `stream_agent_events` signature do not exist.

- [ ] **Step 3: Add storage path settings**

Modify `moss_backend/app/core/config.py`.

Add fields to `Settings`:

```python
    conversation_metadata_db: Path | None = Field(
        default=None,
        alias="CONVERSATION_METADATA_DB",
    )
    langgraph_checkpoint_db: Path | None = Field(
        default=None,
        alias="LANGGRAPH_CHECKPOINT_DB",
    )
```

Add properties below `allowed_cors_origins`:

```python
    @property
    def conversation_metadata_path(self) -> Path:
        return self._storage_path(self.conversation_metadata_db, "conversations.sqlite3")

    @property
    def langgraph_checkpoint_path(self) -> Path:
        return self._storage_path(
            self.langgraph_checkpoint_db,
            "langgraph_checkpoints.sqlite3",
        )

    def _storage_path(self, configured: Path | None, filename: str) -> Path:
        if configured is not None:
            return configured
        return self.storage_dir / filename
```

- [ ] **Step 4: Add `conversation_id` to `ChatRequest`**

Modify `moss_backend/app/api/schemas.py`.

Add this import:

```python
from app.services.conversations import is_valid_conversation_id
```

Add this field to `ChatRequest`:

```python
    conversation_id: str | None = None
```

Extend `normalize_legacy_payload()` before the empty `user_input` check returns:

```python
        if self.conversation_id is not None and not is_valid_conversation_id(self.conversation_id):
            raise ValueError("conversation_id must match conv-[A-Za-z0-9_-]{8,64}")
```

- [ ] **Step 5: Resolve conversations in the route**

Modify imports in `moss_backend/app/api/routes.py`:

```python
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from app.services.conversations import (
    DEFAULT_USER_ID,
    ConversationStore,
    InvalidConversationId,
)
```

Add this helper near router creation:

```python
def get_conversation_store() -> ConversationStore:
    settings = get_settings()
    return ConversationStore(settings.conversation_metadata_path)
```

Replace `chat_stream()` with:

```python
@api_router.post("/chat-stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    try:
        resolved = get_conversation_store().resolve(
            user_id=DEFAULT_USER_ID,
            conversation_id=payload.conversation_id,
        )
    except InvalidConversationId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    async def generator():
        try:
            if resolved.created:
                yield _sse(
                    "conversation",
                    {
                        "conversation_id": resolved.record.conversation_id,
                        "user_id": resolved.record.user_id,
                    },
                )

            async for event in stream_agent_events(
                session_id=payload.session_id,
                conversation_id=resolved.record.conversation_id,
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

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 6: Run route tests to verify pass**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_conversations tests.test_chat_stream_conversations -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add moss_backend/app/core/config.py moss_backend/app/api/schemas.py moss_backend/app/api/routes.py moss_backend/tests/test_chat_stream_conversations.py
git commit -m "feat: resolve chat conversations before streaming"
```

Expected: commit succeeds. If `.git/index.lock` permission is denied, record it and keep the working tree changes.

---

### Task 3: Add LangGraph Threading, History, And SQLite Checkpointer

**Files:**
- Modify: `moss_backend/requirements.txt`
- Modify: `moss_backend/app/agent/state.py`
- Modify: `moss_backend/app/agent/graph.py`
- Modify: `moss_backend/app/main.py`
- Create: `moss_backend/tests/test_graph_threading.py`

- [ ] **Step 1: Add SQLite checkpointer dependency**

Modify `moss_backend/requirements.txt` by adding:

```text
langgraph-checkpoint-sqlite>=2.0,<3.0
```

Run:

```powershell
cd moss_backend
python -m pip install -r requirements.txt
```

Expected: installs `langgraph-checkpoint-sqlite` and its SQLite async dependency. If the network is unavailable, stop this task and report the package installation error.

- [ ] **Step 2: Write failing graph threading tests**

Create `moss_backend/tests/test_graph_threading.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, AsyncGenerator

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agent.graph import (
    _build_execute_messages,
    compile_agent_graph,
    stream_agent_events,
)


class FakeCompiledGraph:
    def __init__(self) -> None:
        self.captured_initial_state: dict[str, Any] | None = None
        self.captured_config: dict[str, Any] | None = None

    async def astream_events(
        self,
        initial_state: dict[str, Any],
        *,
        config: dict[str, Any],
        version: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.captured_initial_state = initial_state
        self.captured_config = config
        yield {
            "event": "on_chain_start",
            "name": "intent",
            "data": {},
        }


async def drain_events(generator: AsyncGenerator[dict[str, Any], None]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in generator:
        events.append(event)
    return events


class GraphThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_agent_events_uses_conversation_id_as_thread_id(self) -> None:
        fake_graph = FakeCompiledGraph()

        await drain_events(
            stream_agent_events(
                session_id="session-a",
                conversation_id="conv-thread123",
                user_input="hello",
                focus_element_id=None,
                focus_block_id=None,
                canvas_snapshot="",
                compiled_graph=fake_graph,
            )
        )

        self.assertEqual(
            fake_graph.captured_config,
            {"configurable": {"thread_id": "conv-thread123"}},
        )
        self.assertIsNotNone(fake_graph.captured_initial_state)
        messages = fake_graph.captured_initial_state["messages"]
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(fake_graph.captured_initial_state["conversation_id"], "conv-thread123")

    async def test_same_conversation_persists_messages_and_different_conversation_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "checkpoints.sqlite3"
            async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
                compiled_graph = compile_agent_graph(checkpointer=saver)
                same_config = {"configurable": {"thread_id": "conv-same1234"}}
                other_config = {"configurable": {"thread_id": "conv-other123"}}

                await drain_events(
                    stream_agent_events(
                        session_id="session-a",
                        conversation_id="conv-same1234",
                        user_input="first turn",
                        focus_element_id=None,
                        focus_block_id=None,
                        canvas_snapshot="",
                        compiled_graph=compiled_graph,
                    )
                )
                await drain_events(
                    stream_agent_events(
                        session_id="session-b",
                        conversation_id="conv-same1234",
                        user_input="second turn",
                        focus_element_id=None,
                        focus_block_id=None,
                        canvas_snapshot="",
                        compiled_graph=compiled_graph,
                    )
                )
                await drain_events(
                    stream_agent_events(
                        session_id="session-c",
                        conversation_id="conv-other123",
                        user_input="isolated turn",
                        focus_element_id=None,
                        focus_block_id=None,
                        canvas_snapshot="",
                        compiled_graph=compiled_graph,
                    )
                )

                same_state = await compiled_graph.aget_state(same_config)
                other_state = await compiled_graph.aget_state(other_config)

        same_contents = [
            str(message.content)
            for message in same_state.values["messages"]
            if isinstance(message, BaseMessage)
        ]
        other_contents = [
            str(message.content)
            for message in other_state.values["messages"]
            if isinstance(message, BaseMessage)
        ]

        self.assertIn("first turn", same_contents)
        self.assertIn("second turn", same_contents)
        self.assertNotIn("isolated turn", same_contents)
        self.assertIn("isolated turn", other_contents)
        self.assertNotIn("first turn", other_contents)

    def test_execute_messages_include_bounded_conversation_history(self) -> None:
        history = [HumanMessage(content=f"history {index}") for index in range(10)]
        task_messages = [HumanMessage(content="task-local")]

        messages = _build_execute_messages(
            system_prompt="system",
            conversation_messages=history,
            task_messages=task_messages,
        )

        contents = [str(message.content) for message in messages]
        self.assertEqual(contents[0], "system")
        self.assertNotIn("history 0", contents)
        self.assertNotIn("history 1", contents)
        self.assertIn("history 2", contents)
        self.assertIn("history 9", contents)
        self.assertEqual(contents[-1], "task-local")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run graph tests to verify failure**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_graph_threading -v
```

Expected: FAIL because `langgraph-checkpoint-sqlite`, `compile_agent_graph`, `_build_execute_messages`, and the new `stream_agent_events` parameters are not fully wired.

- [ ] **Step 4: Add `conversation_id` to graph state**

Modify `moss_backend/app/agent/state.py`.

Add this field near `session_id`:

```python
    conversation_id: str
```

- [ ] **Step 5: Refactor graph compilation into a factory**

Modify the graph definition section in `moss_backend/app/agent/graph.py`.

Replace the direct global compile:

```python
graph = builder.compile()
```

with:

```python
def compile_agent_graph(checkpointer: Any | None = None) -> Any:
    return builder.compile(checkpointer=checkpointer)


graph = compile_agent_graph()
```

`Any` is already imported at the top of the file.

- [ ] **Step 6: Add bounded execute-message helper**

Add this constant and helper above `execute_node()` in `moss_backend/app/agent/graph.py`:

```python
MAX_CONVERSATION_HISTORY_MESSAGES = 8


def _build_execute_messages(
    *,
    system_prompt: str,
    conversation_messages: list[Any],
    task_messages: list[Any],
) -> list[Any]:
    bounded_history = conversation_messages[-MAX_CONVERSATION_HISTORY_MESSAGES:]
    return [SystemMessage(content=system_prompt)] + bounded_history + task_messages
```

In `execute_node()`, replace:

```python
    task_messages = list(task.get("task_message", []))
    messages = [SystemMessage(content=task["task_prompt"])] + task_messages
```

with:

```python
    task_messages = list(task.get("task_message", []))
    conversation_messages = list(state.get("messages", []))
    messages = _build_execute_messages(
        system_prompt=task["task_prompt"],
        conversation_messages=conversation_messages,
        task_messages=task_messages,
    )
```

- [ ] **Step 7: Persist the current user message and pass LangGraph config**

Change the signature of `stream_agent_events()` in `moss_backend/app/agent/graph.py`:

```python
async def stream_agent_events(
    session_id: str,
    conversation_id: str,
    user_input: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
    compiled_graph: Any | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
```

In `initial_state`, replace `"messages": []` with:

```python
        "messages": [HumanMessage(content=user_input)],
```

Add `conversation_id` to `initial_state`:

```python
        "conversation_id": conversation_id,
```

Before streaming events, add:

```python
    runtime_graph = compiled_graph or graph
    config = {"configurable": {"thread_id": conversation_id}}
```

Replace:

```python
    async for event in graph.astream_events(initial_state, version="v2"):
```

with:

```python
    async for event in runtime_graph.astream_events(
        initial_state,
        config=config,
        version="v2",
    ):
```

- [ ] **Step 8: Compile the app graph with `AsyncSqliteSaver` in FastAPI lifespan**

Modify `moss_backend/app/main.py`.

Add imports:

```python
from contextlib import asynccontextmanager
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.agent.graph import compile_agent_graph
```

Replace:

```python
app = FastAPI(title=settings.app_name)
```

with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = settings.langgraph_checkpoint_path
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        app.state.agent_graph = compile_agent_graph(checkpointer=checkpointer)
        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
```

- [ ] **Step 9: Run graph and route tests to verify pass**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_graph_threading tests.test_chat_stream_conversations -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```powershell
git add moss_backend/requirements.txt moss_backend/app/agent/state.py moss_backend/app/agent/graph.py moss_backend/app/main.py moss_backend/tests/test_graph_threading.py
git commit -m "feat: persist chat threads with sqlite checkpoints"
```

Expected: commit succeeds. If `.git/index.lock` permission is denied, record it and keep the working tree changes.

---

### Task 4: Persist Current Conversation Id In The Frontend

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add conversation state near session state**

In `index.html`, inside `setup()`, directly after the existing session id initialization:

```javascript
            const currentConversationId = ref(localStorage.getItem('moss-current-conversation-id') || '');

            const setCurrentConversationId = (conversationId) => {
                if (!conversationId) return;
                currentConversationId.value = conversationId;
                localStorage.setItem('moss-current-conversation-id', conversationId);
            };
```

- [ ] **Step 2: Send `conversation_id` when present**

In `sendMessage()`, replace the `JSON.stringify` body object with:

```javascript
                        body: JSON.stringify({
                            session_id: sessionId,
                            ...(currentConversationId.value ? { conversation_id: currentConversationId.value } : {}),
                            user_input: currentInput,
                            focus_element_id: requestAnchors.focusElementId,
                            focus_block_id: requestAnchors.focusBlockId,
                            canvas_snapshot: requestAnchors.canvasSnapshot
                        })
```

- [ ] **Step 3: Handle the SSE `conversation` event**

Inside the `readEventStream(response, async ({ event, data }) => { ... })` handler, before `chat_chunk` handling, add:

```javascript
                        if (event === 'conversation' && data.conversation_id) {
                            setCurrentConversationId(data.conversation_id);
                        }
```

- [ ] **Step 4: Return conversation state from setup for Vue devtools visibility**

Near the end of `setup()`, add these to the returned object:

```javascript
                currentConversationId,
                setCurrentConversationId,
```

- [ ] **Step 5: Manual frontend verification**

Run the backend:

```powershell
cd moss_backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Manual checks:

- Clear `localStorage.removeItem('moss-current-conversation-id')` in browser devtools.
- Send one message.
- Confirm localStorage contains a value starting with `conv-`.
- Refresh the page.
- Send another message.
- Confirm the Network request payload includes the same `conversation_id`.
- Clear `moss-current-conversation-id` and send a third message.
- Confirm the new Network response includes `event: conversation` with a different `conversation_id`.

- [ ] **Step 6: Commit**

Run:

```powershell
git add index.html
git commit -m "feat: persist active conversation in frontend"
```

Expected: commit succeeds. If `.git/index.lock` permission is denied, record it and keep the working tree changes.

---

### Task 5: Document The C-lite Contract

**Files:**
- Modify: `moss_backend/README.md`

- [ ] **Step 1: Update backend configuration docs**

In the configuration block of `moss_backend/README.md`, add:

```env
CONVERSATION_METADATA_DB=
LANGGRAPH_CHECKPOINT_DB=
```

Below the block, add:

```markdown
When omitted, `CONVERSATION_METADATA_DB` resolves to `storage/conversations.sqlite3` and `LANGGRAPH_CHECKPOINT_DB` resolves to `storage/langgraph_checkpoints.sqlite3`.
```

- [ ] **Step 2: Update chat-stream payload docs**

In the `POST /api/v1/chat-stream` payload example, add:

```json
  "conversation_id": "conv-clientGenerated123",
```

Below the payload example, add:

```markdown
`conversation_id` is optional. If omitted, the backend creates a conversation for the fixed development user `test-user` and uses that id as the LangGraph `thread_id`. `session_id` remains a browser/session marker and does not control thread recovery.
```

- [ ] **Step 3: Update SSE event docs**

Add `conversation` to the SSE event list:

```markdown
- `conversation`: emitted when the backend creates conversation metadata; data includes `conversation_id` and `user_id`.
```

Add example data:

```json
{
  "conversation_id": "conv-clientGenerated123",
  "user_id": "test-user"
}
```

- [ ] **Step 4: Commit**

Run:

```powershell
git add moss_backend/README.md
git commit -m "docs: document conversation threading contract"
```

Expected: commit succeeds. If `.git/index.lock` permission is denied, record it and keep the working tree changes.

---

### Task 6: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted C-lite tests**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_conversations tests.test_chat_stream_conversations tests.test_graph_threading -v
```

Expected: PASS.

- [ ] **Step 2: Run existing focused tests that should remain unaffected**

Run:

```powershell
cd moss_backend
python -m unittest tests.test_document_content -v
```

Expected at plan time: the current repository may already fail `test_rejects_unsupported_task_type` because the implementation accepts `document_qa`. If it fails with that exact pre-existing assertion, record it separately from C-lite. If new C-lite tests fail, fix C-lite before proceeding.

- [ ] **Step 3: Run full test discovery**

Run:

```powershell
cd moss_backend
python -m unittest discover -s tests -v
```

Expected at plan time: full discovery may fail because existing tests reference the unfinished `app.agent.skill_runtime` refactor. Record exact failures. C-lite completion requires the three targeted C-lite test modules to pass.

- [ ] **Step 4: Inspect changed files**

Run:

```powershell
git status --short
git diff --stat
```

Expected changed files for C-lite:

```text
index.html
moss_backend/README.md
moss_backend/requirements.txt
moss_backend/app/agent/graph.py
moss_backend/app/agent/state.py
moss_backend/app/api/routes.py
moss_backend/app/api/schemas.py
moss_backend/app/core/config.py
moss_backend/app/main.py
moss_backend/app/services/conversations.py
moss_backend/tests/test_chat_stream_conversations.py
moss_backend/tests/test_conversations.py
moss_backend/tests/test_graph_threading.py
```

Pre-existing dirty files `README.md` and `Blueprint/SYSTEM_DESIGN.md` should remain outside C-lite commits.

- [ ] **Step 5: Manual SSE smoke test**

Start the backend:

```powershell
cd moss_backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another PowerShell terminal, run:

```powershell
$body = @{
  session_id = "session-smoke-a"
  user_input = "hello"
  canvas_snapshot = "<p id='moss-block-1'>hello</p>"
} | ConvertTo-Json
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/chat-stream" -Method Post -ContentType "application/json" -Body $body | Select-Object -ExpandProperty Content
```

Expected output contains:

```text
event: conversation
data: {"conversation_id":"conv-
event: chat_chunk
event: done
```

Use the emitted id for a second request:

```powershell
$body = @{
  session_id = "session-smoke-b"
  conversation_id = "conv-replaceWithEmittedId"
  user_input = "continue"
  canvas_snapshot = "<p id='moss-block-1'>hello</p>"
} | ConvertTo-Json
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/chat-stream" -Method Post -ContentType "application/json" -Body $body | Select-Object -ExpandProperty Content
```

Expected output does not contain `event: conversation` when the id already exists, and still contains `event: chat_chunk` and `event: done`.

- [ ] **Step 6: Final commit if earlier commits were blocked**

If prior commit steps were blocked by `.git` ACLs, do not try destructive git repair commands. Report the permission problem and leave the working tree with only C-lite file changes plus the pre-existing user changes.

