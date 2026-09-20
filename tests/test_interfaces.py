"""Exercise the contracts using in-memory components, without real execution."""

from subprocess import CompletedProcess
import unittest

from src.interfaces import Agent, AgentResult, Method, MethodResult, Task, Workspace


class MemoryWorkspace:
    root = "/repo"

    def __init__(self):
        self.files = {"fix.py": "broken"}
        self.commands = []

    def execute(self, argv, *, timeout=None):
        self.commands.append((list(argv), timeout))
        return CompletedProcess(argv, 1, "test failed", "")

    def read_text(self, path):
        return self.files[path]

    def write_text(self, path, content):
        self.files[path] = content


class EditingAgent:
    def run(self, prompt, workspace, options):
        workspace.write_text("fix.py", workspace.read_text("fix.py") + "\nfix")
        check = workspace.execute(["check"], timeout=options["timeout"])
        return AgentResult("fake", prompt, check.stdout, 7, 1, 0.5,
                           [{"returncode": check.returncode}], error="check failed")


class TwoCallMethod:
    def run(self, task, agent, workspace, options):
        calls = [agent.run(task.problem_statement, workspace, {"timeout": 3})]
        calls.append(agent.run(calls[0].output, workspace, {"timeout": 3}))
        return MethodResult(calls)


class InterfaceTests(unittest.TestCase):
    def test_method_can_use_multiple_agent_calls_and_workspace_operations(self):
        workspace: Workspace = MemoryWorkspace()
        agent: Agent = EditingAgent()
        method: Method = TwoCallMethod()
        task = Task("sample", "owner/repo", "abc", "Repair the bug")
        result = method.run(task, agent, workspace, {})
        self.assertEqual(workspace.read_text("fix.py"), "broken\nfix\nfix")
        self.assertEqual(workspace.commands, [(["check"], 3), (["check"], 3)])
        self.assertEqual([call.prompt for call in result.calls],
                         ["Repair the bug", "test failed"])
        self.assertEqual([call.tokens_used for call in result.calls], [7, 7])
        self.assertEqual(result.calls[0].raw_trace, [{"returncode": 1}])
        self.assertEqual(result.calls[0].error, "check failed")

    def test_workspace_failure_is_not_replaced_with_success(self):
        class UnavailableWorkspace(MemoryWorkspace):
            def execute(self, argv, *, timeout=None):
                raise TimeoutError("workspace unavailable")

        with self.assertRaisesRegex(TimeoutError, "workspace unavailable"):
            TwoCallMethod().run(Task("sample", "repo", "abc", "fix"),
                                EditingAgent(), UnavailableWorkspace(), {})
