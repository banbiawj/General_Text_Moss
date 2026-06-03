from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Literal

from app.services.document_content import _extract_moss_blocks


CanvasDirection = Literal["before", "after"]
MAX_READ_BLOCKS = 8
BLOCK_REF_PREFIX = "b"
BLOCK_REF_RE = re.compile(r"^b[1-9]\d*$")
MOSS_BLOCK_ID_ATTR_RE = re.compile(r"\s+id=([\"'])moss-block-[^\"']+\1")


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


def assign_block_refs(context_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(context_blocks, key=lambda block: int(block["index"]))
    assigned: list[dict[str, Any]] = []

    for offset, block in enumerate(ordered, start=1):
        item = dict(block)
        item["block_ref"] = f"{BLOCK_REF_PREFIX}{offset}"
        assigned.append(item)

    return assigned


def block_ref_map_from_context_blocks(context_blocks: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for block in assign_block_refs(context_blocks):
        block_ref = str(block.get("block_ref") or "")
        block_id = str(block.get("block_id") or "")
        if block_ref and block_id:
            mapping[block_ref] = block_id
    return mapping


def block_ref_for_block_id(context_blocks: list[dict[str, Any]], block_id: Any) -> str | None:
    if not isinstance(block_id, str) or not block_id:
        return None
    for block in assign_block_refs(context_blocks):
        if block.get("block_id") == block_id:
            return str(block.get("block_ref") or "")
    return None


def block_id_for_block_ref(context_blocks: list[dict[str, Any]], block_ref: Any) -> str | None:
    if not is_block_ref(block_ref):
        return None
    return block_ref_map_from_context_blocks(context_blocks).get(str(block_ref))


def is_block_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(BLOCK_REF_RE.fullmatch(value))


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
    return _read_result(direction, anchor_block_id, anchor_index, selected, [])


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
    ordered = assign_block_refs(context_blocks)
    if not ordered:
        return ""

    lines = [
        "[Canvas Context]",
        "The following blocks are ordered as they appear in the document.",
        "Use the block reference, such as b1 or b2, when calling document tools.",
        "Gap markers mean intervening blocks have not been loaded into this context.",
        "",
    ]
    previous_index: int | None = None

    for block in ordered:
        index = int(block["index"])
        if previous_index is not None and index > previous_index + 1:
            lines.append("[Omitted intervening blocks]")
            lines.append("")

        heading_path = block.get("heading_path") or []
        heading_suffix = f" | {' / '.join(heading_path)}" if heading_path else ""
        lines.append(
            f"[block: {block.get('block_ref')} | tag: {block.get('tag', 'unknown')}{heading_suffix}]"
        )
        lines.append(strip_moss_block_id(str(block.get("html") or block.get("text") or "")))
        lines.append("")
        previous_index = index

    return "\n".join(lines).rstrip()


def strip_moss_block_id(html: str) -> str:
    return MOSS_BLOCK_ID_ATTR_RE.sub("", html or "", count=1)


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
