"""Run the real prompt builder with a simulated agent, without a model call."""

import unittest
from unittest.mock import Mock

from methods.run_free.adapter import RunFree
from methods.run_free.upstream.experiments.prompt_builder import PromptBuilder
from src.interfaces import Agent, AgentResult, Task, Workspace


class RunFreeTests(unittest.TestCase):
    def test_git_compatible_prompt_and_original_result_are_preserved(self):
        for commit, problem, error in (
            ("abc123", "Repair the parser.\nKeep whitespace.\n", None),
            ("", "修复 Unicode：`λ` 与 {value}", "agent failed"),
        ):
            with self.subTest(commit=commit, error=error):
                task = Task("example-1", "owner/project", commit, problem)
                expected = PromptBuilder.build_run_free_prompt({
                    "repo": "owner/project", "base_commit": commit,
                    "problem_statement": problem,
                })
                git_rule = "❌ Run `git` commands (interferes with experiment)\n"
                self.assertIn(git_rule, expected)
                expected = expected.replace(git_rule, "")
                original_result = AgentResult(
                    "fake", expected, "unchanged output", 123, 2, 0.75,
                    [{"type": "fixture"}], error=error,
                )
                agent = Mock(spec=Agent)
                agent.run.return_value = original_result
                workspace = Mock(spec=Workspace)

                result = RunFree().run(task, agent, workspace, {})

                agent.run.assert_called_once_with(expected, workspace, {})
                self.assertEqual(len(result.calls), 1)
                self.assertIs(result.calls[0], original_result)
                self.assertEqual(workspace.mock_calls, [])

    def test_agent_exception_propagates_without_retry(self):
        agent = Mock(spec=Agent)
        agent.run.side_effect = TimeoutError("agent timeout")
        with self.assertRaisesRegex(TimeoutError, "agent timeout"):
            RunFree().run(Task("example-1", "repo", "abc", "fix"),
                          agent, Mock(spec=Workspace), {})
        agent.run.assert_called_once()
