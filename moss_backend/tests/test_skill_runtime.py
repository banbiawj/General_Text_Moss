from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from app.agent.skill_runtime import (
    build_task_from_skill,
    build_skill_system_prompt,
    load_skill_registry,
    route_skill,
)


class SkillRuntimeTests(unittest.TestCase):
    def test_registry_loads_skill_manifest_and_prompt(self) -> None:
        temp_dir = Path.cwd() / ".tmp" / "tests" / f"skill-runtime-{uuid4().hex}"
        skill_dir = temp_dir / "local-edit"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.yaml").write_text(
            "\n".join(
                [
                    "id: local-edit",
                    "name: Local Edit",
                    "description: Edit nearby document content.",
                    "task_type: local_edit",
                    "priority: 80",
                    "triggers:",
                    "  include:",
                    "    - 润色",
                    "  exclude:",
                    "    - 全文",
                    "context:",
                    "  strategy: near_focus",
                    "  include_html: true",
                    "  max_blocks: 5",
                    "tools:",
                    "  allow:",
                    "    - search_document_blocks",
                    "    - update_canvas_element",
                    "output:",
                    "  contract: canvas_mutation",
                ]
            ),
            encoding="utf-8",
        )
        (skill_dir / "prompt.md").write_text("Only edit the target block.", encoding="utf-8")

        registry = load_skill_registry(temp_dir)

        skill = registry.require("local-edit")
        self.assertEqual(skill.task_type, "local_edit")
        self.assertEqual(skill.priority, 80)
        self.assertEqual(skill.context.strategy, "near_focus")
        self.assertTrue(skill.context.include_html)
        self.assertEqual(skill.context.max_blocks, 5)
        self.assertEqual(skill.tools, ["search_document_blocks", "update_canvas_element"])
        self.assertEqual(skill.output_contract, "canvas_mutation")
        self.assertEqual(skill.prompt, "Only edit the target block.")

    def test_router_prefers_global_edit_when_global_scope_is_present(self) -> None:
        registry = load_skill_registry()

        skill = route_skill("帮我统一全文风格", registry)

        self.assertEqual(skill.id, "global-edit")
        self.assertEqual(skill.task_type, "global_edit")
        self.assertIn("update_canvas_element", skill.tools)

    def test_router_uses_local_edit_for_edit_words_without_global_scope(self) -> None:
        registry = load_skill_registry()

        skill = route_skill("帮我润色这段", registry)

        self.assertEqual(skill.id, "local-edit")
        self.assertEqual(skill.task_type, "local_edit")
        self.assertEqual(skill.context.strategy, "near_focus")

    def test_router_uses_document_qa_for_document_questions(self) -> None:
        registry = load_skill_registry()

        skill = route_skill("总结一下项目经历", registry)

        self.assertEqual(skill.id, "document-qa")
        self.assertEqual(skill.task_type, "document_qa")
        self.assertEqual(skill.tools, ["search_document_blocks"])

    def test_router_uses_document_qa_for_specific_document_fact_questions(self) -> None:
        registry = load_skill_registry()

        skill = route_skill("项目经历用了什么技术", registry)

        self.assertEqual(skill.id, "document-qa")

    def test_router_uses_local_edit_for_unify_style_without_global_scope(self) -> None:
        registry = load_skill_registry()

        skill = route_skill("帮我统一风格", registry)

        self.assertEqual(skill.id, "local-edit")

    def test_router_uses_general_chat_without_document_need(self) -> None:
        registry = load_skill_registry()

        skill = route_skill("你好", registry)

        self.assertEqual(skill.id, "general-chat")
        self.assertEqual(skill.task_type, "general_chat")
        self.assertEqual(skill.tools, [])

    def test_build_task_from_skill_uses_manifest_tools_and_context(self) -> None:
        registry = load_skill_registry()
        skill = route_skill("帮我润色这段", registry)

        task = build_task_from_skill(
            skill=skill,
            user_input="帮我润色这段",
            focus_block_id="moss-block-2",
            canvas_snapshot='<p id="moss-block-1">before</p><p id="moss-block-2">target</p>',
        )

        self.assertEqual(task["skill_id"], "local-edit")
        self.assertEqual(task["task_type"], "local_edit")
        self.assertEqual(task["task_tools"], ["search_document_blocks", "update_canvas_element"])
        self.assertEqual(task["allowed_element_ids"], ["moss-block-2"])
        self.assertIn('id="moss-block-2"', task["canvas_context"])
        self.assertIn("帮我润色这段", task["task_prompt"])

    def test_build_skill_system_prompt_uses_selected_skill_context(self) -> None:
        registry = load_skill_registry()
        skill = route_skill("帮我润色这段", registry)
        task = build_task_from_skill(
            skill=skill,
            user_input="帮我润色这段",
            focus_block_id="moss-block-2",
            canvas_snapshot='<p id="moss-block-2">target</p>',
        )

        prompt = build_skill_system_prompt(task)

        self.assertIn("local-edit", prompt)
        self.assertIn("update_canvas_element", prompt)
        self.assertIn('<p id="moss-block-2">target</p>', prompt)
        self.assertNotIn("canvas_snapshot", prompt)


if __name__ == "__main__":
    unittest.main()
