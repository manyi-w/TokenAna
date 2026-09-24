"""Run the unchanged mini-SWE-agent CLI inside a prepared workspace."""

from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import re
from shlex import join, quote
import subprocess
import time
from typing import Any, Mapping

from src.components import ConfigError
from src.raw_usage import read_case_usage
from src.interfaces import AgentResult, Workspace
from src.models import ModelConfig
from src.agent_artifacts import collect_agent_patch, check_session_outcome, save_patch_eligibility
from src.workspaces import execution_timeout


_MODEL_PREFIX = {
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "openai": "openai",
    "dashscope": "dashscope",
}


class MiniSweAgent:
    read_case_usage = staticmethod(read_case_usage)

    capabilities = ("turn_control",)
    session_capabilities = ("events", "replace_history", "state", "reminder", "terminate")
    session_version = "mini-session-compatible-v1"
    model_protocols = ("responses", "chat_completions", "anthropic_messages")
    raw_usage_protocols = model_protocols
    explicit_cache_protocols = ("anthropic_messages",)
    original_compatible_trace_formats = ("codex-jsonl", "agent-final-summary-v1")
    original_accounting_variant = "mini-trajectory-compatible-v1"

    def validate_session_options(self, options):
        python = options.get("python_executable")
        if not isinstance(python, str) or not python.startswith("/"):
            raise ValueError("mini session requires an absolute prepared python_executable")
        if not options.get("record_raw_usage"):
            raise ValueError("mini session requires record_raw_usage=true for actual model inputs")
        self.validate_raw_usage(options)
        timeout = options.get("session_timeout", 60)
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ValueError("session_timeout must be positive and at most 3600 seconds")

    def run_session(self, prompt, workspace, options, callback):
        self.validate_session_options(options)
        if not callable(callback):
            raise ValueError("session callback must be callable")
        return self._run(prompt, workspace, options, callback=callback)

    def validate_raw_usage(self, options):
        if (options.get("model_protocol") not in self.raw_usage_protocols
                or not options.get("model_base_url")):
            raise ValueError("mini recording requires an explicit supported protocol and model_base_url")
        expected = "litellm_response" if options["model_protocol"] == "responses" else "litellm"
        if options.get("model_class", "litellm") != expected:
            raise ValueError("mini model_class does not match the recording protocol")

    def read_original_case(self, directory, case_id):
        from src.accounting import OriginalCase
        from src.accounting_trace import atom

        path = _original_trajectory(directory)
        if not path.exists() and (directory / "session-channel").exists():
            raise ValueError("session original trajectory is missing")
        trace = None
        if path.exists():
            trajectory, error = _read_trajectory(path)
            if error:
                raise ValueError(error)
            trace = []
            for message_index, message in enumerate(trajectory["messages"]):
                response = message.get("extra", {}).get("response")
                usage = response.get("usage") if isinstance(response, dict) else None
                if not isinstance(usage, dict):
                    usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                # Project native log fields; the method still owns filtering and means.
                incoming = usage.get("input_tokens", usage.get("prompt_tokens", 0))
                outgoing = usage.get("output_tokens", usage.get("completion_tokens", 0))
                if _integer(incoming) is None or _integer(outgoing) is None:
                    raise ValueError("mini trajectory contains invalid original token counts")
                trace.append({"type": "turn.completed", "usage": {
                    "input_tokens": incoming, "output_tokens": outgoing},
                    "_calculation": {name: atom(value, str(path),
                        f'/messages/{message_index}/' + ('extra/response/usage/' if isinstance(response, dict) and isinstance(response.get('usage'), dict) else 'usage/') + field,
                        kind='native_aggregate' if field in usage else 'rule_default', description='原生 usage 或作者默认零')
                        for name, value, field in (("input_tokens", incoming, "input_tokens" if "input_tokens" in usage else "prompt_tokens"),
                                                   ("output_tokens", outgoing, "output_tokens" if "output_tokens" in usage else "completion_tokens"))}})
        patch = directory / "patch.diff"
        return OriginalCase(case_id, trace, patch.read_text(encoding="utf-8") if patch.exists() else None)

    def read_final_summary_case(self, directory, case_id):
        from src.accounting import OriginalCase
        from .summary import final_summary

        trajectory, error = _read_trajectory(_original_trajectory(directory))
        if error:
            raise ValueError(error)
        return OriginalCase(case_id, None, None, final_summary(trajectory, str(_original_trajectory(directory))))

    def configure_model(self, model: ModelConfig, options: Mapping[str, Any]) -> dict:
        if model.protocol not in self.model_protocols:
            raise ConfigError("unsupported mini-SWE-agent model protocol")
        if model.provider not in _MODEL_PREFIX:
            raise ConfigError(f"mini-SWE-agent has no LiteLLM mapping for provider {model.provider!r}")
        if model.protocol == "responses" and model.provider not in {"openai", "dashscope"}:
            raise ConfigError(
                "mini-SWE-agent Responses mapping is currently limited to the OpenAI provider"
            )
        if model.protocol == "anthropic_messages" and model.provider != "anthropic":
            raise ConfigError("Anthropic Messages requires the anthropic provider")
        if model.provider == "anthropic" and model.protocol != "anthropic_messages":
            raise ConfigError("mini's anthropic provider uses anthropic_messages")
        selected = {
            "model": f"{'openai' if model.protocol == 'responses' else _MODEL_PREFIX[model.provider]}/{model.model_id}",
            "model_class": "litellm_response" if model.protocol == "responses" else "litellm",
            "model_base_url": model.base_url,
            "model_protocol": model.protocol,
            "model_provider": model.provider,
            "model_api_key_env": model.api_key_env,
        }
        conflicts = selected.keys() & options.keys()
        if conflicts:
            raise ConfigError(
                "use [model], not agent options or call overrides for: "
                + ", ".join(sorted(conflicts))
            )
        return {**options, **selected}

    def plan(self, options: Mapping[str, Any]) -> dict:
        missing = []
        if not options.get("executable"):
            missing.append("agent.options.executable")
        if not options.get("model"):
            missing.append("model selection or agent.options.model")
        if options.get("turn_control") and not options.get("python_executable"):
            missing.append("agent.options.python_executable for controlled mini")
        return {
            "model": {
                key: options.get(key)
                for key in (
                    "model",
                    "model_class",
                    "model_base_url",
                    "model_protocol",
                    "model_provider",
                    "model_api_key_env",
                )
            },
            "executable": options.get("executable"),
            "timeout": options.get("timeout", 600),
            "missing_options": missing,
            "required": [
                "Source-built mini-SWE-agent CLI available inside the task environment",
                "Selected API key environment variable available inside the task environment",
            ],
            "command_template": [
                options.get("executable") or "<source-built-mini>",
                "-c",
                options.get("default_config", "mini.yaml"),
                "-c",
                "<call-config.yaml>",
                "--yolo",
                "--exit-immediately",
                "--output",
                "<trajectory.json>",
            ],
            "note": "Template only; the adapter also saves stdout, stderr and git diff",
            "control": "Optional query-boundary subclass through native --agent-class; prepared Python required",
            "environment_checked": False,
        }

    def run(
        self, prompt: str, workspace: Workspace, options: Mapping[str, Any]
    ) -> AgentResult:
        return self._run(prompt, workspace, options)

    def _run(self, prompt, workspace, options, callback=None):
        if self.plan(options)["missing_options"]:
            raise ConfigError("mini-SWE-agent requires executable and model")
        key = options.get("model_api_key_env")
        if key and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError("invalid model_api_key_env")
        artifacts = workspace.new_artifacts()
        timeout = execution_timeout(workspace, options)
        started = time.monotonic()
        context = nullcontext(None)
        session_context = nullcontext(None)
        if callback is not None:
            from src.session_channel import session_server
            from src.records import write_json
            write_json(artifacts.host / "session-version.json", {
                "version": self.session_version, "original_trace": "original-trajectory.json",
                "active_history": "trajectory.json", "wire_inputs": "api-records/*/request.body"})
            session_context = session_server(artifacts, callback, identity=getattr(workspace, 'usage_identity', {}))
        if options.get("record_raw_usage"):
            self.validate_raw_usage(options)
            from src.model_channel import recording_model_channel

            context = recording_model_channel(
                workspace, artifacts,
                options["model_base_url"],
                timeout=options.get("proxy_timeout", 60),
                protocol=options["model_protocol"], provider=options.get("model_provider"),
                attribution={"purpose": "main"},
                **({"request_guard": lambda: session.started and not session.finished
                    and not session.termination} if callback is not None else {}),
            )

        process = None
        timeout_error = None
        with ExitStack() as stack:
            session = stack.enter_context(session_context)
            endpoint = stack.enter_context(context)
            config_path = artifacts.host / "config.yaml"
            (artifacts.host / "prompt.txt").write_text(prompt, encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    _call_config(
                        prompt,
                        workspace.root,
                        options,
                        endpoint or options.get("model_base_url"),
                        control_path=str(Path(artifacts.execution) / "control.json"),
                        session_path=(str(Path(artifacts.execution) / "session-channel")
                                      if callback is not None else None),
                    ),
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            argv = [
                    options["executable"],
                    "-c",
                    options.get("default_config", "mini.yaml"),
                    "-c",
                    str(Path(artifacts.execution) / "config.yaml"),
                    "--yolo",
                    "--exit-immediately",
                    "--output",
                    str(Path(artifacts.execution) / "trajectory.json"),
                ]
            if callback is not None:
                sources = {
                    "tokenana_mini_session.py": Path(__file__).with_name("session_runner.py"),
                    "tokenana_mini_mapping.py": Path(__file__).with_name("session_mapping.py"),
                    "tokenana_mini_control.py": Path(__file__).with_name("controlled_runner.py"),
                    "tokenana_session_channel.py": Path(__file__).resolve().parents[2] / "src/session_channel.py",
                }
                for target, source in sources.items():
                    (artifacts.host / target).write_text(source.read_text(), encoding="utf-8")
                agent_class = "ControlledSessionMini" if options.get("turn_control") else "SessionMini"
                argv = [options["python_executable"], "-B", str(Path(artifacts.execution) / "tokenana_mini_session.py"),
                        *argv[1:], "--agent-class", "tokenana_mini_session." + agent_class]
            elif options.get("turn_control"):
                worker = "tokenana_mini_control.py"
                (artifacts.host / worker).write_text(
                    Path(__file__).with_name("controlled_runner.py").read_text(encoding="utf-8"), encoding="utf-8")
                argv = [options["python_executable"], "-B", str(Path(artifacts.execution) / worker),
                        *argv[1:], "--agent-class", "tokenana_mini_control.ControlledMini"]
            prefix = (
                "export MSWEA_CONFIGURED=true MSWEA_SILENT_STARTUP=true "
                f"MSWEA_GLOBAL_CONFIG_DIR={quote(artifacts.execution + '/mini-config')}; "
            )
            if key:
                provider_key = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                                "deepseek": "DEEPSEEK_API_KEY", "dashscope": "DASHSCOPE_API_KEY"}[
                                    options["model_provider"]]
                if options['model_protocol'] == 'responses':
                    provider_key = 'OPENAI_API_KEY'
                prefix += f'export {provider_key}="${{{key}:?model API key is required}}"; '
            command = workspace.launch_command(["bash", "-c", prefix + "exec " + join(argv)])
            try:
                from src.process_logs import run_logged
                process = run_logged(
                    command, artifacts.host, text=True, timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as error:
                timeout_error = error

        stdout = _text(process.stdout if process else getattr(timeout_error, "stdout", ""))
        stderr = _text(process.stderr if process else getattr(timeout_error, "stderr", ""))
        (artifacts.host / "stdout.txt").write_text(stdout, encoding="utf-8")
        (artifacts.host / "stderr.txt").write_text(stderr, encoding="utf-8")

        errors = []
        if timeout_error:
            errors.append("Timeout")
        elif process and process.returncode:
            errors.append(f"Non-zero exit code: {process.returncode}")

        if callback is not None:
            check_session_outcome(artifacts.host, errors,
                incomplete="Session callback failed, did not finish, or requested termination",
                missing="Missing session outcome", invalid_errors=(OSError, ValueError, AttributeError))

        patch = collect_agent_patch(workspace, artifacts, process, options, errors)

        trajectory, trajectory_error = _read_trajectory(
            _original_trajectory(artifacts.host)
        )
        if trajectory_error:
            errors.append(trajectory_error)
        info = trajectory.get("info", {}) if trajectory else {}
        status = info.get("exit_status") if isinstance(info, dict) else None
        if status and status != "Submitted":
            errors.append(f"mini-SWE-agent exit status: {status}")
        if trajectory and not status:
            errors.append("mini-SWE-agent trajectory has no exit status")
        submission = info.get("submission") if isinstance(info, dict) else None

        eligible, control = True, None
        if callback is not None:
            eligible = not errors and status == "Submitted" and bool(patch.strip())
            save_patch_eligibility(artifacts.host, patch, eligible)
        if options.get("turn_control"):
            eligible = False
            try:
                control = json.loads((artifacts.host / "control.json").read_text())
                from .summary import validate_control
                validate_control(control, options["turn_control"], info)
                eligible = (not errors and status == "Submitted"
                            and control["termination_reason"] == "completed" and bool(patch.strip()))
            except (OSError, ValueError, TypeError, KeyError):
                control = None
                errors.append("Missing or invalid mini turn control outcome")
            save_patch_eligibility(artifacts.host, patch, eligible)
            if not eligible:
                errors.append("Controlled generation has no eligible completed patch")

        return AgentResult(
            agent_type="mini-swe-agent",
            prompt=prompt,
            output=submission if isinstance(submission, str) and submission else stdout.strip(),
            tokens_used=_token_count(trajectory),
            exec_count=_action_count(trajectory),
            duration_sec=time.monotonic() - started,
            raw_trace=[trajectory] if trajectory else [],
            error="; ".join(dict.fromkeys(errors)) or None,
            artifacts=artifacts,
            submission_eligible=eligible,
            control=control,
        )


def _call_config(
    prompt: str, root: str, options: Mapping[str, Any], base_url: str | None,
    *, control_path: str | None = None, session_path: str | None = None,
) -> dict:
    model = {
        "model_name": options["model"],
        "model_class": options.get("model_class", "litellm"),
    }
    if base_url:
        model["model_kwargs"] = {"api_base": base_url}
    if options.get('cost_tracking'):
        model['cost_tracking'] = options['cost_tracking']
    if options.get("model_protocol") == "anthropic_messages":
        model["set_cache_control"] = "default_end"
    config = {
        "run": {"task": prompt},
        "environment": {"environment_class": "local", "cwd": root},
        "model": model,
    }
    if options.get("turn_control"):
        from src.control import TurnBudget
        budget = TurnBudget(**options["turn_control"])
        if not control_path:
            raise ValueError("controlled mini requires a per-call control path")
        config["agent"] = {"control_budget": budget.options(),
                           "control_path": control_path}
    if session_path:
        config.setdefault("agent", {}).update(session_path=session_path,
                                             session_timeout=options.get("session_timeout", 60))
    return config


def _original_trajectory(directory):
    original = directory / "original-trajectory.json"
    return original if original.exists() or (directory / "session-channel").exists() else directory / "trajectory.json"


def _read_trajectory(path: Path) -> tuple[dict, str | None]:
    if not path.is_file():
        return {}, "mini-SWE-agent trajectory is missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"mini-SWE-agent trajectory is invalid: {error}"
    if not isinstance(value, dict):
        return {}, "mini-SWE-agent trajectory must be a JSON object"
    if (not isinstance(value.get("info"), dict) or
            not isinstance(value.get("messages"), list) or
            any(not isinstance(m, dict) or not isinstance(m.get("extra", {}), dict)
                for m in value["messages"])):
        return {}, "mini-SWE-agent trajectory has invalid info/messages"
    return value, None


def _token_count(trajectory: dict) -> int:
    total = 0
    for message in trajectory.get("messages", []):
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        response = message.get("extra", {}).get("response")
        if isinstance(response, dict) and isinstance(response.get("usage"), dict):
            usage = response["usage"]
        if not isinstance(usage, dict):
            continue
        direct = _integer(usage.get("total_tokens"))
        if direct is not None:
            total += direct
            continue
        input_tokens = _integer(usage.get("input_tokens", usage.get("prompt_tokens")))
        output_tokens = _integer(usage.get("output_tokens", usage.get("completion_tokens")))
        if input_tokens is not None and output_tokens is not None:
            total += input_tokens + output_tokens
    return total


def _action_count(trajectory: dict) -> int:
    return sum(
        len(actions)
        for message in trajectory.get("messages", [])
        if isinstance(message, dict)
        and isinstance((actions := message.get("extra", {}).get("actions")), list)
    )


def _integer(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
