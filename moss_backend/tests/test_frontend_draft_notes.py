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

    def test_frontend_uses_local_tailwind_stylesheet(self) -> None:
        for html_name in ("index.html", "library.html"):
            html = (self.repo_root() / html_name).read_text(encoding="utf-8")

            self.assertIn('<link rel="stylesheet" href="/static/css/tailwind.css">', html)
            self.assertNotIn("https://cdn.tailwindcss.com", html)
            self.assertNotIn("tailwind.config", html)

    def test_library_note_card_actions_are_direct_icon_buttons(self) -> None:
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertNotIn("fa-ellipsis", library_html)
        self.assertNotIn("openMenuNoteId", library_html)
        self.assertNotIn("toggleNoteMenu", library_html)
        self.assertIn("@click.stop=\"togglePinned(note)\"", library_html)
        self.assertIn("@click.stop=\"startRename(note)\"", library_html)
        self.assertIn("@click.stop=\"startDelete(note)\"", library_html)
        self.assertIn('class="absolute top-3 right-3 md:top-4 md:right-4 flex gap-1 z-10"', library_html)
        self.assertNotIn("absolute bottom-3 right-3", library_html)
        self.assertIn("note.pinned_at ? 'opacity-100' : 'opacity-100 md:opacity-0 md:group-hover:opacity-100'", library_html)
        self.assertIn("hover:text-red-600", library_html)
        self.assertIn("leading-snug pr-24 break-words", library_html)
        self.assertNotIn("border-t border-gray-50/50 pr-16", library_html)
        self.assertNotIn("bg-white/90 backdrop-blur-sm shadow-sm border border-gray-100", library_html)

        rename_index = library_html.index('@click.stop="startRename(note)"')
        delete_index = library_html.index('@click.stop="startDelete(note)"')
        pin_index = library_html.index('@click.stop="togglePinned(note)"')
        self.assertLess(rename_index, delete_index)
        self.assertLess(delete_index, pin_index)

    def test_library_dates_and_card_hover_have_recency_cues(self) -> None:
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertIn("transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;", library_html)
        self.assertIn("transform: translateY(-2px);", library_html)
        self.assertIn("const diffMs = Date.now() - date.getTime();", library_html)
        self.assertIn("if (diffMinutes < 1) return '刚刚';", library_html)
        self.assertIn("return `${diffHours}小时前`;", library_html)
        self.assertIn("return '昨天';", library_html)


if __name__ == "__main__":
    unittest.main()
