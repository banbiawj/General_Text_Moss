from __future__ import annotations

import unittest
import json

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
        self.assertEqual([block["block_ref"] for block in task["canvas_context_blocks"]], [
            "b1",
            "b2",
            "b3",
            "b4",
            "b5",
            "b6",
        ])
        self.assertEqual(task["block_ref_map"]["b3"], "moss-block-2")
        self.assertIn("[block: b3 | tag: p]", task["task_prompt"])
        self.assertNotIn("moss-block-", task["task_prompt"])

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
        self.assertEqual([block["block_ref"] for block in updated_task["canvas_context_blocks"]], [
            "b1",
            "b2",
            "b3",
            "b4",
        ])
        self.assertIn("[block: b4 | tag: p]", updated_task["canvas_context"])
        self.assertIn("[block: b4 | tag: p]", updated_task["task_prompt"])
        self.assertNotIn("moss-block-", updated_task["task_prompt"])
        tool_payload = json.loads(updated_task["task_message"][-1].content)
        self.assertEqual(tool_payload["anchor_block_ref"], "b2")
        self.assertEqual([block["block_ref"] for block in tool_payload["blocks"]], ["b3", "b4"])
        self.assertIn("<p>block 3</p>", tool_payload["blocks"][0]["html"])
        self.assertNotIn("anchor_block_id", tool_payload)
        self.assertNotIn("block_id", json.dumps(tool_payload))
        self.assertNotIn("moss-block-", json.dumps(tool_payload))

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

        self.assertLess(rendered.index("[block: b1"), rendered.index("[block: b4"))
        self.assertEqual([block["block_id"] for block in updated_task["canvas_context_blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
            "moss-block-4",
        ])
        self.assertNotIn("moss-block-", updated_task["task_prompt"])

    def test_tools_node_resolves_canvas_read_anchor_block_ref(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "canvas_read_before",
                            "args": {"anchor_block_ref": "b2", "block_count": 2},
                            "id": "call-read-before-ref",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [
                {
                    "block_id": "moss-block-2",
                    "block_ref": "b1",
                    "index": 2,
                    "tag": "p",
                    "heading_path": [],
                    "text": "block 2",
                    "html": '<p id="moss-block-2">block 2</p>',
                    "source": "initial",
                    "added_at": 0,
                },
                {
                    "block_id": "moss-block-3",
                    "block_ref": "b2",
                    "index": 3,
                    "tag": "p",
                    "heading_path": [],
                    "text": "block 3",
                    "html": '<p id="moss-block-3">block 3</p>',
                    "source": "initial",
                    "added_at": 0,
                },
            ],
            "block_ref_map": {"b1": "moss-block-2", "b2": "moss-block-3"},
            "canvas_context_operation_seq": 0,
            "task_tools": ["canvas_read_before"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Read before b2",
            "canvas_snapshot": _snapshot(5),
            "focus_element_id": "moss-block-3",
            "focus_block_id": "moss-block-3",
            "task_type": "document_qa",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        updated_task = result["tasks"][0]
        self.assertEqual([block["block_id"] for block in updated_task["canvas_context_blocks"]], [
            "moss-block-1",
            "moss-block-2",
            "moss-block-3",
        ])
        payload = json.loads(updated_task["task_message"][-1].content)
        self.assertEqual(payload["anchor_block_ref"], "b3")
        self.assertEqual([block["block_ref"] for block in payload["blocks"]], ["b1", "b2"])
        self.assertIn("<p>block 1</p>", payload["blocks"][0]["html"])
        self.assertNotIn("moss-block-", json.dumps(payload))

    def test_tools_node_resolves_update_canvas_element_block_ref(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_canvas_element",
                            "args": {
                                "block_ref": "b1",
                                "action_type": "replace",
                                "new_html": "<p>updated</p>",
                            },
                            "id": "call-update-ref",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [
                {
                    "block_id": "moss-block-real",
                    "block_ref": "b1",
                    "index": 0,
                    "tag": "p",
                    "heading_path": [],
                    "text": "real",
                    "html": '<p id="moss-block-real">real</p>',
                    "source": "initial",
                    "added_at": 0,
                }
            ],
            "block_ref_map": {"b1": "moss-block-real"},
            "canvas_context_operation_seq": 0,
            "task_tools": ["update_canvas_element"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Rewrite this",
            "canvas_snapshot": '<p id="moss-block-real">real</p>',
            "focus_element_id": "moss-block-real",
            "focus_block_id": "moss-block-real",
            "task_type": "local_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        self.assertEqual(
            result["pending_mutations"],
            [{"element_id": "moss-block-real", "action_type": "replace", "new_html": "<p>updated</p>"}],
        )
        payload = json.loads(result["tasks"][0]["task_message"][-1].content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["block_ref"], "b1")
        self.assertNotIn("element_id", payload)

    def test_tools_node_resolves_batch_update_canvas_elements_with_partial_errors(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_canvas_elements",
                            "args": {
                                "operations": [
                                    {
                                        "block_ref": "b1",
                                        "action_type": "replace",
                                        "new_html": "<p>first updated</p>",
                                    },
                                    {
                                        "block_ref": "b99",
                                        "action_type": "replace",
                                        "new_html": "<p>missing</p>",
                                    },
                                    {
                                        "block_ref": "b2",
                                        "action_type": "delete",
                                        "new_html": "",
                                    },
                                ]
                            },
                            "id": "call-update-batch",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [
                {
                    "block_id": "moss-block-one",
                    "block_ref": "b1",
                    "index": 0,
                    "tag": "p",
                    "heading_path": [],
                    "text": "one",
                    "html": '<p id="moss-block-one">one</p>',
                    "source": "initial",
                    "added_at": 0,
                },
                {
                    "block_id": "moss-block-two",
                    "block_ref": "b2",
                    "index": 1,
                    "tag": "p",
                    "heading_path": [],
                    "text": "two",
                    "html": '<p id="moss-block-two">two</p>',
                    "source": "initial",
                    "added_at": 0,
                },
            ],
            "block_ref_map": {"b1": "moss-block-one", "b2": "moss-block-two"},
            "canvas_context_operation_seq": 0,
            "task_tools": ["update_canvas_elements"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Rewrite these",
            "canvas_snapshot": (
                '<p id="moss-block-one">one</p>'
                '<p id="moss-block-two">two</p>'
            ),
            "focus_element_id": "moss-block-one",
            "focus_block_id": "moss-block-one",
            "task_type": "local_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        self.assertEqual(
            result["pending_mutations"],
            [
                {
                    "element_id": "moss-block-one",
                    "action_type": "replace",
                    "new_html": "<p>first updated</p>",
                },
                {
                    "element_id": "moss-block-two",
                    "action_type": "delete",
                    "new_html": "",
                },
            ],
        )
        payload = json.loads(result["tasks"][0]["task_message"][-1].content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["operation"], "update_canvas_elements")
        self.assertEqual(payload["applied_count"], 2)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["results"][0]["block_ref"], "b1")
        self.assertEqual(payload["results"][1]["error"], "unknown_block_ref")
        self.assertEqual(payload["results"][2]["block_ref"], "b2")
        self.assertNotIn("moss-block-", result["tasks"][0]["task_message"][-1].content)

    def test_batch_update_canvas_elements_rejects_missing_element_id(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_canvas_elements",
                            "args": {
                                "operations": [
                                    {
                                        "block_ref": "b1",
                                        "action_type": "replace",
                                        "new_html": "<p>updated</p>",
                                    }
                                ]
                            },
                            "id": "call-update-batch-missing",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [
                {"block_id": "moss-block-missing", "block_ref": "b1", "index": 0}
            ],
            "block_ref_map": {"b1": "moss-block-missing"},
            "canvas_context_operation_seq": 0,
            "task_tools": ["update_canvas_elements"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Rewrite this",
            "canvas_snapshot": '<p id="moss-block-real">real</p>',
            "focus_element_id": "moss-block-real",
            "focus_block_id": "moss-block-real",
            "task_type": "local_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        self.assertEqual(result["pending_mutations"], [])
        payload = json.loads(result["tasks"][0]["task_message"][-1].content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["applied_count"], 0)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["results"][0]["error"], "element_id_not_found")
        self.assertEqual(payload["results"][0]["block_ref"], "b1")
        self.assertNotIn("moss-block-", result["tasks"][0]["task_message"][-1].content)

    def test_tools_node_rejects_unknown_block_ref_before_tool_invoke(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_canvas_element",
                            "args": {
                                "block_ref": "b99",
                                "action_type": "replace",
                                "new_html": "<p>updated</p>",
                            },
                            "id": "call-update-unknown-ref",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [
                {"block_id": "moss-block-real", "block_ref": "b1", "index": 0}
            ],
            "block_ref_map": {"b1": "moss-block-real"},
            "canvas_context_operation_seq": 0,
            "task_tools": ["update_canvas_element"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Rewrite this",
            "canvas_snapshot": '<p id="moss-block-real">real</p>',
            "focus_element_id": "moss-block-real",
            "focus_block_id": "moss-block-real",
            "task_type": "local_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        self.assertEqual(result["pending_mutations"], [])
        payload = json.loads(result["tasks"][0]["task_message"][-1].content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "unknown_block_ref")
        self.assertEqual(payload["block_ref"], "b99")
        self.assertNotIn("moss-block-", result["tasks"][0]["task_message"][-1].content)

    def test_tools_node_rejects_update_canvas_element_for_missing_element_id(self) -> None:
        task = {
            "task_message": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_canvas_element",
                            "args": {
                                "element_id": "moss-block-53",
                                "action_type": "replace",
                                "new_html": '<p id="moss-block-53">wrong</p>',
                            },
                            "id": "call-update-missing",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "canvas_context_blocks": [],
            "canvas_context_operation_seq": 0,
            "task_tools": ["update_canvas_element"],
            "task_prompt": "",
        }
        state = {
            "messages": [],
            "user_input": "Rewrite this",
            "canvas_snapshot": '<p id="moss-block-real">real</p>',
            "focus_element_id": "moss-block-real",
            "focus_block_id": "moss-block-real",
            "task_type": "global_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
            "tasks": [task],
        }

        result = tools_node(state)

        self.assertEqual(result["pending_mutations"], [])
        tool_message = result["tasks"][0]["task_message"][-1]
        payload = json.loads(tool_message.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "element_id_not_found")
        self.assertNotIn("element_id", payload)

    def test_global_edit_context_reads_share_one_budget(self) -> None:
        initial_state = {
            "messages": [],
            "user_input": "Polish the whole document",
            "canvas_snapshot": _snapshot(5),
            "focus_element_id": "moss-block-1",
            "focus_block_id": "moss-block-1",
            "task_type": "global_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
        }
        task = task_assemble_node(initial_state)["tasks"][0]
        task["canvas_context_blocks"] = [
            block
            for block in task["canvas_context_blocks"]
            if block["block_id"] in {"moss-block-1", "moss-block-2"}
        ]
        task["canvas_context"] = '<p id="moss-block-1">block 1</p><p id="moss-block-2">block 2</p>'
        task["task_message"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "canvas_read_after",
                        "args": {"block_count": 1},
                        "id": "call-read-after",
                        "type": "tool_call",
                    },
                    {
                        "name": "canvas_read_before",
                        "args": {"block_count": 1},
                        "id": "call-read-before",
                        "type": "tool_call",
                    },
                ],
            )
        ]

        result = tools_node({**initial_state, "tasks": [task]})
        updated_task = result["tasks"][0]
        first_payload = json.loads(updated_task["task_message"][-2].content)
        second_payload = json.loads(updated_task["task_message"][-1].content)

        self.assertNotEqual(first_payload.get("error"), "tool_budget_exceeded")
        self.assertEqual(second_payload["error"], "tool_budget_exceeded")
        self.assertEqual(second_payload["budget_group"], "context_read")
        self.assertEqual(second_payload["tool"], "canvas_read_before")
        self.assertEqual(updated_task["tool_budget_usage"], {"context_read": 1})
        self.assertEqual(
            [block["block_id"] for block in updated_task["canvas_context_blocks"]],
            ["moss-block-1", "moss-block-2", "moss-block-3"],
        )

    def test_local_edit_context_reads_are_not_limited_by_global_edit_budget(self) -> None:
        initial_state = {
            "messages": [],
            "user_input": "Polish around here",
            "canvas_snapshot": _snapshot(5),
            "focus_element_id": "moss-block-2",
            "focus_block_id": "moss-block-2",
            "task_type": "local_edit",
            "task_reason": "",
            "current_task_index": 0,
            "pending_mutations": [],
        }
        task = task_assemble_node(initial_state)["tasks"][0]
        task["canvas_context_blocks"] = [
            block
            for block in task["canvas_context_blocks"]
            if block["block_id"] == "moss-block-2"
        ]
        task["canvas_context"] = '<p id="moss-block-2">block 2</p>'
        task["task_message"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "canvas_read_after",
                        "args": {"block_count": 1},
                        "id": "call-read-after",
                        "type": "tool_call",
                    },
                    {
                        "name": "canvas_read_before",
                        "args": {"block_count": 1},
                        "id": "call-read-before",
                        "type": "tool_call",
                    },
                ],
            )
        ]

        result = tools_node({**initial_state, "tasks": [task]})
        updated_task = result["tasks"][0]
        payloads = [
            json.loads(message.content)
            for message in updated_task["task_message"][-2:]
        ]

        self.assertTrue(all(payload.get("error") != "tool_budget_exceeded" for payload in payloads))
        self.assertEqual(updated_task["tool_budget_usage"], {})
        self.assertEqual(
            [block["block_id"] for block in updated_task["canvas_context_blocks"]],
            ["moss-block-1", "moss-block-2", "moss-block-3"],
        )

    def test_global_edit_prompt_exposes_context_read_budget(self) -> None:
        task = task_assemble_node(
            {
                "task_type": "global_edit",
                "canvas_snapshot": _snapshot(3),
                "focus_block_id": "moss-block-1",
                "focus_element_id": "moss-block-1",
                "user_input": "Polish the whole document",
            }
        )["tasks"][0]

        self.assertIn("canvas_read_before", task["task_prompt"])
        self.assertIn("canvas_read_after", task["task_prompt"])
        self.assertIn("合计最多只能使用 1 次", task["task_prompt"])

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
        self.assertIn("ordered as they appear in the document", task["task_prompt"])
        self.assertNotIn("moss-block-", task["task_prompt"])

    def test_edit_task_prompts_expose_batch_update_tool(self) -> None:
        for task_type in ("local_edit", "global_edit"):
            with self.subTest(task_type=task_type):
                state = {
                    "task_type": task_type,
                    "canvas_snapshot": _snapshot(3),
                    "focus_block_id": "moss-block-1",
                    "focus_element_id": "moss-block-1",
                    "user_input": "Polish these blocks",
                }

                task = task_assemble_node(state)["tasks"][0]

                self.assertIn("update_canvas_elements", task["task_tools"])
                self.assertIn("update_canvas_elements", task["task_prompt"])

    def test_global_edit_uses_batch_update_tool_without_single_block_update(self) -> None:
        task = task_assemble_node(
            {
                "task_type": "global_edit",
                "canvas_snapshot": _snapshot(3),
                "focus_block_id": "moss-block-1",
                "focus_element_id": "moss-block-1",
                "user_input": "Polish the whole document",
            }
        )["tasks"][0]

        self.assertIn("update_canvas_elements", task["task_tools"])
        self.assertNotIn("update_canvas_element", task["task_tools"])
        self.assertIn("update_canvas_elements", task["task_prompt"])
        self.assertNotIn("'update_canvas_element'", task["task_prompt"])


if __name__ == "__main__":
    unittest.main()
