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

        self.assertIn("[Omitted intervening blocks]", rendered)
        self.assertLess(rendered.index("[block: b1"), rendered.index("[block: b2"))

    def test_render_canvas_context_uses_block_refs_without_dom_ids_or_position(self) -> None:
        blocks = context_blocks_from_html(
            canvas_snapshot=_snapshot(2),
            context_html='<p id="moss-block-0">block 0</p><p id="moss-block-1">block 1</p>',
            source="initial",
            added_at=1,
        )

        rendered = render_canvas_context(blocks)

        self.assertIn("[block: b1 | tag: p]", rendered)
        self.assertIn("[block: b2 | tag: p]", rendered)
        self.assertIn("<p>block 0</p>", rendered)
        self.assertIn("<p>block 1</p>", rendered)
        self.assertNotIn("moss-block-", rendered)
        self.assertNotIn("DOM id", rendered)
        self.assertNotIn("position:", rendered)

    def test_clamp_block_count_limits_single_tool_call_size(self) -> None:
        self.assertEqual(clamp_block_count(0), 1)
        self.assertEqual(clamp_block_count(3), 3)
        self.assertEqual(clamp_block_count(99), 8)


if __name__ == "__main__":
    unittest.main()
