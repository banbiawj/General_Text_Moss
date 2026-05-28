# SSE Waiting Stages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chat waiting bubble in `index.html` with an SSE-driven Moss waiting state.

**Architecture:** Keep the backend unchanged and consume existing `node_start` / `node_end` SSE events on the frontend. The waiting UI renders only stages observed in the current stream, marks them active or done, and disappears when `chat_chunk` creates the answer message.

**Tech Stack:** Static `index.html`, Vue 3 browser modules, Font Awesome, Node static regression tests.

---

### Task 1: Static Regression Test

**Files:**
- Modify: `tests/chat-layout.test.mjs`
- Validate: `index.html`

- [ ] Add assertions that `index.html` defines `waitingStageDefinitions`, handles `node_start`, marks stages complete on `node_end`, renders `waitingStages`, and uses the existing `fa-solid fa-asterisk` Moss icon in the waiting state.
- [ ] Run `npm run test:chat-layout` and confirm it fails before implementation because these identifiers are not present.

### Task 2: Frontend State And SSE Handling

**Files:**
- Modify: `index.html`

- [ ] Add `waitingStageDefinitions` for `intent`, `task_assemble`, `execute`, `tools`, and `task_advance`.
- [ ] Add reactive `waitingStages` state plus helpers to reset, start, and complete stages.
- [ ] Reset stages at the start and end of each `sendMessage()` call.
- [ ] Handle `node_start` by starting a stage, and extend existing `node_end` handling to complete the corresponding stage without changing current task-type behavior.

### Task 3: Waiting UI

**Files:**
- Modify: `index.html`

- [ ] Replace the current `isThinking` ellipsis bubble with the Moss avatar breathing state, a stage list, and subtle skeleton lines.
- [ ] Reuse the existing `fa-solid fa-asterisk` icon.
- [ ] Add small scoped CSS keyframes/classes in `index.html` for avatar breathing, dot glow, shimmer, and reduced motion support.

### Task 4: Verification

**Files:**
- Validate: `tests/chat-layout.test.mjs`
- Validate: `index.html`

- [ ] Run `npm run test:chat-layout`.
- [ ] Run `git diff --check`.
