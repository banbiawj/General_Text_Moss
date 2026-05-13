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

    def test_frontend_contains_note_discussion_switching_hooks(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertIn("const noteConversations = ref([]);", index_html)
        self.assertIn("const loadNoteConversations = async () =>", index_html)
        self.assertIn("const createConversation = async () =>", index_html)
        self.assertIn("const switchConversation = async (conversation) =>", index_html)
        self.assertIn("const showConversationTree = ref(false);", index_html)
        self.assertIn("const conversationTreeStyle = ref(", index_html)
        self.assertIn("const updateConversationTreePosition = () =>", index_html)
        self.assertIn("const toggleConversationTree = async () =>", index_html)
        self.assertIn('ref="conversationTreePanelRef"', index_html)
        self.assertIn(
            "note.active_conversation_id || note.default_conversation_id",
            library_html,
        )

    def test_discussion_menu_uses_modern_rows_instead_of_ascii_tree(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("fa-regular fa-message text-[11px] shrink-0", index_html)
        self.assertIn("{{ conversationTitle(conversation) }}", index_html)
        self.assertIn('v-if="isCurrentConversation(conversation)"', index_html)
        self.assertIn('class="rounded-full bg-black shrink-0"', index_html)
        self.assertIn('style="width: 0.375rem; height: 0.375rem;"', index_html)
        self.assertIn("group w-full flex items-center justify-between px-2 py-1.5 text-sm", index_html)
        self.assertNotIn("const conversationTreeLine = (conversation, index) =>", index_html)
        self.assertNotIn("const conversationTreePrefix = (index) =>", index_html)
        self.assertNotIn("const conversationActiveMarker = (conversation = {}) =>", index_html)
        self.assertNotIn("{{ conversationTreeLine(conversation, index) }}", index_html)
        self.assertNotIn("grid-cols-[", index_html)

    def test_discussion_menu_has_floating_panel_treatment_and_animation(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("@keyframes popIn", index_html)
        self.assertIn(".animate-pop-in", index_html)
        self.assertIn("bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl", index_html)
        self.assertIn("class=\"fixed bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl text-gray-800 flex flex-col gap-1 overflow-hidden animate-pop-in\"", index_html)
        self.assertIn("box-shadow: 0 8px 30px rgb(0 0 0 / 0.08); padding: 0.375rem;", index_html)
        self.assertIn("border-b border-gray-100 mb-1", index_html)
        self.assertIn("rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-800", index_html)
        self.assertNotIn("font-mono", index_html)

    def test_discussion_tree_positions_between_left_edge_and_editor(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn('ref="libraryButtonRef"', index_html)
        self.assertIn("const libraryButtonRef = ref(null);", index_html)
        self.assertIn("const preferredTreeWidth = 236;", index_html)
        self.assertIn("const leftSpace = panelRect ? panelRect.left : window.innerWidth;", index_html)
        self.assertIn("const centeredLeft = (leftSpace - treeWidth) / 2;", index_html)
        self.assertIn("const buttonRect = libraryButtonRef.value?.getBoundingClientRect?.();", index_html)
        self.assertIn("const top = buttonRect ? buttonRect.bottom + 14 : fallbackTop;", index_html)
        self.assertNotIn("top: '40%'", index_html)


if __name__ == "__main__":
    unittest.main()
