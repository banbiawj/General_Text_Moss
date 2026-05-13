# Startup Loading Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the startup loading overlay without changing the current CDN scope for Vue, Tiptap, Font Awesome, or Google Fonts.

**Architecture:** Move duplicated loading styles into a shared static stylesheet, keep each HTML page responsible for rendering the same full-screen `* + loading...` startup overlay, and hide the overlay only after the relevant startup work has completed. A minimum display duration prevents a fast load from flashing the overlay.

**Tech Stack:** Static HTML, vanilla CSS, Vue 3 browser modules, FastAPI static file serving, Node-based static checks.

---

### Task 1: Static Regression Check

**Files:**
- Validate: `index.html`
- Validate: `library.html`
- Validate: `static/css/app-loading.css`

- [ ] Run a Node check before implementation that expects the shared loading stylesheet, full-screen stage markup, `*` mark, `loading...` text, no shell/card markup, `v-cloak`, and `minimumLoadingMs` behavior. It should fail before the code change.

### Task 2: Shared Overlay Styling

**Files:**
- Create: `static/css/app-loading.css`
- Modify: `index.html`
- Modify: `library.html`

- [ ] Create shared CSS for `.app-loading`, `.app-loading-stage`, `.app-loading-mark`, `.app-loading-text`, and reduced-motion behavior.
- [ ] Link `/static/css/app-loading.css` from both pages.
- [ ] Remove duplicated inline `.app-loading` styles from both pages.
- [ ] Remove the previous card-like shell, product title, explanatory copy, and spinner from the overlay markup.

### Task 3: Overlay Behavior

**Files:**
- Modify: `index.html`
- Modify: `library.html`

- [ ] Keep `v-cloak` inline in both pages so Vue templates are hidden before app CSS loads.
- [ ] Add a `minimumLoadingMs` delay to `hideInitialLoading()`.
- [ ] Hide the editor overlay after note load and Tiptap initialization.
- [ ] Hide the library overlay after the first `loadNotes()` attempt completes.

### Task 4: Verification

**Files:**
- Validate: `index.html`
- Validate: `library.html`
- Validate: `static/css/app-loading.css`

- [ ] Run `npm run build:css`.
- [ ] Run the static Node regression check.
- [ ] Run `git diff --check`.
- [ ] Verify `/`, `/library`, `/static/css/tailwind.css`, and `/static/css/app-loading.css` return HTTP 200 from the local FastAPI server.
