"""Launch the unchanged Trae CLI; keep accounting outside native execution."""

from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import re
from shlex import join, quote
import subprocess
import time

from src.accounting import OriginalCase
from src.components import ConfigError
from src.interfaces import AgentResult
from src.raw_usage import read_raw_usage
from src.patches import capture_patch
from src.workspaces import execution_timeout


SUPPORTED_MODELS = {("openai", "responses"), ("openai", "chat_completions"), ("anthropic", "anthropic_messages"),
                    ("deepseek", "chat_completions"), ("dashscope", "chat_completions"),
                    ("dashscope", "responses")}


class Trae:
    capabilities = ("turn_control",)
    session_capabilities = ("events", "replace_history", "state", "reminder", "terminate")
    session_version = "trae-session-compatible-v1"
    model_protocols = ("responses", "anthropic_messages", "chat_completions")
    raw_usage_protocols = model_protocols
    explicit_cache_protocols = ("anthropic_messages",)
    original_compatible_trace_formats = ("codex-jsonl", "agent-final-summary-v1")
    original_accounting_variant = "trae-trajectory-compatible-v1"

    def configure_model(self, model, options):
        if (model.provider, model.protocol) not in SUPPORTED_MODELS:
            raise ConfigError("Trae has no direct provider/protocol adapter for this model")
        selected = {"model": model.model_id, "model_protocol": model.protocol,
                    "model_provider": model.provider, "model_base_url": model.base_url,
                    "model_api_key_env": model.api_key_env}
        if selected.keys() & options.keys():
            raise ConfigError("use [model] for Trae model settings, not agent options")
        return {**options, **selected}

    def validate_raw_usage(self, options):
        if ((options.get("model_provider"), options.get("model_protocol")) not in SUPPORTED_MODELS
                or not options.get("model_base_url")):
            raise ConfigError("Trae requires explicit supported native protocol model settings")
        if options.get("model_provider") != "openai" and not options.get("python_executable"):
            raise ConfigError("Trae provider adapter requires python_executable in the prepared image")

    def plan(self, options):
        return {
            "missing_options": [name for name in ("executable", "model", "model_base_url", "model_api_key_env")
                                if not options.get(name)],
            "command_template": [options.get("executable") or "<source-built-trae>", "run",
                                 "--file", "<prompt.txt>", "--config-file", "<config.yaml>",
                                 "--working-dir", "<workspace>", "--trajectory-file", "<trajectory.json>"],
            "max_steps": options.get("max_steps", 200), "lakeview": "enabled, same selected model",
            "tools": ["bash", "str_replace_based_edit_tool", "sequentialthinking", "task_done"],
            "environment_checked": False,
            "note": "Native prompts and step limit retained; direct protocol clients; no MCP servers configured",
        }

    def read_case_usage(self, directory, **identity):
        return read_raw_usage(directory / "api-records", **identity)

    def read_original_case(self, directory, case_id):
        from src.accounting import FinalSummary

        path = directory / "trajectory.json"
        trace, summary = None, None
        if path.exists():
            trajectory = _trajectory(path)
            trace = _original_events(trajectory)
            summary = FinalSummary(bool(trajectory.get("end_time")),
                                   sum(len(step.get("tool_calls") or []) for step in trajectory["agent_steps"]),
                                   sum(event["usage"]["input_tokens"] for event in trace),
                                   sum(event["usage"]["output_tokens"] for event in trace),
                                   "trajectory.json:end_time,agent_steps,llm_interactions")
        patch = directory / "patch.diff"
        return OriginalCase(case_id, trace, patch.read_text(encoding="utf-8") if patch.exists() else None, summary)

    def read_final_summary_case(self, directory, case_id):
        from src.accounting import FinalSummary
        trajectory = _trajectory(directory / "trajectory.json")
        interactions, steps = trajectory["llm_interactions"], trajectory["agent_steps"]
        calls = [step.get("tool_calls") or [] for step in steps]
        count = sum(len(value) for value in calls) if all(isinstance(v, list) for v in calls) else None
        def total(name):
            values = []
            for interaction in interactions:
                response = interaction.get("response")
                usage = response.get("usage") if isinstance(response, dict) else None
                value = usage.get(name) if isinstance(usage, dict) else None
                values.append(value)
            return (sum(values) if len(interactions) == len(steps)
                    and all(type(v) is int and v >= 0 for v in values) else None)
        summary = FinalSummary(bool(trajectory.get("end_time")), count,
                               total("input_tokens"), total("output_tokens"),
                               "trae-native-summary-v2: trajectory.json final steps and interactions")
        return OriginalCase(case_id, None, None, summary)

    def validate_session_options(self, options):
        if not str(options.get("python_executable", "")).startswith("/"):
            raise ConfigError("Trae session requires absolute prepared python_executable")
        if not options.get("record_raw_usage"):
            raise ConfigError("Trae session requires record_raw_usage=true")
        timeout = options.get("session_timeout", 60)
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ConfigError("session_timeout must be positive and at most 3600 seconds")
        self.validate_raw_usage(options)

    def run_session(self, prompt, workspace, options, callback):
        self.validate_session_options(options)
        return self._run(prompt, workspace, options, callback)

    def run(self, prompt, workspace, options):
        return self._run(prompt, workspace, options)

    def _run(self, prompt, workspace, options, callback=None):
        if self.plan(options)["missing_options"]:
            raise ConfigError("Trae executable and explicit model settings are required")
        self.validate_raw_usage(options)
        key = options["model_api_key_env"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError("invalid model_api_key_env")
        steps = options.get("max_steps", 200)
        if type(steps) is not int or steps <= 0:
            raise ConfigError("max_steps must be a positive integer")
        artifacts = workspace.new_artifacts()
        host, target = artifacts.host, Path(artifacts.execution)
        (host / "prompt.txt").write_text(prompt, encoding="utf-8")
        started = time.monotonic()
        errors, trajectory, process = [], {}, None
        stdout = stderr = ""
        with ExitStack() as stack:
            session = None
            if callback is not None:
                from src.session_channel import session_server
                session = stack.enter_context(session_server(artifacts, callback, identity=getattr(workspace, 'usage_identity', {})))
                (host / "session-version.json").write_text(json.dumps({"version": self.session_version}))
            endpoint = lakeview_endpoint = options["model_base_url"]
            if options.get("record_raw_usage"):
                from src.model_channel import recording_model_channel
                recorder = dict(protocol=options["model_protocol"], provider=options["model_provider"],
                                timeout=options.get("proxy_timeout", 60))
                guard = (lambda: session.started and not session.finished and not session.termination) if session else None
                endpoint = stack.enter_context(recording_model_channel(workspace, artifacts,
                    options["model_base_url"], attribution={"purpose": "main"}, request_guard=guard, **recorder))
                lakeview_endpoint = stack.enter_context(recording_model_channel(workspace, artifacts,
                    options["model_base_url"], channel_name="lakeview",
                    attribution={"purpose": "agent_auxiliary"}, **recorder))
            (host / "config.yaml").write_text(json.dumps(_config(options, endpoint, lakeview_endpoint), indent=2), encoding="utf-8")
            argv = [options["executable"], "run", "--file", str(target / "prompt.txt"),
                    "--config-file", str(target / "config.yaml"), "--working-dir", workspace.root,
                    "--model-base-url", endpoint, "--trajectory-file", str(target / "trajectory.json"),
                    "--patch-path", str(target / "native.patch.diff"), "--console-type", "simple"]
            if callback is not None or options.get("turn_control") or options["model_provider"] != "openai" or options["model_protocol"] != "responses":
                from src.control import TurnBudget

                budget = TurnBudget(**options["turn_control"]) if options.get("turn_control") else None
                if not options.get("python_executable"):
                    raise ConfigError("turn_control requires the prepared Trae python_executable")
                (host / "control-request.json").write_text(json.dumps({
                    "budget": budget.options() if budget else None,
                    "session_timeout": options.get("session_timeout", 60),
                    "eet_system_suffix": options.get("eet_system_suffix", ""),
                    "patch_base_commit": getattr(workspace, "patch_base_commit", ""),
                }))
                (host / "controlled_runner.py").write_text(
                    Path(__file__).with_name("controlled_runner.py").read_text(encoding="utf-8"), encoding="utf-8")
                argv = [options["python_executable"], str(target / "controlled_runner.py"),
                        str(target), workspace.root]
            if callback is not None:
                sources = {
                    "session_runner.py": Path(__file__).with_name("session_runner.py"),
                    "tokenana_mini_mapping.py": Path(__file__).parents[1] / "mini_swe_agent/session_mapping.py",
                    "tokenana_session_channel.py": Path(__file__).parents[2] / "src/session_channel.py",
                }
                for name, source in sources.items():
                    (host / name).write_text(source.read_text(), encoding="utf-8")
                argv = [options["python_executable"], str(target / "session_runner.py"), str(target), workspace.root]
            # Native config resolves OPENAI_API_KEY. Never write its value to artifacts or argv.
            provider_env = "ANTHROPIC" if options["model_provider"] == "anthropic" else "OPENAI"
            script = (f'export {provider_env}_API_KEY="${{{key}:?model API key is required}}"; '
                      f'export {provider_env}_BASE_URL={join([endpoint])}; '
                      f'export TOKENANA_PROTOCOL={quote(options["model_protocol"])} '
                      f'TOKENANA_PROVIDER={quote(options["model_provider"])}; exec ' + join(argv))
            try:
                from src.process_logs import run_logged
                process = run_logged(workspace.launch_command(["bash", "-c", script]), host,
                                         text=True, stdin=subprocess.DEVNULL,
                                         timeout=execution_timeout(workspace, options))
                stdout, stderr = process.stdout, process.stderr
                if process.returncode:
                    errors.append(f"Non-zero exit code: {process.returncode}")
            except subprocess.TimeoutExpired as error:
                errors.append("Timeout")
                stdout, stderr = _text(error.stdout), _text(error.stderr)
            finally:
                (host / "stdout.txt").write_text(stdout, encoding="utf-8")
                (host / "stderr.txt").write_text(stderr, encoding="utf-8")
        patch = ""
        if process is not None:
            try:
                patch = capture_patch(workspace, artifacts, timeout=options.get("diff_timeout", 60))
            except Exception as error:
                errors.append(f"patch capture failed: {error}")
        (host / "patch.diff").write_text(patch, encoding="utf-8")
        tokens = count = 0
        try:
            trajectory = _trajectory(host / "trajectory.json")
            if not trajectory.get("end_time") or trajectory.get("success") is not True:
                errors.append("Trae trajectory does not report completed success")
            tokens = sum(sum(event["usage"].values()) for event in _original_events(trajectory))
            count = sum(len(step.get("tool_results") or []) for step in trajectory["agent_steps"])
        except (OSError, ValueError, TypeError, AttributeError) as error:
            errors.append(f"Trae trajectory unreadable: {type(error).__name__}")
        eligible, control = True, None
        if options.get("turn_control"):
            eligible = False
            try:
                control = json.loads((host / "control.json").read_text())
                eligible = not errors and control.get("termination_reason") == "completed" and bool(patch.strip())
            except (OSError, ValueError, AttributeError):
                errors.append("Missing or invalid turn control outcome")
            (host / "diagnostic.diff").write_text(patch, encoding="utf-8")
            if not eligible:
                (host / "patch.diff").write_text("", encoding="utf-8")
                errors.append("Controlled generation has no eligible completed patch")
        if callback is not None:
            try:
                outcome = json.loads((host / "session-outcome.json").read_text())
                if not outcome.get("complete") or outcome.get("termination"):
                    errors.append("Incomplete or terminated method session")
            except (OSError, ValueError):
                errors.append("Missing method session outcome")
            eligible = not errors and bool(patch.strip())
            if not eligible:
                (host / "diagnostic.diff").write_text(patch, encoding="utf-8")
                (host / "patch.diff").write_text("", encoding="utf-8")
        return AgentResult(agent_type="trae", prompt=prompt,
                           output=trajectory.get("final_result") or stdout.strip(),
                           tokens_used=tokens, exec_count=count, duration_sec=time.monotonic() - started,
                           raw_trace=[trajectory] if trajectory else [],
                           error="; ".join(errors) or None, artifacts=artifacts,
                           submission_eligible=eligible, control=control)


def _config(options, endpoint, lakeview_endpoint=None):
    model = {"model_provider": "selected", "model": options["model"],
             "max_tokens": 4096, "temperature": 0.5, "top_p": 1, "top_k": 0,
             "max_retries": 10, "parallel_tool_calls": True}
    return {
        "model_providers": {"selected": {"provider": "anthropic" if options["model_provider"] == "anthropic" else "openai",
                                           "api_key": "", "base_url": endpoint},
                            "selected_lakeview": {"provider": "anthropic" if options["model_provider"] == "anthropic" else "openai",
                                                  "api_key": "", "base_url": lakeview_endpoint or endpoint}},
        # Lakeview mutates temperature; keep separate native ModelConfig instances.
        "models": {"selected": dict(model), "lakeview": {**model, "model_provider": "selected_lakeview"}},
        "agents": {"trae_agent": {"model": "selected", "max_steps": options.get("max_steps", 200),
                                  "enable_lakeview": True}},
        "lakeview": {"model": "lakeview"},
        "allow_mcp_servers": [], "mcp_servers": {},
    }


def _trajectory(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), list) or any(not isinstance(item, dict) for item in value[key])
        for key in ("llm_interactions", "agent_steps")
    ):
        raise ValueError("invalid Trae trajectory")
    return value


def _original_events(trajectory):
    events = []
    for interaction in trajectory["llm_interactions"]:
        response = interaction.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            continue
        counts = {name: usage.get(name, 0) for name in ("input_tokens", "output_tokens")}
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError("invalid Trae original usage")
        events.append({"type": "turn.completed", "usage": counts})
    return events


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
