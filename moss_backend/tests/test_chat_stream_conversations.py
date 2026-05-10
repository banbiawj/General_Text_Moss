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
        self.temp_dir = (
            Path.cwd() / ".tmp" / "tests" / f"chat-conversations-{uuid4().hex}"
        )
        self.temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.store = ConversationStore(self.temp_dir / "conversations.sqlite3")
        self.original_store_getter = routes.get_conversation_store
        self.original_stream = routes.stream_agent_events
        routes.get_conversation_store = lambda: self.store
        routes.stream_agent_events = fake_stream_agent_events

    def tearDown(self) -> None:
        routes.get_conversation_store = self.original_store_getter
        routes.stream_agent_events = self.original_stream

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
