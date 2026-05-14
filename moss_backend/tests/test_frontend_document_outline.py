from __future__ import annotations

import unittest
from pathlib import Path


class FrontendDocumentOutlineTests(unittest.TestCase):
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def index_html(self) -> str:
        return (self.repo_root() / "index.html").read_text(encoding="utf-8")

    def test_index_contains_document_outline_state_and_methods(self) -> None:
        index_html = self.index_html()

        self.assertIn("const documentOutline = ref([]);", index_html)
        self.assertIn("const activeOutlineId = ref('');", index_html)
        self.assertIn("const showDocumentOutline = ref(true);", index_html)
        self.assertIn("const outlineStyle = ref(", index_html)
        self.assertIn("const outlineButtonStyle = ref(", index_html)
        self.assertIn("const outlineScrollSpacerHeight = ref(0);", index_html)
        self.assertIn("const outlinePanelRef = ref(null);", index_html)
        self.assertIn("const documentBodyRef = ref(null);", index_html)
        self.assertIn("let outlineRefreshFrame = 0;", index_html)
        self.assertIn("const ensureDocumentOutlineHeadingIds = () =>", index_html)
        self.assertIn("const refreshDocumentOutline = () =>", index_html)
        self.assertIn("const scheduleDocumentOutlineRefresh = () =>", index_html)
        self.assertIn("const updateActiveOutline = () =>", index_html)
        self.assertIn("const updateOutlineLayout = () =>", index_html)
        self.assertIn("const scrollToOutlineItem = async (item) =>", index_html)
        self.assertIn("const openDocumentOutline = () =>", index_html)
        self.assertIn("const closeDocumentOutline = () =>", index_html)
        self.assertIn("const ensureOutlineScrollRoom = async (container, targetTop) =>", index_html)
        self.assertNotIn("const showOutlineDrawer = ref(false);", index_html)
        self.assertNotIn("const outlineMode = ref(", index_html)

    def test_index_renders_default_outline_with_manual_toggle(self) -> None:
        index_html = self.index_html()

        self.assertIn('ref="outlinePanelRef"', index_html)
        self.assertIn('ref="documentBodyRef"', index_html)
        self.assertIn('v-if="showDocumentOutline"', index_html)
        self.assertIn('v-if="!showDocumentOutline"', index_html)
        self.assertIn('@click.stop="openDocumentOutline"', index_html)
        self.assertIn('@click.stop="closeDocumentOutline"', index_html)
        self.assertIn('@click="scrollToOutlineItem(item)"', index_html)
        self.assertIn('v-for="item in documentOutline"', index_html)
        self.assertIn("outlineIndentClass(item.level)", index_html)
        self.assertIn("isActiveOutlineItem(item)", index_html)
        self.assertIn('aria-hidden="true"', index_html)
        self.assertIn(":style=\"{ height: outlineScrollSpacerHeight + 'px' }\"", index_html)
        self.assertIn("暂无目录", index_html)
        self.assertIn("目录", index_html)
        self.assertNotIn("showOutlineDrawer", index_html)
        self.assertNotIn("outlineMode ===", index_html)
        self.assertNotIn('class="fixed inset-0 z-[1100]"', index_html)

    def test_document_outline_uses_discussion_panel_visual_language(self) -> None:
        index_html = self.index_html()

        self.assertIn("bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl", index_html)
        self.assertIn("box-shadow: 0 8px 30px rgb(0 0 0 / 0.08); padding: 0.375rem;", index_html)
        self.assertIn("border-b border-gray-100 mb-1", index_html)
        self.assertIn("rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-800", index_html)
        self.assertIn("bg-gray-100 text-gray-900 font-medium", index_html)
        self.assertNotIn("font-mono", index_html)

    def test_document_outline_layout_does_not_auto_collapse_or_move_on_fullscreen(self) -> None:
        index_html = self.index_html()

        self.assertIn("const preferredOutlineWidth = 236;", index_html)
        self.assertIn("const rightSpace = Math.max(0, window.innerWidth - panelRect.right);", index_html)
        self.assertIn("const hasExternalRightSpace = rightSpace >= outlineWidth + pagePadding * 2;", index_html)
        self.assertIn("const left = hasExternalRightSpace", index_html)
        self.assertIn("panelRect.right + (rightSpace - outlineWidth) / 2", index_html)
        self.assertIn("window.innerWidth - outlineWidth - pagePadding", index_html)
        self.assertNotIn("const requiredFloatingSpace = 260;", index_html)
        self.assertNotIn("outlineMode.value = canFloat ? 'floating' : 'drawer';", index_html)
        self.assertNotIn("const anchorRect = isFullScreen.value && bodyRect ? bodyRect : panelRect;", index_html)

    def test_document_outline_scroll_uses_container_relative_position(self) -> None:
        index_html = self.index_html()

        self.assertIn("const scrollToOutlineItem = async (item) =>", index_html)
        self.assertIn("const containerRect = container.getBoundingClientRect();", index_html)
        self.assertIn("const targetTop = container.scrollTop + target.getBoundingClientRect().top - containerRect.top - 24;", index_html)
        self.assertIn("await ensureOutlineScrollRoom(container, targetTop);", index_html)
        self.assertIn("top: Math.max(0, targetTop),", index_html)
        self.assertNotIn("target.offsetTop - 24", index_html)

    def test_document_outline_adds_scroll_room_for_short_documents(self) -> None:
        index_html = self.index_html()

        self.assertIn("const missingHeight = targetTop + container.clientHeight - container.scrollHeight;", index_html)
        self.assertIn("if (missingHeight <= 0) return;", index_html)
        self.assertIn("outlineScrollSpacerHeight.value += Math.ceil(missingHeight) + 24;", index_html)
        self.assertIn("await nextTick();", index_html)
        self.assertIn("outlineScrollSpacerHeight.value = 0;", index_html)

    def test_document_outline_is_wired_to_editor_updates_and_scroll(self) -> None:
        index_html = self.index_html()

        self.assertIn("scheduleDocumentOutlineRefresh();", index_html)
        self.assertIn("documentContainer.addEventListener('scroll', updateActiveOutline, { passive: true });", index_html)
        self.assertIn("documentContainer?.removeEventListener('scroll', updateActiveOutline);", index_html)
        self.assertIn("updateOutlineLayout();", index_html)
        self.assertIn("updateActiveOutline();", index_html)
        self.assertIn("if (outlineRefreshFrame) window.cancelAnimationFrame(outlineRefreshFrame);", index_html)


if __name__ == "__main__":
    unittest.main()
