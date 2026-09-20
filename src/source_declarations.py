"""Load selected unchanged declarations, with explicit dependencies, in isolation.

Third-party runner module import would start clients, parse CLI flags or import
unused GPU stacks. We compile its selected declarations unchanged into a fresh
namespace; never mutate an imported upstream module or its functions.
"""
import ast
from pathlib import Path
import sys
from types import ModuleType
from uuid import uuid4


def declarations(path, names, dependencies=None):
    path = Path(path)
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    selected, found = [], set()
    for node in tree.body:
        declared = ({node.name} if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else
                    {target.id for target in node.targets if isinstance(target, ast.Name)}
                    if isinstance(node, ast.Assign) else set())
        if declared & set(names):
            selected.append(node)
            found.update(declared & set(names))
    if found != set(names):
        raise ValueError(f'upstream declarations changed: {sorted(set(names) - found)}')
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    name = '_tokenana_source_' + uuid4().hex
    context = ModuleType(name)
    namespace = context.__dict__
    namespace.update(dependencies or {})
    sys.modules[name] = context
    try:
        exec(compile(module, str(path), 'exec'), namespace)
    finally:
        sys.modules.pop(name, None)
    return namespace
