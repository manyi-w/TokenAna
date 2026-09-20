"""Explicitly load one selected adapter without scanning component directories."""

from importlib import import_module
import sys
from types import ModuleType
from uuid import uuid4

from .components import Component, ConfigError


def load_adapter(component: Component):
    """Instantiate the declared zero-argument entry point from adapter.py.

    Each load gets a private package for relative imports. Configuration options
    remain with the caller; constructors must not start experiments or install
    dependencies. Import and constructor errors propagate without retries.
    """
    if component.entrypoint is None:
        raise ConfigError(f"{component.path}: no adapter entrypoint declared")
    name = f"_tokenana_component_{uuid4().hex}"
    package = ModuleType(name)
    package.__path__ = [str(component.path)]
    package.__package__ = name
    sys.modules[name] = package
    try:
        module = import_module(f"{name}.adapter")
        return getattr(module, component.entrypoint)()
    except Exception:
        # Remove only modules belonging to this failed load, including helpers.
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                del sys.modules[key]
        raise
