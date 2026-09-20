"""Bind public dataset delivery instructions and persist the final agent prompt."""

from .records import write_json


class PromptWorkspace:
    def __init__(self, workspace, prompt, method_version):
        self.workspace, self.prompt = workspace, prompt
        self.method_version = method_version

    def __getattr__(self, name):
        return getattr(self.workspace, name)

    def new_artifacts(self):
        artifacts = self.workspace.new_artifacts()
        (artifacts.host / "final-prompt.txt").write_text(self.prompt, encoding="utf-8")
        write_json(artifacts.host / "prompt-metadata.json", {
            "method_version": self.method_version,
            "delivery_instructions": getattr(self.workspace, "submission_instructions", ""),
            "patch_rule": getattr(self.workspace, "patch_rule", "git-diff"),
        })
        return artifacts


def bind_prompt(prompt, workspace, method_version):
    instructions = getattr(workspace, "submission_instructions", "")
    if instructions:
        prompt = prompt.rstrip() + "\n\n" + instructions + "\n"
    return prompt, PromptWorkspace(workspace, prompt, method_version)
