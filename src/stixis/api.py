"""Public value serialization and raw-CSS canonicalization entry points (`serialize_value`, `canonicalize`)."""

from __future__ import annotations

from typing import Any

from stixis._grammar.matcher import evaluate_canonicalize, evaluate_serialize_value
from stixis._grammar.targets import lookup_value_node, resolve_target_syntax
from stixis.options import SerializationOptions, current_options

__all__ = [
    "canonicalize",
    "serialize_value",
]


def serialize_value(
    target: str,
    value: Any,
    *,
    at_rule: str | None = None,
    options: SerializationOptions | None = None,
) -> str:
    """Validate and serialize a structured Python `value` (or PEP 750 `Template`) for `target`."""
    opts = current_options(options)
    norm_target, norm_at, syntax, node = resolve_target_syntax(
        target, at_rule=at_rule, options=opts
    )
    return evaluate_serialize_value(
        norm_target,
        syntax,
        node,
        value,
        specs_lookup=lookup_value_node,
        at_rule=norm_at,
        options=opts,
    )


def canonicalize(
    target: str,
    css_text: str,
    *,
    at_rule: str | None = None,
    options: SerializationOptions | None = None,
) -> str:
    """Tokenize and validate raw `css_text` against `target`'s `ValueNode` grammar, returning canonical CSSOM text."""
    opts = current_options(options)
    norm_target, norm_at, syntax, node = resolve_target_syntax(
        target, at_rule=at_rule, options=opts
    )
    return evaluate_canonicalize(
        norm_target,
        syntax,
        node,
        css_text,
        specs_lookup=lookup_value_node,
        at_rule=norm_at,
        options=opts,
    )
