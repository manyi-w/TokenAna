"""Minimal TOML experiment configuration and explicit component selection."""

from dataclasses import asdict, dataclass
from pathlib import Path

from .components import Component, ConfigError, load_component, read_toml
from .models import ModelConfig, load_model


@dataclass(frozen=True)
class ExperimentConfig:
    path: Path
    method: Component
    agent: Component
    dataset: Component
    model: ModelConfig | None = None


def config_dict(config: ExperimentConfig) -> dict:
    result = asdict(config)
    # Preserve the identity of existing runs without an explicit model profile.
    if config.model is None:
        result.pop("model")
    return result


def restore_experiment(snapshot: dict, path: Path, *, task_id: str) -> ExperimentConfig:
    """Restore a frozen study profile without reapplying current launcher defaults."""
    components = {}
    for kind in ("method", "agent", "dataset"):
        item = dict(snapshot[kind])
        item["path"] = Path(item["path"])
        item["options"] = dict(item["options"])
        if kind == "dataset":
            item["options"]["task_ids"] = [task_id]
        components[kind] = Component(**item)
    model = ModelConfig(**snapshot["model"]) if snapshot.get("model") else None
    return ExperimentConfig(path=path, model=model, **components)


def load_experiment(path: str | Path) -> ExperimentConfig:
    path = Path(path).resolve()
    document = read_toml(path)
    kinds = ("method", "agent", "dataset")
    unknown = document.keys() - {*kinds, "model"}
    if unknown:
        raise ConfigError(f"{path}: unknown sections: {', '.join(sorted(unknown))}")

    components = {}
    for kind in kinds:
        section = document.get(kind)
        if not isinstance(section, dict):
            raise ConfigError(f"{path}: [{kind}] must be a table")
        unknown = section.keys() - {"path", "options"}
        if unknown:
            raise ConfigError(f"{path}: unknown {kind} fields: {', '.join(sorted(unknown))}")
        component_path = section.get("path")
        if not isinstance(component_path, str) or not component_path.strip():
            raise ConfigError(f"{path}: {kind}.path must be a non-empty string")
        options = section.get("options", {})
        if not isinstance(options, dict):
            raise ConfigError(f"{path}: {kind}.options must be a table")
        directory = (path.parent / component_path).resolve()
        components[kind] = load_component(directory, kind, options)

    model = load_model(document["model"], path.parent) if "model" in document else None
    return ExperimentConfig(path=path, model=model, **components)
