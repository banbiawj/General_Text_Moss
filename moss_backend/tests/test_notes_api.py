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
        self.assertEqual(
            loaded.active_conversation_id,
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
        self.assertEqual(
            notes[0]["active_conversation_id"],
            created.default_conversation.conversation_id,
        )
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
        self.assertEqual(
            payload["active_conversation_id"],
            created.default_conversation.conversation_id,
        )
        self.assertEqual(
            payload["last_opened_conversation_id"],
            created.default_conversation.conversation_id,
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

    def test_list_note_conversations_returns_attached_discussions(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        discussion = self.store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        response = self.request(
            "GET",
            f"/api/v1/notes/{created.note.note_id}/conversations",
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["active_conversation_id"], discussion.conversation_id)
        self.assertEqual(
            [item["conversation_id"] for item in payload["conversations"]],
            [
                created.default_conversation.conversation_id,
                discussion.conversation_id,
            ],
        )
        self.assertTrue(payload["conversations"][0]["is_default"])
        self.assertFalse(payload["conversations"][1]["is_default"])

    def test_create_note_conversation_adds_non_default_discussion(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.request(
            "POST",
            f"/api/v1/notes/{created.note.note_id}/conversations",
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["note_id"], created.note.note_id)
        self.assertFalse(payload["is_default"])
        loaded = self.store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.active_conversation_id, payload["conversation_id"])

    def test_patch_note_conversation_renames_discussion(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        discussion = self.store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        response = self.request(
            "PATCH",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{discussion.conversation_id}"
            ),
            json={"title": "  Structure rewrite  "},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["conversation_id"], discussion.conversation_id)
        self.assertEqual(payload["title"], "Structure rewrite")
        conversations = self.store.list_note_conversations(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.assertIn("Structure rewrite", [item.title for item in conversations])

    def test_patch_note_conversation_toggles_pin(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        discussion = self.store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        response = self.request(
            "PATCH",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{discussion.conversation_id}"
            ),
            json={"pinned": True},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["conversation_id"], discussion.conversation_id)
        self.assertIsNotNone(payload["pinned_at"])
        list_response = self.request(
            "GET",
            f"/api/v1/notes/{created.note.note_id}/conversations",
        )
        conversations = list_response.json()["conversations"]
        self.assertEqual(conversations[0]["conversation_id"], discussion.conversation_id)
        self.assertIsNotNone(conversations[0]["pinned_at"])

        unpin_response = self.request(
            "PATCH",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{discussion.conversation_id}"
            ),
            json={"pinned": False},
        )

        self.assertEqual(unpin_response.status_code, 200, unpin_response.text)
        self.assertIsNone(unpin_response.json()["pinned_at"])

    def test_delete_note_conversation_hides_it_and_keeps_default(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        discussion = self.store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        response = self.request(
            "DELETE",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{discussion.conversation_id}"
            ),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["conversation_id"], discussion.conversation_id)
        self.assertIsNotNone(response.json()["deleted_at"])
        list_response = self.request(
            "GET",
            f"/api/v1/notes/{created.note.note_id}/conversations",
        )
        conversations = list_response.json()["conversations"]
        self.assertEqual(
            [item["conversation_id"] for item in conversations],
            [created.default_conversation.conversation_id],
        )
        loaded = self.store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(
            loaded.active_conversation_id,
            created.default_conversation.conversation_id,
        )

    def test_delete_default_note_conversation_returns_conflict(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)

        response = self.request(
            "DELETE",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{created.default_conversation.conversation_id}"
            ),
        )

        self.assertEqual(response.status_code, 409)

    def test_get_conversation_messages_marks_discussion_active(self) -> None:
        created = self.store.create_note(DEFAULT_USER_ID)
        discussion = self.store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.store.mark_conversation_opened(
            DEFAULT_USER_ID,
            created.note.note_id,
            created.default_conversation.conversation_id,
        )

        response = self.request(
            "GET",
            (
                f"/api/v1/notes/{created.note.note_id}/conversations/"
                f"{discussion.conversation_id}/messages"
            ),
        )

        self.assertEqual(response.status_code, 200, response.text)
        loaded = self.store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.active_conversation_id, discussion.conversation_id)

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

    def test_static_tailwind_css_is_served(self) -> None:
        with TestClient(app) as client:
            response = client.get("/static/css/tailwind.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers["content-type"])
        self.assertIn("--font-sans", response.text)


if __name__ == "__main__":
    unittest.main()
