from __future__ import annotations

import unittest

from app.services.document_content import tailor_context


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

    def test_global_edit_returns_four_block_batches(self) -> None:
        contexts = tailor_context(_snapshot(10), task_type="global_edit")

        self.assertEqual(len(contexts), 3)
        self.assertEqual(contexts[0], _snapshot(4))
        self.assertEqual(contexts[1], "".join(f'<p id="moss-block-{index}">block {index}</p>' for index in range(4, 8)))
        self.assertEqual(contexts[2], "".join(f'<p id="moss-block-{index}">block {index}</p>' for index in range(8, 10)))

    def test_rejects_unsupported_task_type(self) -> None:
        with self.assertRaises(ValueError):
            tailor_context(_snapshot(4), task_type="document_qa")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
