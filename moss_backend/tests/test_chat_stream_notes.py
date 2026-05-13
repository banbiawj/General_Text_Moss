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

    def test_matching_note_chat_saves_request_snapshot_to_note(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.post_chat(
            {
                "session_id": "session-save",
                "note_id": created.note.note_id,
                "conversation_id": created.default_conversation.conversation_id,
                "user_input": "hello",
                "canvas_snapshot": "<h1>Chat title</h1><p>Chat body</p>",
            }
        )

        self.assertEqual(response.status_code, 200, response.text)
        loaded = self.store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.title, "Chat title")
        self.assertEqual(loaded.preview_text, "Chat title Chat body")
        self.assertEqual(
            loaded.active_conversation_id,
            created.default_conversation.conversation_id,
        )
        self.assertEqual(
            loaded.canvas_snapshot,
            "<h1>Chat title</h1><p>Chat body</p>",
        )

    def test_matching_note_chat_touches_conversation_title(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.post_chat(
            {
                "session_id": "session-title",
                "note_id": created.note.note_id,
                "conversation_id": created.default_conversation.conversation_id,
                "user_input": "Improve the opening",
                "canvas_snapshot": "<p>doc</p>",
            }
        )

        self.assertEqual(response.status_code, 200, response.text)
        conversation = self.store.get_conversation(
            created.default_conversation.conversation_id
        )
        self.assertIsNotNone(conversation)
        assert conversation is not None
        self.assertEqual(conversation.title, "Improve the opening")

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
