"""Reuse the original Codex caller with a prepared workspace's launch command."""

from pathlib import Path, PurePosixPath
from contextlib import nullcontext
from shlex import join, quote
import json
from typing import Any, Mapping

from src.interfaces import AgentResult, ArtifactDirectory, Workspace
from src.components import ConfigError
from src.models import ModelConfig
from src.patches import patch_capture_command
from src.workspaces import execution_timeout
from .compatibility.agent_caller import AgentCaller


class WorkspaceCaller(AgentCaller):
    def __init__(self, workspace: Workspace, artifacts: ArtifactDirectory, executable: str,
                 options: Mapping[str, Any]):
        super().__init__(agent_type="codex")
        self.workspace = workspace
        self.artifacts = artifacts
        self.executable = executable
        self.options = options

    def _build_codex_command(self, prompt: str, trace_path: str) -> list[str]:
        Path(trace_path).with_name("prompt.txt").write_text(prompt, encoding="utf-8")
        output = PurePosixPath(self.artifacts.execution)
        # Keep the original invocation; only the dataset chooses patch capture.
        overrides = []
        provider = self.options.get("model_provider")
        base_url = self.options.get("model_base_url")
        if provider and base_url:
            provider_name = json.dumps(str(provider))
            provider_config = (
                "{ name = " + provider_name + ", base_url = " + json.dumps(str(base_url)) +
                ", wire_api = " + json.dumps(str(self.options.get("wire_api", "responses"))) +
                (", env_key = " + json.dumps(self.options["model_api_key_env"])
                 if self.options.get("model_api_key_env") else "") +
                (", supports_websockets = false" if self.options.get("record_raw_usage") else "") + " }"
            )
            overrides.extend(["-c", quote(f"model_providers.{provider}={provider_config}"),
                              "-c", quote(f"model_provider={json.dumps(str(provider))}")])
        if self.options.get("model"):
            overrides.extend(["-c", quote(f"model={json.dumps(str(self.options['model']))}")])
        override_text = (" " + " ".join(overrides)) if overrides else ""
        script = (
            f'{quote(self.executable)} exec{override_text} "$(cat {quote(str(output / "prompt.txt"))})" '
            '--json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox '
            f'> {quote(str(output / "trace.jsonl"))} 2> {quote(str(output / "stderr.txt"))}; '
            'agent_status=$?; '
            + join(patch_capture_command(self.workspace, self.artifacts))
            + '; capture_status=$?; cat ' + quote(str(output / 'stderr.txt')) + ' >&2; '
              'if [ "$agent_status" -ne 0 ]; then exit "$agent_status"; fi; exit "$capture_status"'
        )
        return self.workspace.launch_command(["bash", "-c", script])


class Codex:
    model_protocols = ("responses", "chat_completions")
    raw_usage_protocols = model_protocols
    session_capabilities = ("events", "replace_history", "state", "reminder", "terminate")
    session_version = "codex-session-compatible-v1"
    original_trace_format = "codex-jsonl"
    original_compatible_trace_formats = ("agent-final-summary-v1",)
    capabilities = ("turn_control",)
    control_runtime = "native_sampling_hook"

    def validate_control_options(self, options):
        from .controlled_runner import validate_options
        validate_options(options)

    def read_final_summary_case(self, directory, case_id):
        from src.accounting import OriginalCase
        from .summary import final_summary
        try:
            return OriginalCase(case_id, None, None, final_summary(directory))
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("invalid Codex native summary structure") from error

    def validate_session_options(self, options):
        from .session_runner import validate_options
        validate_options(options)
        self.validate_raw_usage(options)

    def run_session(self, prompt, workspace, options, callback):
        self.validate_session_options(options)
        from .session_runner import run
        return run(prompt, workspace, options, callback)

    def validate_raw_usage(self, options):
        if not options.get("model_base_url") or options.get("wire_api", "responses") not in self.raw_usage_protocols:
            raise ValueError("raw usage recording requires an explicit supported model_base_url")
        if options.get("wire_api") == "chat_completions":
            from .session_runner import validate_options
            validate_options(options)

    def read_case_usage(self, directory, **identity):
        from src.raw_usage import read_raw_usage

        return read_raw_usage(directory / "api-records", **identity)

    def read_original_case(self, directory, case_id):
        from src.accounting import OriginalCase

        trace_path, patch_path = directory / "trace.jsonl", directory / "patch.diff"
        trace = None
        if trace_path.exists():
            trace = []
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                try:
                    trace.append(json.loads(line))
                except ValueError:
                    pass  # Original analysis ignores malformed JSONL records.
        patch = patch_path.read_text(encoding="utf-8") if patch_path.exists() else None
        return OriginalCase(case_id, trace, patch)

    def configure_model(self, model: ModelConfig, options: Mapping[str, Any]) -> dict:
        if model.protocol not in self.model_protocols:
            raise ConfigError("Codex supports Responses and the independent native Chat Completions build")
        selected = {"model": model.model_id, "model_provider": "tokenana-model",
                    "model_base_url": model.base_url, "wire_api": model.protocol,
                    "model_api_key_env": model.api_key_env, "usage_provider": model.provider}
        conflicts = selected.keys() & options.keys()
        if conflicts:
            raise ConfigError("use [model], not agent options or call overrides for: " +
                              ", ".join(sorted(conflicts)))
        if model.protocol == "chat_completions":
            self.validate_session_options({**options, **selected})
        return {**options, **selected}

    def plan(self, options: Mapping[str, Any]) -> dict:
        return {
            "model": {key: options.get(key) for key in
                      ("model", "model_provider", "model_base_url", "wire_api", "model_api_key_env")},
            "executable": options.get("executable"),
            "timeout": options.get("timeout", 600),
            "missing_options": [] if options.get("executable") else ["agent.options.executable"],
            "required": ["Source-built Codex binary available inside the task environment",
                         "Explicit model configuration pointing to a fake model service"],
            "command_template": [options.get("executable") or "<source-built-codex>",
                                 "exec", "<prompt>", "--json", "--skip-git-repo-check",
                                 "--dangerously-bypass-approvals-and-sandbox"],
            "note": "Template only; the caller also redirects trace and writes git diff",
            "control": {"executable": options.get("controlled_executable"),
                        "version": options.get("control_version"),
                        "required": ["Prepared optional-hook build; never built at runtime",
                                     "GNU timeout and recorded Responses model channel"],
                        "validation": "Hook initialization gates upstream model access"},
            "environment_checked": False,
        }

    def run(
        self, prompt: str, workspace: Workspace, options: Mapping[str, Any]
    ) -> AgentResult:
        """executable names the source-built binary inside the task environment."""
        if options.get("wire_api") == "chat_completions":
            from src.session import SessionDecision
            return self.run_session(prompt, workspace, options, lambda event: SessionDecision())
        if options.get("turn_control"):
            self.validate_raw_usage(options)
            from .controlled_runner import run
            return run(prompt, workspace, options)
        executable = options["executable"]
        artifacts = workspace.new_artifacts()
        options = dict(options)
        context = nullcontext(None)
        if options.get("record_raw_usage"):
            from src.model_channel import recording_model_channel
            from src.attribution import codex_attribution

            self.validate_raw_usage(options)
            context = recording_model_channel(workspace, artifacts, options["model_base_url"],
                                      timeout=options.get("proxy_timeout", 60),
                                      protocol="responses", provider=options.get("usage_provider"),
                                      attribution_resolver=codex_attribution)
        with context as endpoint:
            if endpoint:
                options.update(model_provider="tokenana-recorder", model_base_url=endpoint)
            caller = WorkspaceCaller(workspace, artifacts, executable, options)
            trace = caller.call(prompt, timeout=execution_timeout(workspace, options),
                                trace_output_path=str(artifacts.host / "trace.jsonl"))
        return AgentResult(**vars(trace), artifacts=artifacts)
