# Skill Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hard-coded task behavior with a manifest-driven skill runtime that can load new Moss skills from folders.

**Architecture:** Keep one LangGraph agent. Add a small runtime that loads `skill.yaml` and `prompt.md`, routes a request to one skill, derives context policy and allowed tools from the selected skill, then lets the existing graph execute with those values.

**Tech Stack:** Python 3.12, unittest, LangGraph/LangChain existing backend, PyYAML via `langchain-core` dependency chain if available, with a stdlib fallback for the simple manifest shape.

---

### Task 1: Add Skill Runtime Models And Registry

**Files:**
- Create: `moss_backend/app/agent/skill_runtime/models.py`
- Create: `moss_backend/app/agent/skill_runtime/registry.py`
- Create: `moss_backend/app/agent/skill_runtime/__init__.py`
- Test: `moss_backend/tests/test_skill_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that create temporary skill folders, load them through the registry, and assert that prompts, tools, context strategy, and priority are parsed.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_skill_runtime -v`
Expected: FAIL because `app.agent.skill_runtime` does not exist.

- [ ] **Step 3: Implement registry**

Create dataclasses for `SkillDefinition` and `SkillContextPolicy`, plus `load_skill_registry()`.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m unittest tests.test_skill_runtime -v`
Expected: PASS.

### Task 2: Add Router And Built-In Skills

**Files:**
- Create: `moss_backend/app/agent/skill_runtime/router.py`
- Create: `moss_backend/app/agent/skills/general-chat/skill.yaml`
- Create: `moss_backend/app/agent/skills/general-chat/prompt.md`
- Create: `moss_backend/app/agent/skills/document-qa/skill.yaml`
- Create: `moss_backend/app/agent/skills/document-qa/prompt.md`
- Create: `moss_backend/app/agent/skills/local-edit/skill.yaml`
- Create: `moss_backend/app/agent/skills/local-edit/prompt.md`
- Create: `moss_backend/app/agent/skills/global-edit/skill.yaml`
- Create: `moss_backend/app/agent/skills/global-edit/prompt.md`
- Test: `moss_backend/tests/test_skill_runtime.py`

- [ ] **Step 1: Write failing router tests**

Assert that broad edit words route to `global-edit`, local edit words route to `local-edit`, document questions route to `document-qa`, and normal chat routes to `general-chat`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_skill_runtime -v`
Expected: FAIL because router and built-in skills do not exist.

- [ ] **Step 3: Implement router and manifests**

Add trigger matching with include/exclude/priority. Keep the four coarse task types unchanged.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m unittest tests.test_skill_runtime -v`
Expected: PASS.

### Task 3: Wire Runtime Into Graph

**Files:**
- Modify: `moss_backend/app/agent/graph.py`
- Modify: `moss_backend/app/agent/state.py`
- Test: `moss_backend/tests/test_skill_runtime.py`

- [ ] **Step 1: Write graph-adjacent tests**

Assert that a selected skill produces task fields including `skill_id`, `task_tools`, `canvas_context`, and `allowed_element_ids`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_skill_runtime -v`
Expected: FAIL because graph helper does not use skills yet.

- [ ] **Step 3: Implement graph helper**

Replace `_default_task_tools(task_type)` with skill-derived tools, and build the system prompt from the selected skill prompt plus existing document snapshot inputs.

- [ ] **Step 4: Run targeted tests**

Run: `python -m unittest tests.test_skill_runtime -v`
Expected: PASS.

### Task 4: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run backend tests**

Run: `python -m unittest discover -s tests -v`
Expected: PASS if dependencies are installed; otherwise report missing dependencies precisely.

- [ ] **Step 2: Inspect diff**

Run: `git diff --stat`
Expected: only skill runtime, built-in skills, tests, and graph/state wiring changed.
