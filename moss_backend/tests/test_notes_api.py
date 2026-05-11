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
        self.original_get_conversation_messages = routes.get_conversation_messages
        routes.get_note_store = lambda: self.store
        routes.get_conversation_messages = self.fake_get_conversation_messages

    def tearDown(self) -> None:
        if self.original_note_store_getter is None:
            delattr(routes, "get_note_store")
        else:
            routes.get_note_store = self.original_note_store_getter
        routes.get_conversation_messages = self.original_get_conversation_messages

    async def fake_get_conversation_messages(
        self,
        compiled_graph: Any,
        conversation_id: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "user", "content": f"user:{conversation_id}"},
            {"role": "ai", "content": f"ai:{conversation_id}"},
        ]

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

    def test_get_conversation_messages_returns_note_chat_history(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.request(
            "GET",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{created.default_conversation.conversation_id}/messages"
            ),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["messages"],
            [
                {
                    "role": "user",
                    "content": f"user:{created.default_conversation.conversation_id}",
                },
                {
                    "role": "ai",
                    "content": f"ai:{created.default_conversation.conversation_id}",
                },
            ],
        )

    def test_get_conversation_messages_rejects_mismatched_note(self) -> None:
        first = self.store.create_note(DEFAULT_USER_ID)
        second = self.store.create_note(DEFAULT_USER_ID)

        response = self.request(
            "GET",
            (
                f"/api/v1/notes/{first.note.note_id}/conversations/"
                f"{second.default_conversation.conversation_id}/messages"
            ),
        )

        self.assertEqual(response.status_code, 409)

    def test_get_unknown_note_returns_404(self) -> None:
        response = self.request("GET", "/api/v1/notes/note-missing123")

        self.assertEqual(response.status_code, 404)

    def test_invalid_note_id_returns_422(self) -> None:
        response = self.request("GET", "/api/v1/notes/bad id")

        self.assertEqual(response.status_code, 422)

    def test_library_routes_serve_html(self) -> None:
        with TestClient(app) as client:
            response = client.get("/library")
            response_html = client.get("/library.html")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response_html.status_code, 200)
        self.assertIn("text/html", response_html.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
