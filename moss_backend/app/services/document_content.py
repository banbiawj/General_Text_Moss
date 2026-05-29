from __future__ import annotations

import math
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal


MOSS_BLOCK_ID_PREFIX = "moss-block-"
TailorContextTaskType = Literal["local_edit", "global_edit", "document_qa"]
GLOBAL_EDIT_TOKEN_THRESHOLD = 10_000
GLOBAL_EDIT_TARGET_CHUNK_TOKENS = 10_000


@dataclass(frozen=True)
class HtmlBlock:
    block_id: str
    html: str

# 裁剪功能------------------------------------------------------------------------------------
def tailor_context(
    canvas_snapshot: str,
    focus_block_id: str | None = None,
    task_type: TailorContextTaskType = "local_edit",
) -> list[str]:
    """把完整画布 HTML 裁剪成适合进入模型上下文的片段列表。"""
    # 主要代码区------------------------------------------------------------------------------------
    if task_type not in {"local_edit", "global_edit", "document_qa"}:
        raise ValueError("task_type must be 'local_edit', 'global_edit', or 'document_qa'")

    # 只抽取最外层 Moss 文档块，并保留每块原始 HTML。
    blocks = _extract_moss_blocks(canvas_snapshot)
    if not blocks:
        return []

    if task_type == "global_edit":
        return chunk_global_edit_blocks_by_estimated_tokens(blocks)

    focus_index = _find_block_index(blocks, focus_block_id)
    if focus_index is None:
        return []

    # 局部编辑使用 7 段窗口：焦点段前 3 段、焦点段本身、焦点段后 3 段。
    start_index = max(0, focus_index - 3)
    end_index = min(len(blocks), focus_index + 4)
    return [_serialize_blocks(blocks[start_index:end_index])]


def _extract_moss_blocks(canvas_snapshot: str) -> list[HtmlBlock]:
    parser = MossBlockParser(canvas_snapshot or "")
    parser.feed(canvas_snapshot or "")
    parser.close()
    return parser.blocks


def _find_block_index(blocks: list[HtmlBlock], focus_block_id: str | None) -> int | None:
    if not focus_block_id:
        return None

    for index, block in enumerate(blocks):
        if block.block_id == focus_block_id:
            return index
    return None


def _serialize_blocks(blocks: list[HtmlBlock]) -> str:
    return "".join(block.html for block in blocks)


def estimate_token_count(text: str) -> int:
    """Return a cheap, conservative token estimate for routing document chunks."""
    if not text:
        return 0

    cjk_chars = 0
    non_cjk_chars = 0
    for character in text:
        if character.isspace():
            continue
        if _is_cjk_character(character):
            cjk_chars += 1
        else:
            non_cjk_chars += 1

    return cjk_chars + math.ceil(non_cjk_chars / 4)


def chunk_global_edit_blocks_by_estimated_tokens(
    blocks: list[HtmlBlock],
    *,
    threshold_tokens: int = GLOBAL_EDIT_TOKEN_THRESHOLD,
    target_chunk_tokens: int = GLOBAL_EDIT_TARGET_CHUNK_TOKENS,
) -> list[str]:
    if not blocks:
        return []

    block_token_counts = [
        (block, estimate_token_count(block.html))
        for block in blocks
    ]
    total_tokens = sum(token_count for _, token_count in block_token_counts)
    if total_tokens <= threshold_tokens:
        return [_serialize_blocks(blocks)]

    target_tokens = max(1, target_chunk_tokens)
    chunks: list[str] = []
    current_blocks: list[HtmlBlock] = []
    current_tokens = 0

    for block, token_count in block_token_counts:
        if current_blocks and current_tokens + token_count > target_tokens:
            chunks.append(_serialize_blocks(current_blocks))
            current_blocks = []
            current_tokens = 0

        current_blocks.append(block)
        current_tokens += token_count

    if current_blocks:
        chunks.append(_serialize_blocks(current_blocks))

    return chunks


def _is_cjk_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


class MossBlockParser(HTMLParser):
    """从 HTML 片段中抽取 Moss 文档块，同时不改写原始 HTML 格式。"""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = _line_start_offsets(source)
        self.blocks: list[HtmlBlock] = []
        self.tag_stack: list[str] = []
        # 只追踪一个当前块，因此嵌套的 moss-block id 会被忽略。
        self.active_block: tuple[str, str, int, int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start_index = self._absolute_position()
        block_id = _moss_block_id(attrs)
        if block_id and self.active_block is None:
            self.active_block = (block_id, tag, len(self.tag_stack), start_index)
        self.tag_stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        block_id = _moss_block_id(attrs)
        if block_id and self.active_block is None:
            start_index = self._absolute_position()
            end_index = self._tag_end_position(start_index)
            self.blocks.append(HtmlBlock(block_id=block_id, html=self.source[start_index:end_index]))

    def handle_endtag(self, tag: str) -> None:
        start_index = self._absolute_position()
        end_index = self._tag_end_position(start_index)
        matching_index = _last_matching_tag_index(self.tag_stack, tag)

        if self.active_block is not None:
            block_id, block_tag, block_depth, block_start = self.active_block
            if tag == block_tag and matching_index == block_depth:
                self.blocks.append(HtmlBlock(block_id=block_id, html=self.source[block_start:end_index]))
                self.active_block = None

        if matching_index is not None:
            del self.tag_stack[matching_index:]

    def close(self) -> None:
        super().close()
        if self.active_block is not None:
            # 如果 HTML 片段不完整，就保留当前块从开始到结尾的内容。
            block_id, _, _, block_start = self.active_block
            self.blocks.append(HtmlBlock(block_id=block_id, html=self.source[block_start:]))
            self.active_block = None

    def _absolute_position(self) -> int:
        line_number, offset = self.getpos()
        return self.line_starts[line_number - 1] + offset

    def _tag_end_position(self, start_index: int) -> int:
        end_index = self.source.find(">", start_index)
        if end_index == -1:
            return len(self.source)
        return end_index + 1


def _moss_block_id(attrs: list[tuple[str, str | None]]) -> str | None:
    for name, value in attrs:
        if name == "id" and value and value.startswith(MOSS_BLOCK_ID_PREFIX):
            return value
    return None


def _line_start_offsets(source: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(source):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _last_matching_tag_index(tag_stack: list[str], tag: str) -> int | None:
    for index in range(len(tag_stack) - 1, -1, -1):
        if tag_stack[index] == tag:
            return index
    return None
# -----------------------------------------------------------------------------------------
