#!/usr/bin/env python3
"""
spec_generator_s2.py — S2 strategy: lean spec + code-clone detection

Extends S1 by adding clone_hints to each function entry.

Clone detection (Algorithm A):
  1. Extract hw.*/ctrl.* assignment sequences per function as normalised tokens
       token = "attr=val_category"  (e.g. "hw.z_trigger=F", "hw.z_location=expr")
       val_category: F (False), T (True), 0 (zero), const (other literal), expr (expression)
  2. Use difflib.SequenceMatcher to find matching contiguous subsequences across function pairs
  3. Report blocks with >= MIN_BLOCK_SIZE matching ops and an overall similarity score

clone_hints schema per function:
  clone_hints:
    - similar_to: <other_func_name>
      similarity: 0.0-1.0        # matched_ops / max(len_a, len_b)
      matching_blocks:
        - self_lines:  "X-Y"     # line range of the matching block in this function
          other_lines: "X-Y"     # line range in the other function
          similarity:  1.0       # always 1.0 for Algorithm A (exact token match)

Usage:
  python3 spec_generator_s2.py <source.py> [output.yaml]
"""

import ast
import difflib
import sys
import yaml
from pathlib import Path

# Reuse all S1 helpers
sys.path.insert(0, str(Path(__file__).parent))
from spec_generator_s1 import GLOBAL_OBJECTS, build_spec


MIN_BLOCK_SIZE = 3    # minimum hw.*/ctrl.* write-ops to qualify as a clone block
MIN_SIMILARITY  = 0.1  # minimum overall similarity to add clone_hints entry


# ── Write-op extraction ───────────────────────────────────────────────────────

def _classify_value(node: ast.expr) -> str:
    """Map a written value to a coarse category token."""
    if isinstance(node, ast.Constant):
        if node.value is False:
            return 'F'
        if node.value is True:
            return 'T'
        if node.value == 0:
            return '0'
        return 'const'
    return 'expr'


def extract_write_ops(func_node: ast.FunctionDef) -> list[dict]:
    """
    Return hw.*/ctrl.* assignment records for func_node, sorted by line number.

    Each record: {'line': int, 'attr': str, 'token': str}
    where token = "<obj>.<attr>=<category>" (e.g. "hw.z_trigger=F").
    """
    ops: list[dict] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in GLOBAL_OBJECTS):
                    attr  = f"{target.value.id}.{target.attr}"
                    vtype = _classify_value(node.value)
                    ops.append({
                        'line':  node.lineno,
                        'attr':  attr,
                        'token': f"{attr}={vtype}",
                    })
    return sorted(ops, key=lambda x: x['line'])


# ── Clone detection ───────────────────────────────────────────────────────────

def _line_range(ops: list[dict], start: int, size: int) -> str:
    first = ops[start]['line']
    last  = ops[start + size - 1]['line']
    return f"{first}-{last}" if first != last else str(first)


def detect_clones(
    func_ops: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """
    Compare every function pair using SequenceMatcher over write-op token sequences.
    Returns {func_name: [clone_hint, ...]}.
    """
    names  = list(func_ops.keys())
    result: dict[str, list[dict]] = {name: [] for name in names}

    for i, name_a in enumerate(names):
        ops_a = func_ops[name_a]
        if not ops_a:
            continue
        tokens_a = [op['token'] for op in ops_a]

        for name_b in names[i + 1:]:
            ops_b = func_ops[name_b]
            if not ops_b:
                continue
            tokens_b = [op['token'] for op in ops_b]

            sm = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
            sig_blocks = [
                (a, b, size)
                for a, b, size in sm.get_matching_blocks()
                if size >= MIN_BLOCK_SIZE
            ]
            if not sig_blocks:
                continue

            matched    = sum(size for _, _, size in sig_blocks)
            similarity = round(matched / max(len(tokens_a), len(tokens_b)), 2)
            if similarity < MIN_SIMILARITY:
                continue

            matching_blocks = [
                {
                    'self_lines':  _line_range(ops_a, a, size),
                    'other_lines': _line_range(ops_b, b, size),
                    'similarity':  1.0,
                }
                for a, b, size in sig_blocks
            ]

            result[name_a].append({
                'similar_to':      name_b,
                'similarity':      similarity,
                'matching_blocks': matching_blocks,
            })
            # Mirror the hint for name_b with self/other swapped
            result[name_b].append({
                'similar_to':      name_a,
                'similarity':      similarity,
                'matching_blocks': [
                    {
                        'self_lines':  mb['other_lines'],
                        'other_lines': mb['self_lines'],
                        'similarity':  1.0,
                    }
                    for mb in matching_blocks
                ],
            })

    for name in result:
        result[name].sort(key=lambda h: -h['similarity'])

    return result


# ── S2 spec builder ───────────────────────────────────────────────────────────

def build_spec_s2(source_path: Path) -> dict:
    """Build S1 spec then inject clone_hints from Algorithm A detection."""
    spec = build_spec(source_path)

    source = source_path.read_text(encoding='utf-8')
    tree   = ast.parse(source)

    func_ops: dict[str, list[dict]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            func_ops[node.name] = extract_write_ops(node)

    hints = detect_clones(func_ops)

    spec['meta']['spec_strategy'] = 'S2-AST+Clone'
    spec['meta']['generator']     = 'spec_generator_s2.py'
    spec['meta']['note'] = (
        'S1 lean spec augmented with code-clone detection (Algorithm A: '
        'hw.*/ctrl.* write-op sequence matching via SequenceMatcher). '
        'clone_hints report which function pairs share structurally identical '
        'write-op subsequences, with source-line ranges for each block.'
    )

    for name, fn in spec['functions'].items():
        h = hints.get(name, [])
        fn['clone_hints'] = h if h else None

    return spec


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <source.py> [output.yaml]')
        sys.exit(1)

    src = Path(sys.argv[1])
    out = (Path(sys.argv[2]) if len(sys.argv) > 2
           else src.with_name(src.stem + '_spec_s2.yaml'))

    spec = build_spec_s2(src)

    with open(out, 'w', encoding='utf-8') as f:
        yaml.dump(spec, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False)

    print(f'Spec written → {out}')
    print(f'  {len(spec["functions"])} function(s):')
    for name, fn in spec['functions'].items():
        hints = fn.get('clone_hints') or []
        r = len(fn['reads_globals']  or [])
        w = len(fn['writes_globals'] or [])
        c = len(fn['calls']          or [])
        h = len(hints)
        pairs    = ', '.join(f'{x["similar_to"]}({x["similarity"]})' for x in hints)
        hint_str = f'  clone_hints={h}: {pairs}' if h else ''
        print(f'    {name:30s}  reads={r}  writes={w}  calls={c}{hint_str}')


if __name__ == '__main__':
    main()
