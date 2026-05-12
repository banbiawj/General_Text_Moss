from __future__ import annotations

import unittest
from pathlib import Path


class FrontendDraftNoteTests(unittest.TestCase):
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def test_library_new_note_opens_local_draft_without_posting(self) -> None:
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertIn("window.location.href = '/?draft=1'", library_html)
        self.assertNotIn(
            "fetch(apiUrl('/api/v1/notes'), { method: 'POST' })",
            library_html,
        )

    def test_editor_deferred_persistence_for_blank_drafts(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("const isDraftNote = ref(urlParams.get('draft') === '1');", index_html)
        self.assertIn("const isBlankSnapshot = (html) =>", index_html)
        self.assertIn("const ensurePersistedNote = async", index_html)
        self.assertIn(
            "if (!(await ensurePersistedNote(contentHTML.value))) return;",
            index_html,
        )
        self.assertIn("await ensurePersistedNote(requestAnchors.canvasSnapshot, { allowBlank: true });", index_html)


if __name__ == "__main__":
    unittest.main()
