from __future__ import annotations

import shutil
import sqlite3
import time
import unittest
import warnings
from pathlib import Path
from uuid import uuid4

from app.services.conversations import DEFAULT_CONVERSATION_TITLE, DEFAULT_USER_ID
from app.services.notes import (
    DEFAULT_NOTE_TITLE,
    DEFAULT_THREAD_TITLE,
    InvalidNoteId,
    NoteStore,
    extract_note_metadata,
    is_valid_note_id,
)


class NoteStoreTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_dir = Path.cwd() / ".tmp" / "tests" / f"notes-{uuid4().hex}"
        temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return temp_dir

    def write_checkpoint_snapshot(
        self,
        db_path: Path,
        conversation_id: str,
        checkpoint_id: str,
        snapshot: str,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

            serializer = JsonPlusSerializer()
        payload_type, payload_value = serializer.dumps_typed(snapshot)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT,
                value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO writes (
                thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
                channel, type, value
            )
            VALUES (?, '', ?, 'task-1', 0, 'canvas_snapshot', ?, ?)
            """,
            (conversation_id, checkpoint_id, payload_type, payload_value),
        )
        conn.commit()
        conn.close()

    def test_create_note_creates_default_conversation(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        self.assertTrue(created.note.note_id.startswith("note-"))
        self.assertTrue(created.default_conversation.conversation_id.startswith("conv-"))
        self.assertEqual(created.note.title, DEFAULT_NOTE_TITLE)
        self.assertEqual(created.note.canvas_snapshot, "")
        self.assertEqual(created.note.preview_text, "")
        self.assertEqual(created.default_conversation.note_id, created.note.note_id)
        self.assertEqual(created.default_conversation.title, DEFAULT_THREAD_TITLE)
        self.assertTrue(created.default_conversation.is_default)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(
            loaded.default_conversation_id,
            created.default_conversation.conversation_id,
        )
        self.assertEqual(
            loaded.active_conversation_id,
            created.default_conversation.conversation_id,
        )
        self.assertEqual(
            loaded.last_opened_conversation_id,
            created.default_conversation.conversation_id,
        )

    def test_list_notes_returns_summaries_without_snapshot_ordered_by_updated_at(
        self,
    ) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        first = store.create_note(DEFAULT_USER_ID)
        time.sleep(0.01)
        second = store.create_note(DEFAULT_USER_ID)
        store.save_snapshot(
            DEFAULT_USER_ID,
            first.note.note_id,
            "<h1>First title</h1><p>alpha</p>",
        )
        time.sleep(0.01)
        store.save_snapshot(
            DEFAULT_USER_ID,
            second.note.note_id,
            "<h1>Second title</h1><p>beta</p>",
        )
        notes = store.list_notes(DEFAULT_USER_ID)
        self.assertEqual(
            [note.note_id for note in notes],
            [second.note.note_id, first.note.note_id],
        )
        self.assertEqual(notes[0].title, "Second title")
        self.assertEqual(notes[0].preview_text, "Second title beta")
        self.assertEqual(
            notes[0].active_conversation_id,
            second.default_conversation.conversation_id,
        )
        self.assertFalse(hasattr(notes[0], "canvas_snapshot"))

    def test_save_snapshot_updates_title_preview_and_timestamp(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        original_updated_at = created.note.updated_at
        time.sleep(0.01)
        saved = store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            '<h2 id="a">Draft title</h2><p>Hello <strong>world</strong>.</p>',
        )
        self.assertEqual(saved.title, "Draft title")
        self.assertEqual(saved.preview_text, "Draft title Hello world.")
        self.assertGreater(saved.updated_at, original_updated_at)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(
            loaded.canvas_snapshot,
            '<h2 id="a">Draft title</h2><p>Hello <strong>world</strong>.</p>',
        )

    def test_save_snapshot_is_idempotent_for_unchanged_snapshot(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        snapshot = "<h1>Stable title</h1><p>Stable body</p>"
        saved = store.save_snapshot(DEFAULT_USER_ID, created.note.note_id, snapshot)
        time.sleep(0.01)

        repeated = store.save_snapshot(DEFAULT_USER_ID, created.note.note_id, snapshot)

        self.assertEqual(repeated.updated_at, saved.updated_at)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.updated_at, saved.updated_at)

    def test_extract_note_metadata_prefers_heading_then_text_then_default(self) -> None:
        self.assertEqual(
            extract_note_metadata("<p>Lead paragraph</p><h1>Later</h1>").title,
            "Later",
        )
        self.assertEqual(
            extract_note_metadata("<h1>First heading</h1><h2>Second heading</h2>").title,
            "First heading",
        )
        self.assertEqual(
            extract_note_metadata("<p>Only paragraph text here</p>").title,
            "Only paragraph text here",
        )
        self.assertEqual(extract_note_metadata("<p> </p>").title, DEFAULT_NOTE_TITLE)

    def test_invalid_note_id_is_rejected(self) -> None:
        invalid_ids = ["abc", "note-", "note-short", "note-has space", "note-中文"]
        for note_id in invalid_ids:
            with self.subTest(note_id=note_id):
                self.assertFalse(is_valid_note_id(note_id))
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        with self.assertRaises(InvalidNoteId):
            store.get_note(DEFAULT_USER_ID, "note-has space")

    def test_verify_conversation_for_note_rejects_mismatch(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        first = store.create_note(DEFAULT_USER_ID)
        second = store.create_note(DEFAULT_USER_ID)
        verified = store.verify_conversation_for_note(
            DEFAULT_USER_ID,
            first.note.note_id,
            first.default_conversation.conversation_id,
        )
        self.assertEqual(verified.note_id, first.note.note_id)
        with self.assertRaises(ValueError):
            store.verify_conversation_for_note(
                DEFAULT_USER_ID,
                first.note.note_id,
                second.default_conversation.conversation_id,
            )

    def test_create_conversation_for_note_adds_non_default_discussion(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)

        discussion = store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        self.assertEqual(discussion.note_id, created.note.note_id)
        self.assertFalse(discussion.is_default)
        conversations = store.list_note_conversations(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.assertEqual(len(conversations), 2)
        self.assertEqual(conversations[0].conversation_id, created.default_conversation.conversation_id)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.active_conversation_id, discussion.conversation_id)
        self.assertEqual(loaded.last_opened_conversation_id, discussion.conversation_id)

    def test_rename_conversation_updates_attached_discussion_title(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        discussion = store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        renamed = store.rename_conversation(
            DEFAULT_USER_ID,
            created.note.note_id,
            discussion.conversation_id,
            "  Polished structure  ",
        )

        self.assertEqual(renamed.title, "Polished structure")
        conversations = store.list_note_conversations(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.assertIn("Polished structure", [item.title for item in conversations])

    def test_pin_conversation_moves_it_above_unpinned_discussions(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        first = store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        time.sleep(0.01)
        second = store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )

        pinned = store.update_conversation(
            DEFAULT_USER_ID,
            created.note.note_id,
            first.conversation_id,
            pinned=True,
        )

        self.assertIsNotNone(pinned.pinned_at)
        conversations = store.list_note_conversations(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.assertEqual(conversations[0].conversation_id, first.conversation_id)
        self.assertEqual(conversations[1].conversation_id, created.default_conversation.conversation_id)
        self.assertEqual(conversations[2].conversation_id, second.conversation_id)

    def test_delete_conversation_soft_deletes_and_selects_default_fallback(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        discussion = store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.assertEqual(
            store.get_note(DEFAULT_USER_ID, created.note.note_id).active_conversation_id,
            discussion.conversation_id,
        )

        deleted = store.delete_conversation(
            DEFAULT_USER_ID,
            created.note.note_id,
            discussion.conversation_id,
        )

        self.assertEqual(deleted.conversation_id, discussion.conversation_id)
        self.assertIsNotNone(deleted.deleted_at)
        conversations = store.list_note_conversations(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        self.assertEqual(
            [item.conversation_id for item in conversations],
            [created.default_conversation.conversation_id],
        )
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(
            loaded.active_conversation_id,
            created.default_conversation.conversation_id,
        )

    def test_delete_default_conversation_is_rejected(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)

        with self.assertRaises(ValueError):
            store.delete_conversation(
                DEFAULT_USER_ID,
                created.note.note_id,
                created.default_conversation.conversation_id,
            )

    def test_mark_conversation_opened_does_not_touch_note_updated_at(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        discussion = store.create_conversation_for_note(
            DEFAULT_USER_ID,
            created.note.note_id,
        )
        saved = store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Stable note</h1>",
        )

        opened = store.mark_conversation_opened(
            DEFAULT_USER_ID,
            created.note.note_id,
            created.default_conversation.conversation_id,
        )

        self.assertEqual(opened.conversation_id, created.default_conversation.conversation_id)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.active_conversation_id, created.default_conversation.conversation_id)
        self.assertEqual(loaded.updated_at, saved.updated_at)
        self.assertNotEqual(discussion.conversation_id, loaded.active_conversation_id)

    def test_touch_conversation_sets_title_from_first_user_message(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)

        touched = store.touch_conversation(
            created.default_conversation.conversation_id,
            title_hint="  Help me improve the opening paragraph  ",
        )

        self.assertEqual(touched.title, "Help me improve the opening paragraph")

    def test_legacy_conversations_are_migrated_to_notes(self) -> None:
        db_path = self.make_temp_dir() / "metadata.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "conv-legacy123",
                DEFAULT_USER_ID,
                "Legacy conversation",
                "2026-05-11T00:00:00+00:00",
                "2026-05-11T00:01:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        store = NoteStore(db_path)
        notes = store.list_notes(DEFAULT_USER_ID)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Legacy conversation")
        loaded = store.get_note(DEFAULT_USER_ID, notes[0].note_id)
        self.assertEqual(loaded.default_conversation_id, "conv-legacy123")
        conversation = store.get_conversation("conv-legacy123")
        self.assertIsNotNone(conversation)
        assert conversation is not None
        self.assertEqual(conversation.note_id, notes[0].note_id)
        self.assertTrue(conversation.is_default)

    def test_legacy_conversation_migration_restores_checkpoint_snapshot(
        self,
    ) -> None:
        temp_dir = self.make_temp_dir()
        db_path = temp_dir / "metadata.sqlite3"
        checkpoint_db_path = temp_dir / "langgraph_checkpoints.sqlite3"
        conversation_id = "conv-legacybody123"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                DEFAULT_USER_ID,
                DEFAULT_CONVERSATION_TITLE,
                "2026-05-11T00:00:00+00:00",
                "2026-05-11T00:01:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        self.write_checkpoint_snapshot(
            checkpoint_db_path,
            conversation_id,
            "checkpoint-1",
            "<h1>Checkpoint title</h1><p>Checkpoint body</p>",
        )

        store = NoteStore(db_path, checkpoint_db_path=checkpoint_db_path)

        notes = store.list_notes(DEFAULT_USER_ID)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Checkpoint title")
        self.assertEqual(notes[0].preview_text, "Checkpoint title Checkpoint body")
        loaded = store.get_note(DEFAULT_USER_ID, notes[0].note_id)
        self.assertEqual(
            loaded.canvas_snapshot,
            "<h1>Checkpoint title</h1><p>Checkpoint body</p>",
        )

    def test_empty_migrated_note_is_hydrated_from_latest_checkpoint_snapshot(
        self,
    ) -> None:
        temp_dir = self.make_temp_dir()
        db_path = temp_dir / "metadata.sqlite3"
        checkpoint_db_path = temp_dir / "langgraph_checkpoints.sqlite3"
        conversation_id = "conv-hydrate123"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                note_id TEXT,
                is_default INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE notes (
                note_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                preview_text TEXT NOT NULL,
                canvas_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO notes (
                note_id, user_id, title, preview_text, canvas_snapshot,
                created_at, updated_at
            )
            VALUES (
                'note-existing123', ?, ?, '', '',
                '2026-05-11T00:00:00+00:00',
                '2026-05-11T00:00:00+00:00'
            )
            """,
            (DEFAULT_USER_ID, DEFAULT_NOTE_TITLE),
        )
        conn.execute(
            """
            INSERT INTO conversations (
                conversation_id, user_id, title, created_at, updated_at,
                note_id, is_default
            )
            VALUES (
                ?, ?, ?, '2026-05-11T00:00:00+00:00',
                '2026-05-11T00:00:00+00:00', 'note-existing123', 1
            )
            """,
            (conversation_id, DEFAULT_USER_ID, DEFAULT_CONVERSATION_TITLE),
        )
        conn.commit()
        conn.close()
        self.write_checkpoint_snapshot(
            checkpoint_db_path,
            conversation_id,
            "checkpoint-1",
            "<p>Older body</p>",
        )
        self.write_checkpoint_snapshot(
            checkpoint_db_path,
            conversation_id,
            "checkpoint-2",
            "<h1>Recovered title</h1><p>Recovered body</p>",
        )

        store = NoteStore(db_path, checkpoint_db_path=checkpoint_db_path)

        loaded = store.get_note(DEFAULT_USER_ID, "note-existing123")
        self.assertEqual(loaded.title, "Recovered title")
        self.assertEqual(loaded.preview_text, "Recovered title Recovered body")
        self.assertEqual(
            loaded.canvas_snapshot,
            "<h1>Recovered title</h1><p>Recovered body</p>",
        )

    def test_note_management_fields_are_added_to_existing_notes_table(self) -> None:
        db_path = self.make_temp_dir() / "metadata.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.execute(
            """
            CREATE TABLE notes (
                note_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                preview_text TEXT NOT NULL,
                canvas_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                note_id TEXT,
                is_default INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

        NoteStore(db_path)

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
        conn.close()
        self.assertIn("display_title", columns)
        self.assertIn("deleted_at", columns)
        self.assertIn("pinned_at", columns)
        self.assertIn("last_opened_conversation_id", columns)

    def test_update_note_display_title_does_not_modify_snapshot_or_updated_at(
        self,
    ) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        saved = store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Body title</h1><p>Body text</p>",
        )

        updated = store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Library name",
        )

        self.assertEqual(updated.display_title, "Library name")
        self.assertEqual(updated.effective_title, "Library name")
        self.assertEqual(updated.title, "Body title")
        self.assertEqual(updated.updated_at, saved.updated_at)
        loaded = store.get_note(DEFAULT_USER_ID, created.note.note_id)
        self.assertEqual(loaded.canvas_snapshot, "<h1>Body title</h1><p>Body text</p>")

    def test_clearing_display_title_restores_effective_auto_title(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        store.save_snapshot(
            DEFAULT_USER_ID,
            created.note.note_id,
            "<h1>Auto title</h1><p>Body text</p>",
        )
        store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="Manual title",
        )

        updated = store.update_note(
            DEFAULT_USER_ID,
            created.note.note_id,
            display_title="   ",
        )

        self.assertIsNone(updated.display_title)
        self.assertEqual(updated.effective_title, "Auto title")

    def test_pinned_notes_sort_before_unpinned_without_touching_updated_at(
        self,
    ) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        first = store.create_note(DEFAULT_USER_ID)
        time.sleep(0.01)
        second = store.create_note(DEFAULT_USER_ID)
        first_saved = store.save_snapshot(
            DEFAULT_USER_ID,
            first.note.note_id,
            "<h1>First</h1>",
        )
        time.sleep(0.01)
        second_saved = store.save_snapshot(
            DEFAULT_USER_ID,
            second.note.note_id,
            "<h1>Second</h1>",
        )

        pinned = store.update_note(DEFAULT_USER_ID, first.note.note_id, pinned=True)
        notes = store.list_notes(DEFAULT_USER_ID)

        self.assertEqual(
            [note.note_id for note in notes],
            [first.note.note_id, second.note.note_id],
        )
        self.assertIsNotNone(pinned.pinned_at)
        self.assertEqual(pinned.updated_at, first_saved.updated_at)
        self.assertGreater(second_saved.updated_at, first_saved.updated_at)

    def test_unpin_clears_pinned_at(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        store.update_note(DEFAULT_USER_ID, created.note.note_id, pinned=True)

        updated = store.update_note(DEFAULT_USER_ID, created.note.note_id, pinned=False)

        self.assertIsNone(updated.pinned_at)

    def test_soft_delete_hides_note_from_list_and_get(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)

        deleted = store.delete_note(DEFAULT_USER_ID, created.note.note_id)

        self.assertEqual(deleted.note_id, created.note.note_id)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(store.list_notes(DEFAULT_USER_ID), [])
        with self.assertRaises(KeyError):
            store.get_note(DEFAULT_USER_ID, created.note.note_id)

    def test_soft_delete_is_idempotent(self) -> None:
        store = NoteStore(self.make_temp_dir() / "metadata.sqlite3")
        created = store.create_note(DEFAULT_USER_ID)
        deleted = store.delete_note(DEFAULT_USER_ID, created.note.note_id)

        repeated = store.delete_note(DEFAULT_USER_ID, created.note.note_id)

        self.assertEqual(repeated.deleted_at, deleted.deleted_at)


if __name__ == "__main__":
    unittest.main()
