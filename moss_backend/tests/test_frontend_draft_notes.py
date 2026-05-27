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

    def test_library_has_floating_create_note_button(self) -> None:
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertIn(".floating-create-note {", library_html)
        self.assertIn("bottom: calc(1rem + env(safe-area-inset-bottom));", library_html)
        self.assertIn("z-index: 30;", library_html)
        self.assertIn('class="floating-create-note"', library_html)
        self.assertIn('@click="createNote"', library_html)
        self.assertIn(':disabled="isCreating"', library_html)
        self.assertIn('aria-label="新建笔记"', library_html)
        self.assertIn('title="新建笔记"', library_html)
        self.assertIn('fa-solid fa-plus', library_html)
        self.assertIn('fa-solid fa-circle-notch fa-spin', library_html)

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

    def test_library_grid_uses_data_driven_masonry_columns(self) -> None:
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertIn("const columnCount = ref(1);", library_html)
        self.assertIn("const updateColumnCount = () =>", library_html)
        self.assertIn("if (width >= 1536) columnCount.value = 4;", library_html)
        self.assertIn("else if (width >= 1024) columnCount.value = 3;", library_html)
        self.assertIn("else if (width >= 640) columnCount.value = 2;", library_html)
        self.assertIn("const masonryColumns = computed(() =>", library_html)
        self.assertIn("Array.from({ length: columnCount.value }, () => [])", library_html)
        self.assertIn("cols[index % columnCount.value].push(note);", library_html)
        self.assertIn("const handleResize = () =>", library_html)
        self.assertIn("window.addEventListener('resize', handleResize);", library_html)
        self.assertIn("window.removeEventListener('resize', handleResize);", library_html)
        self.assertIn("masonryColumns,", library_html)

        self.assertIn('v-if="viewMode === \'grid\'" class="flex gap-5 items-start"', library_html)
        self.assertIn('v-for="(col, colIndex) in masonryColumns"', library_html)
        self.assertIn('v-for="note in col"', library_html)
        self.assertIn('<div v-else class="list-layout">', library_html)

        self.assertNotIn("column-count", library_html)
        self.assertNotIn("break-inside", library_html)
        self.assertNotIn("page-break-inside", library_html)
        self.assertNotIn("margin-bottom: 1.25rem", library_html)

    def test_frontend_contains_note_discussion_switching_hooks(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")
        library_html = (self.repo_root() / "library.html").read_text(encoding="utf-8")

        self.assertIn("const noteConversations = ref([]);", index_html)
        self.assertIn("const loadNoteConversations = async () =>", index_html)
        self.assertIn("const createConversation = async () =>", index_html)
        self.assertIn("const switchConversation = async (conversation) =>", index_html)
        self.assertIn("const startRenameConversation = (conversation) =>", index_html)
        self.assertIn("const saveConversationTitle = async (conversation) =>", index_html)
        self.assertIn("const cancelRenameConversation = () =>", index_html)
        self.assertIn("const showConversationTree = ref(true);", index_html)
        self.assertIn("const conversationTreeStyle = ref(", index_html)
        self.assertIn("const updateConversationTreePosition = () =>", index_html)
        self.assertIn("const toggleConversationTree = async () =>", index_html)
        self.assertIn('ref="conversationTreePanelRef"', index_html)
        self.assertIn(
            "note.active_conversation_id || note.default_conversation_id",
            library_html,
        )

    def test_discussion_menu_supports_inline_conversation_rename(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("const editingConversationId = ref('');", index_html)
        self.assertIn("const renameConversationDraft = ref('');", index_html)
        self.assertIn("@click.stop=\"startRenameConversation(conversation)\"", index_html)
        self.assertIn("fa-regular fa-pen-to-square", index_html)
        self.assertIn("v-if=\"editingConversationId === conversation.conversation_id\"", index_html)
        self.assertIn("v-model=\"renameConversationDraft\"", index_html)
        self.assertIn("@keydown.enter.prevent=\"saveConversationTitle(conversation)\"", index_html)
        self.assertIn("@keydown.esc.prevent=\"cancelRenameConversation\"", index_html)
        self.assertIn("@blur=\"saveConversationTitle(conversation)\"", index_html)
        self.assertIn("@click.stop=\"saveConversationTitle(conversation)\"", index_html)
        self.assertIn("@click.stop=\"cancelRenameConversation\"", index_html)
        self.assertIn("method: 'PATCH'", index_html)
        self.assertIn("body: JSON.stringify({ title })", index_html)

    def test_discussion_menu_closes_from_panel_header_or_title_toggle(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("const showConversationTree = ref(true);", index_html)
        self.assertIn("@click.stop=\"closeConversationTree\"", index_html)
        self.assertIn("closeConversationTree,", index_html)
        self.assertIn("data-composer-conversation-button", index_html)
        self.assertIn(":title=\"showConversationTree ? 'Close discussions' : 'Open discussions'\"", index_html)
        self.assertIn("fa-regular fa-comment text-[12px]", index_html)
        self.assertIn('class="fa-solid fa-minus text-[10px]"', index_html)
        self.assertNotIn('class="fa-solid fa-chevron-up text-[10px]"', index_html)
        self.assertIn('title="收起讨论"', index_html)
        self.assertIn("if (showConversationTree.value) {\n                    closeConversationTree();\n                    return;\n                }", index_html)
        self.assertIn("showConversationTree.value = true;", index_html)
        self.assertNotIn("const handleDocumentPointerDown = (event) =>", index_html)
        self.assertNotIn("document.addEventListener('pointerdown', handleDocumentPointerDown);", index_html)
        self.assertNotIn("document.removeEventListener('pointerdown', handleDocumentPointerDown);", index_html)
        self.assertNotIn("await loadConversationMessages();\n                    await loadNoteConversations();\n                    closeConversationTree();", index_html)

    def test_discussion_menu_uses_modern_rows_instead_of_ascii_tree(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("conversationStatusIcon(conversation)", index_html)
        self.assertIn("fa-regular fa-message text-[11px] shrink-0", index_html)
        self.assertIn("fa-solid fa-message text-[11px] shrink-0", index_html)
        self.assertIn("{{ conversationTitle(conversation) }}", index_html)
        self.assertNotIn("conversation-current-marker-slot", index_html)
        self.assertNotIn('class="rounded-full bg-black shrink-0"', index_html)
        self.assertIn("group w-full flex items-center justify-between px-2 py-1.5 text-sm", index_html)
        self.assertNotIn("const conversationTreeLine = (conversation, index) =>", index_html)
        self.assertNotIn("const conversationTreePrefix = (index) =>", index_html)
        self.assertNotIn("const conversationActiveMarker = (conversation = {}) =>", index_html)
        self.assertNotIn("{{ conversationTreeLine(conversation, index) }}", index_html)
        self.assertNotIn("grid-cols-[", index_html)

    def test_discussion_rows_use_message_icon_state_instead_of_marker_slot(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("conversation-action-slot", index_html)
        self.assertNotIn("conversation-current-marker-slot", index_html)
        self.assertIn("conversationActionIcon(conversation)", index_html)
        self.assertIn("conversationStatusIcon(conversation)", index_html)
        self.assertIn("const conversationStatusIcon = (conversation = {}) =>", index_html)
        self.assertIn("isCurrentConversation(conversation) ? 'fa-solid fa-message text-[11px] shrink-0' : 'fa-regular fa-message text-[11px] shrink-0'", index_html)
        self.assertIn("openConversationMenuId === conversation.conversation_id", index_html)
        self.assertIn("@click.stop=\"toggleConversationMenu(conversation, $event)\"", index_html)
        self.assertIn("fa-solid fa-ellipsis", index_html)
        self.assertIn("fa-solid fa-thumbtack", index_html)
        self.assertNotIn("v-if=\"isCurrentConversation(conversation)\"", index_html)
        self.assertNotIn("class=\"rounded-full bg-black shrink-0\"", index_html)

    def test_discussion_action_menu_uses_blueprint_dropdown_treatment(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn(".conversation-dropdown-menu", index_html)
        self.assertIn("<teleport to=\"body\">", index_html)
        self.assertIn("position: fixed;", index_html)
        self.assertIn("transform-origin: top left;", index_html)
        self.assertIn("class=\"conversation-action-slot relative h-6 w-6", index_html)
        self.assertIn(":data-conversation-menu-trigger=\"conversation.conversation_id\"", index_html)
        self.assertIn(":style=\"conversationMenuStyle\"", index_html)
        self.assertIn("const updateConversationMenuPosition = (conversation = {}, triggerElement = null) =>", index_html)
        self.assertIn("const conversationMenuStyle = ref(", index_html)
        self.assertIn("const desiredLeft = rect.left;", index_html)
        self.assertNotIn("const desiredLeft = rect.right;", index_html)
        self.assertIn("const desiredTop = rect.bottom + 6;", index_html)
        self.assertIn("@pointerdown.stop", index_html)
        self.assertNotIn("right: 0;", index_html)
        self.assertNotIn("left: 100%;", index_html)
        self.assertIn("transform: scale(0.97) translateY(-4px);", index_html)
        self.assertIn("transition: opacity 0.15s cubic-bezier(0.2, 0, 0.2, 1)", index_html)
        self.assertIn(".conversation-dropdown-menu.show", index_html)
        self.assertIn("conversation-menu-item", index_html)
        self.assertIn("toggleConversationPinned(conversation)", index_html)
        self.assertIn("startDeleteConversation(conversation)", index_html)
        self.assertIn("conversation.is_default", index_html)
        self.assertIn("删除", index_html)

    def test_discussion_delete_uses_library_style_confirmation_dialog(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn('v-if="deleteConversationTarget"', index_html)
        self.assertIn(".conversation-delete-dialog-overlay", index_html)
        self.assertIn("z-index: 1200;", index_html)
        self.assertIn('class="conversation-delete-dialog-overlay fixed inset-0 flex items-center justify-center bg-black/20 px-4"', index_html)
        self.assertNotIn('class="fixed inset-0 z-[1200] flex items-center justify-center bg-black/20 px-4"', index_html)
        self.assertIn('class="w-full max-w-sm rounded-lg bg-white border border-gray-100 shadow-xl p-4"', index_html)
        self.assertIn("删除会话", index_html)
        self.assertIn("{{ conversationTitle(deleteConversationTarget) }}", index_html)
        self.assertIn("@click.self=\"cancelDeleteConversation\"", index_html)
        self.assertIn("@click=\"cancelDeleteConversation\"", index_html)
        self.assertIn("@click=\"confirmDeleteConversation\"", index_html)
        self.assertIn("const deleteConversationTarget = ref(null);", index_html)
        self.assertIn("const startDeleteConversation = (conversation) =>", index_html)
        self.assertIn("const cancelDeleteConversation = () =>", index_html)
        self.assertIn("const confirmDeleteConversation = async () =>", index_html)
        self.assertNotIn("window.confirm", index_html)

    def test_discussion_menu_has_floating_panel_treatment_and_animation(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("@keyframes popIn", index_html)
        self.assertIn(".animate-pop-in", index_html)
        self.assertIn("bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl", index_html)
        self.assertIn("class=\"fixed bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl text-gray-800 flex flex-col gap-1 overflow-visible animate-pop-in\"", index_html)
        self.assertIn("box-shadow: 0 8px 30px rgb(0 0 0 / 0.08); padding: 0.375rem;", index_html)
        self.assertIn("border-b border-gray-100 mb-1", index_html)
        self.assertIn("rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-800", index_html)
        self.assertNotIn("font-mono", index_html)

    def test_discussion_tree_positions_between_left_edge_and_editor(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("data-library-header-button", index_html)
        self.assertNotIn("data-library-external-button", index_html)
        self.assertIn('data-library-header-button\n                        ref="libraryButtonRef"', index_html)
        self.assertIn('class="p-2 -ml-2 rounded-lg text-gray-500 hover:bg-white/70 hover:text-gray-800 transition-colors flex items-center justify-center shrink-0"', index_html)
        self.assertNotIn('class="hidden xl:flex fixed top-[2.125rem]', index_html)
        self.assertNotIn(":class=\"isFullScreen ? 'flex' : 'flex xl:hidden'\"", index_html)
        self.assertIn('ref="libraryButtonRef"', index_html)
        self.assertIn("const libraryButtonRef = ref(null);", index_html)
        self.assertIn("const preferredTreeWidth = 236;", index_html)
        self.assertIn("const leftSpace = panelRect ? panelRect.left : window.innerWidth;", index_html)
        self.assertIn("const centeredLeft = (leftSpace - treeWidth) / 2;", index_html)
        self.assertIn("const rawButtonRect = libraryButtonRef.value?.getBoundingClientRect?.();", index_html)
        self.assertIn("const buttonRect = rawButtonRect?.width || rawButtonRect?.height ? rawButtonRect : null;", index_html)
        self.assertIn("const top = buttonRect ? buttonRect.bottom + 14 : fallbackTop;", index_html)
        self.assertNotIn("top: '40%'", index_html)

    def test_header_actions_and_floating_discussion_control_use_requested_layout(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn("const isConversationTreeCompact = ref(false);", index_html)
        self.assertIn("const openConversationTree = async () =>", index_html)
        self.assertIn("const toggleDocumentOutline = () =>", index_html)
        self.assertIn("data-header-toolbar-actions", index_html)
        self.assertIn("data-header-upload-button", index_html)
        self.assertIn("data-header-export-button", index_html)
        self.assertIn("data-header-outline-button", index_html)
        self.assertIn("data-chat-composer-frame", index_html)
        self.assertIn('data-chat-composer-frame class="chat-composer-frame bg-white shadow-[0_4px_20px_rgb(0,0,0,0.05)] border border-gray-200 px-3 py-2 flex flex-col gap-1', index_html)
        self.assertIn("data-chat-composer-input-row", index_html)
        self.assertIn("data-chat-composer-toolbar", index_html)
        self.assertIn("data-composer-conversation-button", index_html)
        self.assertIn("data-composer-send-button", index_html)
        self.assertIn("@click=\"triggerUpload\"", index_html)
        self.assertIn("@click=\"exportDocument('markdown')\"", index_html)
        self.assertIn("@click.stop=\"toggleDocumentOutline\"", index_html)
        self.assertIn("@click.stop=\"toggleConversationTree\"", index_html)
        self.assertIn("{{ currentConversationTitle() }}", index_html)
        self.assertIn("fa-regular fa-comment text-[12px]", index_html)
        self.assertIn("fa-solid fa-list-ul text-[11px]", index_html)
        self.assertIn("fa-solid fa-upload text-[11px]", index_html)
        self.assertIn("fa-solid fa-download text-[11px]", index_html)
        self.assertIn("<span class=\"hidden sm:inline\">Upload</span>", index_html)
        self.assertIn("<span class=\"hidden sm:inline\">Export</span>", index_html)
        self.assertIn("data-header-upload-button\n                        @click=\"triggerUpload\"\n                        class=\"text-xs bg-white border", index_html)
        self.assertIn("data-header-export-button\n                        @click=\"exportDocument('markdown')\"\n                        class=\"text-xs bg-white border", index_html)
        self.assertIn("data-header-outline-button\n                        @click.stop=\"toggleDocumentOutline\"\n                        class=\"text-xs bg-white border", index_html)
        self.assertIn("data-header-upload-button\n                        @click=\"triggerUpload\"\n                        class=\"text-xs bg-white border h-9", index_html)
        self.assertIn("data-header-export-button\n                        @click=\"exportDocument('markdown')\"\n                        class=\"text-xs bg-white border h-9", index_html)
        self.assertIn("data-header-outline-button\n                        @click.stop=\"toggleDocumentOutline\"\n                        class=\"text-xs bg-white border h-9", index_html)
        self.assertNotIn("fa-regular fa-comments text-xs", index_html)
        self.assertNotIn("data-header-discussions-button", index_html)
        self.assertNotIn("data-floating-discussions-button", index_html)
        self.assertNotIn('v-if="!showDocumentOutline"', index_html)
        self.assertNotIn("const conversationTreeButtonStyle = ref(", index_html)
        self.assertNotIn("const outlineButtonStyle = ref(", index_html)
        self.assertNotIn("absolute -top-1 -right-1 min-w-4 h-4 px-1 rounded-full bg-black text-white text-[10px] leading-4 text-center", index_html)
        self.assertIn("const hasExternalLeftSpace = leftSpace >= preferredTreeWidth + pagePadding * 2;", index_html)
        self.assertIn("isConversationTreeCompact.value = !hasExternalLeftSpace;", index_html)
        self.assertIn("const treeLeft = hasExternalLeftSpace", index_html)
        self.assertIn("openConversationTree,", index_html)
        self.assertIn("currentConversationTitle,", index_html)
        self.assertIn("toggleDocumentOutline,", index_html)
        self.assertIn("isConversationTreeCompact,", index_html)

    def test_chat_composer_wraps_long_input_without_covering_history(self) -> None:
        index_html = (self.repo_root() / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="chat-scroll-shell relative w-full max-w-4xl', index_html)
        self.assertIn(".chat-scroll-shell {", index_html)
        self.assertIn("padding-bottom: 14rem;", index_html)
        self.assertIn(".chat-composer-input {", index_html)
        self.assertIn("white-space: pre-wrap;", index_html)
        self.assertIn("overflow-wrap: anywhere;", index_html)
        self.assertIn("max-height: 10rem;", index_html)
        self.assertIn("resize: none;", index_html)
        self.assertIn("ref=\"inputTextareaRef\"", index_html)
        self.assertIn("<textarea v-model=\"inputText\"", index_html)
        self.assertIn("data-chat-composer-frame", index_html)
        self.assertIn("data-chat-composer-input-row", index_html)
        self.assertIn("data-chat-composer-toolbar", index_html)
        self.assertIn("data-composer-conversation-button", index_html)
        self.assertIn("data-composer-send-button", index_html)
        self.assertIn("fa-solid fa-arrow-up text-sm", index_html)
        self.assertIn("{{ currentConversationTitle() }}", index_html)
        self.assertIn("@input=\"adjustInputTextareaHeight\"", index_html)
        self.assertIn("@keydown.enter.exact.prevent=\"sendMessage()\"", index_html)
        self.assertIn("@keydown.enter.shift.stop", index_html)
        self.assertIn("const inputTextareaRef = ref(null);", index_html)
        self.assertIn("const adjustInputTextareaHeight = () =>", index_html)
        self.assertIn("inputTextareaRef.value.style.height = 'auto';", index_html)
        self.assertIn("inputTextareaRef.value.style.height = `${inputTextareaRef.value.scrollHeight}px`;", index_html)
        self.assertNotIn('type="text"\n                   placeholder=', index_html)


if __name__ == "__main__":
    unittest.main()
