"""Shared read-only accounting preflight for planning and execution."""

from .run_accounting import original_supported
from .usage_protocols import API_PATHS
from .model_channel import validate_channel


def accounting_plan(method, agent, options, model=None, runtime=None, dataset=None):
    enabled = bool(options.get("record_raw_usage"))
    original = original_supported(method, agent)
    protocols = getattr(agent, "raw_usage_protocols", ())
    protocol = model.protocol if model else options.get("model_protocol", options.get("wire_api", "responses"))
    provider = model.provider if model else options.get("usage_provider", options.get("model_provider"))
    errors = []
    required = getattr(method, "required_session_capabilities", ())
    if required:
        from .session import require_session
        try:
            require_session(agent, required)
            validate_session = getattr(agent, "validate_session_options", None)
            if callable(validate_session):
                validate_session(options)
        except ValueError as error:
            errors.append(str(error))
    if getattr(dataset, "requires_record_raw_usage", False) and not enabled:
        errors.append("this dataset requires raw usage recording through its isolated model channel")
    if enabled:
        if not original:
            errors.append("original accounting is unavailable for this method/agent combination")
        if protocol not in protocols or not callable(getattr(agent, "read_case_usage", None)):
            errors.append("agent has no raw usage reader/recording support for the selected protocol")
        validate = getattr(agent, "validate_raw_usage", None)
        if callable(validate):
            try:
                validate(options)
            except ValueError as error:
                errors.append(str(error))
        else:
            errors.append("agent has no raw usage preflight")
        if runtime is not None:
            try:
                validate_channel(runtime, supported=getattr(dataset, "supports_model_channel", False))
            except ValueError as error:
                errors.append(str(error))
    cache = "provider_default_unverified"
    if provider == "anthropic":
        cache = "explicit_cache_control_required; agent integration must enable it"
        if protocol in getattr(agent, "explicit_cache_protocols", ()):
            cache = "explicit_cache_control_configured; actual markers/hits require API records"
    elif provider in ("openai", "deepseek"):
        cache = "automatic_provider_cache; actual hits require response usage"
    return {
        "status": "blocked" if errors else ("configuration_compatible" if enabled else "recording_disabled"),
        "errors": errors, "original_supported": original, "record_raw_usage": enabled,
        "required_session_capabilities": list(required),
        "agent_session_capabilities": list(getattr(agent, "session_capabilities", ())),
        "original_variant": getattr(agent, "original_accounting_variant", None),
        "protocol": protocol, "agent_recording_protocols": list(protocols),
        "recorded_paths": list(API_PATHS.get(protocol, ())) if protocol in protocols else [],
        "cache_strategy": cache, "service_verified": False,
        "coverage_note": "Only recorded HTTP paths; bypassed requests and sessions are unverified",
    }
