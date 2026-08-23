import json
import tempfile
from pathlib import Path
from unittest import TestCase

from core.ai_workflow import AIWorkflowService
from core.project import NovelProject


class FakeDSH:
    def __init__(self, summary: str, state: dict):
        self.summary = summary
        self.state = state
        self.generate_calls: list[tuple[str, str]] = []
        self.json_calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str, *args, **kwargs) -> str:
        self.generate_calls.append((system_prompt, user_prompt))
        return self.summary

    def generate_json(self, system_prompt: str, user_prompt: str, *args, **kwargs) -> dict:
        self.json_calls.append((system_prompt, user_prompt))
        return self.state


class AIWorkflowTests(TestCase):
    def test_memory_workflow_keeps_prompt_sequence_and_parses_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            dsh = FakeDSH(
                json.dumps(
                    {
                        "type": "chapter_summary",
                        "summary": "主角抵达旧站。",
                        "completion_message": "摘要完成",
                    },
                    ensure_ascii=False,
                ),
                {
                    "type": "story_state_update",
                    "current_chapter": 1,
                    "current_location": "旧站",
                    "characters": {},
                    "foreshadowing": [],
                    "completion_message": "状态完成",
                },
            )

            result = AIWorkflowService(dsh).update_memory(project, "chapter_01")

            self.assertEqual(result[0], "主角抵达旧站。")
            self.assertEqual(result[1]["current_location"], "旧站")
            self.assertEqual(result[2], "摘要完成；状态完成")
            self.assertEqual(len(dsh.generate_calls), 1)
            self.assertEqual(len(dsh.json_calls), 1)
            self.assertIn("chapter_summary", dsh.generate_calls[0][1])
            self.assertIn("story_state_update", dsh.json_calls[0][1])
