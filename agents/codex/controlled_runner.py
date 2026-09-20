"""Launch the reviewed optional-hook build; ordinary Codex keeps its original caller."""

from dataclasses import asdict
import json
from pathlib import PurePosixPath
import re
from shlex import join, quote
import subprocess
import time

from src.components import ConfigError
from src.control import TurnBudget
from src.interfaces import AgentResult
from src.model_channel import recording_model_channel
from src.attribution import codex_attribution
from src.patches import capture_patch
from src.workspaces import execution_timeout
from .summary import VERSION, PROFILES, control_ready, final_summary, load_outcome


def validate_options(options):
    executable = options.get("controlled_executable")
    if not isinstance(executable, str) or not PurePosixPath(executable).is_absolute():
        raise ConfigError("controlled Codex requires an absolute prepared controlled_executable")
    if options.get("control_version") != VERSION:
        raise ConfigError(f"controlled Codex requires control_version={VERSION}")
    if options.get("record_raw_usage") is not True:
        raise ConfigError("controlled Codex requires record_raw_usage=true and its hook-ready guard")


def _arguments(options, endpoint):
    provider = {"name": "tokenana-recorder", "base_url": endpoint,
                "wire_api": options.get("wire_api", "responses"), "supports_websockets": False}
    key = options.get("model_api_key_env")
    if key:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError("invalid model_api_key_env")
        provider["env_key"] = key
    toml = "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in provider.items()) + " }"
    argv = [options["controlled_executable"], "exec", "-c",
            "model_providers.tokenana-recorder=" + toml,
            "-c", 'model_provider="tokenana-recorder"', "-c", "ephemeral=false"]
    if options.get("model"):
        argv += ["-c", "model=" + json.dumps(options["model"])]
    return argv + ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]


def run(prompt, workspace, options):
    validate_options(options)
    budget = TurnBudget(**options["turn_control"]).options()
    profiles = [name for name, pair in PROFILES.items() if pair == (budget["initial"], budget["final"])]
    if len(profiles) != 1:
        raise ConfigError("Codex control accepts only the three approved budget tiers")
    timeout = execution_timeout(workspace, options)
    if type(timeout) not in (int, float) or not 0 < timeout < float("inf"):
        raise ConfigError("controlled Codex timeout must be finite and positive")
    artifacts = workspace.new_artifacts()
    host, target = artifacts.host, PurePosixPath(artifacts.execution)
    (host / "prompt.txt").write_text(prompt, encoding="utf-8")
    (host / "control-request.json").write_text(json.dumps(
        {"version": VERSION, "profile": profiles[0]}), encoding="utf-8")
    (host / "codex-home").mkdir()
    (host / "control-launch.json").write_text(json.dumps({
        "version": VERSION, "executable": options["controlled_executable"],
        "budget": budget, "timeout": timeout, "protocol": "responses",
        "native_home": "codex-home", "method_resume": "never_regenerate",
    }, indent=2), encoding="utf-8")
    process, transport_error = None, None
    started = time.monotonic()
    errors = []
    try:
        with recording_model_channel(
            workspace, artifacts, options["model_base_url"], protocol="responses",
            provider=options.get("usage_provider"), timeout=options.get("proxy_timeout", 60),
            request_guard=lambda: control_ready(host, budget), attribution_resolver=codex_attribution,
        ) as endpoint:
            argv = ["timeout", "--signal=TERM", "--kill-after=5s", f"{timeout}s", "env",
                    "TOKENANA_CONTROL_DIRECTORY=" + str(target),
                    "CODEX_HOME=" + str(target / "codex-home"), *_arguments(options, endpoint)]
            # GNU timeout controls the in-container process group; the outer timeout is
            # transport insurance only. Never collect a patch after transport uncertainty.
            script = (join(argv) + ' "$(cat ' + quote(str(target / "prompt.txt")) + ')"'
                      + " > " + quote(str(target / "trace.jsonl"))
                      + " 2> " + quote(str(target / "stderr.txt")))
            command = workspace.launch_command(["bash", "-c", "exec " + script])
            with (host / "transport-stdout.txt").open("w") as stdout, \
                    (host / "transport-stderr.txt").open("w") as stderr:
                process = subprocess.run(command, stdout=stdout, stderr=stderr,
                                         stdin=subprocess.DEVNULL, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        transport_error = "transport timeout; agent termination not confirmed"
    except Exception as error:
        transport_error = f"{type(error).__name__}: {error}"
    (host / "process.json").write_text(json.dumps({
        "returncode": process.returncode if process is not None else None,
        "transport_error": transport_error,
    }, indent=2), encoding="utf-8")
    if transport_error:
        errors.append(transport_error)
    if process is not None and process.returncode:
        errors.append("Timeout" if process.returncode in (124, 137) else
                      f"Non-zero exit code: {process.returncode}")
    patch = ""
    capture_status = "not_attempted_unconfirmed_stop"
    if process is not None and transport_error is None:
        try:
            patch = capture_patch(workspace, artifacts, timeout=options.get("diff_timeout", 60))
            capture_status = "nonempty" if patch.strip() else "empty"
        except Exception as error:
            capture_status = "failed"
            errors.append(f"patch capture failed: {error}")
    (host / "capture-result.json").write_text(json.dumps({"status": capture_status}), encoding="utf-8")
    (host / "diagnostic.diff").write_bytes(patch.encode("utf-8"))
    outcome, summary, trace = None, None, []
    try:
        outcome, trace, _, _, _ = load_outcome(host, budget)
        summary = final_summary(host)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(f"invalid Codex control/native outcome: {error}")
    eligible = bool(not errors and outcome and outcome["success"] and patch.strip())
    if not eligible:
        errors.append("Controlled generation has no eligible completed patch")
    # Keep model.patch untouched; only the submission-facing artifact is cleared.
    (host / "patch.diff").write_bytes(patch.encode("utf-8") if eligible else b"")
    (host / "control-result.json").write_text(json.dumps({
        "control": outcome, "summary": asdict(summary) if summary else None,
        "submission_eligible": eligible, "errors": errors,
    }, indent=2), encoding="utf-8")
    output = "\n".join(row["item"].get("text", "") for row in trace
                       if row.get("type") == "item.completed"
                       and isinstance(row.get("item"), dict)
                       and row["item"].get("type") == "agent_message")
    # Legacy fields are only a native known subtotal. Accounting always rereads raw artifacts.
    tokens = sum(value for value in (summary.input_tokens, summary.output_tokens)
                 if value is not None) if summary else 0
    return AgentResult("codex", prompt, output, tokens,
                       summary.function_calls if summary and summary.function_calls is not None else 0,
                       time.monotonic() - started, trace, "; ".join(errors) or None,
                       artifacts, eligible, outcome)
