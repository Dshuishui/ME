#!/usr/bin/env python3
"""
llm_analyzer.py — Send a lean spec to Claude and collect modularization analysis.

Output format: two-stage (Option C)
  Stage 1: free-text reasoning — LLM explains what it observes
  Stage 2: structured YAML     — LLM lists atomic module candidates

Results are printed to stdout and optionally saved to a Markdown experiment log.

Usage:
  python3 llm_analyzer.py <spec.yaml> [--save <exp_log.md>] [--model <model>]

Auth (checked in order):
  OpenRouter: set ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN  (e.g. via claude-or alias)
  Anthropic:  set ANTHROPIC_API_KEY

Requires:
  pip install anthropic pyyaml "httpx[socks]"
"""

import argparse
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import anthropic
import yaml


DEFAULT_MODEL = 'anthropic/claude-sonnet-4-6'
MAX_TOKENS = 4096

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a software architect specializing in code modularization.
    Your task is to analyze a structural spec of a codebase and identify
    opportunities to extract reusable atomic modules.

    The spec was auto-generated from source code via AST analysis.
    It contains: function purpose, parameters, return type, calls graph,
    and which global-state fields each function reads and writes.
    It does NOT contain any redundancy annotations — you must discover those yourself.

    Respond in exactly two stages, separated by the marker ---STRUCTURED---

    STAGE 1 (free text):
    Analyze the spec carefully. Explain:
    - What patterns of code duplication or shared global-state access you observe
    - Which functions appear to share common sub-sequences of global-variable operations
    - What the implied layered structure is (which functions orchestrate others)
    - Any implicit coupling through shared global state

    STAGE 2 (structured YAML, after the marker):
    Output a YAML block with your recommendations.
    Use this exact schema:

    ```yaml
    atomic_module_candidates:
      - name: <proposed_module_name>
        purpose: "<one-line description>"
        extracted_from:
          - <function_name>:<what_part>
        interface:
          inputs:
            - name: <param>
              type: <type>
          outputs:
            - name: <param>
              type: <type>
        replaces_globals:
          reads:  [<hw.field>, ...]
          writes: [<hw.field>, ...]
        rationale: "<why this should be a module>"

    redundancies_found:
      - id: R<n>
        pattern: "<pattern name>"
        occurrences:
          - <function_name> (lines <anchor>)
        description: "<what is duplicated>"

    refactored_call_sites:
      - original_function: <function_name>
        changes:
          - "<replace lines X-Y with: call_to_new_module(args)>"
    ```
""")


def build_user_prompt(spec: dict) -> str:
    spec_yaml = yaml.dump(spec, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
    return (
        f"Here is the lean spec (S1-AST strategy) for `{spec['meta']['source_file']}`:\n\n"
        f"```yaml\n{spec_yaml}```\n\n"
        "Please analyze this spec and provide your modularization recommendations "
        "following the two-stage format described in the system prompt."
    )


# ── API client ────────────────────────────────────────────────────────────────

def make_client() -> anthropic.Anthropic:
    """
    Build Anthropic client, preferring OpenRouter when ANTHROPIC_AUTH_TOKEN is set.
    Falls back to standard ANTHROPIC_API_KEY for direct Anthropic API access.
    """
    auth_token = os.environ.get('ANTHROPIC_AUTH_TOKEN')
    base_url   = os.environ.get('ANTHROPIC_BASE_URL')

    if auth_token and base_url:
        return anthropic.Anthropic(base_url=base_url, auth_token=auth_token)

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    print('Error: set ANTHROPIC_AUTH_TOKEN+ANTHROPIC_BASE_URL (OpenRouter) '
          'or ANTHROPIC_API_KEY (Anthropic)', file=sys.stderr)
    sys.exit(1)


# ── API call ──────────────────────────────────────────────────────────────────

def run_analysis(spec: dict, model: str) -> str:
    client = make_client()
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {'role': 'user', 'content': build_user_prompt(spec)}
        ],
    )
    return message.content[0].text


# ── Parse output ──────────────────────────────────────────────────────────────

def split_response(response: str) -> tuple[str, str]:
    """Split LLM response into (free_text, structured_yaml)."""
    marker = '---STRUCTURED---'
    if marker in response:
        parts = response.split(marker, 1)
        return parts[0].strip(), parts[1].strip()
    return response.strip(), ''


# ── Experiment log ────────────────────────────────────────────────────────────

def save_experiment_log(spec_path: Path, response: str, out_path: Path,
                        model: str) -> None:
    free_text, structured = split_response(response)
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    content = f"""\
# Exp01 — S1 Strategy: AST Lean Spec + LLM Analysis

**Date**: {now}
**Model**: {model}
**Spec file**: {spec_path.name}
**Spec strategy**: S1-AST (auto-generated, no redundancy annotations)

---

## Prompt (system)

```
{SYSTEM_PROMPT}
```

---

## Stage 1 — LLM Free-Text Analysis

{free_text}

---

## Stage 2 — Structured Recommendations

{structured}

---

## Observations (fill in after review)

- [ ] Did LLM find the z_move redundancy (4 occurrences)?
- [ ] Did LLM find the xy_move redundancy (2 occurrences)?
- [ ] Did LLM identify the global-state coupling (adp_draw_done)?
- [ ] Is the proposed interface for each atomic module sensible?
- [ ] Is the structured output parseable for Step 3 (code generation)?

## Conclusion (fill in after review)

(pending)
"""
    out_path.write_text(content, encoding='utf-8')
    print(f'\nExperiment log saved → {out_path}')


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='LLM modularization analysis')
    parser.add_argument('spec', help='Path to spec YAML file')
    parser.add_argument('--save', metavar='FILE',
                        help='Save experiment log to this Markdown file')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f'Model to use (default: {DEFAULT_MODEL})')
    args = parser.parse_args()

    spec_path = Path(args.spec)
    spec = yaml.safe_load(spec_path.read_text(encoding='utf-8'))

    print(f'Sending {spec_path.name} to {args.model}...')
    response = run_analysis(spec, args.model)

    free_text, structured = split_response(response)

    print('\n' + '='*60)
    print('STAGE 1 — FREE TEXT ANALYSIS')
    print('='*60)
    print(free_text)

    print('\n' + '='*60)
    print('STAGE 2 — STRUCTURED RECOMMENDATIONS')
    print('='*60)
    print(structured)

    if args.save:
        save_experiment_log(spec_path, response, Path(args.save), args.model)


if __name__ == '__main__':
    main()
