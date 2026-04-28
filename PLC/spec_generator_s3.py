#!/usr/bin/env python3
"""
spec_generator_s3.py — S3 strategy: LLM-generated semantic spec

Hybrid approach:
  - AST:  source_anchor (line ranges) — reliable, no LLM guessing
  - LLM:  purpose, semantic_summary, preconditions, postconditions,
          inputs/outputs, calls, reads_globals, writes_globals

Extra fields vs S1:
  semantic_summary  — narrative description of what the function does step-by-step
  preconditions     — list of conditions that must hold before the function is called
  postconditions    — list of guaranteed outcomes after the function returns

The LLM is instructed to be thorough; the result may be verbose/noisy.
Use spec_simplifier_s3.py to produce a compressed version for comparison.

Usage:
  python3 spec_generator_s3.py <source.py> [output.yaml] [--model <model>]
"""

import ast
import argparse
import os
import re
import sys
import textwrap
from pathlib import Path

import anthropic
import yaml


DEFAULT_MODEL = 'anthropic/claude-sonnet-4-6'

SPEC_GEN_SYSTEM = textwrap.dedent("""\
    You are a program analysis expert. Your job is to read Python source code
    and produce a structured YAML spec for each top-level function.

    For each function, output the following fields:
      purpose          — 1-2 sentence description of the function's intent
      semantic_summary — 2-4 sentence step-by-step narrative of what the function does
      preconditions    — list of conditions that must be true before calling
                         (focus on global state: hw.*, ctrl.*, external invariants)
      postconditions   — list of guaranteed outcomes after the function returns
                         (focus on observable global state changes)
      inputs           — list of {name, type, description} for each parameter
      returns          — return type and meaning (null if None)
      calls            — list of other module-level functions called (not builtins)
      reads_globals    — list of hw.* and ctrl.* attributes read (not written)
      writes_globals   — list of hw.* and ctrl.* attributes written (assigned to)

    Output ONLY a valid YAML block. Use this exact top-level structure:

    ```yaml
    functions:
      <function_name>:
        purpose: "<string>"
        semantic_summary: "<string>"
        preconditions:
          - "<condition>"
        postconditions:
          - "<condition>"
        inputs:
          - name: <param_name>
            type: <type>
            description: "<what it represents>"
        returns: <type or null>
        calls:
          - <function_name>
        reads_globals:
          - <hw.field>
        writes_globals:
          - <hw.field>
    ```

    Rules:
    - Include ALL top-level functions, even private ones (prefix _).
    - For functions with no preconditions / postconditions, use empty list [].
    - reads_globals and writes_globals must only contain hw.* or ctrl.* attributes.
    - A field that appears only in an assignment target is a write; otherwise a read.
    - Do not include source_anchor — it will be added automatically.
    - Do not add any commentary outside the YAML block.
""")


def make_client() -> anthropic.Anthropic:
    auth_token = os.environ.get('ANTHROPIC_AUTH_TOKEN')
    base_url   = os.environ.get('ANTHROPIC_BASE_URL')
    if auth_token and base_url:
        return anthropic.Anthropic(base_url=base_url, auth_token=auth_token)
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    print('Error: set ANTHROPIC_AUTH_TOKEN+ANTHROPIC_BASE_URL or ANTHROPIC_API_KEY',
          file=sys.stderr)
    sys.exit(1)


def call_llm_for_spec(source: str, filename: str, model: str) -> dict:
    client = make_client()
    user_prompt = (
        f"Here is the Python source file `{filename}`:\n\n"
        f"```python\n{source}\n```\n\n"
        "Please generate the YAML spec for all top-level functions as described."
    )
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SPEC_GEN_SYSTEM,
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    return message.content[0].text


def extract_yaml_from_response(response: str) -> dict:
    """Extract and parse the YAML block from LLM response."""
    # Try fenced code block first
    match = re.search(r'```(?:yaml)?\s*\n(.*?)```', response, re.DOTALL)
    yaml_str = match.group(1) if match else response.strip()
    return yaml.safe_load(yaml_str)


def collect_anchors(tree: ast.Module, filename: str) -> dict[str, dict]:
    """Extract source_anchor for every top-level FunctionDef via AST."""
    anchors = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            anchors[node.name] = {
                'file':  filename,
                'lines': f'{node.lineno}-{node.end_lineno}',
            }
    return anchors


def collect_module_level(tree: ast.Module) -> tuple[list[str], dict]:
    """Collect module-level globals and literal constants (same as S1)."""
    globals_: list[str] = []
    constants: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        val = ast.literal_eval(node.value)
                        constants[target.id] = val
                    except (ValueError, TypeError):
                        globals_.append(target.id)
    return globals_, constants


def build_spec_s3(source_path: Path, model: str) -> dict:
    source   = source_path.read_text(encoding='utf-8')
    tree     = ast.parse(source)
    anchors  = collect_anchors(tree, source_path.name)
    globals_, constants = collect_module_level(tree)

    print(f'Calling {model} to generate S3 spec for {source_path.name}...')
    response = call_llm_for_spec(source, source_path.name, model)
    llm_data = extract_yaml_from_response(response)

    functions = llm_data.get('functions', {})

    # Inject AST-derived source_anchor (authoritative; don't trust LLM line numbers)
    for name, fn in functions.items():
        if name in anchors:
            fn['source_anchor'] = anchors[name]
        # Normalise list fields: replace None/missing with null-safe values
        for list_field in ('calls', 'reads_globals', 'writes_globals',
                           'preconditions', 'postconditions'):
            if not fn.get(list_field):
                fn[list_field] = None

    return {
        'meta': {
            'source_file':   source_path.name,
            'spec_strategy': 'S3-LLM',
            'generator':     'spec_generator_s3.py',
            'model':         model,
            'note': (
                'Rich semantic spec generated by LLM directly reading source code. '
                'Contains purpose, semantic_summary, preconditions, postconditions '
                'in addition to the S1 structural fields. '
                'source_anchor is authoritative (AST-derived). '
                'May contain verbose or noisy semantic descriptions — '
                'use spec_simplifier_s3.py for a compressed version.'
            ),
        },
        'module_globals': globals_ or None,
        'constants':      constants or None,
        'functions':      functions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='S3 spec generator (LLM-based)')
    parser.add_argument('source', help='Python source file')
    parser.add_argument('output', nargs='?', help='Output YAML file (default: *_spec_s3.yaml)')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    args = parser.parse_args()

    src = Path(args.source)
    out = Path(args.output) if args.output else src.with_name(src.stem + '_spec_s3.yaml')

    spec = build_spec_s3(src, args.model)

    with open(out, 'w', encoding='utf-8') as f:
        yaml.dump(spec, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False)

    print(f'Spec written → {out}')
    print(f'  {len(spec["functions"])} function(s):')
    for name, fn in spec['functions'].items():
        pre  = len(fn.get('preconditions')  or [])
        post = len(fn.get('postconditions') or [])
        r    = len(fn.get('reads_globals')  or [])
        w    = len(fn.get('writes_globals') or [])
        print(f'    {name:30s}  reads={r}  writes={w}  pre={pre}  post={post}')


if __name__ == '__main__':
    main()
