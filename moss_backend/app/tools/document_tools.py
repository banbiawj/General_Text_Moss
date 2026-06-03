from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Annotated, Any, Literal
from uuid import uuid4

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from app.services.canvas_context import read_neighbor_blocks, resolve_paging_anchor
from app.services.document_content import _extract_moss_blocks


MAX_SEARCH_RESULTS = 12
TEXT_SNIPPET_LIMIT = 900
OUTLINE_LIMIT = 30


class DownloadLinkArgs(BaseModel):
    export_format: Literal["markdown", "html", "pdf"] = Field(description="导出格式")
    content: str = Field(default="", description="需要导出的文档内容")


class UpdateCanvasElementArgs(BaseModel):
    block_ref: str = Field(description="Block reference shown in canvas_context, such as b1 or b2.")
    action_type: Literal["replace", "append", "insert", "delete"] = Field(
        default="replace",
        description="Document mutation type: replace, append, insert, or delete.",
    )
    new_html: str = Field(
        default="",
        description="Replacement or inserted HTML. May be empty for delete.",
    )


class UpdateCanvasElementsOperation(BaseModel):
    block_ref: str = Field(description="Block reference shown in canvas_context, such as b1 or b2.")
    action_type: Literal["replace", "append", "insert", "delete"] = Field(
        default="replace",
        description="Document mutation type: replace, append, insert, or delete.",
    )
    new_html: str = Field(
        default="",
        description="Replacement or inserted HTML. May be empty for delete.",
    )


class UpdateCanvasElementsArgs(BaseModel):
    operations: list[UpdateCanvasElementsOperation] = Field(
        description="Ordered document mutations to apply. Each operation targets one block_ref."
    )


class ParsedHtmlBlock(BaseModel):
    block_id: str
    index: int
    tag: str
    text: str
    html: str
    heading_path: list[str]


DOWNLOAD_CACHE: dict[str, dict] = {}


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


def _next_context_operation_seq(task: dict[str, Any]) -> int:
    current = task.get("canvas_context_operation_seq", 0)
    try:
        return int(current) + 1
    except (TypeError, ValueError):
        return 1


@tool
def search_document_blocks(
    query: Annotated[
        str,
        (
            "用于检索 canvas_snapshot 文档块的关键词查询。不要机械复制用户原问题；"
            "应先改写成更容易命中文档的检索词。保留主题词、实体名、章节名和核心名词，"
            "例如“项目经历”“教育经历”“技能”“公司”“学校”。补充答案类型和同义词，"
            "例如问技术时加入“技术 技术栈 框架 工具 数据库 后端 前端”；"
            "问时间时加入“时间 日期 年份”；问职责时加入“负责 职责 工作 内容 成果”。"
            "删除“哪些/什么/是否/请问/帮我”等无检索价值的疑问词和语气词。"
            "推荐用空格分隔 3-12 个关键词。"
        ),
    ],
    top_k: Annotated[int, "Maximum number of matched blocks to return."] = 5,
    scope: Annotated[
        Literal["auto", "focus", "near_focus", "document"],
        "Search scope. Use focus/near_focus for questions about the current selection.",
    ] = "auto",
    include_html: Annotated[
        bool,
        "Whether to include original block HTML. Keep false for document QA; use true only for edit planning.",
    ] = False,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Search canvas_snapshot evidence blocks after rewriting the user request into retrieval keywords."""

    state = state or {}
    canvas_snapshot = str(state.get("canvas_snapshot") or "")
    focus_block_id = state.get("focus_block_id")
    top_k = _clamp_top_k(top_k)
    blocks = _parse_snapshot_blocks(canvas_snapshot)
    warnings: list[str] = []

    if not canvas_snapshot.strip():
        warnings.append("canvas_snapshot is empty")
    if not blocks:
        return _json_result(
            {
                "query": query,
                "source": "canvas_snapshot",
                "retrieval_mode": "empty",
                "total_blocks": 0,
                "matched_count": 0,
                "focus_block_id": focus_block_id,
                "blocks": [],
                "outline": [],
                "warnings": warnings or ["no moss-block elements found"],
            }
        )

    focus_index = _find_parsed_block_index(blocks, focus_block_id)
    resolved_scope = _resolve_scope(query, scope, focus_index)
    outline = _build_outline(blocks)

    if resolved_scope == "focus":
        selected_blocks = [blocks[focus_index]] if focus_index is not None else []
        if focus_index is None:
            warnings.append("focus_block_id is missing or not found")
        retrieval_mode = "focus"
    elif resolved_scope == "near_focus":
        selected_blocks = _near_focus_blocks(blocks, focus_index, top_k)
        if focus_index is None:
            warnings.append("focus_block_id is missing or not found")
        retrieval_mode = "near_focus"
    elif _is_broad_document_query(query):
        selected_blocks = _representative_blocks(blocks, top_k)
        retrieval_mode = "outline"
    else:
        selected_blocks = _ranked_keyword_blocks(blocks, query, focus_index, top_k)
        retrieval_mode = "keyword"
        if not selected_blocks:
            warnings.append("no matching blocks found")

    result_blocks = [
        _serialize_search_block(
            block,
            blocks,
            score=_score_block(block, query, focus_index) if retrieval_mode == "keyword" else None,
            include_html=include_html,
        )
        for block in selected_blocks
    ]

    return _json_result(
        {
            "query": query,
            "source": "canvas_snapshot",
            "retrieval_mode": retrieval_mode,
            "total_blocks": len(blocks),
            "matched_count": len(result_blocks),
            "focus_block_id": focus_block_id,
            "blocks": result_blocks,
            "outline": outline,
            "warnings": warnings,
        }
    )


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


@tool(args_schema=DownloadLinkArgs)
def generate_download_link(export_format: str, content: str = "") -> str:
    """Prepare a temporary download URL for the requested document format."""

    token = uuid4().hex
    DOWNLOAD_CACHE[token] = {
        "format": export_format,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return f"/api/v1/download/{token}"


@tool(args_schema=UpdateCanvasElementArgs)
def update_canvas_element(
    block_ref: Annotated[
        str | None,
        "Block reference shown in canvas_context, such as b1 or b2. Prefer this over DOM ids.",
    ] = None,
    action_type: Annotated[
        Literal["replace", "append", "insert", "delete"],
        "Document mutation type: replace, append, insert, or delete.",
    ] = "replace",
    new_html: Annotated[str, "Replacement or inserted HTML. May be empty for delete."] = "",
    element_id: Annotated[
        str | None,
        "Internal DOM node id resolved by the tool node.",
    ] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Dispatch a structured document mutation to the browser canvas."""

    state = state or {}
    valid_element_ids = {
        block.block_id
        for block in _extract_moss_blocks(str(state.get("canvas_snapshot") or ""))
    }

    if not element_id or element_id not in valid_element_ids:
        return _json_result(
            {
                "ok": False,
                "operation": "update_canvas_element",
                "error": "element_id_not_found",
                "block_ref": block_ref,
                "action_type": action_type,
                "message": "element_id does not exist in current canvas_snapshot.",
                "hint": "Use the exact block_ref shown in canvas_context, such as b1 or b2.",
            }
        )

    return _json_result(
        {
            "ok": True,
            "operation": "update_canvas_element",
            "element_id": element_id,
            "block_ref": block_ref,
            "action_type": action_type,
            "new_html": new_html,
        }
    )


@tool(args_schema=UpdateCanvasElementsArgs)
def update_canvas_elements(
    operations: list[dict[str, Any]],
    _batch_results: list[dict[str, Any]] | None = None,
) -> str:
    """Dispatch ordered document mutations for multiple canvas blocks."""

    results = _batch_results
    if results is None:
        results = [
            {
                "ok": True,
                "block_ref": operation.get("block_ref"),
                "action_type": operation.get("action_type", "replace"),
            }
            for operation in operations
        ]

    applied_count = sum(1 for result in results if result.get("ok") is True)
    error_count = sum(1 for result in results if result.get("ok") is not True)

    return _json_result(
        {
            "ok": applied_count > 0,
            "operation": "update_canvas_elements",
            "applied_count": applied_count,
            "error_count": error_count,
            "results": results,
        }
    )


DOCUMENT_TOOLS = [
    search_document_blocks,
    canvas_read_before,
    canvas_read_after,
    update_canvas_element,
    update_canvas_elements,
    generate_download_link,
]


def _parse_snapshot_blocks(canvas_snapshot: str) -> list[ParsedHtmlBlock]:
    raw_blocks = _extract_moss_blocks(canvas_snapshot)
    parsed_blocks: list[ParsedHtmlBlock] = []
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
            ParsedHtmlBlock(
                block_id=raw_block.block_id,
                index=index,
                tag=tag,
                text=normalized_text,
                html=raw_block.html,
                heading_path=heading_path,
            )
        )

    return parsed_blocks


def _extract_tag_and_text(html: str) -> tuple[str, str]:
    parser = BlockTextParser()
    parser.feed(html or "")
    parser.close()
    return parser.first_tag or "unknown", " ".join(parser.text_parts)


def _normalize_visible_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clamp_top_k(top_k: int) -> int:
    try:
        value = int(top_k)
    except (TypeError, ValueError):
        value = 5
    return max(1, min(value, MAX_SEARCH_RESULTS))


def _resolve_scope(query: str, scope: str, focus_index: int | None) -> str:
    if scope in {"focus", "near_focus", "document"}:
        return scope
    if focus_index is not None and _mentions_focus(query):
        return "near_focus"
    return "document"


def _mentions_focus(query: str) -> bool:
    return any(marker in query for marker in ("这段", "当前", "附近", "选中", "光标", "此处", "这里"))


def _is_broad_document_query(query: str) -> bool:
    broad_markers = ("全文", "整篇", "整体", "全部", "文档", "这篇", "总结", "概括", "大纲", "结构")
    specific_markers = ("项目", "教育", "经历", "技能", "时间", "姓名", "公司", "学校", "职位")
    return any(marker in query for marker in broad_markers) and not any(
        marker in query for marker in specific_markers
    )


def _representative_blocks(blocks: list[ParsedHtmlBlock], top_k: int) -> list[ParsedHtmlBlock]:
    text_blocks = [block for block in blocks if block.text]
    headings = [block for block in text_blocks if _heading_level(block.tag) is not None]
    selected: list[ParsedHtmlBlock] = []

    for block in headings + text_blocks:
        if block.block_id not in {item.block_id for item in selected}:
            selected.append(block)
        if len(selected) >= top_k:
            break

    return selected


def _near_focus_blocks(
    blocks: list[ParsedHtmlBlock],
    focus_index: int | None,
    top_k: int,
) -> list[ParsedHtmlBlock]:
    if focus_index is None:
        return []
    radius = max(1, top_k // 2)
    start = max(0, focus_index - radius)
    end = min(len(blocks), focus_index + radius + 1)
    return blocks[start:end][:top_k]


def _ranked_keyword_blocks(
    blocks: list[ParsedHtmlBlock],
    query: str,
    focus_index: int | None,
    top_k: int,
) -> list[ParsedHtmlBlock]:
    scored = [
        (block, _score_block(block, query, focus_index))
        for block in blocks
        if block.text or block.heading_path
    ]
    matched = [(block, score) for block, score in scored if score > 0]
    matched.sort(key=lambda item: (-item[1], item[0].index))
    return [block for block, _ in matched[:top_k]]


def _score_block(block: ParsedHtmlBlock, query: str, focus_index: int | None) -> float:
    normalized_query = _normalize_for_match(query)
    haystack = _normalize_for_match(" ".join([block.text, *block.heading_path]))
    if not normalized_query or not haystack:
        return 0.0

    score = 0.0
    if normalized_query in haystack:
        score += 5.0

    for token in _query_tokens(normalized_query):
        if token in haystack:
            score += min(len(token), 8) / 4

    if score > 0 and focus_index is not None:
        distance = abs(block.index - focus_index)
        if distance <= 4:
            score += (4 - distance) * 0.15

    if _heading_level(block.tag) is not None and score > 0:
        score += 0.3

    return round(score, 3)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _query_tokens(normalized_query: str) -> list[str]:
    stop_words = {
        "什么",
        "哪些",
        "如何",
        "是否",
        "这个",
        "这段",
        "当前",
        "文档",
        "总结",
        "概括",
        "the",
        "and",
        "for",
        "with",
        "what",
        "which",
        "how",
    }
    raw_tokens = re.findall(r"[a-z0-9_+-]{2,}|[\u4e00-\u9fff]+", normalized_query)
    tokens: list[str] = []

    for token in raw_tokens:
        if token in stop_words:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.extend(_cjk_ngrams(token))
            if 2 <= len(token) <= 8:
                tokens.append(token)
        else:
            tokens.append(token)

    deduped: list[str] = []
    for token in tokens:
        if token not in stop_words and token not in deduped:
            deduped.append(token)
    return deduped


def _cjk_ngrams(token: str) -> list[str]:
    if len(token) <= 2:
        return [token]
    ngrams = [token[index : index + 2] for index in range(0, len(token) - 1)]
    ngrams.extend(token[index : index + 3] for index in range(0, len(token) - 2))
    return ngrams


def _build_outline(blocks: list[ParsedHtmlBlock]) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for block in blocks:
        level = _heading_level(block.tag)
        if level is None or not block.text:
            continue
        outline.append({"block_id": block.block_id, "level": level, "text": block.text})
        if len(outline) >= OUTLINE_LIMIT:
            break
    return outline


def _heading_level(tag: str) -> int | None:
    if len(tag) == 2 and tag.startswith("h") and tag[1].isdigit():
        level = int(tag[1])
        if 1 <= level <= 6:
            return level
    return None


def _find_parsed_block_index(blocks: list[ParsedHtmlBlock], block_id: Any) -> int | None:
    if not isinstance(block_id, str) or not block_id:
        return None
    for index, block in enumerate(blocks):
        if block.block_id == block_id:
            return index
    return None


def _serialize_search_block(
    block: ParsedHtmlBlock,
    all_blocks: list[ParsedHtmlBlock],
    *,
    score: float | None,
    include_html: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "block_id": block.block_id,
        "index": block.index,
        "tag": block.tag,
        "score": score,
        "heading_path": block.heading_path,
        "text": _truncate(block.text, TEXT_SNIPPET_LIMIT),
        "html": block.html if include_html else None,
        "neighbor_ids": _neighbor_ids(block.index, all_blocks),
    }
    return payload


def _neighbor_ids(index: int, blocks: list[ParsedHtmlBlock]) -> list[str]:
    ids: list[str] = []
    if index > 0:
        ids.append(blocks[index - 1].block_id)
    if index + 1 < len(blocks):
        ids.append(blocks[index + 1].block_id)
    return ids


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


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

