# Index Document Outline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a document outline to `index.html` that is a right-side floating panel on wide screens and a right-side drawer on constrained screens.

**Architecture:** Keep the feature entirely in the existing Vue single-file page. Extract `h1`/`h2`/`h3` headings from the rendered Tiptap document DOM, keep a small outline state model, position the panel using real measured available space, and reuse the current discussion selector visual treatment. Add focused static frontend tests so later changes do not remove the outline hooks.

**Tech Stack:** Vue 3 browser ESM, Tiptap, Tailwind utility classes, Font Awesome, Python `unittest` static frontend tests.

---

## File Structure

- Create: `moss_backend/tests/test_frontend_document_outline.py`
  - Static regression tests for the new outline template, state, behavior hooks, responsive mode, and discussion-selector visual treatment.
- Modify: `index.html`
  - Add outline panel, outline button, and drawer markup near the existing conversation selector panel.
  - Add `documentBodyRef` to the existing centered document body wrapper.
  - Add Vue refs and methods for outline extraction, active heading tracking, layout mode selection, and drawer controls.
  - Wire outline refresh into Tiptap updates, imports, AI DOM mutations, resize, drag, full-screen toggles, and document scroll.

No backend API, schema, or persistence file changes are part of this implementation.

---

### Task 1: Add Static Regression Tests

**Files:**
- Create: `moss_backend/tests/test_frontend_document_outline.py`

- [ ] **Step 1: Write the failing tests**

Create `moss_backend/tests/test_frontend_document_outline.py` with this exact content:

```python
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
        self.assertIn("const showOutlineDrawer = ref(false);", index_html)
        self.assertIn("const outlineMode = ref('drawer');", index_html)
        self.assertIn("const outlineStyle = ref(", index_html)
        self.assertIn("const outlineButtonStyle = ref(", index_html)
        self.assertIn("const outlinePanelRef = ref(null);", index_html)
        self.assertIn("const documentBodyRef = ref(null);", index_html)
        self.assertIn("let outlineRefreshFrame = 0;", index_html)
        self.assertIn("const ensureDocumentOutlineHeadingIds = () =>", index_html)
        self.assertIn("const refreshDocumentOutline = () =>", index_html)
        self.assertIn("const scheduleDocumentOutlineRefresh = () =>", index_html)
        self.assertIn("const updateActiveOutline = () =>", index_html)
        self.assertIn("const updateOutlineLayout = () =>", index_html)
        self.assertIn("const scrollToOutlineItem = (item) =>", index_html)
        self.assertIn("const openOutlineDrawer = () =>", index_html)
        self.assertIn("const closeOutlineDrawer = () =>", index_html)

    def test_index_renders_floating_outline_and_drawer(self) -> None:
        index_html = self.index_html()

        self.assertIn('ref="outlinePanelRef"', index_html)
        self.assertIn('ref="documentBodyRef"', index_html)
        self.assertIn('v-if="outlineMode === \'floating\'"', index_html)
        self.assertIn('v-if="outlineMode === \'drawer\'"', index_html)
        self.assertIn('v-if="showOutlineDrawer"', index_html)
        self.assertIn('@click.stop="openOutlineDrawer"', index_html)
        self.assertIn('@click.stop="closeOutlineDrawer"', index_html)
        self.assertIn('@click="closeOutlineDrawer"', index_html)
        self.assertIn('@click="scrollToOutlineItem(item)"', index_html)
        self.assertIn('v-for="item in documentOutline"', index_html)
        self.assertIn("outlineIndentClass(item.level)", index_html)
        self.assertIn("isActiveOutlineItem(item)", index_html)
        self.assertIn("暂无目录", index_html)
        self.assertIn("目录", index_html)

    def test_document_outline_uses_discussion_panel_visual_language(self) -> None:
        index_html = self.index_html()

        self.assertIn("bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl", index_html)
        self.assertIn("box-shadow: 0 8px 30px rgb(0 0 0 / 0.08); padding: 0.375rem;", index_html)
        self.assertIn("border-b border-gray-100 mb-1", index_html)
        self.assertIn("rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-800", index_html)
        self.assertIn("bg-gray-100 text-gray-900 font-medium", index_html)
        self.assertNotIn("font-mono", index_html)

    def test_document_outline_layout_uses_available_space_not_only_breakpoints(self) -> None:
        index_html = self.index_html()

        self.assertIn("const requiredFloatingSpace = 260;", index_html)
        self.assertIn("const preferredOutlineWidth = 236;", index_html)
        self.assertIn("const rightSpace = window.innerWidth - anchorRect.right;", index_html)
        self.assertIn("outlineMode.value = canFloat ? 'floating' : 'drawer';", index_html)
        self.assertIn("if (outlineMode.value === 'floating') showOutlineDrawer.value = false;", index_html)
        self.assertIn("const anchorRect = isFullScreen.value && bodyRect ? bodyRect : panelRect;", index_html)

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
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run from the repo root:

```powershell
python -m unittest moss_backend.tests.test_frontend_document_outline -v
```

Expected result: the tests run and fail with missing strings such as `const documentOutline = ref([]);` and `ref="outlinePanelRef"`.

- [ ] **Step 3: Commit the failing tests**

```powershell
git add moss_backend/tests/test_frontend_document_outline.py
git commit -m "test: add document outline frontend checks"
```

Expected result: one commit containing only `moss_backend/tests/test_frontend_document_outline.py`.

---

### Task 2: Add Outline Template Markup

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add the document body ref**

Find this existing wrapper inside `#document-container`:

```html
<div class="max-w-3xl mx-auto w-full">
    <div ref="editorContainer" :class="{'cursor-blink': isModifying}"></div>
</div>
```

Replace it with:

```html
<div ref="documentBodyRef" class="max-w-3xl mx-auto w-full">
    <div ref="editorContainer" :class="{'cursor-blink': isModifying}"></div>
</div>
```

- [ ] **Step 2: Add the floating panel, button, and drawer markup**

Place this block immediately after the existing conversation tree panel closing `</div>` and before the chat container that starts with `<div class="relative w-full max-w-4xl mx-auto h-screen`:

```html
    <div v-if="outlineMode === 'floating'"
         ref="outlinePanelRef"
         class="fixed bg-white/80 backdrop-blur-xl border border-gray-100 rounded-xl text-gray-800 flex flex-col gap-1 overflow-hidden animate-pop-in"
         style="box-shadow: 0 8px 30px rgb(0 0 0 / 0.08); padding: 0.375rem;"
         :style="outlineStyle"
         @click.stop>
        <div class="flex items-center justify-between gap-3 px-2 py-1.5 border-b border-gray-100 mb-1">
            <div class="min-w-0 font-semibold text-gray-800 text-xs truncate">目录</div>
            <button @click.stop="outlineMode = 'drawer'"
                    class="h-6 w-6 rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-800 transition-colors flex items-center justify-center shrink-0"
                    title="收起目录">
                <i class="fa-solid fa-minus text-[10px]"></i>
            </button>
        </div>

        <div class="mt-1 flex flex-col gap-1 overflow-y-auto no-scrollbar" style="line-height: 1.5;">
            <button v-for="item in documentOutline"
                    :key="item.id"
                    @click="scrollToOutlineItem(item)"
                    class="group w-full flex items-center gap-2 px-2 py-1.5 text-sm transition-all rounded-lg text-left"
                    :class="[
                        outlineIndentClass(item.level),
                        isActiveOutlineItem(item) ? 'bg-gray-100 text-gray-900 font-medium' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-800'
                    ]"
                    :title="item.text">
                <span class="rounded-full shrink-0"
                      :class="isActiveOutlineItem(item) ? 'bg-black' : 'bg-transparent group-hover:bg-gray-300'"
                      style="width: 0.375rem; height: 0.375rem;"></span>
                <span class="truncate">{{ item.text }}</span>
            </button>
            <div v-if="!documentOutline.length"
                 class="px-2 py-2 text-xs text-gray-400 text-center">
                暂无目录
            </div>
        </div>
    </div>

    <button v-if="outlineMode === 'drawer'"
            @click.stop="openOutlineDrawer"
            class="fixed w-10 h-10 rounded-full bg-white/90 backdrop-blur-xl border border-gray-200 shadow-lg text-gray-700 hover:bg-gray-50 transition-colors flex items-center justify-center"
            :style="outlineButtonStyle"
            title="打开目录">
        <i class="fa-solid fa-list-ul text-xs"></i>
    </button>

    <div v-if="showOutlineDrawer"
         class="fixed inset-0 z-[1100]"
         @click="closeOutlineDrawer">
        <div class="absolute inset-0 bg-black/10"></div>
        <div class="absolute top-0 right-0 h-full w-[min(18rem,calc(100vw-2rem))] bg-white/90 backdrop-blur-xl border-l border-gray-100 shadow-[0_8px_30px_rgb(0,0,0,0.12)] text-gray-800 flex flex-col gap-1 overflow-hidden"
             style="padding: 0.375rem;"
             @click.stop>
            <div class="flex items-center justify-between gap-3 px-2 py-2 border-b border-gray-100 mb-1">
                <div class="min-w-0 font-semibold text-gray-800 text-xs truncate">目录</div>
                <button @click.stop="closeOutlineDrawer"
                        class="h-6 w-6 rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-800 transition-colors flex items-center justify-center shrink-0"
                        title="关闭目录">
                    <i class="fa-solid fa-xmark text-[11px]"></i>
                </button>
            </div>

            <div class="mt-1 flex flex-col gap-1 overflow-y-auto no-scrollbar" style="line-height: 1.5;">
                <button v-for="item in documentOutline"
                        :key="item.id"
                        @click="scrollToOutlineItem(item)"
                        class="group w-full flex items-center gap-2 px-2 py-1.5 text-sm transition-all rounded-lg text-left"
                        :class="[
                            outlineIndentClass(item.level),
                            isActiveOutlineItem(item) ? 'bg-gray-100 text-gray-900 font-medium' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-800'
                        ]"
                        :title="item.text">
                    <span class="rounded-full shrink-0"
                          :class="isActiveOutlineItem(item) ? 'bg-black' : 'bg-transparent group-hover:bg-gray-300'"
                          style="width: 0.375rem; height: 0.375rem;"></span>
                    <span class="truncate">{{ item.text }}</span>
                </button>
                <div v-if="!documentOutline.length"
                     class="px-2 py-2 text-xs text-gray-400 text-center">
                    暂无目录
                </div>
            </div>
        </div>
    </div>
```

- [ ] **Step 3: Run the focused tests and verify template checks still fail on missing script hooks**

```powershell
python -m unittest moss_backend.tests.test_frontend_document_outline -v
```

Expected result: template-related assertions pass, and state/method assertions still fail.

- [ ] **Step 4: Commit the template markup**

```powershell
git add index.html
git commit -m "feat: add document outline shell"
```

Expected result: one commit containing only the template markup and `documentBodyRef` attribute.

---

### Task 3: Add Outline State and Data Extraction

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add outline state near the existing conversation tree state**

Find:

```javascript
            const showConversationTree = ref(true);
            const conversationTreeStyle = ref({
                left: '1rem',
                top: '5.5rem',
                width: '14.75rem',
                zIndex: 1000
            });
```

Add this block immediately after it:

```javascript
            const documentOutline = ref([]);
            const activeOutlineId = ref('');
            const showOutlineDrawer = ref(false);
            const outlineMode = ref('drawer');
            const outlineStyle = ref({
                left: '1rem',
                top: '5.5rem',
                width: '14.75rem',
                maxHeight: 'calc(100vh - 8rem)',
                zIndex: 1000
            });
            const outlineButtonStyle = ref({
                right: '1rem',
                top: '5.5rem',
                zIndex: 1000
            });
```

- [ ] **Step 2: Add refs and the refresh frame variable**

Find:

```javascript
            const editorContainer = ref(null);
            const fileInputRef = ref(null);
            const libraryButtonRef = ref(null);
            const panelRef = ref(null);
            const conversationTreeRef = ref(null);
            const conversationTreePanelRef = ref(null);
```

Replace it with:

```javascript
            const editorContainer = ref(null);
            const documentBodyRef = ref(null);
            const fileInputRef = ref(null);
            const libraryButtonRef = ref(null);
            const panelRef = ref(null);
            const conversationTreeRef = ref(null);
            const conversationTreePanelRef = ref(null);
            const outlinePanelRef = ref(null);
```

Find:

```javascript
            let autosaveTimer = null;
            let lastSavedSnapshot = '';
```

Replace it with:

```javascript
            let autosaveTimer = null;
            let lastSavedSnapshot = '';
            let outlineRefreshFrame = 0;
```

- [ ] **Step 3: Add outline helper methods before `handleKeyDown`**

Find the existing `findById` function:

```javascript
            const findById = (root, id) => {
                const escaped = String(id).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
                return root.querySelector(`[id="${escaped}"]`);
            };
```

Add this block immediately after it:

```javascript
            const outlineIndentClass = (level) => {
                if (level === 2) return 'pl-5';
                if (level === 3) return 'pl-8';
                return 'pl-2';
            };

            const isActiveOutlineItem = (item) => {
                return Boolean(item?.id && item.id === activeOutlineId.value);
            };

            const ensureDocumentOutlineHeadingIds = () => {
                if (!tiptapEditor) return;

                const ids = collectEditorIds();
                const seenHeadingIds = new Set();
                let tr = tiptapEditor.state.tr;

                tiptapEditor.state.doc.descendants((node, pos) => {
                    if (node.type?.name !== 'heading' || !nodeSupportsAttr(node, 'id')) return;

                    const currentId = node.attrs?.id ? String(node.attrs.id) : '';
                    const needsGeneratedId = !currentId
                        || currentId.startsWith(MOSS_TEMP_ANCHOR_PREFIX)
                        || seenHeadingIds.has(currentId);

                    if (!needsGeneratedId) {
                        seenHeadingIds.add(currentId);
                        return;
                    }

                    const nextId = createUniqueEditorId(MOSS_BLOCK_ID_PREFIX, ids);
                    seenHeadingIds.add(nextId);
                    tr = tr.setNodeMarkup(pos, undefined, {
                        ...node.attrs,
                        id: nextId
                    });
                });

                if (!tr.docChanged) return;
                tiptapEditor.view.dispatch(tr);
                contentHTML.value = tiptapEditor.getHTML();
                refreshTopLevelSignature();
            };

            const refreshDocumentOutline = () => {
                const container = document.getElementById('document-container');
                if (!container) {
                    documentOutline.value = [];
                    activeOutlineId.value = '';
                    return;
                }

                ensureDocumentOutlineHeadingIds();

                const headings = Array.from(container.querySelectorAll('h1, h2, h3'));
                const nextOutline = headings
                    .map((heading) => {
                        const level = Number.parseInt(heading.tagName.slice(1), 10);
                        const text = heading.textContent?.trim() || '';
                        const id = heading.id || '';
                        if (!id || !text) return null;
                        return { id, text, level };
                    })
                    .filter(Boolean);

                documentOutline.value = nextOutline;
                if (activeOutlineId.value && !nextOutline.some(item => item.id === activeOutlineId.value)) {
                    activeOutlineId.value = nextOutline[0]?.id || '';
                }
                updateOutlineLayout();
                updateActiveOutline();
            };

            const scheduleDocumentOutlineRefresh = () => {
                if (outlineRefreshFrame) return;
                outlineRefreshFrame = window.requestAnimationFrame(async () => {
                    outlineRefreshFrame = 0;
                    await nextTick();
                    refreshDocumentOutline();
                });
            };
```

- [ ] **Step 4: Add outline state and helpers to the setup return object**

Find this section in the returned object:

```javascript
                editorContainer,
                fileInputRef,
                libraryButtonRef,
                panelRef,
```

Replace it with:

```javascript
                editorContainer,
                documentBodyRef,
                fileInputRef,
                libraryButtonRef,
                panelRef,
                outlinePanelRef,
```

Find this section in the returned object:

```javascript
                conversationTreeStyle,
                toggleConversationTree,
```

Replace it with:

```javascript
                conversationTreeStyle,
                documentOutline,
                activeOutlineId,
                showOutlineDrawer,
                outlineMode,
                outlineStyle,
                outlineButtonStyle,
                outlineIndentClass,
                isActiveOutlineItem,
                openOutlineDrawer,
                closeOutlineDrawer,
                scrollToOutlineItem,
                toggleConversationTree,
```

- [ ] **Step 5: Run the focused tests and verify remaining failures are layout and wiring hooks**

```powershell
python -m unittest moss_backend.tests.test_frontend_document_outline -v
```

Expected result: state and extraction assertions pass; failures remain for `updateActiveOutline`, `updateOutlineLayout`, `scrollToOutlineItem`, drawer controls, and lifecycle wiring.

- [ ] **Step 6: Commit outline state and extraction**

```powershell
git add index.html
git commit -m "feat: extract document outline headings"
```

Expected result: one commit containing the new state, refs, extraction helpers, and return-object exports.

---

### Task 4: Add Layout Mode, Drawer Controls, and Scrolling

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add layout and interaction methods after `scheduleDocumentOutlineRefresh`**

Add this block immediately after the `scheduleDocumentOutlineRefresh` function from Task 3:

```javascript
            const updateActiveOutline = () => {
                const container = document.getElementById('document-container');
                if (!container || !documentOutline.value.length) {
                    activeOutlineId.value = '';
                    return;
                }

                const containerRect = container.getBoundingClientRect();
                const activationOffset = 72;
                let activeItem = documentOutline.value[0];

                for (const item of documentOutline.value) {
                    const heading = document.getElementById(item.id);
                    if (!heading) continue;
                    const headingTop = heading.getBoundingClientRect().top - containerRect.top;
                    if (headingTop <= activationOffset) {
                        activeItem = item;
                    } else {
                        break;
                    }
                }

                activeOutlineId.value = activeItem?.id || '';
            };

            const updateOutlineLayout = () => {
                const panelRect = panelRef.value?.getBoundingClientRect?.();
                if (!panelRect) {
                    outlineMode.value = 'drawer';
                    outlineButtonStyle.value = {
                        right: '1rem',
                        top: '5.5rem',
                        zIndex: 1000
                    };
                    return;
                }

                const bodyRect = documentBodyRef.value?.getBoundingClientRect?.();
                const anchorRect = isFullScreen.value && bodyRect ? bodyRect : panelRect;
                const pagePadding = 12;
                const preferredOutlineWidth = 236;
                const minOutlineWidth = 196;
                const requiredFloatingSpace = 260;
                const rightSpace = window.innerWidth - anchorRect.right;
                const canFloat = rightSpace >= requiredFloatingSpace;
                outlineMode.value = canFloat ? 'floating' : 'drawer';
                if (outlineMode.value === 'floating') showOutlineDrawer.value = false;

                const top = isFullScreen.value
                    ? 88
                    : Math.max(pagePadding, panelRect.top + 64);
                const bottomLimit = isFullScreen.value
                    ? window.innerHeight - 96
                    : Math.min(window.innerHeight - 84, panelRect.bottom - 72);
                const maxHeight = Math.max(160, bottomLimit - top);

                if (canFloat) {
                    const outlineWidth = Math.min(
                        preferredOutlineWidth,
                        Math.max(minOutlineWidth, rightSpace - pagePadding * 2)
                    );
                    const centeredLeft = anchorRect.right + (rightSpace - outlineWidth) / 2;
                    const minLeft = anchorRect.right + pagePadding;
                    const maxLeft = window.innerWidth - outlineWidth - pagePadding;
                    const left = Math.max(minLeft, Math.min(centeredLeft, maxLeft));

                    outlineStyle.value = {
                        left: `${left}px`,
                        top: `${top}px`,
                        width: `${outlineWidth}px`,
                        maxHeight: `${maxHeight}px`,
                        zIndex: 1000
                    };
                }

                outlineButtonStyle.value = {
                    right: `${pagePadding}px`,
                    top: `${top}px`,
                    zIndex: 1000
                };
            };

            const scrollToOutlineItem = (item) => {
                if (!item?.id) return;

                const container = document.getElementById('document-container');
                const target = document.getElementById(item.id);
                if (!container || !target) {
                    refreshDocumentOutline();
                    return;
                }

                activeOutlineId.value = item.id;
                container.scrollTo({
                    top: Math.max(0, target.offsetTop - 24),
                    behavior: 'smooth'
                });

                if (outlineMode.value === 'drawer' && window.innerWidth < 1024) {
                    showOutlineDrawer.value = false;
                }
            };

            const openOutlineDrawer = () => {
                showOutlineDrawer.value = true;
                refreshDocumentOutline();
            };

            const closeOutlineDrawer = () => {
                showOutlineDrawer.value = false;
            };
```

- [ ] **Step 2: Update full-screen toggling to recalculate outline layout**

Find:

```javascript
            const toggleFullScreen = () => {
                isFullScreen.value = !isFullScreen.value;
            };
```

Replace it with:

```javascript
            const toggleFullScreen = async () => {
                isFullScreen.value = !isFullScreen.value;
                await nextTick();
                updateOutlineLayout();
                updateActiveOutline();
            };
```

- [ ] **Step 3: Update drag and resize layout wiring**

Find:

```javascript
                if (showConversationTree.value) updateConversationTreePosition();
```

inside `handleDragging`, and replace that single line with:

```javascript
                if (showConversationTree.value) updateConversationTreePosition();
                updateOutlineLayout();
```

Find:

```javascript
                if (showConversationTree.value) updateConversationTreePosition();
```

inside `handleResize`, and replace that single line with:

```javascript
                if (showConversationTree.value) updateConversationTreePosition();
                updateOutlineLayout();
                updateActiveOutline();
```

- [ ] **Step 4: Run focused tests and verify only lifecycle refresh assertions remain**

```powershell
python -m unittest moss_backend.tests.test_frontend_document_outline -v
```

Expected result: layout and interaction method assertions pass; failures remain for scroll listener, cancellation, and repeated refresh hooks.

- [ ] **Step 5: Commit layout and interaction behavior**

```powershell
git add index.html
git commit -m "feat: position document outline responsively"
```

Expected result: one commit containing layout, drawer controls, outline scrolling, and full-screen recalculation.

---

### Task 5: Wire Refresh Hooks and Lifecycle Cleanup

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Refresh outline after top-level ID maintenance**

Find:

```javascript
            const queueTopLevelIdMaintenance = () => {
                if (idMaintenanceQueued || isMaintainingDocumentIds) return;
                idMaintenanceQueued = true;
                window.requestAnimationFrame(() => {
                    idMaintenanceQueued = false;
                    ensureTopLevelBlockIds();
                });
            };
```

Replace it with:

```javascript
            const queueTopLevelIdMaintenance = () => {
                if (idMaintenanceQueued || isMaintainingDocumentIds) return;
                idMaintenanceQueued = true;
                window.requestAnimationFrame(() => {
                    idMaintenanceQueued = false;
                    ensureTopLevelBlockIds();
                    scheduleDocumentOutlineRefresh();
                });
            };
```

- [ ] **Step 2: Refresh outline after upload and AI DOM mutation changes**

In `handleUpload`, find:

```javascript
                    cleanupTemporaryAnchors();
                    ensureTopLevelBlockIds();
                    await persistNoteSnapshot({ immediate: true }).catch(() => {});
```

Replace it with:

```javascript
                    cleanupTemporaryAnchors();
                    ensureTopLevelBlockIds();
                    scheduleDocumentOutlineRefresh();
                    await persistNoteSnapshot({ immediate: true }).catch(() => {});
```

In `applyDomMutation`, find:

```javascript
                contentHTML.value = root.innerHTML;
                tiptapEditor.commands.setContent(contentHTML.value);
                ensureTopLevelBlockIds();
                await persistNoteSnapshot({ immediate: true }).catch(() => {});
```

Replace it with:

```javascript
                contentHTML.value = root.innerHTML;
                tiptapEditor.commands.setContent(contentHTML.value);
                ensureTopLevelBlockIds();
                scheduleDocumentOutlineRefresh();
                await persistNoteSnapshot({ immediate: true }).catch(() => {});
```

- [ ] **Step 3: Refresh outline from Tiptap updates**

Find the current `onUpdate` body:

```javascript
                        onUpdate: ({ editor }) => {
                            contentHTML.value = editor.getHTML();
                            if (!isMaintainingDocumentIds) queueAutosave();
                            if (isMaintainingDocumentIds) return;
                            const signature = topLevelStructureSignature();
                            if (signature !== lastTopLevelSignature) {
                                lastTopLevelSignature = signature;
                                queueTopLevelIdMaintenance();
                            }
                        }
```

Replace it with:

```javascript
                        onUpdate: ({ editor }) => {
                            contentHTML.value = editor.getHTML();
                            if (!isMaintainingDocumentIds) {
                                queueAutosave();
                                const signature = topLevelStructureSignature();
                                if (signature !== lastTopLevelSignature) {
                                    lastTopLevelSignature = signature;
                                    queueTopLevelIdMaintenance();
                                }
                            }
                            scheduleDocumentOutlineRefresh();
                        }
```

- [ ] **Step 4: Add mounted scroll listener and initial outline refresh**

Find:

```javascript
                    cleanupTemporaryAnchors();
                    ensureTopLevelBlockIds();
                    refreshTopLevelSignature();

                    window.addEventListener('keydown', handleKeyDown);
                    window.addEventListener('resize', handleResize);
```

Replace it with:

```javascript
                    cleanupTemporaryAnchors();
                    ensureTopLevelBlockIds();
                    refreshTopLevelSignature();
                    await nextTick();
                    refreshDocumentOutline();

                    const documentContainer = document.getElementById('document-container');
                    documentContainer?.addEventListener('scroll', updateActiveOutline, { passive: true });
                    window.addEventListener('keydown', handleKeyDown);
                    window.addEventListener('resize', handleResize);
```

- [ ] **Step 5: Add unmounted cleanup**

Find:

```javascript
                window.removeEventListener('keydown', handleKeyDown);
                window.removeEventListener('resize', handleResize);
                document.removeEventListener('mousemove', handleDragging);
```

Replace it with:

```javascript
                window.removeEventListener('keydown', handleKeyDown);
                window.removeEventListener('resize', handleResize);
                const documentContainer = document.getElementById('document-container');
                documentContainer?.removeEventListener('scroll', updateActiveOutline);
                document.removeEventListener('mousemove', handleDragging);
```

Find:

```javascript
                if (autosaveTimer) window.clearTimeout(autosaveTimer);
                tiptapEditor?.destroy();
```

Replace it with:

```javascript
                if (autosaveTimer) window.clearTimeout(autosaveTimer);
                if (outlineRefreshFrame) window.cancelAnimationFrame(outlineRefreshFrame);
                tiptapEditor?.destroy();
```

- [ ] **Step 6: Run the focused tests and verify they pass**

```powershell
python -m unittest moss_backend.tests.test_frontend_document_outline -v
```

Expected result: all tests in `FrontendDocumentOutlineTests` pass.

- [ ] **Step 7: Commit lifecycle wiring**

```powershell
git add index.html
git commit -m "feat: keep document outline in sync"
```

Expected result: one commit containing refresh hooks and lifecycle cleanup.

---

### Task 6: Verification and Manual Browser Check

**Files:**
- No planned source changes unless verification finds a defect.

- [ ] **Step 1: Run the focused frontend-outline tests**

```powershell
python -m unittest moss_backend.tests.test_frontend_document_outline -v
```

Expected result:

```text
Ran 5 tests

OK
```

- [ ] **Step 2: Run the existing frontend static tests**

```powershell
python -m unittest moss_backend.tests.test_frontend_draft_notes -v
```

Expected result: all tests in `FrontendDraftNoteTests` pass.

- [ ] **Step 3: Run whitespace validation**

```powershell
git diff --check
```

Expected result: no output and exit code `0`.

- [ ] **Step 4: Start the local app**

```powershell
cd moss_backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected result: Uvicorn starts and serves `http://127.0.0.1:8000/`.

- [ ] **Step 5: Manually verify wide non-full-screen mode**

Open `http://127.0.0.1:8000/` in a desktop-width browser.

Verify:

- A right-side floating outline appears when there is at least `260px` of right-side space.
- The panel uses the same visual language as the discussion selector: translucent white, blur, thin border, rounded `xl`, light shadow, compact gray rows.
- H1/H2/H3 headings appear with increasing indentation.
- Clicking a heading scrolls the document container to that heading.
- Scrolling the document updates the active dot and active row.
- The right-side panel does not shrink the document body.

- [ ] **Step 6: Manually verify drawer mode**

Resize the browser narrower until the floating panel disappears.

Verify:

- A round right-side outline button appears.
- Clicking the button opens the right-side drawer.
- Clicking outside the drawer closes it.
- Clicking a heading scrolls the document to the heading.
- On a narrow viewport below `1024px`, clicking a heading closes the drawer.

- [ ] **Step 7: Manually verify full-screen behavior**

Click the existing full-screen button.

Verify:

- On wide screens, the outline uses the document body right-side whitespace.
- On constrained screens, full-screen mode uses the outline button and drawer.
- Opening the drawer does not exit full-screen mode.
- The outline does not overlap the bottom-right save or full-screen buttons in a way that blocks their use.

- [ ] **Step 8: Manually verify document updates**

In the editor:

- Add a new H2 with the keyboard shortcut already supported by the app.
- Delete an existing heading.
- Upload or load a document with headings if available.

Verify:

- The outline refreshes after heading additions and deletions.
- Deleted headings disappear from the outline.
- New headings receive stable IDs and can be clicked.
- Empty documents or documents without headings show `暂无目录`.

- [ ] **Step 9: Commit verification fixes if any were needed**

If manual verification required code changes:

```powershell
git add index.html moss_backend/tests/test_frontend_document_outline.py
git commit -m "fix: polish document outline behavior"
```

Expected result: a small commit containing only verification-driven corrections.

---

## Self-Review Notes

- Spec coverage: the tasks cover H1/H2/H3 extraction, click-to-scroll, active heading highlight, floating panel, drawer fallback, full-screen behavior, visual treatment matching the discussion selector, empty state, no backend changes, and refresh after document updates.
- File scope: only `index.html` and a focused frontend static test file are planned.
- Risk: the feature is mostly layout and DOM behavior, so static tests only prove integration hooks are present. Task 6 includes browser checks for the real interaction behavior.
