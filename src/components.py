"""Read selected component metadata without importing component code."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


class ConfigError(ValueError):
    """An experiment or selected component has invalid configuration."""


@dataclass(frozen=True)
class Component:
    kind: str
    name: str
    path: Path
    options: dict[str, Any]
    entrypoint: str | None = None


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error


def load_component(path: Path, kind: str, options: dict[str, Any]) -> Component:
    """Read only this directory's manifest; do not scan siblings or import code."""
    manifest_path = path / "manifest.toml"
    manifest = read_toml(manifest_path)
    if manifest.get("kind") != kind:
        raise ConfigError(f"{manifest_path}: kind must be {kind!r}")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{manifest_path}: name must be a non-empty string")
    entrypoint = manifest.get("entrypoint")
    if entrypoint is not None and (not isinstance(entrypoint, str) or not entrypoint.isidentifier()):
        raise ConfigError(f"{manifest_path}: entrypoint must be a Python identifier")
    return Component(kind=kind, name=name, path=path, options=options, entrypoint=entrypoint)
