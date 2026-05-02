from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.agent.state import AgentState


HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
MAX_PREVIEW_CHARS = 360
MAX_OUTLINE_ITEMS = 80
GLOBAL_BATCH_BLOCK_LIMIT = 1
GLOBAL_BATCH_CHAR_LIMIT = 8000


def classify_intent(user_input: str, focus_element_id: str | None = None) -> tuple[str, str]:
    text = user_input.strip()
    normalized = text.lower()

    edit_words = (
        "修改",
        "改写",
        "润色",
        "优化",
        "调整",
        "替换",
        "删除",
        "移除",
        "新增",
        "追加",
        "插入",
        "补充",
        "精简",
        "扩写",
        "排版",
        "统一",
        "整理",
        "重写",
        "rewrite",
        "polish",
        "edit",
        "format",
    )
    global_words = (
        "全文",
        "整篇",
        "通篇",
        "全部",
        "所有",
        "整体",
        "全局",
        "整份",
        "整本文档",
        "统一风格",
        "full document",
        "whole document",
    )
    document_words = (
        "文档",
        "这篇",
        "上面",
        "当前内容",
        "内容",
        "总结",
        "概括",
        "提炼",
        "分析",
        "根据",
        "查找",
        "找出",
        "在哪",
        "有哪些",
        "为什么",
        "document",
        "find",
        "mentioned",
        "summarize",
        "summary",
    )

    has_edit = any(word in normalized for word in edit_words)
    has_global = any(word in normalized for word in global_words)
    has_document = any(word in normalized for word in document_words)

    if has_edit and has_global:
        return "global_edit", "用户要求对全文或全局范围执行编辑类操作。"
    if has_edit:
        if focus_element_id:
            return "local_edit", "用户要求编辑，且请求携带焦点元素。"
        return "local_edit", "用户要求编辑，但未明确全文范围，按局部编辑处理并允许检索兜底。"
    if has_document:
        return "document_qa", "用户在询问、总结或分析文档内容。"
    return "general_chat", "用户输入未表现出依赖文档内容或编辑文档的需求。"


def build_context_update(state: AgentState) -> dict[str, Any]:
    parsed = parse_canvas_snapshot(state.get("canvas_snapshot", ""))
    intent = state.get("intent", "general_chat")
    focus_element_id = state.get("focus_element_id")
    focus_block_id = state.get("focus_block_id")
    retrieved_ids = _unique(state.get("retrieved_block_ids", []))

    focus = _resolve_focus(parsed["blocks"], focus_element_id, focus_block_id)
    if intent == "general_chat":
        canvas_context = "当前任务不包含文档上下文。不要引用或假设文档正文。"
        context_block_ids: list[str] = []
        allowed_element_ids: list[str] = _authorized_retrieved_ids(retrieved_ids, parsed["blocks"])
        global_update: dict[str, Any] = {}
    elif intent == "document_qa":
        canvas_context = _build_document_qa_context(parsed, focus)
        context_block_ids = [focus["block_id"]] if focus.get("block_id") else []
        allowed_element_ids = _authorized_retrieved_ids(retrieved_ids, parsed["blocks"])
        global_update = {}
    elif intent == "global_edit":
        global_context = _build_global_context(state, parsed, retrieved_ids)
        canvas_context = global_context["canvas_context"]
        context_block_ids = global_context["current_batch_ids"]
        allowed_element_ids = _unique(
            [*global_context["current_batch_ids"], *_authorized_retrieved_ids(retrieved_ids, parsed["blocks"])]
        )
        global_update = {
            "global_task": global_context["global_task"],
            "batch_index": global_context["batch_index"],
            "batches": global_context["batches"],
            "current_batch_ids": global_context["current_batch_ids"],
        }
    else:
        canvas_context = _build_local_edit_context(parsed, focus)
        context_block_ids = [focus["block_id"]] if focus.get("block_id") else []
        allowed_element_ids = _unique(
            [
                item
                for item in (focus.get("element_id"), focus.get("block_id"))
                if isinstance(item, str) and _id_exists(parsed["blocks"], item)
            ]
            + _authorized_retrieved_ids(retrieved_ids, parsed["blocks"])
        )
        global_update = {}

    return {
        "document_blocks": parsed["blocks"],
        "document_outline": parsed["outline"],
        "block_count": len(parsed["blocks"]),
        "canvas_context": canvas_context,
        "context_block_ids": context_block_ids,
        "allowed_element_ids": allowed_element_ids,
        **global_update,
    }


def parse_canvas_snapshot(canvas_snapshot: str) -> dict[str, Any]:
    soup = BeautifulSoup(f'<div data-moss-root="true">{canvas_snapshot or ""}</div>', "html.parser")
    root = soup.find("div", attrs={"data-moss-root": "true"}) or soup
    children = [child for child in root.children if isinstance(child, Tag)]
    blocks: list[dict[str, Any]] = []
    outline: list[dict[str, Any]] = []
    heading_stack: list[dict[str, Any]] = []

    for index, child in enumerate(children):
        explicit_id = child.get("id")
        block_id = str(explicit_id) if explicit_id else f"__unaddressed_block_{index}"
        block_outline = _collect_outline_for_block(child, block_id, index, heading_stack)
        outline.extend(block_outline)
        text = _normalize_text(child.get_text(" ", strip=True))
        element_ids = _collect_element_ids(child)
        if explicit_id and str(explicit_id) not in element_ids:
            element_ids.insert(0, str(explicit_id))
        blocks.append(
            {
                "block_id": block_id,
                "index": index,
                "tag_name": child.name,
                "addressable": bool(explicit_id),
                "heading_path": [item["title"] for item in heading_stack],
                "text": text,
                "text_preview": _truncate(text, MAX_PREVIEW_CHARS),
                "html": str(child),
                "element_ids": element_ids,
            }
        )

    return {"blocks": blocks, "outline": outline}


def search_document_blocks(state: AgentState, query: str, top_k: int = 5) -> dict[str, Any]:
    blocks = state.get("document_blocks") or parse_canvas_snapshot(state.get("canvas_snapshot", ""))["blocks"]
    addressable_count = sum(1 for block in blocks if block.get("addressable", True))
    limit = max(1, min(int(top_k or 5), 10))
    if addressable_count > 1:
        limit = min(limit, addressable_count - 1)
    tokens = _tokenize(query)
    normalized_query = _normalize_for_search(query)
    scored: list[tuple[float, dict[str, Any]]] = []

    for block in blocks:
        haystack = _normalize_for_search(
            " ".join(
                [
                    block.get("text", ""),
                    " ".join(block.get("heading_path", [])),
                    block.get("block_id", ""),
                ]
            )
        )
        score = 0.0
        if normalized_query and normalized_query in haystack:
            score += 10.0
        for token in tokens:
            if token and token in haystack:
                score += 1.0
        if score:
            scored.append((score, block))

    if not scored:
        scored = [(0.1, block) for block in blocks[:limit]]

    scored.sort(key=lambda item: (-item[0], item[1].get("index", 0)))
    selected = [block for _, block in scored[:limit]]
    return {
        "blocks": [
            {
                "block_id": block["block_id"],
                "heading_path": block.get("heading_path", []),
                "text_preview": block.get("text_preview", ""),
                "html": block.get("html", ""),
            }
            for block in selected
            if block.get("addressable", True)
        ]
    }


def validate_canvas_mutation(
    state: AgentState,
    *,
    element_id: str,
    action_type: str,
    new_html: str,
) -> str | None:
    allowed_ids = set(state.get("allowed_element_ids", []))
    if element_id not in allowed_ids:
        return f"element_id `{element_id}` is not authorized for the current context."
    if action_type not in {"replace", "append", "insert", "delete"}:
        return f"Unsupported action_type `{action_type}`."
    if action_type == "delete":
        return None
    if not new_html.strip():
        return "new_html cannot be empty for non-delete mutations."
    if not html_fragment_contains_id(new_html, element_id):
        return f"new_html must preserve target id `{element_id}`."
    return None


def html_fragment_contains_id(fragment: str, element_id: str) -> bool:
    soup = BeautifulSoup(f'<div data-moss-root="true">{fragment or ""}</div>', "html.parser")
    return soup.find(id=element_id) is not None


def update_authorization_after_retrieval(
    state: AgentState,
    search_result: dict[str, Any],
    query: str,
    top_k: int,
) -> dict[str, Any]:
    returned_ids = [
        block.get("block_id")
        for block in search_result.get("blocks", [])
        if isinstance(block, dict) and block.get("block_id")
    ]
    retrieved_block_ids = _unique([*state.get("retrieved_block_ids", []), *returned_ids])
    allowed_element_ids = _unique([*state.get("allowed_element_ids", []), *returned_ids])
    retrieval_history = [
        *state.get("retrieval_history", []),
        {"query": query, "top_k": top_k, "block_ids": returned_ids},
    ]
    return {
        "retrieved_block_ids": retrieved_block_ids,
        "allowed_element_ids": allowed_element_ids,
        "retrieval_history": retrieval_history,
    }


def mark_global_batch_processed(state: AgentState, mutated_ids: list[str]) -> dict[str, Any]:
    if state.get("intent") != "global_edit":
        return {}
    current_batch_ids = state.get("current_batch_ids", [])
    touched_current_batch = [item for item in mutated_ids if item in current_batch_ids]
    if not touched_current_batch:
        return {}

    batch_index = int(state.get("batch_index", 0))
    batches = state.get("batches", [])
    next_index = min(batch_index + 1, len(batches))
    processed_block_ids = _unique([*state.get("processed_block_ids", []), *touched_current_batch])
    global_task = dict(state.get("global_task", {}))
    global_task["batch_advanced"] = next_index != batch_index
    global_task["status"] = "complete" if next_index >= len(batches) else "in_progress"
    return {
        "batch_index": next_index,
        "processed_block_ids": processed_block_ids,
        "global_task": global_task,
    }


def should_build_next_global_batch(state: AgentState) -> bool:
    if state.get("intent") != "global_edit":
        return False
    if not state.get("global_task", {}).get("batch_advanced"):
        return False
    return int(state.get("batch_index", 0)) < len(state.get("batches", []))


def _build_document_qa_context(parsed: dict[str, Any], focus: dict[str, Any]) -> str:
    lines = [
        "文档问答初始上下文：",
        f"- top_level_block_count: {len(parsed['blocks'])}",
        "",
        "Document outline:",
        *_format_outline(parsed["outline"]),
    ]
    if focus.get("block_id"):
        lines.extend(
            [
                "",
                "Focus block brief:",
                f"- focus_block_id: {focus['block_id']}",
                f"- heading_path: {_format_heading_path(focus.get('heading_path', []))}",
                f"- text_preview: {focus.get('text_preview') or 'empty'}",
            ]
        )
    else:
        lines.extend(["", "Focus block brief: not provided or not found."])
    lines.extend(
        [
            "",
            "如果这些信息不足以回答用户问题，必须调用 search_document_blocks。",
        ]
    )
    return "\n".join(lines)


def _build_local_edit_context(parsed: dict[str, Any], focus: dict[str, Any]) -> str:
    lines = [
        "局部编辑初始上下文：",
        f"- focus_element_id: {focus.get('element_id') or 'not found'}",
        f"- focus_block_id: {focus.get('block_id') or 'not found'}",
    ]
    if focus.get("element_html"):
        lines.extend(["", "Focus element HTML:", "```html", focus["element_html"], "```"])
    if focus.get("block_html"):
        lines.extend(["", "Focus block HTML:", "```html", focus["block_html"], "```"])
    lines.extend(["", "Adjacent block previews:"])
    lines.extend(_format_adjacent_previews(parsed["blocks"], focus.get("block_id")))
    lines.extend(
        [
            "",
            "只能修改 allowed_element_ids 中的元素。若焦点与用户指令明显不匹配，先调用 search_document_blocks。",
        ]
    )
    return "\n".join(lines)


def _build_global_context(
    state: AgentState,
    parsed: dict[str, Any],
    retrieved_ids: list[str],
) -> dict[str, Any]:
    previous_global_task = state.get("global_task", {})
    batches = state.get("batches") or _make_batches(parsed["blocks"])
    batch_index = min(max(int(state.get("batch_index", 0)), 0), len(batches))
    current_batch_ids = batches[batch_index] if batch_index < len(batches) else []
    blocks_by_id = {block["block_id"]: block for block in parsed["blocks"]}
    batch_html = "\n".join(blocks_by_id[block_id]["html"] for block_id in current_batch_ids if block_id in blocks_by_id)
    message_window_start = int(previous_global_task.get("message_window_start", 0) or 0)
    if previous_global_task.get("batch_advanced"):
        message_window_start = len(state.get("messages", []))
    global_task = {
        **previous_global_task,
        "status": "complete" if batch_index >= len(batches) else "in_progress",
        "batch_advanced": False,
        "message_window_start": message_window_start,
        "total_batches": len(batches),
        "total_blocks": len(parsed["blocks"]),
    }
    lines = [
        "全文编辑批处理上下文：",
        f"- top_level_block_count: {len(parsed['blocks'])}",
        f"- batch_index: {batch_index + 1 if current_batch_ids else batch_index}/{len(batches)}",
        f"- current_batch_ids: {json.dumps(current_batch_ids, ensure_ascii=False)}",
        f"- retrieved_block_ids: {json.dumps(retrieved_ids, ensure_ascii=False)}",
        "",
        "Document outline:",
        *_format_outline(parsed["outline"]),
        "",
        "Current batch HTML:",
        "```html",
        batch_html or "<empty-batch />",
        "```",
        "",
        "本轮只能处理 current_batch_ids。完成本批修改后，后端会推进到下一批。",
    ]
    return {
        "canvas_context": "\n".join(lines),
        "global_task": global_task,
        "batch_index": batch_index,
        "batches": batches,
        "current_batch_ids": current_batch_ids,
    }


def _collect_outline_for_block(
    block: Tag,
    block_id: str,
    block_index: int,
    heading_stack: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    headings: list[Tag] = []
    if block.name in HEADING_TAGS:
        headings.append(block)
    headings.extend(block.find_all(HEADING_TAGS))

    for heading in headings:
        level = int(heading.name[1])
        title = _normalize_text(heading.get_text(" ", strip=True)) or f"Untitled heading {block_index + 1}"
        while heading_stack and heading_stack[-1]["level"] >= level:
            heading_stack.pop()
        heading_stack.append({"level": level, "title": title})
        outline.append(
            {
                "block_id": block_id,
                "level": level,
                "title": title,
                "heading_path": [item["title"] for item in heading_stack],
                "index": block_index,
            }
        )
    return outline


def _resolve_focus(
    blocks: list[dict[str, Any]],
    focus_element_id: str | None,
    focus_block_id: str | None,
) -> dict[str, Any]:
    block = None
    if focus_block_id:
        block = next((item for item in blocks if item.get("block_id") == focus_block_id), None)
    if not block and focus_element_id:
        block = next((item for item in blocks if focus_element_id in item.get("element_ids", [])), None)
    if not block:
        return {"element_id": focus_element_id, "block_id": focus_block_id}

    element_html = ""
    if focus_element_id:
        soup = BeautifulSoup(block.get("html", ""), "html.parser")
        element = soup.find(id=focus_element_id)
        if element is not None:
            element_html = str(element)

    return {
        "element_id": focus_element_id if focus_element_id in block.get("element_ids", []) else block.get("block_id"),
        "block_id": block.get("block_id"),
        "heading_path": block.get("heading_path", []),
        "text_preview": block.get("text_preview", ""),
        "element_html": element_html or block.get("html", ""),
        "block_html": block.get("html", ""),
    }


def _make_batches(blocks: list[dict[str, Any]]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for block in blocks:
        if not block.get("addressable", True):
            continue
        html = block.get("html", "")
        block_id = block.get("block_id")
        if not block_id:
            continue
        would_exceed = (
            current
            and (len(current) >= GLOBAL_BATCH_BLOCK_LIMIT or current_chars + len(html) > GLOBAL_BATCH_CHAR_LIMIT)
        )
        if would_exceed:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(block_id)
        current_chars += len(html)

    if current:
        batches.append(current)
    return batches


def _format_outline(outline: list[dict[str, Any]]) -> list[str]:
    if not outline:
        return ["- no headings found"]
    lines = []
    for item in outline[:MAX_OUTLINE_ITEMS]:
        indent = "  " * max(0, int(item.get("level", 1)) - 1)
        lines.append(f"- {indent}H{item.get('level')}: {item.get('title')} [{item.get('block_id')}]")
    if len(outline) > MAX_OUTLINE_ITEMS:
        lines.append(f"- ... {len(outline) - MAX_OUTLINE_ITEMS} more headings omitted")
    return lines


def _format_adjacent_previews(blocks: list[dict[str, Any]], block_id: str | None) -> list[str]:
    if not block_id:
        return ["- none"]
    index = next((idx for idx, block in enumerate(blocks) if block.get("block_id") == block_id), None)
    if index is None:
        return ["- none"]
    lines = []
    for label, adjacent_index in (("previous", index - 1), ("next", index + 1)):
        if 0 <= adjacent_index < len(blocks):
            block = blocks[adjacent_index]
            lines.append(f"- {label}: {block.get('block_id')} | {block.get('text_preview') or 'empty'}")
    return lines or ["- none"]


def _format_heading_path(path: list[str]) -> str:
    return " > ".join(path) if path else "none"


def _collect_element_ids(tag: Tag) -> list[str]:
    ids: list[str] = []
    if tag.get("id"):
        ids.append(str(tag.get("id")))
    for item in tag.find_all(True):
        item_id = item.get("id")
        if item_id:
            ids.append(str(item_id))
    return _unique(ids)


def _id_exists(blocks: list[dict[str, Any]], element_id: str) -> bool:
    return any(element_id in block.get("element_ids", []) for block in blocks)


def _authorized_retrieved_ids(retrieved_ids: list[str], blocks: list[dict[str, Any]]) -> list[str]:
    return [block_id for block_id in retrieved_ids if _id_exists(blocks, block_id)]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _normalize_for_search(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def _tokenize(value: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", value or "")
    compact = _normalize_for_search(value)
    grams = [compact[index : index + 2] for index in range(max(0, len(compact) - 1))]
    return _unique([*words, *grams])


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
