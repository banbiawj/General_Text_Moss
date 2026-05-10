from __future__ import annotations

import shutil
import time
import unittest
from pathlib import Path
from uuid import uuid4

from app.services.conversations import (
    DEFAULT_USER_ID,
    ConversationStore,
    InvalidConversationId,
    is_valid_conversation_id,
)


class ConversationStoreTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_dir = Path.cwd() / ".tmp" / "tests" / f"conversations-{uuid4().hex}"
        temp_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return temp_dir

    def test_missing_conversation_id_creates_record_for_default_user(self) -> None:
        temp_dir = self.make_temp_dir()
        store = ConversationStore(temp_dir / "conversations.sqlite3")

        result = store.resolve(user_id=DEFAULT_USER_ID, conversation_id=None)

        self.assertTrue(result.created)
        self.assertTrue(result.record.conversation_id.startswith("conv-"))
        self.assertEqual(result.record.user_id, "test-user")
        self.assertEqual(result.record.title, "Untitled conversation")
        self.assertIsNotNone(store.get(result.record.conversation_id))

    def test_existing_conversation_id_is_reused_and_touched(self) -> None:
        temp_dir = self.make_temp_dir()
        store = ConversationStore(temp_dir / "conversations.sqlite3")
        created = store.resolve(DEFAULT_USER_ID, "conv-existing_123")
        original_updated_at = created.record.updated_at
        time.sleep(0.01)

        reused = store.resolve(DEFAULT_USER_ID, "conv-existing_123")

        self.assertFalse(reused.created)
        self.assertEqual(reused.record.conversation_id, "conv-existing_123")
        self.assertGreater(reused.record.updated_at, original_updated_at)

    def test_valid_unknown_conversation_id_creates_record(self) -> None:
        temp_dir = self.make_temp_dir()
        store = ConversationStore(temp_dir / "conversations.sqlite3")

        result = store.resolve(DEFAULT_USER_ID, "conv-clientCreated123")

        self.assertTrue(result.created)
        self.assertEqual(result.record.conversation_id, "conv-clientCreated123")
        self.assertEqual(result.record.user_id, DEFAULT_USER_ID)

    def test_invalid_conversation_id_is_rejected(self) -> None:
        invalid_ids = ["abc", "conv-", "conv-中文", "conv-short", "conv-has space"]

        for conversation_id in invalid_ids:
            with self.subTest(conversation_id=conversation_id):
                self.assertFalse(is_valid_conversation_id(conversation_id))

        temp_dir = self.make_temp_dir()
        store = ConversationStore(temp_dir / "conversations.sqlite3")

        with self.assertRaises(InvalidConversationId):
            store.resolve(DEFAULT_USER_ID, "conv-has space")

    def test_foreign_user_cannot_reuse_existing_conversation(self) -> None:
        temp_dir = self.make_temp_dir()
        store = ConversationStore(temp_dir / "conversations.sqlite3")
        store.resolve("test-user", "conv-ownedByUser1")

        with self.assertRaises(PermissionError):
            store.resolve("another-user", "conv-ownedByUser1")


if __name__ == "__main__":
    unittest.main()
