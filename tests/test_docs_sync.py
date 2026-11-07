"""Unit tests verifying that README.md and skills/stixis/SKILL.md stay in sync with the public API."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _find_unmarked_internal_stixis_names_in_doc(doc_text: str) -> list[str]:
    """Return Stixis submodule names in backticks that are not in `stixis.__all__` and lack `internal` before them in the same sentence."""
    import importlib
    import pkgutil
    import re

    import stixis

    public_names = set(stixis.__all__)
    internal_names: set[str] = set()
    for mod_info in pkgutil.walk_packages(stixis.__path__, prefix="stixis."):
        mod = importlib.import_module(mod_info.name)
        for name, obj in vars(mod).items():
            if name.startswith("_") or name in public_names:
                continue
            mod_origin = getattr(obj, "__module__", None)
            is_stixis_obj = isinstance(mod_origin, str) and (
                mod_origin == "stixis" or mod_origin.startswith("stixis.")
            )
            is_upper_const = bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", name))
            if is_stixis_obj or is_upper_const:
                internal_names.add(name)

    offending: list[str] = []
    for match in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*)`", doc_text):
        ident = match.group(1)
        if ident not in internal_names:
            continue
        prefix = doc_text[: match.start()]
        same_sentence_before = re.split(r"\n|[.!?]\s+", prefix)[-1]
        if (
            not re.search(r"\binternal\b", same_sentence_before, re.IGNORECASE)
            and ident not in offending
        ):
            offending.append(ident)
    return offending


def test_consumer_skill_and_readme_stay_in_sync_with_public_api() -> None:
    import ast
    import inspect
    import re

    import stixis

    repo_root = Path(__file__).resolve().parent.parent
    skill_path = repo_root / "skills" / "stixis" / "SKILL.md"
    readme_path = repo_root / "README.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    readme_text = readme_path.read_text(encoding="utf-8")

    # 1. Every runtime symbol in `stixis.__all__` (excluding type aliases / constants) must be documented in SKILL.md
    exempt_exports = {
        "DEFAULT_OPTIONS",
        "FontDisplayKeyword",
        "FontFormatKeyword",
        "FontTechKeyword",
        "FontWidthKeyword",
        "Percentage",
        "__version__",
    }
    missing_symbols = [
        sym for sym in stixis.__all__ if sym not in exempt_exports and sym not in skill_text
    ]
    assert not missing_symbols, (
        f"Public exports in stixis.__all__ missing from skills/stixis/SKILL.md: {missing_symbols}"
    )

    # 2. Every public parameter of core entry points and AST constructors must appear in SKILL.md
    core_callables = (
        stixis.dumps,
        stixis.FontFace,
        stixis.StyleRule,
        stixis.AtRule,
        stixis.CommentNode,
        stixis.StyleSheet,
        stixis.serialize_value,
        stixis.canonicalize,
        stixis.format_urange,
        stixis.parse_urange,
        stixis.compact_urange,
        stixis.using_options,
    )
    missing_params: list[str] = []
    for fn in core_callables:
        sig = inspect.signature(fn)
        for param_name in sig.parameters:
            if param_name.startswith("_"):
                continue
            if param_name not in skill_text:
                missing_params.append(f"{fn.__name__}({param_name}=...)")
    assert not missing_params, (
        f"Public parameters missing from skills/stixis/SKILL.md: {missing_params}"
    )

    # 3. Every fenced `python` code block in SKILL.md and README.md must parse and execute cleanly
    code_block_re = re.compile(r"```python\n(.*?)```", re.DOTALL)
    for doc_name, doc_content in (("SKILL.md", skill_text), ("README.md", readme_text)):
        blocks = code_block_re.findall(doc_content)
        assert blocks, f"Expected at least one ```python block in {doc_name}"
        for idx, block in enumerate(blocks):
            tree = ast.parse(block, filename=f"{doc_name}#block-{idx}")
            assert tree.body
            res = subprocess.run(
                [sys.executable, "-c", block],
                capture_output=True,
                text=True,
                check=False,
            )
            assert res.returncode == 0, f"{doc_name}#block-{idx} failed: {res.stderr}"

    # 4. README.md and skills/stixis/SKILL.md must not present internal Stixis names as public API
    unmarked_internal: list[str] = []
    for doc_rel, doc_content in (
        ("README.md", readme_text),
        ("skills/stixis/SKILL.md", skill_text),
    ):
        for name in _find_unmarked_internal_stixis_names_in_doc(doc_content):
            unmarked_internal.append(f"{doc_rel}: {name}")
    assert not unmarked_internal, (
        f"Internal Stixis names presented without 'internal' in docs: {unmarked_internal}"
    )


def test_unmarked_internal_stixis_names_detected_in_pre_fix_readme_sample() -> None:
    pre_fix_readme_sample = """\
* **Compiles W3C Value Definition Syntax.** `compile_value_syntax` turns `@webref/css` grammars into a `ValueNode` tree. `serialize_value` formats structured Python values against that tree, and `canonicalize` validates relative color channel keywords (`RELATIVE_COLOR_CHANNEL_KEYWORDS` generated from `@webref/css`). Both the library and the CLI resolve targets through `resolve_target_syntax`: standard CSS properties (`PROPERTY_SYNTAXES`), `@webref/css` value types (`VALUE_SYNTAXES`), and evaluator-supported built-in primitive types (`SUPPORTED_BUILTIN_TYPES`). Standard and custom properties accept `CSS_WIDE_KEYWORDS`, while `RESERVED_CUSTOM_IDENT_KEYWORDS` remains excluded from `<custom-ident>`.
* **Gates experimental CSS properties by engine support (`min_engines`).** According to `@mdn/browser-compat-data` (`PROPERTY_ENGINE_COUNTS`), plus `ALWAYS_ALLOWED_PROPERTIES` in `codegen/spec_patches.toml`.
* **Escapes PEP 750 `Template` (`t"..."`) slots by context.** `render_css_template` checks the CSS tokens before each `Interpolation` slot, and validates the rendered tokens against the target `ValueNode` grammar.
* Standard-library types such as `Template`, `Interpolation`, `TypedDict`, `Mapping`, and `Sequence` are allowed without marking.
"""
    expected_offending = [
        "compile_value_syntax",
        "ValueNode",
        "RELATIVE_COLOR_CHANNEL_KEYWORDS",
        "resolve_target_syntax",
        "PROPERTY_SYNTAXES",
        "VALUE_SYNTAXES",
        "SUPPORTED_BUILTIN_TYPES",
        "CSS_WIDE_KEYWORDS",
        "RESERVED_CUSTOM_IDENT_KEYWORDS",
        "PROPERTY_ENGINE_COUNTS",
        "ALWAYS_ALLOWED_PROPERTIES",
        "render_css_template",
    ]
    assert _find_unmarked_internal_stixis_names_in_doc(pre_fix_readme_sample) == expected_offending
