"""Property-based Hypothesis tests derived from `ValueNode` grammar trees and range bounds."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from stixis import (
    Deg,
    Pct,
    SerializationOptions,
    StixisValidationError,
    canonicalize,
    compact_urange,
    format_urange,
    parse_urange_codepoints,
    serialize_value,
)
from stixis.nodes import lookup_value_node, resolve_target_syntax
from stixis.syntax import (
    AllOfNode,
    AnyOrderNode,
    DelimNode,
    ExclusiveOrNode,
    FunctionNode,
    JuxtaposeNode,
    KeywordNode,
    MultiplierNode,
    NonEmptyGroupNode,
    PrimitiveNode,
    RefNode,
    ValueNode,
)

CSSOM_OPTS = SerializationOptions.cssom()


def strategy_from_value_node(
    node: ValueNode,
    *,
    depth: int = 0,
) -> st.SearchStrategy[str]:
    """Synthesize a Hypothesis `SearchStrategy[str]` from a compiled `ValueNode` combinator tree."""
    if depth > 4:
        return st.nothing()

    if isinstance(node, KeywordNode):
        return st.just(node.name)

    if isinstance(node, DelimNode):
        return st.just(node.delim)

    if isinstance(node, PrimitiveNode):
        kind = node.kind
        lo = node.min_val
        hi = node.max_val
        eff_lo = int(max(lo, -1000.0)) if lo is not None else -100
        eff_hi = int(min(hi, 1000.0)) if hi is not None else 1000
        if eff_lo > eff_hi:
            eff_lo, eff_hi = 0, 100

        if kind in ("<integer>", "<signless-integer>", "<signed-integer>"):
            return st.integers(min_value=eff_lo, max_value=eff_hi).map(str)
        if kind == "<number>":
            return st.integers(min_value=max(eff_lo, 1), max_value=max(eff_hi, 1)).map(str)
        if kind == "<zero>":
            return st.just("0")
        if kind in ("<percentage>", "<length-percentage>", "<angle-percentage>"):
            return st.integers(min_value=max(eff_lo, 0), max_value=max(eff_hi, 100)).map(
                lambda v: f"{v}%"
            )
        if kind == "<length>":
            return st.tuples(
                st.integers(min_value=max(eff_lo, 0), max_value=max(eff_hi, 100)),
                st.sampled_from(["px", "em", "rem", "vw", "cqw"]),
            ).map(lambda t: f"{t[0]}{t[1]}")
        if kind == "<angle>":
            return st.integers(min_value=max(eff_lo, -90), max_value=min(eff_hi, 90)).map(
                lambda v: f"{v}deg"
            )
        if kind in ("<time>", "<time-percentage>"):
            return st.integers(min_value=max(eff_lo, 0), max_value=10).map(lambda v: f"{v}s")
        if kind in ("<frequency>", "<frequency-percentage>"):
            return st.integers(min_value=max(eff_lo, 1), max_value=1000).map(lambda v: f"{v}hz")
        if kind == "<resolution>":
            return st.integers(min_value=1, max_value=300).map(lambda v: f"{v}dpi")
        if kind == "<flex>":
            return st.integers(min_value=max(eff_lo, 0), max_value=10).map(lambda v: f"{v}fr")
        if kind == "<string>":
            return st.sampled_from(['"alpha"', '"beta"', '"gamma"'])
        if kind in ("<custom-ident>", "<ident>"):
            return st.sampled_from(["alpha", "beta", "item-1", "slot-a"])
        if kind == "<dashed-ident>":
            return st.sampled_from(["--theme-a", "--palette-1", "--custom"])
        if kind in ("<url>", "<url-token>"):
            return st.sampled_from(['url("https://example.com/a.woff2")', 'url("font.woff2")'])
        if kind in ("<urange>", "<unicode-range-token>"):
            return st.sampled_from(["U+0041", "U+3040-309F", "U+00??"])
        if kind == "<opentype-tag>":
            return st.sampled_from(['"liga"', '"dlig"', '"smcp"', '"tnum"'])
        if kind == "<feature-tag-value>":
            return st.sampled_from(['"liga"', '"dlig" 0', '"swsh" 2'])
        if kind in ("<family-name>", "<font-family-name>"):
            return st.sampled_from(['"Noto Sans JP"', '"Inter"', '"34J"'])
        if kind in ("<generic-family>", "<generic-font-family>"):
            return st.sampled_from(
                ["serif", "sans-serif", "monospace", "system-ui", "generic(kai)"]
            )
        if kind == "<hex-color>":
            return st.sampled_from(["#ff00aa", "#123456"])
        return st.nothing()

    if isinstance(node, RefNode):
        ref = lookup_value_node(node.name, node.is_property)
        if ref is None:
            return st.nothing()
        if (node.min_val is not None or node.max_val is not None) and isinstance(
            ref, PrimitiveNode
        ):
            ref = PrimitiveNode(
                ref.kind,
                min_val=node.min_val if node.min_val is not None else ref.min_val,
                max_val=node.max_val if node.max_val is not None else ref.max_val,
                min_unit=ref.min_unit,
                max_unit=ref.max_unit,
            )
        return strategy_from_value_node(ref, depth=depth + 1)

    if isinstance(node, FunctionNode):
        if node.arg is None:
            return st.just(f"{node.name}()")
        inner_st = strategy_from_value_node(node.arg, depth=depth + 1)
        return inner_st.map(lambda s: f"{node.name}({s})")

    if isinstance(node, NonEmptyGroupNode):
        return strategy_from_value_node(node.child, depth=depth + 1)

    if isinstance(node, MultiplierNode):
        child_st = strategy_from_value_node(node.child, depth=depth + 1)
        if node.min_count == 0:
            return st.one_of(st.just(""), child_st)
        hi = min(node.max_count or 2, 2)
        lo = max(node.min_count, 1)
        lo = min(lo, hi)
        sep = ", " if node.comma_separated else " "
        return st.lists(child_st, min_size=lo, max_size=hi).map(
            lambda items: sep.join(i for i in items if i)
        )

    if isinstance(node, JuxtaposeNode):
        sub_sts = [strategy_from_value_node(c, depth=depth + 1) for c in node.items]
        return st.tuples(*sub_sts).map(lambda parts: " ".join(p for p in parts if p))

    if isinstance(node, AllOfNode):
        sub_sts = [strategy_from_value_node(c, depth=depth + 1) for c in node.items]
        return st.tuples(*sub_sts).map(lambda parts: " ".join(p for p in parts if p))

    if isinstance(node, AnyOrderNode):
        sub_sts = [strategy_from_value_node(c, depth=depth + 1) for c in node.items]
        return st.one_of(*sub_sts)

    if isinstance(node, ExclusiveOrNode):
        sub_sts = [strategy_from_value_node(c, depth=depth + 1) for c in node.items]
        return st.one_of(*sub_sts)

    return st.nothing()


_GRAMMAR_ROUNDTRIP_PROPERTIES: tuple[str, ...] = (
    "font-kerning",
    "font-optical-sizing",
    "font-palette",
    "font-synthesis-weight",
    "font-synthesis-style",
    "font-synthesis-small-caps",
    "font-synthesis-position",
    "font-variant-caps",
    "font-variant-emoji",
    "font-variant-position",
    "font-display",
    "display",
    "position",
    "visibility",
    "overflow-x",
    "text-transform",
    "white-space-collapse",
    "box-sizing",
    "flex-direction",
    "flex-wrap",
)


@pytest.mark.parametrize("prop_name", _GRAMMAR_ROUNDTRIP_PROPERTIES)
def test_synthesized_grammar_strategy_roundtrip(prop_name: str) -> None:
    """Every `ValueNode` in `_GRAMMAR_ROUNDTRIP_PROPERTIES` synthesizes valid CSS that round-trips."""
    at_rule = "@font-face" if prop_name == "font-display" else None
    _, _, _, node = resolve_target_syntax(prop_name, at_rule=at_rule)
    strat = strategy_from_value_node(node)

    @settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
    @given(val=strat)
    def _check(val: str) -> None:
        first = canonicalize(prop_name, val, at_rule=at_rule, options=CSSOM_OPTS)
        second = canonicalize(prop_name, first, at_rule=at_rule, options=CSSOM_OPTS)
        assert first == second
        assert serialize_value(prop_name, first, at_rule=at_rule, options=CSSOM_OPTS) == first

    _check()


@settings(max_examples=40)
@given(
    lo=st.integers(min_value=1, max_value=1000),
    hi=st.integers(min_value=1, max_value=1000),
)
def test_hypothesis_font_weight_range_roundtrip(lo: int, hi: int) -> None:
    s_single = serialize_value("font-weight", lo, at_rule="@font-face")
    assert canonicalize("font-weight", s_single, at_rule="@font-face") == str(lo)

    s_pair = serialize_value("font-weight", (lo, hi), at_rule="@font-face")
    assert canonicalize("font-weight", s_pair, at_rule="@font-face") == f"{lo} {hi}"


@settings(max_examples=40)
@given(
    bad_weight=st.one_of(
        st.integers(max_value=0),
        st.integers(min_value=1001, max_value=100000),
    )
)
def test_hypothesis_font_weight_out_of_bounds_rejected(bad_weight: int) -> None:
    with pytest.raises(StixisValidationError):
        serialize_value("font-weight", bad_weight, at_rule="@font-face")
    with pytest.raises(StixisValidationError):
        canonicalize("font-weight", str(bad_weight))


@settings(max_examples=40)
@given(
    lo=st.integers(min_value=-90, max_value=90),
    hi=st.integers(min_value=-90, max_value=90),
)
def test_hypothesis_font_style_oblique_range_roundtrip(lo: int, hi: int) -> None:
    s = serialize_value("font-style", ("oblique", Deg(lo), Deg(hi)), at_rule="@font-face")
    assert canonicalize("font-style", s, at_rule="@font-face") == f"oblique {lo}deg {hi}deg"


@settings(max_examples=40)
@given(
    bad_deg=st.one_of(
        st.integers(min_value=-1000, max_value=-91),
        st.integers(min_value=91, max_value=1000),
    )
)
def test_hypothesis_font_style_oblique_out_of_bounds_rejected(bad_deg: int) -> None:
    with pytest.raises(StixisValidationError):
        serialize_value("font-style", ("oblique", Deg(bad_deg)), at_rule="@font-face")
    with pytest.raises(StixisValidationError):
        canonicalize("font-style", f"oblique {bad_deg}deg", at_rule="@font-face")


@settings(max_examples=40)
@given(
    neg_pct=st.floats(min_value=-10000.0, max_value=-0.01, allow_nan=False, allow_infinity=False)
)
def test_hypothesis_non_negative_percentage_bounds_rejected(neg_pct: float) -> None:
    for desc in (
        "size-adjust",
        "ascent-override",
        "descent-override",
        "line-gap-override",
        "font-width",
    ):
        with pytest.raises(StixisValidationError):
            serialize_value(desc, Pct(neg_pct), at_rule="@font-face")


@settings(max_examples=40)
@given(
    cps=st.sets(st.integers(min_value=0, max_value=0x10FFFF), min_size=1, max_size=25),
)
def test_hypothesis_urange_codepoints_roundtrip(cps: set[int]) -> None:
    formatted = format_urange(cps)
    recovered = parse_urange_codepoints(formatted)
    assert recovered == cps
    assert canonicalize("unicode-range", formatted, at_rule="@font-face") == formatted


@settings(max_examples=40)
@given(
    target_cps=st.sets(st.integers(min_value=0x3000, max_value=0x31FF), min_size=1, max_size=30),
    forbidden_cps=st.sets(st.integers(min_value=0x3000, max_value=0x31FF), min_size=0, max_size=30),
    max_gap=st.integers(min_value=0, max_value=0x80),
)
def test_hypothesis_compact_urange_cascade_shadowing_invariants(
    target_cps: set[int],
    forbidden_cps: set[int],
    max_gap: int,
) -> None:
    """`compact_urange` must cover 100% of `target_cps` and 0% of `forbidden_cps - target_cps`."""
    disjoint_forbidden = sorted(forbidden_cps - target_cps)
    sorted_target = sorted(target_cps)
    compacted = compact_urange(sorted_target, disjoint_forbidden, max_gap=max_gap)
    covered = parse_urange_codepoints(compacted)
    assert target_cps.issubset(covered)
    assert covered.isdisjoint(disjoint_forbidden)
    assert len(compacted) <= len(format_urange(sorted_target))


_TOKEN_STREAM_ATOMS: st.SearchStrategy[str] = st.sampled_from(
    [
        "alpha",
        r"foo\;bar",
        r"\31 a",
        r"-a\}b",
        "--x",
        "#abc",
        r"#1\;color:red",
        r"#\}",
        "10px",
        r"1px\;color:red",
        r"2em\}",
        "50%",
        "42",
        '"a;b}c\\"d"',
        "-",
        "+",
        "*",
        "/",
        "#",
        ",",
        ":",
        "var(--x)",
        "calc(1px - var(--x))",
        "min(10px, 5%)",
    ]
)


def _semantic_tokens(
    css_text: str,
) -> list[tuple[str, str, float | int | None, str | None, str | None]]:
    from stixis.syntax import tokenize_css

    return [
        (
            t.kind,
            t.value if t.kind not in ("number", "percentage", "dimension") else "",
            t.num,
            t.unit,
            t.hash_type,
        )
        for t in tokenize_css(css_text)
        if t.kind != "whitespace"
    ]


@settings(max_examples=60)
@given(parts=st.lists(_TOKEN_STREAM_ATOMS, min_size=1, max_size=8))
def test_hypothesis_canonicalize_token_roundtrip(parts: list[str]) -> None:
    """`canonicalize` output must re-tokenize into the same semantic token stream."""
    raw = " ".join(parts)
    canon = canonicalize("--x", raw)
    assert _semantic_tokens(canon) == _semantic_tokens(raw)
    assert canonicalize("--x", canon) == canon
