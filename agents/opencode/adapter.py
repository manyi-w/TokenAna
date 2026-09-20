"""Prepared OpenCode CLI integration; no upstream imports or package installation."""

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


_SDKS = {("openai", "responses"): "@ai-sdk/openai",
         ("dashscope", "responses"): "@ai-sdk/openai",
         ("openai", "chat_completions"): "@ai-sdk/openai-compatible",
         ("anthropic", "anthropic_messages"): "@ai-sdk/anthropic",
         ("deepseek", "chat_completions"): "@ai-sdk/openai-compatible",
         ("dashscope", "chat_completions"): "@ai-sdk/openai-compatible"}


class OpenCode:
    capabilities = ("turn_control",)
    control_runtime = "plugin"
    session_capabilities = ("events", "replace_history", "state", "reminder", "terminate")
    session_version = "opencode-session-compatible-v1"
    model_protocols = ("responses", "chat_completions", "anthropic_messages")
    raw_usage_protocols = model_protocols
    explicit_cache_protocols = ("anthropic_messages",)
    original_compatible_trace_formats = ("codex-jsonl", "agent-final-summary-v1")
    original_accounting_variant = "opencode-step-compatible-v1"

    def validate_control_options(self, options):
        if not options.get("record_raw_usage"):
            raise ConfigError("controlled OpenCode requires raw recording and its plugin-ready request guard")

    def configure_model(self, model, options):
        if (model.provider, model.protocol) not in _SDKS:
            raise ConfigError("unsupported OpenCode provider/protocol combination; no translation")
        selected = {"model": model.model_id, "model_provider": model.provider,
                    "model_protocol": model.protocol, "model_base_url": model.base_url,
                    "model_api_key_env": model.api_key_env}
        if selected.keys() & options.keys():
            raise ConfigError("use [model] for OpenCode model settings")
        return {**options, **selected}

    def validate_raw_usage(self, options):
        if (options.get("model_provider"), options.get("model_protocol")) not in _SDKS:
            raise ConfigError("unsupported OpenCode provider/protocol combination")
        if not options.get("model_base_url"):
            raise ConfigError("OpenCode requires explicit model_base_url")
        for name in ("context_limit", "output_limit"):
            if type(options.get(name)) is not int or options[name] <= 0:
                raise ConfigError(f"OpenCode requires explicit positive {name}")
        if options["output_limit"] > options["context_limit"]:
            raise ConfigError("output_limit must not exceed context_limit")

    def plan(self, options):
        return {
            "missing_options": [key for key in ("executable", "config_root", "context_limit", "output_limit",
                                                 "model", "model_base_url", "model_api_key_env") if not options.get(key)],
            "command_template": [options.get("executable") or "<source-built-opencode>",
                                 "run", "--format", "json", "--agent", "build", "--auto"],
            "required": ["Bundled provider SDKs and rg on PATH in a prepared Linux image",
                         "config_root/opencode must be read-only and contain only .gitignore",
                         "No ~/.opencode directory; explicit context/output limits"],
            "small_model": "same selected model", "environment_checked": False,
            "note": "Native build agent and compaction; only the local turn-control plugin is optionally enabled",
        }

    def read_case_usage(self, directory, **identity):
        return read_raw_usage(directory / "api-records", **identity)

    def read_original_case(self, directory, case_id):
        path = directory / "trace.jsonl"
        trace = _original_events(_events(path)) if path.exists() else None
        patch = directory / "patch.diff"
        return OriginalCase(case_id, trace, patch.read_text(encoding="utf-8") if patch.exists() else None)

    def read_final_summary_case(self, directory, case_id):
        from .summary import final_summary
        try:
            return OriginalCase(case_id, None, None, final_summary(directory))
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("invalid OpenCode final session structure") from error

    def validate_session_options(self, options):
        self.validate_control_options(options)
        self.validate_raw_usage(options)
        timeout = options.get("session_timeout", 60)
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ConfigError("session_timeout must be positive and at most 3600 seconds")

    def run_session(self, prompt, workspace, options, callback):
        self.validate_session_options(options)
        return self._run(prompt, workspace, options, callback)

    def run(self, prompt, workspace, options):
        return self._run(prompt, workspace, options)

    def _run(self, prompt, workspace, options, callback=None):
        if self.plan(options)["missing_options"]:
            raise ConfigError("OpenCode runtime options are incomplete")
        self.validate_raw_usage(options)
        key = options["model_api_key_env"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError("invalid model_api_key_env")
        root = Path(options["config_root"])
        if not root.is_absolute():
            raise ConfigError("config_root must be an absolute prepared image path")
        artifacts = workspace.new_artifacts()
        host, target = artifacts.host, Path(artifacts.execution)
        (host / "prompt.txt").write_text(prompt, encoding="utf-8")
        guard = None
        if options.get("turn_control"):
            from src.control import TurnBudget
            from .summary import plugin_ready
            self.validate_control_options(options)
            budget = TurnBudget(**options["turn_control"]).options()
            (host / "control-request.json").write_text(json.dumps(budget), encoding="utf-8")
            (host / "turn_control.mjs").write_text(
                Path(__file__).with_name("turn_control.mjs").read_text(encoding="utf-8"), encoding="utf-8")
            guard = lambda: plugin_ready(host, budget)
        errors, process = [], None
        started = time.monotonic()
        with ExitStack() as stack:
            session = None
            if callback is not None:
                from src.session_channel import session_server
                session = stack.enter_context(session_server(artifacts, callback, identity=getattr(workspace, 'usage_identity', {})))
                (host / "session.mjs").write_text(Path(__file__).with_name("session.mjs").read_text())
                (host / "session-request.json").write_text(json.dumps({"timeout": options.get("session_timeout", 60)}))
                (host / "session-version.json").write_text(json.dumps({"version": self.session_version}))
            endpoint = options["model_base_url"]
            if options.get("record_raw_usage"):
                (host / "accounting.mjs").write_text(Path(__file__).with_name("accounting.mjs").read_text())
                from src.model_channel import recording_model_channel
                def ready():
                    return (host / "accounting-plugin.ready").is_file() and (guard is None or guard()) and (session is None or
                        ((host / "session-plugin.json").exists() and not session.finished and not session.termination))
                def attribution(request, headers):
                    purpose = next((v for k, v in headers.items() if k.lower() == "x-tokenana-purpose"), "unknown")
                    return {"purpose": purpose if purpose in ("main", "agent_auxiliary") else "unknown"}
                endpoint = stack.enter_context(recording_model_channel(workspace, artifacts, options["model_base_url"],
                    protocol=options["model_protocol"], provider=options["model_provider"],
                    timeout=options.get("proxy_timeout", 60), request_guard=ready, attribution_resolver=attribution))
            config = _config(options, endpoint)
            if options.get("turn_control"):
                config["plugin"] = [(target / "turn_control.mjs").as_uri()]
            if options.get("record_raw_usage"):
                config["plugin"].append((target / "accounting.mjs").as_uri())
            if callback is not None:
                config["plugin"].append((target / "session.mjs").as_uri())
            (host / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
            env = {"OPENCODE_CONFIG": str(target / "config.json"), "XDG_CONFIG_HOME": str(root),
                   "XDG_DATA_HOME": str(target / "data"), "XDG_CACHE_HOME": str(target / "cache"),
                   "XDG_STATE_HOME": str(target / "state"), "OPENCODE_DISABLE_AUTOUPDATE": "1",
                   "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                   "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
                   "OPENCODE_DISABLE_CLAUDE_CODE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
                   "OPENCODE_DISABLE_FFF": "1"}
            if options.get("turn_control"):
                env["TOKENANA_CONTROL_DIRECTORY"] = str(target)
            if callback is not None:
                env["TOKENANA_SESSION_DIRECTORY"] = str(target)
            if options.get("record_raw_usage"):
                env["TOKENANA_ACCOUNTING_DIRECTORY"] = str(target)
            config_dir = quote(str(root / "opencode"))
            # Native Npm.install returns without installing when its directory is not writable.
            checks = (f'test -d {config_dir} && test ! -w {config_dir} && '
                      f'test -f {config_dir}/.gitignore && '
                      f'test -z "$(find {config_dir} -mindepth 1 ! -name .gitignore -print -quit)" && '
                      'test ! -e "$HOME/.opencode" && command -v rg >/dev/null')
            command = ["env", *[f"{name}={value}" for name, value in env.items()],
                       options["executable"], "run", "--format", "json", "--agent", "build", "--auto",
                       "--model", f"{options['model_provider']}/{options['model']}", "--dir", workspace.root]
            script = (checks + ' || { echo "OpenCode requires an isolated read-only configuration" >&2; exit 2; }; '
                      'unset OPENCODE_CONFIG_CONTENT OPENCODE_CONFIG_DIR OPENCODE_MODELS_PATH OPENCODE_DB'
                      + (' OPENCODE_PURE' if options.get("turn_control") or callback is not None or options.get("record_raw_usage") else '')
                      + f'; : "${{{key}:?model API key is required}}"; exec ' + join(command))
            # Stream JSONL directly to disk, preserving partial records on interruption.
            with (host / "trace.jsonl").open("w", encoding="utf-8") as out, \
                    (host / "stderr.txt").open("w", encoding="utf-8") as err:
                try:
                    process = subprocess.run(workspace.launch_command(["bash", "-c", script]),
                                             input=prompt, text=True, stdout=out, stderr=err,
                                             timeout=execution_timeout(workspace, options))
                    if process.returncode:
                        errors.append(f"Non-zero exit code: {process.returncode}")
                except subprocess.TimeoutExpired:
                    errors.append("Timeout")
        if options.get("turn_control") or callback is not None:
            (host / "process.json").write_text(json.dumps({
                "returncode": process.returncode if process is not None else None}), encoding="utf-8")
            if process is not None:
                try:
                    self._export_session(workspace, host, target, options, env, config)
                except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as error:
                    errors.append(f"OpenCode session export failed: {type(error).__name__}")
        patch = ""
        if process is not None:
            try:
                patch = capture_patch(workspace, artifacts, timeout=options.get("diff_timeout", 60))
            except Exception as error:
                errors.append(f"patch capture failed: {error}")
        (host / "patch.diff").write_text(patch, encoding="utf-8")
        events, tokens, count, output = [], 0, 0, ""
        try:
            events = _events(host / "trace.jsonl")
            original = _original_events(events)
            tokens = sum(sum(item["usage"].values()) for item in original)
            parts = [event.get("part", {}) for event in events]
            count = len({part["id"] for part in parts if part.get("type") == "tool" and part.get("id")})
            output = "\n".join(part["text"] for part in parts if part.get("type") == "text" and isinstance(part.get("text"), str))
            if any(event.get("type") == "error" for event in events):
                errors.append("OpenCode emitted a session error")
            if not options.get("turn_control") and not any(
                    part.get("type") == "step-finish" and part.get("reason") == "stop" for part in parts):
                errors.append("No completed OpenCode stop step")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            errors.append(f"OpenCode trace unreadable: {type(error).__name__}")
        eligible, control = True, None
        if options.get("turn_control"):
            eligible = False
            try:
                from .summary import load_outcome
                control, _, _, _ = load_outcome(host, budget)
                eligible = not errors and control["success"] and bool(patch.strip())
                (host / "control-result.json").write_text(json.dumps(control, indent=2), encoding="utf-8")
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                control = None
                errors.append("Missing or invalid OpenCode control/native session outcome")
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
        return AgentResult(agent_type="opencode", prompt=prompt, output=output, tokens_used=tokens,
                           exec_count=count, duration_sec=time.monotonic() - started, raw_trace=events,
                           error="; ".join(errors) or None, artifacts=artifacts,
                           submission_eligible=eligible, control=control)

    def _export_session(self, workspace, host, target, options, env, config):
        control = json.loads((host / ("control.json" if options.get("turn_control") else "session-plugin.json")).read_text())
        session = control.get("main_session")
        if not isinstance(session, str) or not session:
            raise ValueError("control plugin recorded no main session")
        # Native read-only export, with plugins disabled to avoid reinitializing control.
        (host / "export-config.json").write_text(json.dumps({**config, "plugin": []}), encoding="utf-8")
        export_env = {**env, "OPENCODE_CONFIG": str(target / "export-config.json")}
        export_env.pop("TOKENANA_CONTROL_DIRECTORY", None)
        export_env.pop("TOKENANA_SESSION_DIRECTORY", None)
        export_env.pop("TOKENANA_ACCOUNTING_DIRECTORY", None)
        argv = ["env", *[f"{name}={value}" for name, value in export_env.items()],
                options["executable"], "export", session]
        script = ('unset OPENCODE_CONFIG_CONTENT OPENCODE_CONFIG_DIR OPENCODE_MODELS_PATH OPENCODE_DB '
                  'TOKENANA_CONTROL_DIRECTORY TOKENANA_SESSION_DIRECTORY OPENCODE_PURE; exec ' + join(argv))
        with (host / "session.json").open("w") as out, (host / "export-stderr.txt").open("w") as err:
            subprocess.run(workspace.launch_command(["bash", "-c", script]), stdout=out, stderr=err,
                           stdin=subprocess.DEVNULL, timeout=60, check=True)


def _config(options, endpoint):
    provider, model = options["model_provider"], options["model"]
    if options["model_protocol"] == "anthropic_messages" and not endpoint.rstrip("/").endswith("/v1"):
        endpoint = endpoint.rstrip("/") + "/v1"
    # Only bundled SDKs; the native loader does not need Npm.add for these packages.
    return {"model": f"{provider}/{model}", "small_model": f"{provider}/{model}",
            "enabled_providers": [provider], "autoupdate": False, "share": "disabled",
            "plugin": [], "mcp": {}, "lsp": False, "formatter": False,
            "provider": {provider: {"npm": _SDKS[(provider, options["model_protocol"])],
                "whitelist": [model], "options": {"baseURL": endpoint,
                    "apiKey": "{env:" + options["model_api_key_env"] + "}"},
                "models": {model: {"id": model, "tool_call": True,
                    "limit": {"context": options["context_limit"], "output": options["output_limit"]}}}}}}


def _events(path):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("invalid OpenCode event")
        events.append(event)
    return events


def _original_events(events):
    try:
        return _parse_original_events(events)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("invalid OpenCode step usage structure") from error


def _parse_original_events(events):
    unique = {}
    for event in events:
        if event.get("type") != "step_finish":
            continue
        part = event["part"]
        identity = (part["sessionID"], part["id"])
        tokens = part["tokens"]
        # Native counts separate cached input and reasoning from ordinary tokens.
        values = [tokens["input"], tokens["output"], tokens["reasoning"],
                  tokens["cache"]["read"], tokens["cache"]["write"]]
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("invalid OpenCode token counts")
        counts = {"input_tokens": values[0] + values[3] + values[4],
                  "output_tokens": values[1] + values[2]}
        if identity in unique and unique[identity] != counts:
            raise ValueError("conflicting OpenCode step usage")
        unique[identity] = counts
    return [{"type": "turn.completed", "usage": counts} for counts in unique.values()]
