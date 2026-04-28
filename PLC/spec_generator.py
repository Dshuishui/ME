#!/usr/bin/env python3
"""
spec_generator.py — S1 strategy: lean spec extracted from Python AST

Auto-extracts from each top-level function:
  purpose        — first line of docstring
  inputs         — parameter names + type hints
  returns        — return type annotation
  calls          — other module-level functions invoked
  reads_globals  — hw.* / ctrl.* attributes read
  writes_globals — hw.* / ctrl.* attributes written (assignment targets)
  source_anchor  — file name + line range

Intentionally omits (cannot be derived from AST alone):
  preconditions / postconditions
  redundancy / clone analysis  (that is S2's job)

Usage:
  python3 spec_generator.py <source.py> [output.yaml]
"""

import ast
import sys
import yaml
from pathlib import Path


GLOBAL_OBJECTS = frozenset({'hw', 'ctrl'})

BUILTINS = frozenset({
    'print', 'int', 'float', 'str', 'bool', 'len', 'range', 'sorted',
    'list', 'dict', 'set', 'tuple', 'isinstance', 'hasattr', 'getattr',
    'setattr', 'type', 'repr', 'abs', 'min', 'max', 'sum', 'enumerate',
    'zip', 'map', 'filter', 'any', 'all', 'open',
})


# ── Global-variable usage ────────────────────────────────────────────────────

def _collect_write_node_ids(func_node: ast.FunctionDef) -> set[int]:
    """
    Return id() of every Attribute node that appears as an assignment target
    inside func_node.  Used to distinguish reads from writes in Pass 2.
    """
    ids: set[int] = set()

    def mark(target: ast.expr) -> None:
        if isinstance(target, ast.Attribute):
            ids.add(id(target))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                mark(elt)
        elif isinstance(target, ast.Starred):
            mark(target.value)

    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                mark(t)
        elif isinstance(node, ast.AugAssign):
            mark(node.target)

    return ids


def collect_global_usage(func_node: ast.FunctionDef) -> tuple[list[str], list[str]]:
    """
    Return (reads, writes) for hw.* and ctrl.* inside func_node.

    A variable is in 'writes' if it appears as an assignment target.
    A variable is in 'reads'  if it appears in any other position.
    A variable can appear in both (read-then-write within the same function).
    """
    write_ids = _collect_write_node_ids(func_node)
    reads: set[str] = set()
    writes: set[str] = set()

    for node in ast.walk(func_node):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in GLOBAL_OBJECTS):
            attr = f'{node.value.id}.{node.attr}'
            if id(node) in write_ids:
                writes.add(attr)
            else:
                reads.add(attr)

    return sorted(reads), sorted(writes)


# ── Call collection ───────────────────────────────────────────────────────────

def collect_calls(func_node: ast.FunctionDef) -> list[str]:
    """Return sorted list of module-level function names called in func_node."""
    calls: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in BUILTINS and name != func_node.name:
                calls.add(name)
    return sorted(calls)


# ── Parameter extraction ──────────────────────────────────────────────────────

def extract_params(func_node: ast.FunctionDef) -> list[dict]:
    params = []
    for arg in func_node.args.args:
        entry: dict[str, str] = {'name': arg.arg}
        if arg.annotation:
            entry['type'] = ast.unparse(arg.annotation)
        params.append(entry)
    return params


# ── Per-function spec ─────────────────────────────────────────────────────────

def spec_for_function(func_node: ast.FunctionDef, source_name: str) -> dict:
    docstring = ast.get_docstring(func_node)
    purpose   = docstring.strip().split('\n')[0].strip() if docstring else None
    params    = extract_params(func_node)
    returns   = ast.unparse(func_node.returns) if func_node.returns else None
    calls     = collect_calls(func_node)
    reads, writes = collect_global_usage(func_node)

    return {
        'purpose':        purpose,
        'inputs':         params  or None,
        'returns':        returns or None,
        'calls':          calls   or None,
        'reads_globals':  reads   or None,
        'writes_globals': writes  or None,
        'source_anchor': {
            'file':  source_name,
            'lines': f'{func_node.lineno}-{func_node.end_lineno}',
        },
    }


# ── Module-level spec ─────────────────────────────────────────────────────────

def build_spec(source_path: Path) -> dict:
    source = source_path.read_text(encoding='utf-8')
    tree   = ast.parse(source)

    # Collect top-level names: split into literal constants vs object instances
    module_globals: list[str] = []
    module_constants: dict    = {}

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        val = ast.literal_eval(node.value)
                        module_constants[target.id] = val
                    except (ValueError, TypeError):
                        module_globals.append(target.id)

    # Collect specs for every top-level function
    functions: dict = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = spec_for_function(node, source_path.name)

    return {
        'meta': {
            'source_file':    source_path.name,
            'spec_strategy':  'S1-AST',
            'generator':      'spec_generator.py',
            'note': (
                'Lean spec auto-extracted via Python AST. '
                'Contains structure only: interface, call graph, '
                'global-variable reads/writes. '
                'No redundancy annotations — for LLM to discover.'
            ),
        },
        'module_globals': module_globals or None,
        'constants':      module_constants or None,
        'functions':      functions,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <source.py> [output.yaml]')
        sys.exit(1)

    src = Path(sys.argv[1])
    out = (Path(sys.argv[2]) if len(sys.argv) > 2
           else src.with_name(src.stem + '_spec_s1.yaml'))

    spec = build_spec(src)

    with open(out, 'w', encoding='utf-8') as f:
        yaml.dump(spec, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False)

    print(f'Spec written → {out}')
    print(f'  {len(spec["functions"])} function(s):')
    for name, fn in spec['functions'].items():
        r = len(fn['reads_globals']  or [])
        w = len(fn['writes_globals'] or [])
        c = len(fn['calls']          or [])
        print(f'    {name:30s}  reads={r}  writes={w}  calls={c}')


if __name__ == '__main__':
    main()
