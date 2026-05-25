# Canvas Paging Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `canvas_read_before` and `canvas_read_after` tools that expand the agent's current reading context from `canvas_snapshot` while preserving source document order.

**Architecture:** Add a small `canvas_context` service that parses snapshot blocks, resolves paging anchors, merges structured context blocks, and renders prompt text. Paging tools return structured deltas, and `tools_node` applies those deltas to the current task before the next LLM call.

**Tech Stack:** Python, LangGraph, LangChain tools, Pydantic, unittest.

---

## File Structure

- Create: `moss_backend/app/services/canvas_context.py`
  - Owns structured context block parsing, neighbor reads, merge, anchor resolution, and prompt rendering.
- Create: `moss_backend/tests/test_canvas_context.py`
  - Unit tests for ordering, neighbor reads, de-duplication, default anchors, and gap rendering.
- Modify: `moss_backend/app/tools/document_tools.py`
  - Add `canvas_read_before` and `canvas_read_after`; add them to `DOCUMENT_TOOLS`.
- Create: `moss_backend/tests/test_document_tools_paging.py`
  - Unit tests for tool output and state-based anchor fallback.
- Modify: `moss_backend/app/agent/state.py`
  - Add optional `canvas_context_blocks` to `AgentTask`.
- Modify: `moss_backend/app/agent/graph.py`
  - Seed structured context blocks during task assembly, inject state into stateful tools, merge paging tool results, rebuild `canvas_context`, and rebuild `task_prompt`.
- Create: `moss_backend/tests/test_canvas_paging_graph.py`
  - Graph-node tests for task assembly and tool-result merge.
- Modify: `moss_backend/app/agent/prompts/document_qa_prompt.yaml`
  - Clarify that before/after read tools expand the current context.
- Modify: `moss_backend/app/agent/prompts/local_edit_prompt.yaml`
  - Clarify that before/after read tools can inspect nearby context before editing.

## Task 1: Add Structured Canvas Context Service

**Files:**
- Create: `moss_backend/tests/test_canvas_context.py`
- Create: `moss_backend/app/services/canvas_context.py`

- [ ] **Step 1: Write failing tests for parsing, paging, merging, and rendering**

Create `moss_backend/tests/test_canvas_context.py`:

```python
from __future__ import annotations

import unittest

from app.services.canvas_context import (
    clamp_block_count,
    context_blocks_from_html,
    merge_canvas_context_blocks,
    read_neighbor_blocks,
    render_canvas_context,
    resolve_paging_anchor,
)


def _snapshot(block_count: int) -> str:
    return "".join(
        f'<p id="moss-block-{index}">block {index}</p>'
        for index in range(block_count)
    )


class CanvasContextTests(unittest.TestCase):
    def test_context_blocks_from_html_uses_snapshot_order_not_id_order(self) -> None:
        snapshot = (
            '<p id="moss-block-z">first</p>'
            '<p id="moss-block-a">second</p>'
        )

        blocks = context_blocks_from_html(
            canvas_snapshot=snapshot,
            context_html='<p id="moss-block-a">second</p><p id="moss-block-z">first</p>',
            source="initial",
            added_at=1,
        )

        self.assertEqual([block["block_id"] for block in blocks], ["moss-block-z", "moss-block-a"])
        self.assertEqual([block["index"] for block in blocks], [0, 1])

    def test_read_neighbor_blocks_before_returns_previous_blocks_in_document_order(self) -> None:
        result = read_neighbor_blocks(
            canvas_snapshot=_snapshot(8),
            anchor_block_id="moss-block-4",
            direction="before",
            block_count=3,
            added_at=2,
        )

        self.assertEqual(result["anchor_index"], 4)
        self.assertEqual([block["block_id"] for block in result["blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
        ])
        self.assertEqual(result["warnings"], [])

    def test_read_neighbor_blocks_after_clamps_at_document_end(self) -> None:
        result = read_neighbor_blocks(
            canvas_snapshot=_snapshot(5),
            anchor_block_id="moss-block-3",
            direction="after",
            block_count=8,
            added_at=2,
        )

        self.assertEqual([block["block_id"] for block in result["blocks"]], ["moss-block-4"])

    def test_read_neighbor_blocks_reports_missing_anchor(self) -> None:
        result = read_neighbor_blocks(
            canvas_snapshot=_snapshot(3),
            anchor_block_id="moss-block-missing",
            direction="after",
            block_count=2,
            added_at=2,
        )

        self.assertEqual(result["blocks"], [])
        self.assertIn("anchor_block_id not found", result["warnings"])

    def test_merge_orders_by_snapshot_index_and_deduplicates(self) -> None:
        existing = context_blocks_from_html(
            canvas_snapshot=_snapshot(6),
            context_html='<p id="moss-block-2">block 2</p><p id="moss-block-3">block 3</p>',
            source="initial",
            added_at=1,
        )
        addition = read_neighbor_blocks(
            canvas_snapshot=_snapshot(6),
            anchor_block_id="moss-block-2",
            direction="before",
            block_count=3,
            added_at=2,
        )["blocks"]

        merged = merge_canvas_context_blocks(existing, addition)

        self.assertEqual([block["block_id"] for block in merged], [
            "moss-block-0",
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
        ])

    def test_resolve_paging_anchor_uses_context_edge_before_focus(self) -> None:
        context_blocks = context_blocks_from_html(
            canvas_snapshot=_snapshot(5),
            context_html='<p id="moss-block-1">block 1</p><p id="moss-block-3">block 3</p>',
            source="initial",
            added_at=1,
        )

        self.assertEqual(
            resolve_paging_anchor(
                direction="before",
                explicit_anchor_block_id=None,
                context_blocks=context_blocks,
                focus_block_id="moss-block-2",
            ),
            "moss-block-1",
        )
        self.assertEqual(
            resolve_paging_anchor(
                direction="after",
                explicit_anchor_block_id=None,
                context_blocks=context_blocks,
                focus_block_id="moss-block-2",
            ),
            "moss-block-3",
        )

    def test_resolve_paging_anchor_falls_back_to_focus_for_empty_context(self) -> None:
        self.assertEqual(
            resolve_paging_anchor(
                direction="after",
                explicit_anchor_block_id=None,
                context_blocks=[],
                focus_block_id="moss-block-2",
            ),
            "moss-block-2",
        )

    def test_render_canvas_context_marks_gaps(self) -> None:
        first = context_blocks_from_html(
            canvas_snapshot=_snapshot(6),
            context_html='<p id="moss-block-0">block 0</p>',
            source="initial",
            added_at=1,
        )
        second = context_blocks_from_html(
            canvas_snapshot=_snapshot(6),
            context_html='<p id="moss-block-3">block 3</p>',
            source="read_after",
            added_at=2,
        )

        rendered = render_canvas_context(merge_canvas_context_blocks(first, second))

        self.assertIn("[Omitted blocks 1-2]", rendered)
        self.assertLess(rendered.index("moss-block-0"), rendered.index("moss-block-3"))

    def test_clamp_block_count_limits_single_tool_call_size(self) -> None:
        self.assertEqual(clamp_block_count(0), 1)
        self.assertEqual(clamp_block_count(3), 3)
        self.assertEqual(clamp_block_count(99), 8)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_context -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.canvas_context'`.

- [ ] **Step 3: Implement the canvas context service**

Create `moss_backend/app/services/canvas_context.py`:

```python
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Literal

from app.services.document_content import _extract_moss_blocks


CanvasDirection = Literal["before", "after"]
MAX_READ_BLOCKS = 8


def clamp_block_count(block_count: Any) -> int:
    try:
        value = int(block_count)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, MAX_READ_BLOCKS))


def parse_canvas_snapshot_blocks(canvas_snapshot: str) -> list[dict[str, Any]]:
    raw_blocks = _extract_moss_blocks(canvas_snapshot)
    parsed_blocks: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []

    for index, raw_block in enumerate(raw_blocks):
        tag, text = _extract_tag_and_text(raw_block.html)
        normalized_text = _normalize_visible_text(text)
        heading_level = _heading_level(tag)

        if heading_level is not None:
            heading_stack = [(level, title) for level, title in heading_stack if level < heading_level]
            heading_path = [title for _, title in heading_stack]
            if normalized_text:
                heading_path.append(normalized_text)
                heading_stack.append((heading_level, normalized_text))
        else:
            heading_path = [title for _, title in heading_stack]

        parsed_blocks.append(
            {
                "block_id": raw_block.block_id,
                "index": index,
                "tag": tag,
                "heading_path": heading_path,
                "text": normalized_text,
                "html": raw_block.html,
            }
        )

    return parsed_blocks


def context_blocks_from_html(
    *,
    canvas_snapshot: str,
    context_html: str,
    source: str,
    added_at: int,
) -> list[dict[str, Any]]:
    context_ids = {block.block_id for block in _extract_moss_blocks(context_html or "")}
    blocks = [
        _with_context_metadata(block, source=source, added_at=added_at)
        for block in parse_canvas_snapshot_blocks(canvas_snapshot)
        if block["block_id"] in context_ids
    ]
    return sorted(blocks, key=lambda block: int(block["index"]))


def read_neighbor_blocks(
    *,
    canvas_snapshot: str,
    anchor_block_id: str | None,
    direction: CanvasDirection,
    block_count: Any,
    added_at: int,
) -> dict[str, Any]:
    blocks = parse_canvas_snapshot_blocks(canvas_snapshot)
    count = clamp_block_count(block_count)
    warnings: list[str] = []

    if not blocks:
        return _read_result(direction, anchor_block_id, None, [], ["canvas_snapshot has no moss-block elements"])

    if not anchor_block_id:
        return _read_result(direction, anchor_block_id, None, [], ["anchor_block_id is required"])

    anchor_index = _find_block_index(blocks, anchor_block_id)
    if anchor_index is None:
        return _read_result(direction, anchor_block_id, None, [], ["anchor_block_id not found"])

    if direction == "before":
        start = max(0, anchor_index - count)
        end = anchor_index
        source = "read_before"
    else:
        start = anchor_index + 1
        end = min(len(blocks), anchor_index + 1 + count)
        source = "read_after"

    selected = [
        _with_context_metadata(block, source=source, added_at=added_at)
        for block in blocks[start:end]
    ]
    return _read_result(direction, anchor_block_id, anchor_index, selected, warnings)


def resolve_paging_anchor(
    *,
    direction: CanvasDirection,
    explicit_anchor_block_id: str | None,
    context_blocks: list[dict[str, Any]],
    focus_block_id: str | None,
) -> str | None:
    if explicit_anchor_block_id:
        return explicit_anchor_block_id

    if context_blocks:
        sorted_blocks = sorted(context_blocks, key=lambda block: int(block["index"]))
        if direction == "before":
            return str(sorted_blocks[0]["block_id"])
        return str(sorted_blocks[-1]["block_id"])

    return focus_block_id


def merge_canvas_context_blocks(
    existing_blocks: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for block in existing_blocks:
        block_id = str(block.get("block_id") or "")
        if block_id:
            merged[block_id] = dict(block)

    for block in new_blocks:
        block_id = str(block.get("block_id") or "")
        if block_id and block_id not in merged:
            merged[block_id] = dict(block)

    return sorted(merged.values(), key=lambda block: int(block["index"]))


def render_canvas_context(context_blocks: list[dict[str, Any]]) -> str:
    ordered = sorted(context_blocks, key=lambda block: int(block["index"]))
    if not ordered:
        return ""

    lines = [
        "[Canvas Context]",
        "The following blocks are ordered by their position in canvas_snapshot.",
        "Gap markers mean intervening blocks have not been loaded into this context.",
        "",
    ]
    previous_index: int | None = None

    for block in ordered:
        index = int(block["index"])
        if previous_index is not None and index > previous_index + 1:
            lines.append(f"[Omitted blocks {previous_index + 1}-{index - 1}]")
            lines.append("")

        heading_path = block.get("heading_path") or []
        heading_suffix = f" | {' / '.join(heading_path)}" if heading_path else ""
        lines.append(
            f"[Block {index} | id={block['block_id']} | tag={block.get('tag', 'unknown')}{heading_suffix}]"
        )
        lines.append(str(block.get("html") or block.get("text") or ""))
        lines.append("")
        previous_index = index

    return "\n".join(lines).rstrip()


def _read_result(
    direction: CanvasDirection,
    anchor_block_id: str | None,
    anchor_index: int | None,
    blocks: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "operation": "canvas_context_add",
        "direction": direction,
        "anchor_block_id": anchor_block_id,
        "anchor_index": anchor_index,
        "blocks": blocks,
        "warnings": warnings,
    }


def _with_context_metadata(
    block: dict[str, Any],
    *,
    source: str,
    added_at: int,
) -> dict[str, Any]:
    item = dict(block)
    item["source"] = source
    item["added_at"] = added_at
    return item


def _find_block_index(blocks: list[dict[str, Any]], block_id: str) -> int | None:
    for index, block in enumerate(blocks):
        if block["block_id"] == block_id:
            return index
    return None


def _extract_tag_and_text(html: str) -> tuple[str, str]:
    parser = BlockTextParser()
    parser.feed(html or "")
    parser.close()
    return parser.first_tag or "unknown", " ".join(parser.text_parts)


def _normalize_visible_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _heading_level(tag: str) -> int | None:
    if len(tag) == 2 and tag.startswith("h") and tag[1].isdigit():
        level = int(tag[1])
        if 1 <= level <= 6:
            return level
    return None


class BlockTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.first_tag: str | None = None
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.first_tag is None:
            self.first_tag = tag.lower()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.first_tag is None:
            self.first_tag = tag.lower()

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.text_parts.append(data.strip())
```

- [ ] **Step 4: Run the canvas context tests**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_context -v
```

Expected: PASS.

## Task 2: Add Paging Tools

**Files:**
- Modify: `moss_backend/app/tools/document_tools.py`
- Create: `moss_backend/tests/test_document_tools_paging.py`

- [ ] **Step 1: Write failing tests for paging tool JSON output**

Create `moss_backend/tests/test_document_tools_paging.py`:

```python
from __future__ import annotations

import json
import unittest

from app.tools.document_tools import canvas_read_after, canvas_read_before


def _snapshot(block_count: int) -> str:
    return "".join(
        f'<p id="moss-block-{index}">block {index}</p>'
        for index in range(block_count)
    )


class DocumentPagingToolTests(unittest.TestCase):
    def test_canvas_read_after_uses_explicit_anchor(self) -> None:
        payload = json.loads(
            canvas_read_after.invoke(
                {
                    "anchor_block_id": "moss-block-2",
                    "block_count": 2,
                    "state": {
                        "canvas_snapshot": _snapshot(6),
                        "focus_block_id": "moss-block-1",
                        "tasks": [],
                        "current_task_index": 0,
                    },
                }
            )
        )

        self.assertEqual(payload["operation"], "canvas_context_add")
        self.assertEqual(payload["direction"], "after")
        self.assertEqual([block["block_id"] for block in payload["blocks"]], [
            "moss-block-3",
            "moss-block-4",
        ])

    def test_canvas_read_before_uses_context_edge_when_anchor_is_missing(self) -> None:
        payload = json.loads(
            canvas_read_before.invoke(
                {
                    "block_count": 2,
                    "state": {
                        "canvas_snapshot": _snapshot(7),
                        "focus_block_id": "moss-block-4",
                        "current_task_index": 0,
                        "tasks": [
                            {
                                "canvas_context_blocks": [
                                    {
                                        "block_id": "moss-block-3",
                                        "index": 3,
                                        "tag": "p",
                                        "heading_path": [],
                                        "text": "block 3",
                                        "html": '<p id="moss-block-3">block 3</p>',
                                        "source": "initial",
                                        "added_at": 1,
                                    },
                                    {
                                        "block_id": "moss-block-5",
                                        "index": 5,
                                        "tag": "p",
                                        "heading_path": [],
                                        "text": "block 5",
                                        "html": '<p id="moss-block-5">block 5</p>',
                                        "source": "initial",
                                        "added_at": 1,
                                    },
                                ]
                            }
                        ],
                    },
                }
            )
        )

        self.assertEqual(payload["anchor_block_id"], "moss-block-3")
        self.assertEqual([block["block_id"] for block in payload["blocks"]], [
            "moss-block-1",
            "moss-block-2",
        ])

    def test_canvas_read_after_falls_back_to_focus_for_empty_context(self) -> None:
        payload = json.loads(
            canvas_read_after.invoke(
                {
                    "block_count": 1,
                    "state": {
                        "canvas_snapshot": _snapshot(4),
                        "focus_block_id": "moss-block-1",
                        "current_task_index": 0,
                        "tasks": [{"canvas_context_blocks": []}],
                    },
                }
            )
        )

        self.assertEqual(payload["anchor_block_id"], "moss-block-1")
        self.assertEqual([block["block_id"] for block in payload["blocks"]], ["moss-block-2"])

    def test_canvas_read_after_reports_empty_snapshot(self) -> None:
        payload = json.loads(
            canvas_read_after.invoke(
                {
                    "block_count": 2,
                    "state": {
                        "canvas_snapshot": "",
                        "focus_block_id": "moss-block-1",
                        "current_task_index": 0,
                        "tasks": [],
                    },
                }
            )
        )

        self.assertEqual(payload["blocks"], [])
        self.assertIn("canvas_snapshot has no moss-block elements", payload["warnings"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tool tests to verify they fail**

Run:

```bash
cd moss_backend
python -m unittest tests.test_document_tools_paging -v
```

Expected: FAIL with `ImportError` because `canvas_read_after` and `canvas_read_before` do not exist.

- [ ] **Step 3: Add tool imports and state helpers**

Modify `moss_backend/app/tools/document_tools.py`.

Add these imports near the existing imports:

```python
from app.services.canvas_context import (
    read_neighbor_blocks,
    resolve_paging_anchor,
)
```

Add this helper above the new tools:

```python
def _current_task_from_state(state: dict[str, Any]) -> dict[str, Any]:
    tasks = state.get("tasks") or []
    current_index = state.get("current_task_index", 0)
    if not isinstance(tasks, list):
        return {}
    try:
        index = int(current_index)
    except (TypeError, ValueError):
        index = 0
    if index < 0 or index >= len(tasks):
        return {}
    task = tasks[index]
    return task if isinstance(task, dict) else {}
```

- [ ] **Step 4: Add `canvas_read_before` and `canvas_read_after`**

Add these functions in `moss_backend/app/tools/document_tools.py` after `search_document_blocks` and before `update_canvas_element`:

```python
@tool
def canvas_read_before(
    anchor_block_id: Annotated[
        str | None,
        "Optional moss-block id to read before. If omitted, use the earliest block in current canvas_context, then focus_block_id.",
    ] = None,
    block_count: Annotated[
        int,
        "Number of previous canvas blocks to read. Values are clamped to 1..8.",
    ] = 3,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Read blocks before an anchor from canvas_snapshot and return a structured context delta."""

    state = state or {}
    task = _current_task_from_state(state)
    context_blocks = task.get("canvas_context_blocks") or []
    anchor = resolve_paging_anchor(
        direction="before",
        explicit_anchor_block_id=anchor_block_id,
        context_blocks=context_blocks if isinstance(context_blocks, list) else [],
        focus_block_id=state.get("focus_block_id"),
    )
    result = read_neighbor_blocks(
        canvas_snapshot=str(state.get("canvas_snapshot") or ""),
        anchor_block_id=anchor,
        direction="before",
        block_count=block_count,
        added_at=_next_context_operation_seq(task),
    )
    return _json_result(result)


@tool
def canvas_read_after(
    anchor_block_id: Annotated[
        str | None,
        "Optional moss-block id to read after. If omitted, use the latest block in current canvas_context, then focus_block_id.",
    ] = None,
    block_count: Annotated[
        int,
        "Number of following canvas blocks to read. Values are clamped to 1..8.",
    ] = 3,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Read blocks after an anchor from canvas_snapshot and return a structured context delta."""

    state = state or {}
    task = _current_task_from_state(state)
    context_blocks = task.get("canvas_context_blocks") or []
    anchor = resolve_paging_anchor(
        direction="after",
        explicit_anchor_block_id=anchor_block_id,
        context_blocks=context_blocks if isinstance(context_blocks, list) else [],
        focus_block_id=state.get("focus_block_id"),
    )
    result = read_neighbor_blocks(
        canvas_snapshot=str(state.get("canvas_snapshot") or ""),
        anchor_block_id=anchor,
        direction="after",
        block_count=block_count,
        added_at=_next_context_operation_seq(task),
    )
    return _json_result(result)
```

Add this helper near `_current_task_from_state`:

```python
def _next_context_operation_seq(task: dict[str, Any]) -> int:
    current = task.get("canvas_context_operation_seq", 0)
    try:
        return int(current) + 1
    except (TypeError, ValueError):
        return 1
```

Update the tool registry:

```python
DOCUMENT_TOOLS = [
    search_document_blocks,
    canvas_read_before,
    canvas_read_after,
    update_canvas_element,
    generate_download_link,
]
```

- [ ] **Step 5: Run the paging tool tests**

Run:

```bash
cd moss_backend
python -m unittest tests.test_document_tools_paging -v
```

Expected: PASS.

## Task 3: Seed And Maintain Structured Context In The Graph

**Files:**
- Modify: `moss_backend/app/agent/state.py`
- Modify: `moss_backend/app/agent/graph.py`
- Create: `moss_backend/tests/test_canvas_paging_graph.py`

- [ ] **Step 1: Write failing graph tests**

Create `moss_backend/tests/test_canvas_paging_graph.py`:

```python
from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage

from app.agent.graph import task_assemble_node, tools_node


def _snapshot(block_count: int) -> str:
    return "".join(
        f'<p id="moss-block-{index}">block {index}</p>'
        for index in range(block_count)
    )


class CanvasPagingGraphTests(unittest.TestCase):
    def test_task_assemble_seeds_structured_context_blocks(self) -> None:
        state = {
            "task_type": "document_qa",
            "canvas_snapshot": _snapshot(6),
            "focus_block_id": "moss-block-2",
            "focus_element_id": "moss-block-2",
            "user_input": "What comes next?",
        }

        result = task_assemble_node(state)
        task = result["tasks"][0]

        self.assertIn("canvas_context_blocks", task)
        self.assertEqual([block["block_id"] for block in task["canvas_context_blocks"]], [
            "moss-block-0",
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
            "moss-block-5",
        ])

    def test_tools_node_injects_state_and_merges_read_after_result_into_task_context(self) -> None:
        initial_state = {
            "messages": [],
            "user_input": "What comes next?",
            "canvas_snapshot": _snapshot(6),
            "focus_element_id": "moss-block-1",
            "focus_block_id": "moss-block-1",
            "task_type": "document_qa",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
        }
        assembled = task_assemble_node(initial_state)
        task = assembled["tasks"][0]
        task["canvas_context_blocks"] = [
            block for block in task["canvas_context_blocks"]
            if block["block_id"] in {"moss-block-1", "moss-block-2"}
        ]
        task["canvas_context"] = '<p id="moss-block-1">block 1</p><p id="moss-block-2">block 2</p>'
        task["task_message"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "canvas_read_after",
                        "args": {"block_count": 2},
                        "id": "call-read-after",
                        "type": "tool_call",
                    }
                ],
            )
        ]
        state = {**initial_state, "tasks": [task]}

        result = tools_node(state)
        updated_task = result["tasks"][0]

        self.assertEqual([block["block_id"] for block in updated_task["canvas_context_blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
        ])
        self.assertIn('id="moss-block-4"', updated_task["canvas_context"])
        self.assertIn('id="moss-block-4"', updated_task["task_prompt"])

    def test_tools_node_merges_read_before_result_in_snapshot_order(self) -> None:
        initial_state = {
            "messages": [],
            "user_input": "What came before?",
            "canvas_snapshot": _snapshot(6),
            "focus_element_id": "moss-block-3",
            "focus_block_id": "moss-block-3",
            "task_type": "document_qa",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
        }
        assembled = task_assemble_node(initial_state)
        task = assembled["tasks"][0]
        task["canvas_context_blocks"] = [
            block for block in task["canvas_context_blocks"]
            if block["block_id"] in {"moss-block-3", "moss-block-4"}
        ]
        task["canvas_context"] = '<p id="moss-block-3">block 3</p><p id="moss-block-4">block 4</p>'
        task["task_message"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "canvas_read_before",
                        "args": {"block_count": 2},
                        "id": "call-read-before",
                        "type": "tool_call",
                    }
                ],
            )
        ]
        state = {**initial_state, "tasks": [task]}

        result = tools_node(state)
        updated_task = result["tasks"][0]
        rendered = updated_task["canvas_context"]

        self.assertLess(rendered.index("moss-block-1"), rendered.index("moss-block-4"))
        self.assertEqual([block["block_id"] for block in updated_task["canvas_context_blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
        ])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the graph tests to verify they fail**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_paging_graph -v
```

Expected: FAIL because `canvas_context_blocks` is not seeded and `tools_node` does not merge paging results.

- [ ] **Step 3: Extend AgentTask state shape**

Modify `moss_backend/app/agent/state.py`.

Add `Any` to the typing import:

```python
from typing import Annotated, Any, Literal, TypedDict
```

Add these optional fields to `AgentTask` after `canvas_context`:

```python
    # Structured reading context used internally for canvas paging tools.
    # The prompt-facing value remains canvas_context.
    canvas_context_blocks: list[dict[str, Any]]
    canvas_context_operation_seq: int
```

- [ ] **Step 4: Add graph imports for context management**

Modify `moss_backend/app/agent/graph.py`.

Add these imports near the existing service/tool imports:

```python
import json

from app.services.canvas_context import (
    context_blocks_from_html,
    merge_canvas_context_blocks,
    render_canvas_context,
)
```

- [ ] **Step 5: Add prompt formatting helpers**

In `moss_backend/app/agent/graph.py`, add this helper below `TASK_TYPE_TOOLS`:

```python
def _prompt_template_for_task_type(task_type: TaskType) -> PromptTemplate:
    if task_type == "document_qa":
        return _load_prompt_template(PROMPTS_DIR / "document_qa_prompt.yaml")
    if task_type == "local_edit":
        return _load_prompt_template(PROMPTS_DIR / "local_edit_prompt.yaml")
    if task_type == "global_edit":
        return _load_prompt_template(PROMPTS_DIR / "global_edit_prompt.yaml")
    return _load_prompt_template(PROMPTS_DIR / "general_chat_prompt.yaml")


def _format_task_prompt(
    *,
    task_type: TaskType,
    user_input: str,
    canvas_context: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    task_tools: list[str],
) -> str:
    return _prompt_template_for_task_type(task_type).format(
        user_input=user_input,
        canvas_context=canvas_context,
        focus_element_id=focus_element_id or "",
        focus_block_id=focus_block_id or "",
        task_tools=str(task_tools),
    )
```

Update `TASK_TYPE_TOOLS`:

```python
TASK_TYPE_TOOLS: dict[TaskType, list[str]] = {
    "general_chat": [],
    "document_qa": ["search_document_blocks", "canvas_read_before", "canvas_read_after"],
    "local_edit": [
        "search_document_blocks",
        "canvas_read_before",
        "canvas_read_after",
        "update_canvas_element",
    ],
    "global_edit": ["update_canvas_element"],
}
```

- [ ] **Step 6: Seed structured context blocks in task assembly**

In `task_assemble_node`, replace the repeated prompt selection branches with `_prompt_template_for_task_type` and build context blocks for each chunk.

Inside the `for chunk in context_chunks:` loop, before creating `task_prompt`, add:

```python
        canvas_context_blocks = context_blocks_from_html(
            canvas_snapshot=canvas_snapshot,
            context_html=chunk,
            source="initial",
            added_at=0,
        )
        rendered_context = render_canvas_context(canvas_context_blocks) if canvas_context_blocks else chunk
        task_prompt = _format_task_prompt(
            task_type=task_type,
            user_input=user_input,
            canvas_context=rendered_context,
            focus_element_id=focus_element_id,
            focus_block_id=focus_block_id,
            task_tools=task_tools,
        )
```

When constructing `AgentTask`, use:

```python
        task = AgentTask(
            task_id=uuid4().hex,
            task_message=[],
            canvas_context=rendered_context,
            canvas_context_blocks=canvas_context_blocks,
            canvas_context_operation_seq=0,
            task_prompt=task_prompt,
            task_tools=task_tools,
            allowed_element_ids=[],
            status="pending",
        )
```

- [ ] **Step 7: Inject state into stateful tools**

In `moss_backend/app/agent/graph.py`, add this constant near `DOCUMENT_TOOLS` usage:

```python
STATEFUL_DOCUMENT_TOOL_NAMES = {
    "search_document_blocks",
    "canvas_read_before",
    "canvas_read_after",
}
```

In `tools_node`, after `args = dict(tool_call["args"])`, add:

```python
            if tool_call["name"] in STATEFUL_DOCUMENT_TOOL_NAMES:
                args["state"] = state
```

This also fixes existing `search_document_blocks` state access for the custom tool node.

- [ ] **Step 8: Add paging result merge helper**

Add this helper above `tools_node`:

```python
def _apply_canvas_context_tool_result(
    *,
    state: AgentState,
    task: AgentTask,
    result_str: str,
) -> AgentTask:
    try:
        payload = json.loads(result_str)
    except json.JSONDecodeError:
        return task

    if not isinstance(payload, dict) or payload.get("operation") != "canvas_context_add":
        return task

    new_blocks = payload.get("blocks")
    if not isinstance(new_blocks, list):
        return task

    existing_blocks = task.get("canvas_context_blocks", [])
    if not isinstance(existing_blocks, list):
        existing_blocks = []

    merged_blocks = merge_canvas_context_blocks(existing_blocks, new_blocks)
    rendered_context = render_canvas_context(merged_blocks)
    operation_seq = int(task.get("canvas_context_operation_seq", 0)) + 1
    task_tools = list(task.get("task_tools", []))
    task_type: TaskType = state.get("task_type", "general_chat")

    return AgentTask(
        **{
            **task,
            "canvas_context_blocks": merged_blocks,
            "canvas_context_operation_seq": operation_seq,
            "canvas_context": rendered_context,
            "task_prompt": _format_task_prompt(
                task_type=task_type,
                user_input=state.get("user_input", ""),
                canvas_context=rendered_context,
                focus_element_id=state.get("focus_element_id"),
                focus_block_id=state.get("focus_block_id"),
                task_tools=task_tools,
            ),
        }
    )
```

In `tools_node`, after `result_str` is assigned and before appending the `ToolMessage`, add:

```python
            task = _apply_canvas_context_tool_result(
                state=state,
                task=task,
                result_str=result_str,
            )
```

At the end of `tools_node`, keep using the possibly updated `task`:

```python
    tasks[current_idx] = {**task, "task_message": task_messages + tool_results}
```

- [ ] **Step 9: Run graph tests**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_paging_graph -v
```

Expected: PASS.

## Task 4: Update Tool Guidance In Prompts

**Files:**
- Modify: `moss_backend/app/agent/prompts/document_qa_prompt.yaml`
- Modify: `moss_backend/app/agent/prompts/local_edit_prompt.yaml`
- Test: `moss_backend/tests/test_canvas_paging_graph.py`

- [ ] **Step 1: Add prompt assertions**

Append this test to `moss_backend/tests/test_canvas_paging_graph.py` inside `CanvasPagingGraphTests`:

```python
    def test_document_qa_prompt_exposes_paging_tools(self) -> None:
        state = {
            "task_type": "document_qa",
            "canvas_snapshot": _snapshot(4),
            "focus_block_id": "moss-block-1",
            "focus_element_id": "moss-block-1",
            "user_input": "Read around this point",
        }

        task = task_assemble_node(state)["tasks"][0]

        self.assertIn("canvas_read_before", task["task_prompt"])
        self.assertIn("canvas_read_after", task["task_prompt"])
        self.assertIn("ordered by their position in canvas_snapshot", task["task_prompt"])
```

- [ ] **Step 2: Run the prompt assertion**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_paging_graph.CanvasPagingGraphTests.test_document_qa_prompt_exposes_paging_tools -v
```

Expected: PASS after Task 3 because `task_tools` and rendered context are already included. If it fails because the prompt omits `canvas_context`, fix the prompt template before continuing.

- [ ] **Step 3: Update document QA prompt wording**

Modify `moss_backend/app/agent/prompts/document_qa_prompt.yaml` template text so the tool section communicates this behavior in Chinese:

```text
如果当前文段不足以回答问题，可以使用 canvas_read_before 或 canvas_read_after 读取当前位置前后更多 canvas_snapshot 文段。工具返回的新文段会按原文顺序合并进 canvas_context。
```

Keep the existing `{task_tools}` interpolation.

- [ ] **Step 4: Update local edit prompt wording**

Modify `moss_backend/app/agent/prompts/local_edit_prompt.yaml` template text so the tool section communicates this behavior in Chinese:

```text
如果修改前需要确认上下文，可以使用 canvas_read_before 或 canvas_read_after 查看当前文段前后的内容。不要把搜索结果或未读取内容当作原文证据。
```

Keep the existing `{task_tools}` interpolation.

- [ ] **Step 5: Run prompt and graph tests**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_paging_graph -v
```

Expected: PASS.

## Task 5: Regression Verification

**Files:**
- Verify existing tests only.

- [ ] **Step 1: Run focused canvas and tool tests**

Run:

```bash
cd moss_backend
python -m unittest tests.test_canvas_context tests.test_document_tools_paging tests.test_canvas_paging_graph -v
```

Expected: PASS.

- [ ] **Step 2: Run existing document content and document tool adjacent tests**

Run:

```bash
cd moss_backend
python -m unittest tests.test_document_content tests.test_intent_routing -v
```

Expected: PASS, unless the repository already has a known unrelated expectation mismatch around `document_qa` support in `test_document_content.py`.

- [ ] **Step 3: Run the full backend test suite**

Run:

```bash
cd moss_backend
python -m unittest discover tests -v
```

Expected: PASS for paging-related tests. If unrelated pre-existing failures appear, record the failing test names and confirm the focused paging tests still pass.

## Self-Review

Spec coverage:

- Before and after tools are covered by Tasks 2 and 3.
- Structured context blocks are covered by Task 1 and seeded by Task 3.
- Snapshot-order rendering is covered by Task 1 and Task 3.
- Duplicate block avoidance is covered by Task 1.
- Prompt refresh after paging is covered by Task 3.
- Budget management is intentionally outside V1 and represented only by `block_count` clamping.

Placeholder scan:

- The plan contains concrete paths, test code, implementation snippets, commands, and expected outcomes.

Type consistency:

- Tool result operation name is consistently `canvas_context_add`.
- Structured context field is consistently `canvas_context_blocks`.
- Operation sequence field is consistently `canvas_context_operation_seq`.
- Paging tools are consistently named `canvas_read_before` and `canvas_read_after`.
