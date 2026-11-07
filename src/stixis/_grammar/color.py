"""Relative color syntax (`from <color>`) channel keyword helpers (`css-color-5` §3)."""

from __future__ import annotations

from stixis._generated.specs import RELATIVE_COLOR_CHANNEL_KEYWORDS
from stixis._grammar.vds import (
    FunctionNode,
    JuxtaposeNode,
    KeywordNode,
    MultiplierNode,
    RefNode,
    ValueNode,
)
from stixis._syntax.tokenizer import CssToken

__all__ = [
    "RELATIVE_COLOR_CHANNEL_KEYWORDS",
    "extract_relative_color_rest_node",
    "narrow_predefined_color_param_channels",
]


def narrow_predefined_color_param_channels(
    ref_name: str,
    current_channels: frozenset[str],
) -> frozenset[str] | None:
    """Return narrowed channel keywords for `<predefined-rgb-params>` or `<xyz-params>`, or `None` if not applicable."""
    if not current_channels or ref_name not in ("<predefined-rgb-params>", "<xyz-params>"):
        return None
    branch_channels = (
        RELATIVE_COLOR_CHANNEL_KEYWORDS.get("rgb", frozenset())
        if ref_name == "<predefined-rgb-params>"
        else frozenset({"x", "y", "z", "alpha"})
    )
    return current_channels & branch_channels


def extract_relative_color_rest_node(
    fn_name: str,
    inner_tokens: list[CssToken],
    node: FunctionNode,
) -> tuple[ValueNode, ValueNode, frozenset[str]] | None:
    """If `node` is a relative color function starting with `from <color>`, return `(from_node, rest_node, channel_keywords)`."""
    if (
        fn_name not in RELATIVE_COLOR_CHANNEL_KEYWORDS
        or not inner_tokens
        or inner_tokens[0].kind != "ident"
        or inner_tokens[0].value.lower() != "from"
        or not isinstance(node.arg, JuxtaposeNode)
        or len(node.arg.items) < 2
    ):
        return None

    rest_items: tuple[ValueNode, ...] | None = None
    first_item = node.arg.items[0]
    if (
        isinstance(first_item, KeywordNode)
        and first_item.name == "from"
        and isinstance(node.arg.items[1], RefNode)
        and node.arg.items[1].name == "<color>"
    ):
        rest_items = node.arg.items[2:]
    elif (
        isinstance(first_item, MultiplierNode)
        and isinstance(first_item.child, JuxtaposeNode)
        and len(first_item.child.items) == 2
        and isinstance(first_item.child.items[0], KeywordNode)
        and first_item.child.items[0].name == "from"
        and isinstance(first_item.child.items[1], RefNode)
        and first_item.child.items[1].name == "<color>"
    ):
        rest_items = node.arg.items[1:]

    if not rest_items:
        return None

    from_node = JuxtaposeNode((KeywordNode("from"), RefNode("<color>", is_property=False)))
    rest_node = rest_items[0] if len(rest_items) == 1 else JuxtaposeNode(rest_items)
    return from_node, rest_node, RELATIVE_COLOR_CHANNEL_KEYWORDS[fn_name]
