from __future__ import annotations

import unittest

from app.services.document_content import (
    HtmlBlock,
    chunk_global_edit_blocks_by_estimated_tokens,
    estimate_token_count,
    tailor_context,
)


def _snapshot(block_count: int) -> str:
    return "".join(f'<p id="moss-block-{index}">block {index}</p>' for index in range(block_count))


class TailorContextTests(unittest.TestCase):
    def test_local_edit_returns_focus_window(self) -> None:
        contexts = tailor_context(_snapshot(9), "moss-block-4")

        self.assertEqual(len(contexts), 1)
        self.assertIn('id="moss-block-1"', contexts[0])
        self.assertIn('id="moss-block-4"', contexts[0])
        self.assertIn('id="moss-block-7"', contexts[0])
        self.assertNotIn('id="moss-block-0"', contexts[0])
        self.assertNotIn('id="moss-block-8"', contexts[0])

    def test_local_edit_clamps_to_document_edges(self) -> None:
        contexts = tailor_context(_snapshot(4), "moss-block-0")

        self.assertEqual(contexts, [_snapshot(4)])

    def test_local_edit_returns_empty_when_focus_block_is_missing(self) -> None:
        self.assertEqual(tailor_context(_snapshot(4), "moss-block-missing"), [])

    def test_global_edit_keeps_short_document_in_one_context(self) -> None:
        contexts = tailor_context(_snapshot(10), task_type="global_edit")

        self.assertEqual(contexts, [_snapshot(10)])

    def test_estimate_token_count_handles_ascii_and_cjk_text(self) -> None:
        self.assertEqual(estimate_token_count(""), 0)
        self.assertEqual(estimate_token_count("abcd"), 1)
        self.assertEqual(estimate_token_count("abcde"), 2)
        self.assertEqual(estimate_token_count("中文"), 2)

    def test_global_edit_chunks_long_document_by_estimated_tokens(self) -> None:
        blocks = [
            HtmlBlock(block_id=f"moss-block-{index}", html=html)
            for index, html in enumerate(["aaaa", "bbbb", "cccc", "dddd", "eeee"])
        ]

        contexts = chunk_global_edit_blocks_by_estimated_tokens(
            blocks,
            threshold_tokens=2,
            target_chunk_tokens=2,
        )

        self.assertEqual(contexts, ["aaaabbbb", "ccccdddd", "eeee"])

    def test_rejects_unknown_task_type(self) -> None:
        with self.assertRaises(ValueError):
            tailor_context(_snapshot(4), task_type="unknown")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
