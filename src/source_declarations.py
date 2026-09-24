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


def configured_module(path, name, bindings):
    """Load an isolated source module with explicit configuration/dependencies.

    Supplied top-level bindings replace declarations *before* import. All other
    AST nodes are compiled unchanged; no imported upstream object is patched.
    This is for endpoint/resource configuration, not replacing algorithms.
    """
    path = Path(path)
    tree = ast.parse(path.read_text(), filename=str(path))
    body, found = [], set()
    for node in tree.body:
        declared = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            declared = {node.name}
        elif isinstance(node, ast.Assign):
            declared = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            declared = {node.target.id}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            declared = {a.asname or (a.name.split('.')[0] if isinstance(node, ast.Import) else a.name) for a in node.names}
        if declared & bindings.keys():
            if declared - bindings.keys():
                raise ValueError('Partial configuration of grouped declaration is unsupported')
            found.update(declared)
        else:
            body.append(node)
    if found != bindings.keys():
        raise ValueError('Unknown source configuration bindings: ' + ', '.join(bindings.keys() - found))
    module = ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition('.')[0]
    module.__dict__.update(bindings)
    sys.modules[name] = module
    try:
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
