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
