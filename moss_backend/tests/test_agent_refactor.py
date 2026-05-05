from __future__ import annotations

import unittest
from pathlib import Path

from app.agent.skill_runtime import (
    build_skill_system_prompt,
    build_task_from_skill,
    load_skill_registry,
    route_skill,
)


class AgentRefactorTests(unittest.TestCase):
    def test_skill_routing_keeps_original_coarse_task_types(self) -> None:
        registry = load_skill_registry()

        self.assertEqual(route_skill("你好", registry).task_type, "general_chat")
        self.assertEqual(route_skill("总结项目经历", registry).task_type, "document_qa")
        self.assertEqual(route_skill("润色这段", registry).task_type, "local_edit")
        self.assertEqual(route_skill("全文统一风格", registry).task_type, "global_edit")

    def test_local_edit_prompt_uses_focus_context_not_full_snapshot(self) -> None:
        registry = load_skill_registry()
        skill = route_skill("润色这段", registry)
        snapshot = (
            '<p id="moss-block-0">before</p>'
            '<p id="moss-block-1">target</p>'
            '<p id="moss-block-2">after</p>'
            '<p id="moss-block-3">outside</p>'
            '<p id="moss-block-4">outside</p>'
            '<p id="moss-block-5">outside</p>'
        )

        task = build_task_from_skill(
            skill=skill,
            user_input="润色这段",
            focus_block_id="moss-block-1",
            canvas_snapshot=snapshot,
        )
        prompt = build_skill_system_prompt(task)

        self.assertIn('id="moss-block-1"', prompt)
        self.assertNotIn("canvas_snapshot", prompt)
        self.assertEqual(task["allowed_element_ids"], ["moss-block-1"])

    def test_global_edit_authorizes_current_batch_ids(self) -> None:
        registry = load_skill_registry()
        skill = route_skill("全文统一风格", registry)
        snapshot = "".join(f'<p id="moss-block-{index}">block {index}</p>' for index in range(5))

        task = build_task_from_skill(
            skill=skill,
            user_input="全文统一风格",
            focus_block_id=None,
            canvas_snapshot=snapshot,
        )

        self.assertEqual(
            task["allowed_element_ids"],
            ["moss-block-0", "moss-block-1", "moss-block-2", "moss-block-3"],
        )
        self.assertNotIn('id="moss-block-4"', task["canvas_context"])

    def test_graph_uses_skill_runtime_instead_of_hard_coded_tool_mapping(self) -> None:
        graph_source = (Path(__file__).parents[1] / "app" / "agent" / "graph.py").read_text(encoding="utf-8")

        self.assertIn("route_skill", graph_source)
        self.assertIn("build_task_from_skill", graph_source)
        self.assertNotIn("IntentDecision", graph_source)
        self.assertNotIn("_default_task_tools", graph_source)


if __name__ == "__main__":
    unittest.main()
