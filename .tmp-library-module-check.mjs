
    import { createApp, ref, computed, onMounted, onUnmounted, nextTick } from 'vue';

    const DEFAULT_API_BASE = 'http://127.0.0.1:8000';
    const API_BASE = window.MOSS_API_BASE || (window.location.protocol.startsWith('http') ? window.location.origin : DEFAULT_API_BASE);
    const apiUrl = (path) => `${API_BASE.replace(/\/$/, '')}${path}`;

    createApp({
        setup() {
            const notes = ref([]);
            const isLoading = ref(false);
            const isCreating = ref(false);
            const loadError = ref('');
            const actionError = ref('');
            const searchQuery = ref('');
            const viewMode = ref(localStorage.getItem('moss-library-view-mode') || 'grid');
            const isSidebarOpen = ref(true);
            const isMobile = ref(false);
            const openMenuNoteId = ref('');
            const renameNote = ref(null);
            const renameValue = ref('');
            const renameInputRef = ref(null);
            const deleteNoteTarget = ref(null);

            const noteTitle = (note) => {
                return note?.effective_title || note?.display_title || note?.title || 'Untitled note';
            };

            const compareNotes = (a, b) => {
                if (a.pinned_at && !b.pinned_at) return -1;
                if (!a.pinned_at && b.pinned_at) return 1;
                if (a.pinned_at && b.pinned_at) {
                    return String(b.pinned_at).localeCompare(String(a.pinned_at));
                }
                return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
            };

            const filteredNotes = computed(() => {
                const query = searchQuery.value.trim().toLowerCase();
                if (!query) return notes.value;
                return notes.value.filter((note) => {
                    return `${noteTitle(note)} ${note.preview_text || ''}`.toLowerCase().includes(query);
                });
            });

            const checkScreenSize = () => {
                const mobile = window.innerWidth < 768;
                if (mobile === isMobile.value) return;
                isMobile.value = mobile;
                isSidebarOpen.value = !mobile;
            };

            const toggleSidebar = () => {
                isSidebarOpen.value = !isSidebarOpen.value;
            };

            const closeSidebarOnMobile = () => {
                if (isMobile.value) isSidebarOpen.value = false;
            };

            const mergeNote = (updated) => {
                notes.value = notes.value.map((note) => (
                    note.note_id === updated.note_id ? { ...note, ...updated } : note
                ));
            };

            const patchNote = async (note, payload) => {
                actionError.value = '';
                const response = await fetch(apiUrl(`/api/v1/notes/${encodeURIComponent(note.note_id)}`), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (!response.ok) throw new Error(await response.text());
                const updated = await response.json();
                mergeNote(updated);
                return updated;
            };

            const removeNoteFromList = (noteId) => {
                notes.value = notes.value.filter((note) => note.note_id !== noteId);
            };

            const loadNotes = async () => {
                isLoading.value = true;
                loadError.value = '';
                actionError.value = '';
                try {
                    const response = await fetch(apiUrl('/api/v1/notes'));
                    if (!response.ok) throw new Error(await response.text());
                    const payload = await response.json();
                    notes.value = (payload.notes || []).sort(compareNotes);
                } catch (error) {
                    loadError.value = error.message || 'unknown error';
                } finally {
                    isLoading.value = false;
                }
            };

            const createNote = async () => {
                if (isCreating.value) return;
                isCreating.value = true;
                actionError.value = '';
                try {
                    const response = await fetch(apiUrl('/api/v1/notes'), { method: 'POST' });
                    if (!response.ok) throw new Error(await response.text());
                    const payload = await response.json();
                    openNote({
                        note_id: payload.note_id,
                        default_conversation_id: payload.default_conversation_id
                    });
                } catch (error) {
                    actionError.value = error.message || 'create failed';
                } finally {
                    isCreating.value = false;
                }
            };

            const openNote = (note) => {
                const params = new URLSearchParams({
                    note_id: note.note_id,
                    conversation_id: note.default_conversation_id
                });
                window.location.href = `/?${params.toString()}`;
            };

            const toggleViewMode = () => {
                viewMode.value = viewMode.value === 'grid' ? 'list' : 'grid';
                localStorage.setItem('moss-library-view-mode', viewMode.value);
            };

            const toggleNoteMenu = (note, event) => {
                event?.stopPropagation?.();
                openMenuNoteId.value = openMenuNoteId.value === note.note_id ? '' : note.note_id;
            };

            const closeNoteMenu = () => {
                openMenuNoteId.value = '';
            };

            const togglePinned = async (note) => {
                closeNoteMenu();
                try {
                    await patchNote(note, { pinned: !note.pinned_at });
                    notes.value = [...notes.value].sort(compareNotes);
                } catch (error) {
                    actionError.value = error.message || '置顶操作失败';
                }
            };

            const startRename = async (note) => {
                closeNoteMenu();
                renameNote.value = note;
                renameValue.value = note.display_title || note.title || '';
                await nextTick();
                renameInputRef.value?.focus?.();
            };

            const cancelRename = () => {
                renameNote.value = null;
                renameValue.value = '';
            };

            const submitRename = async () => {
                if (!renameNote.value) return;
                try {
                    await patchNote(renameNote.value, { display_title: renameValue.value });
                    cancelRename();
                } catch (error) {
                    actionError.value = error.message || '重命名失败';
                }
            };

            const startDelete = (note) => {
                closeNoteMenu();
                deleteNoteTarget.value = note;
            };

            const cancelDelete = () => {
                deleteNoteTarget.value = null;
            };

            const confirmDelete = async () => {
                if (!deleteNoteTarget.value) return;
                const note = deleteNoteTarget.value;
                try {
                    const response = await fetch(apiUrl(`/api/v1/notes/${encodeURIComponent(note.note_id)}`), {
                        method: 'DELETE'
                    });
                    if (!response.ok) throw new Error(await response.text());
                    removeNoteFromList(note.note_id);
                    cancelDelete();
                } catch (error) {
                    actionError.value = error.message || '删除失败';
                }
            };

            const formatDate = (value) => {
                if (!value) return '';
                try {
                    return new Intl.DateTimeFormat('zh-CN', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                    }).format(new Date(value));
                } catch {
                    return value;
                }
            };

            onMounted(() => {
                checkScreenSize();
                window.addEventListener('resize', checkScreenSize);
                loadNotes();
            });

            onUnmounted(() => {
                window.removeEventListener('resize', checkScreenSize);
            });

            return {
                notes,
                filteredNotes,
                isLoading,
                isCreating,
                loadError,
                actionError,
                searchQuery,
                viewMode,
                isSidebarOpen,
                isMobile,
                openMenuNoteId,
                renameNote,
                renameValue,
                renameInputRef,
                deleteNoteTarget,
                noteTitle,
                loadNotes,
                createNote,
                openNote,
                toggleViewMode,
                toggleSidebar,
                closeSidebarOnMobile,
                toggleNoteMenu,
                closeNoteMenu,
                togglePinned,
                startRename,
                cancelRename,
                submitRename,
                startDelete,
                cancelDelete,
                confirmDelete,
                formatDate
            };
        }
    }).mount('#app');
