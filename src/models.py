"""Shared model profiles; no clients, credential reads or network requests."""

from dataclasses import dataclass, fields
from pathlib import Path
import re
from urllib.parse import urlsplit

from .components import ConfigError, read_toml


@dataclass(frozen=True)
class ModelConfig:
    name: str
    provider: str
    model_id: str
    protocol: str
    base_url: str
    api_key_env: str
    note: str = ""


def load_model(section: dict, directory: Path) -> ModelConfig:
    if not isinstance(section, dict):
        raise ConfigError("[model] must be a table")
    allowed = {"path", "base_url", "model_id", "api_key_env"}
    if section.keys() - allowed:
        raise ConfigError("unknown model fields: " + ", ".join(sorted(section.keys() - allowed)))
    path = section.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError("model.path must be a non-empty string")
    values = read_toml((directory / path).resolve())
    names = {field.name for field in fields(ModelConfig)}
    if values.keys() - names:
        raise ConfigError("unknown model profile fields: " + ", ".join(sorted(values.keys() - names)))
    values.update({key: value for key, value in section.items() if key != "path"})
    values.setdefault("note", "")
    for key in names:
        value = values.get(key)
        if not isinstance(value, str) or (key not in {"base_url", "note"} and not value.strip()):
            raise ConfigError(f"model.{key} must be a string" +
                              ("" if key in {"base_url", "note"} else " and non-empty"))
    if values["protocol"] not in {"responses", "chat_completions", "anthropic_messages"}:
        raise ConfigError("unsupported model.protocol")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", values["api_key_env"]):
        raise ConfigError("model.api_key_env must be an environment variable name, not a key")
    if values["base_url"]:
        url = urlsplit(values["base_url"])
        if (url.scheme not in {"https", "http"} or not url.hostname or url.username or
                url.password or url.query or url.fragment or
                any(char.isspace() or char in "{}" for char in values["base_url"])):
            raise ConfigError("model.base_url must be an HTTP(S) URL without credentials or placeholders")
    return ModelConfig(**values)


def model_plan(adapter, model: ModelConfig | None, options) -> dict:
    if model is None:
        return {"status": "legacy_agent_options", "environment_checked": False}
    supported = getattr(adapter, "model_protocols", ())
    errors = []
    if model.protocol not in supported:
        errors.append(f"agent does not support model protocol {model.protocol!r}")
    if not callable(getattr(adapter, "configure_model", None)):
        errors.append("agent must implement configure_model(model, options)")
    if not model.base_url:
        errors.append("model.base_url must be supplied for the selected region/workspace")
    if not errors:
        try:
            adapter.configure_model(model, dict(options))
        except ConfigError as error:
            errors.append(str(error))
    return {"status": "blocked" if errors else "configuration_compatible",
            "supported_protocols": list(supported), "errors": errors,
            "environment_checked": False, "service_verified": False}


def model_options(adapter, model: ModelConfig | None, options) -> dict:
    if model is None:
        return dict(options)
    plan = model_plan(adapter, model, options)
    if plan["errors"]:
        raise ConfigError("; ".join(plan["errors"]))
    return adapter.configure_model(model, dict(options))
