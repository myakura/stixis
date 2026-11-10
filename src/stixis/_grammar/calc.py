"""CSS math-function (`<calc-sum>`, `calc(...)`, `min(...)`, `max(...)`, `clamp(...)`, etc.) parsing and simplification (`css-values-4` §10)."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from stixis._generated.units import LOWER_TO_CANONICAL_UNIT
from stixis._grammar.vds import ValueNode
from stixis._syntax.serialize import (
    format_token_sequence,
    serialize_ident,
    serialize_number,
)
from stixis._syntax.tokenizer import CssToken
from stixis.errors import StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

# CSS-wide keywords + CSS Variables / substitution functions that are valid on any property
GLOBAL_SUBSTITUTION_FUNCTIONS: frozenset[str] = frozenset(
    {"var", "env", "attr", "first-valid", "inherit", "if"}
)
MATH_FUNCTIONS: frozenset[str] = frozenset(
    {
        "calc",
        "min",
        "max",
        "clamp",
        "round",
        "mod",
        "rem",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "pow",
        "sqrt",
        "hypot",
        "log",
        "exp",
        "abs",
        "sign",
        "calc-size",
        "sibling-index",
        "sibling-count",
    }
)
CALC_VALUE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "progress",
        "calc-mix",
        "calc-interpolate",
        "random",
    }
)
CALC_KEYWORDS: frozenset[str] = frozenset({"e", "pi", "infinity", "-infinity", "nan"})
ROUNDING_STRATEGY_KEYWORDS: frozenset[str] = frozenset({"nearest", "up", "down", "to-zero"})
ALL_LOWER_TO_CANONICAL_UNIT: dict[str, str] = {
    u_low: u_canon
    for unit_map in LOWER_TO_CANONICAL_UNIT.values()
    for u_low, u_canon in unit_map.items()
}


def _consume_balanced_group(
    tokens: Sequence[CssToken],
    pos: int,
    closing_kind: str,
) -> tuple[list[CssToken], int] | None:
    """Consume tokens starting at `pos` until the matching `closing_kind` at depth 0."""
    n = len(tokens)
    stack: list[str] = [closing_kind]
    matching = {"(": ")", "[": "]", "{": "}"}
    closing = {")", "]", "}"}
    start = pos

    while pos < n and stack:
        tok = tokens[pos]
        if tok.kind == "function":
            stack.append(")")
        elif tok.kind in matching:
            stack.append(matching[tok.kind])
        elif tok.kind in closing:
            if stack[-1] != tok.kind:
                return None
            stack.pop()
            if not stack:
                return list(tokens[start:pos]), pos + 1
        pos += 1
    return None


def _split_top_level_comma_args(tokens: Sequence[CssToken]) -> list[list[CssToken]] | None:
    """Split a non-whitespace token slice at top-level commas (`depth == 0`), rejecting empty segments."""
    args: list[list[CssToken]] = []
    current: list[CssToken] = []
    depth = 0
    for tok in tokens:
        if tok.kind in ("function", "(", "[", "{"):
            depth += 1
            current.append(tok)
        elif tok.kind in (")", "]", "}"):
            if depth == 0:
                return None
            depth -= 1
            current.append(tok)
        elif tok.kind == "comma" and depth == 0:
            if not current:
                return None
            args.append(current)
            current = []
        else:
            current.append(tok)
    if depth != 0 or not current:
        return None
    args.append(current)
    return args


@dataclass(frozen=True, slots=True)
class _CalcNumeric:
    value: float
    unit: str = ""  # "" for number, "%" for percentage, canonical unit for dimension


@dataclass(frozen=True, slots=True)
class _CalcAtom:
    text: str


@dataclass(frozen=True, slots=True)
class _CalcInvert:
    child: _CalcNode


@dataclass(frozen=True, slots=True)
class _CalcProduct:
    children: tuple[_CalcNode, ...]


@dataclass(frozen=True, slots=True)
class _CalcSum:
    children: tuple[_CalcNode, ...]


type _CalcNode = _CalcNumeric | _CalcAtom | _CalcInvert | _CalcProduct | _CalcSum


def _format_calc_numeric(node: _CalcNumeric) -> str:
    val = round(node.value) if abs(node.value - round(node.value)) < 1e-12 else node.value
    if val == 0:
        val = 0
    return f"{serialize_number(val)}{node.unit}"


def _negate_calc_node(node: _CalcNode) -> _CalcNode:
    if isinstance(node, _CalcNumeric):
        return _CalcNumeric(-node.value, node.unit)
    if isinstance(node, _CalcProduct) and node.children:
        first = node.children[0]
        if isinstance(first, _CalcNumeric):
            return _CalcProduct((_CalcNumeric(-first.value, first.unit), *node.children[1:]))
        return _CalcProduct((_CalcNumeric(-1.0, ""), *node.children))
    return _CalcProduct((_CalcNumeric(-1.0, ""), node))


def _is_lossless_css_number(val: float) -> bool:
    if not math.isfinite(val):
        return False
    if val == 0.0:
        return True
    try:
        ser = serialize_number(val)
    except StixisValidationError:
        return False
    parsed = float(ser)
    if parsed == 0.0:
        return False
    return abs(parsed - val) <= 1e-12 * max(1.0, abs(val))


def _simplify_calc_product(factors: Sequence[_CalcNode]) -> _CalcNode:
    # Step 1-2: flatten nested _CalcProduct nodes
    flat: list[_CalcNode] = []
    for f in factors:
        if isinstance(f, _CalcProduct):
            flat.extend(f.children)
        else:
            flat.append(f)

    # Fold pure numbers and Invert(number) when finite, non-zero, and lossless in 6-decimal CSSOM serialization.
    num_factors: list[_CalcNumeric] = []
    inv_num_factors: list[_CalcInvert] = []
    remaining: list[_CalcNode] = []
    for f in flat:
        if isinstance(f, _CalcNumeric) and f.unit == "":
            num_factors.append(f)
            continue
        if (
            isinstance(f, _CalcInvert)
            and isinstance(f.child, _CalcNumeric)
            and f.child.unit == ""
            and f.child.value != 0
        ):
            inv_num_factors.append(f)
            continue
        remaining.append(f)

    num_acc = 1.0
    num_ok = True
    for nf in num_factors:
        cand = num_acc * nf.value
        if not math.isfinite(cand) or (cand == 0.0 and nf.value != 0.0 and num_acc != 0.0):
            num_ok = False
            break
        num_acc = cand

    combined_acc = num_acc
    combined_ok = num_ok
    if combined_ok:
        for inv_f in inv_num_factors:
            assert isinstance(inv_f.child, _CalcNumeric)
            cand = combined_acc * (1.0 / inv_f.child.value)
            if not math.isfinite(cand) or (cand == 0.0 and combined_acc != 0.0):
                combined_ok = False
                break
            combined_acc = cand

    if not remaining and len(num_factors) + len(inv_num_factors) == 1:
        if num_factors:
            return num_factors[0]
        return inv_num_factors[0]

    # Single percentage or dimension with scalar factors/divisors: try multiplying directly into the dimension
    if len(remaining) == 1 and isinstance(remaining[0], _CalcNumeric):
        dim_node = remaining[0]
        if combined_ok:
            cand = combined_acc * dim_node.value
            if _is_lossless_css_number(cand) and (
                cand != 0.0 or dim_node.value == 0.0 or combined_acc == 0.0
            ):
                return _CalcNumeric(cand, dim_node.unit)
        if num_factors and num_ok and not inv_num_factors:
            cand = num_acc * dim_node.value
            if _is_lossless_css_number(cand) and (
                cand != 0.0 or dim_node.value == 0.0 or num_acc == 0.0
            ):
                return _CalcNumeric(cand, dim_node.unit)

    has_num = False
    if inv_num_factors:
        if combined_ok and _is_lossless_css_number(combined_acc):
            num_acc = combined_acc
            has_num = True
        else:
            if num_factors:
                if len(num_factors) == 1 or (num_ok and _is_lossless_css_number(num_acc)):
                    has_num = True
                else:
                    remaining = [*num_factors, *remaining]
            remaining.extend(inv_num_factors)
    elif num_factors:
        if len(num_factors) == 1 or (num_ok and _is_lossless_css_number(num_acc)):
            has_num = True
        else:
            remaining = [*num_factors, *remaining]

    if not remaining:
        return _CalcNumeric(num_acc, "")

    # If remaining contains a single percentage or dimension and has_num is True,
    # multiply num_acc into that percentage/dimension and move it to the front!
    dim_indices = [
        idx
        for idx, item in enumerate(remaining)
        if isinstance(item, _CalcNumeric) and item.unit != ""
    ]
    if len(dim_indices) == 1:
        d_idx = dim_indices[0]
        d_node = remaining[d_idx]
        assert isinstance(d_node, _CalcNumeric)
        if has_num:
            scaled_val = d_node.value * num_acc
            if _is_lossless_css_number(scaled_val) and (
                scaled_val != 0.0 or d_node.value == 0.0 or num_acc == 0.0
            ):
                leading_dim = _CalcNumeric(scaled_val, d_node.unit)
                others = [item for idx, item in enumerate(remaining) if idx != d_idx]
                if not others:
                    return leading_dim
                return _CalcProduct((leading_dim, *others))
        else:
            others = [item for idx, item in enumerate(remaining) if idx != d_idx]
            if not others:
                return d_node
            return _CalcProduct((d_node, *others))

    if has_num:
        if abs(num_acc - 1.0) < 1e-12 and len(remaining) == 1:
            return remaining[0]
        return _CalcProduct((_CalcNumeric(num_acc, ""), *remaining))

    if len(remaining) == 1:
        return remaining[0]
    return _CalcProduct(tuple(remaining))


def _simplify_calc_sum(terms: Sequence[_CalcNode]) -> _CalcNode:
    # Flatten nested _CalcSum nodes
    flat: list[_CalcNode] = []
    for t in terms:
        if isinstance(t, _CalcSum):
            flat.extend(t.children)
        else:
            flat.append(t)

    # Combine identical numeric units ("" for number, "%" for percentage, canonical unit for dimension)
    # when the combined sum can be serialized without loss of precision under 6-decimal CSSOM rules.
    unit_terms: dict[str, list[_CalcNumeric]] = {}
    unit_order: list[str] = []
    other_terms: list[_CalcNode] = []

    for t in flat:
        if isinstance(t, _CalcNumeric):
            if t.unit not in unit_terms:
                unit_terms[t.unit] = []
                unit_order.append(t.unit)
            unit_terms[t.unit].append(t)
        else:
            other_terms.append(t)

    # Sort numeric terms per CSS Values 4 §10.10: number ("") first, percentage ("%") second,
    # dimensions ordered alphabetically by canonical ASCII-lowercase unit
    ordered_units: list[str] = []
    if "" in unit_terms:
        ordered_units.append("")
    if "%" in unit_terms:
        ordered_units.append("%")
    dim_units = sorted(
        (u for u in unit_order if u not in ("", "%")),
        key=lambda u: (u.lower(), u),
    )
    ordered_units.extend(dim_units)

    sorted_numeric: list[_CalcNumeric] = []
    for u in ordered_units:
        t_list = unit_terms[u]
        if len(t_list) == 1:
            sorted_numeric.append(t_list[0])
            continue
        acc = 0.0
        ok = True
        for item in t_list:
            cand = acc + item.value
            if not math.isfinite(cand):
                ok = False
                break
            acc = cand
        if ok and _is_lossless_css_number(acc):
            sorted_numeric.append(_CalcNumeric(acc, u))
        else:
            sorted_numeric.extend(t_list)

    all_terms: list[_CalcNode] = [*sorted_numeric, *other_terms]
    if len(all_terms) == 1:
        return all_terms[0]
    return _CalcSum(tuple(all_terms))


def _serialize_calc_node(node: _CalcNode, *, is_root: bool = False) -> str:
    if isinstance(node, _CalcNumeric):
        return _format_calc_numeric(node)
    if isinstance(node, _CalcAtom):
        return node.text
    if isinstance(node, _CalcProduct):
        parts: list[str] = []
        for idx, child in enumerate(node.children):
            if idx > 0 and isinstance(child, _CalcInvert):
                inv_child = child.child
                s_inv = _serialize_calc_node(inv_child, is_root=False)
                parts.append(f" / {s_inv}")
            else:
                s_child = _serialize_calc_node(child, is_root=False)
                if idx == 0:
                    parts.append(s_child)
                else:
                    parts.append(f" * {s_child}")
        inner = "".join(parts)
        return inner if is_root else f"({inner})"
    if isinstance(node, _CalcSum):
        parts_sum: list[str] = []
        for idx, child in enumerate(node.children):
            if idx == 0:
                parts_sum.append(_serialize_calc_node(child, is_root=False))
            else:
                if isinstance(child, _CalcNumeric) and child.value < 0:
                    pos_node = _CalcNumeric(-child.value, child.unit)
                    parts_sum.append(f" - {_format_calc_numeric(pos_node)}")
                elif (
                    isinstance(child, _CalcProduct)
                    and child.children
                    and isinstance(child.children[0], _CalcNumeric)
                    and child.children[0].value < 0
                ):
                    first_c = child.children[0]
                    pos_first = _CalcNumeric(-first_c.value, first_c.unit)
                    if (
                        pos_first.unit == ""
                        and abs(pos_first.value - 1.0) < 1e-12
                        and len(child.children) == 2
                    ):
                        pos_prod: _CalcNode = child.children[1]
                    else:
                        pos_prod = _CalcProduct((pos_first, *child.children[1:]))
                    parts_sum.append(f" - {_serialize_calc_node(pos_prod, is_root=False)}")
                else:
                    parts_sum.append(f" + {_serialize_calc_node(child, is_root=False)}")
        inner_sum = "".join(parts_sum)
        return inner_sum if is_root else f"({inner_sum})"
    if isinstance(node, _CalcInvert):
        s_inv = _serialize_calc_node(node.child, is_root=False)
        return f"1 / {s_inv}" if is_root else f"(1 / {s_inv})"
    return ""


def _parse_calc_value_node(
    tokens: Sequence[CssToken],
    pos: int,
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> tuple[_CalcNode, int] | None:
    """Parse `<calc-value> = <number> | <dimension> | <percentage> | <calc-keyword> | ( <calc-sum> )`."""
    if pos >= len(tokens):
        return None
    tok = tokens[pos]
    if tok.kind == "number":
        if tok.num is None or not math.isfinite(tok.num):
            return None
        return _CalcNumeric(float(tok.num), ""), pos + 1
    if tok.kind == "percentage":
        if tok.num is None or not math.isfinite(tok.num):
            return None
        return _CalcNumeric(float(tok.num), "%"), pos + 1
    if tok.kind == "dimension":
        if tok.num is None or not math.isfinite(tok.num):
            return None
        canon_u = ALL_LOWER_TO_CANONICAL_UNIT.get(tok.unit.lower())
        if canon_u is None:
            return None
        return _CalcNumeric(float(tok.num), canon_u), pos + 1
    if tok.kind == "ident":
        low = tok.value.lower()
        if low in CALC_KEYWORDS or low in extra_keywords:
            return _CalcAtom(low), pos + 1
        return None
    if tok.kind == "(":
        grouped = _consume_balanced_group(tokens, pos + 1, ")")
        if grouped is None:
            return None
        inner_toks, next_pos = grouped
        inner_node = _parse_full_calc_sum_node(
            inner_toks,
            extra_keywords=extra_keywords,
            extra_functions=extra_functions,
            specs_lookup=specs_lookup,
            options=options,
        )
        if inner_node is None:
            return None
        return inner_node, next_pos
    if tok.kind == "function":
        fn_low = tok.value.lower()
        if fn_low == "calc":
            grouped = _consume_balanced_group(tokens, pos + 1, ")")
            if grouped is None:
                return None
            inner_toks, next_pos = grouped
            non_ws = [t for t in inner_toks if t.kind != "whitespace"]
            if not non_ws or any(t.kind == "semicolon" for t in non_ws):
                return None
            args = _split_top_level_comma_args(non_ws)
            if args is None or len(args) != 1:
                return None
            inner_node = _parse_full_calc_sum_node(
                args[0],
                extra_keywords=extra_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if inner_node is None:
                return None
            return inner_node, next_pos
        if fn_low in MATH_FUNCTIONS:
            math_res = _is_math_function_token(
                tokens,
                pos,
                extra_keywords=extra_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if math_res is None:
                return None
            return _CalcAtom(math_res[0]), math_res[1]
        if (
            fn_low in CALC_VALUE_FUNCTIONS or fn_low in extra_functions
        ) and specs_lookup is not None:
            grouped = _consume_balanced_group(tokens, pos + 1, ")")
            if grouped is None:
                return None
            inner_toks, next_pos = grouped
            if any(t.kind == "semicolon" for t in inner_toks):
                return None
            fn_node = specs_lookup(f"<{fn_low}()>", False)
            if fn_node is None:
                return None
            from stixis._grammar.matcher import _TokenMatcher

            slice_toks = [t for t in tokens[pos:next_pos] if t.kind != "whitespace"]
            sub = _TokenMatcher(
                slice_toks,
                specs_lookup,
                options,
                calc_context_keywords=extra_keywords,
                calc_context_functions=extra_functions,
            )
            for end_p, rendered in sub.match_node(fn_node, 0):
                if end_p == len(slice_toks):
                    return _CalcAtom(rendered), next_pos
            return None
        if fn_low == "if":
            grouped = _consume_balanced_group(tokens, pos + 1, ")")
            if grouped is None:
                return None
            inner_toks, next_pos = grouped
            rendered_if = _validate_and_format_if_function(inner_toks, options=options)
            if rendered_if is None:
                return None
            return _CalcAtom(rendered_if), next_pos
        if fn_low in GLOBAL_SUBSTITUTION_FUNCTIONS:
            grouped = _consume_balanced_group(tokens, pos + 1, ")")
            if grouped is None:
                return None
            inner_toks, next_pos = grouped
            if not inner_toks or any(t.kind == "semicolon" for t in inner_toks):
                return None
            return _CalcAtom(
                f"{fn_low}({format_token_sequence(inner_toks, options=options)})"
            ), next_pos
    return None


def _parse_calc_product_node(
    tokens: Sequence[CssToken],
    pos: int,
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> tuple[_CalcNode, int] | None:
    """Parse `<calc-product> = <calc-value> [ [ '*' | '/' ] <calc-value> ]*`."""
    first = _parse_calc_value_node(
        tokens,
        pos,
        extra_keywords=extra_keywords,
        extra_functions=extra_functions,
        specs_lookup=specs_lookup,
        options=options,
    )
    if first is None:
        return None
    factors: list[_CalcNode] = [first[0]]
    cur = first[1]
    while cur < len(tokens) and tokens[cur].kind == "delim" and tokens[cur].value in ("*", "/"):
        op = tokens[cur].value
        rhs = _parse_calc_value_node(
            tokens,
            cur + 1,
            extra_keywords=extra_keywords,
            extra_functions=extra_functions,
            specs_lookup=specs_lookup,
            options=options,
        )
        if rhs is None:
            return None
        if op == "/":
            factors.append(_CalcInvert(rhs[0]))
        else:
            factors.append(rhs[0])
        cur = rhs[1]
    if len(factors) == 1:
        return factors[0], cur
    return _simplify_calc_product(factors), cur


def _parse_calc_sum_node(
    tokens: Sequence[CssToken],
    pos: int = 0,
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> tuple[_CalcNode, int] | None:
    """Parse `<calc-sum> = <calc-product> [ [ '+' | '-' ] <calc-product> ]*` requiring whitespace around `+` and `-`."""
    first = _parse_calc_product_node(
        tokens,
        pos,
        extra_keywords=extra_keywords,
        extra_functions=extra_functions,
        specs_lookup=specs_lookup,
        options=options,
    )
    if first is None:
        return None
    terms: list[_CalcNode] = [first[0]]
    cur = first[1]
    while (
        cur < len(tokens)
        and tokens[cur].kind == "delim"
        and tokens[cur].value in ("+", "-")
        and tokens[cur].ws_around
    ):
        op = tokens[cur].value
        rhs = _parse_calc_product_node(
            tokens,
            cur + 1,
            extra_keywords=extra_keywords,
            extra_functions=extra_functions,
            specs_lookup=specs_lookup,
            options=options,
        )
        if rhs is None:
            return None
        if op == "-":
            terms.append(_negate_calc_node(rhs[0]))
        else:
            terms.append(rhs[0])
        cur = rhs[1]
    if len(terms) == 1:
        return terms[0], cur
    return _simplify_calc_sum(terms), cur


def _parse_full_calc_sum_node(
    tokens: Sequence[CssToken],
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> _CalcNode | None:
    non_ws = [t for t in tokens if t.kind != "whitespace"]
    if not non_ws:
        return None
    res = _parse_calc_sum_node(
        non_ws,
        0,
        extra_keywords=extra_keywords,
        extra_functions=extra_functions,
        specs_lookup=specs_lookup,
        options=options,
    )
    if res is None or res[1] != len(non_ws):
        return None
    return res[0]


def _parse_calc_sum(
    tokens: Sequence[CssToken],
    pos: int = 0,
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> tuple[str, int] | None:
    """Parse and simplify `<calc-sum>` into canonical CSSOM string."""
    res = _parse_calc_sum_node(
        tokens,
        pos,
        extra_keywords=extra_keywords,
        extra_functions=extra_functions,
        specs_lookup=specs_lookup,
        options=options,
    )
    if res is None:
        return None
    return _serialize_calc_node(res[0], is_root=True), res[1]


def _parse_full_calc_sum(
    tokens: Sequence[CssToken],
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str | None:
    node = _parse_full_calc_sum_node(
        tokens,
        extra_keywords=extra_keywords,
        extra_functions=extra_functions,
        specs_lookup=specs_lookup,
        options=options,
    )
    if node is None:
        return None
    return _serialize_calc_node(node, is_root=True)


def _is_math_function_token(
    tokens: Sequence[CssToken],
    pos: int,
    *,
    extra_keywords: frozenset[str] = frozenset(),
    extra_functions: frozenset[str] = frozenset(),
    specs_lookup: Callable[[str, bool], ValueNode | None] | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> tuple[str, int] | None:
    """If `tokens[pos]` starts a valid CSS math function (`calc(...)`, `min(...)`, etc.), return `(serialized, next_pos)`."""
    if pos >= len(tokens) or tokens[pos].kind != "function":
        return None
    fn_name = tokens[pos].value.lower()
    if fn_name not in MATH_FUNCTIONS:
        return None
    grouped = _consume_balanced_group(tokens, pos + 1, ")")
    if grouped is None:
        return None
    inner_tokens, next_pos = grouped
    non_ws = [t for t in inner_tokens if t.kind != "whitespace"]
    for t in non_ws:
        if t.kind == "semicolon":
            return None

    if fn_name in ("sibling-index", "sibling-count"):
        if non_ws:
            return None
        return f"{fn_name}()", next_pos

    if not non_ws:
        return None

    args = _split_top_level_comma_args(non_ws)
    if args is None:
        return None

    rendered_args: list[str] = []
    if fn_name in (
        "calc",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "sqrt",
        "exp",
        "abs",
        "sign",
    ):
        if len(args) != 1:
            return None
        s = _parse_full_calc_sum(
            args[0],
            extra_keywords=extra_keywords,
            extra_functions=extra_functions,
            specs_lookup=specs_lookup,
            options=options,
        )
        if s is None:
            return None
        rendered_args.append(s)
    elif fn_name in ("min", "max", "hypot"):
        for arg in args:
            s = _parse_full_calc_sum(
                arg,
                extra_keywords=extra_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if s is None:
                return None
            rendered_args.append(s)
    elif fn_name in ("mod", "rem", "atan2", "pow"):
        if len(args) != 2:
            return None
        for arg in args:
            s = _parse_full_calc_sum(
                arg,
                extra_keywords=extra_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if s is None:
                return None
            rendered_args.append(s)
    elif fn_name == "log":
        if not (1 <= len(args) <= 2):
            return None
        for arg in args:
            s = _parse_full_calc_sum(
                arg,
                extra_keywords=extra_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if s is None:
                return None
            rendered_args.append(s)
    elif fn_name == "clamp":
        if len(args) != 3:
            return None
        for idx, arg in enumerate(args):
            if (
                idx in (0, 2)
                and len(arg) == 1
                and arg[0].kind == "ident"
                and arg[0].value.lower() == "none"
            ):
                rendered_args.append("none")
            else:
                s = _parse_full_calc_sum(
                    arg,
                    extra_keywords=extra_keywords,
                    extra_functions=extra_functions,
                    specs_lookup=specs_lookup,
                    options=options,
                )
                if s is None:
                    return None
                rendered_args.append(s)
    elif fn_name == "round":
        if (
            len(args) >= 2
            and len(args[0]) == 1
            and args[0][0].kind == "ident"
            and args[0][0].value.lower() in ROUNDING_STRATEGY_KEYWORDS
        ):
            rendered_args.append(args[0][0].value.lower())
            sum_args = args[1:]
        else:
            sum_args = args
        if not (1 <= len(sum_args) <= 2):
            return None
        for arg in sum_args:
            s = _parse_full_calc_sum(
                arg,
                extra_keywords=extra_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if s is None:
                return None
            rendered_args.append(s)
    elif fn_name == "calc-size":
        if not (1 <= len(args) <= 2):
            return None
        size_keywords = extra_keywords | frozenset({"size"})
        if len(args[0]) == 1 and args[0][0].kind == "ident":
            rendered_args.append(serialize_ident(args[0][0].value.lower()))
        else:
            s0 = _parse_full_calc_sum(
                args[0],
                extra_keywords=size_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if s0 is None:
                return None
            rendered_args.append(s0)
        if len(args) == 2:
            s1 = _parse_full_calc_sum(
                args[1],
                extra_keywords=size_keywords,
                extra_functions=extra_functions,
                specs_lookup=specs_lookup,
                options=options,
            )
            if s1 is None:
                return None
            rendered_args.append(s1)
    else:
        return None

    return f"{fn_name}({', '.join(rendered_args)})", next_pos


def _validate_flat_if_branches(inner_tokens: Sequence[CssToken]) -> list[CssToken] | None:
    """Validate flat `<if-args>` (`[ <if-args-branch> ; ]* <if-args-branch> ;?`) assuming nested `if(...)` calls are normalized."""
    non_ws = [t for t in inner_tokens if t.kind != "whitespace"]
    if not non_ws:
        return None

    matching = {"(": ")", "[": "]", "{": "}"}
    closing = {")", "]", "}"}
    branches: list[list[CssToken]] = []
    current: list[CssToken] = []
    stack: list[str] = []

    for tok in non_ws:
        if tok.kind == "function":
            stack.append(")")
            current.append(tok)
        elif tok.kind in matching:
            stack.append(matching[tok.kind])
            current.append(tok)
        elif tok.kind in closing:
            if not stack or stack[-1] != tok.kind:
                return None
            stack.pop()
            current.append(tok)
        elif not stack and tok.kind == "semicolon":
            if not current:
                return None
            branches.append(current)
            current = []
        else:
            current.append(tok)

    if stack:
        return None
    if current:
        branches.append(current)
    if not branches:
        return None

    normalized_tokens: list[CssToken] = []
    for b_idx, branch in enumerate(branches):
        colon_idx: int | None = None
        b_stack: list[str] = []
        for idx, tok in enumerate(branch):
            if tok.kind == "function":
                b_stack.append(")")
            elif tok.kind in matching:
                b_stack.append(matching[tok.kind])
            elif tok.kind in closing:
                if not b_stack or b_stack[-1] != tok.kind:
                    return None
                b_stack.pop()
            elif not b_stack:
                if tok.kind == "delim" and tok.value == "!":
                    return None
                if tok.kind == "colon" and colon_idx is None:
                    colon_idx = idx
        if b_stack or colon_idx is None or colon_idx == 0:
            return None
        if b_idx > 0:
            normalized_tokens.append(CssToken("semicolon", ";"))
        normalized_tokens.extend(branch[:colon_idx])
        normalized_tokens.append(branch[colon_idx])
        normalized_tokens.extend(branch[colon_idx + 1 :])

    return normalized_tokens


def _normalize_tokens_with_if_validation(tokens: Sequence[CssToken]) -> list[CssToken] | None:
    """Iteratively validate and normalize all `if(...)` calls at any nesting depth within `tokens`."""
    non_ws = [t for t in tokens if t.kind != "whitespace"]
    matching = {"(": ")", "[": "]", "{": "}"}
    closing = {")", "]", "}"}

    if not any(t.kind == "function" and t.value.lower() == "if" for t in non_ws):
        stack: list[str] = []
        for tok in non_ws:
            if tok.kind == "function":
                stack.append(")")
            elif tok.kind in matching:
                stack.append(matching[tok.kind])
            elif tok.kind in closing:
                if not stack or stack[-1] != tok.kind:
                    return None
                stack.pop()
        return None if stack else non_ws

    frames: list[tuple[list[CssToken], list[str]]] = [([], [])]
    for tok in non_ws:
        if tok.kind == "function":
            if tok.value.lower() == "if":
                frames.append(([], []))
            else:
                frames[-1][0].append(tok)
                frames[-1][1].append(")")
        elif tok.kind in matching:
            frames[-1][0].append(tok)
            frames[-1][1].append(matching[tok.kind])
        elif tok.kind in closing:
            if frames[-1][1]:
                if frames[-1][1][-1] != tok.kind:
                    return None
                frames[-1][1].pop()
                frames[-1][0].append(tok)
            else:
                if len(frames) == 1 or tok.kind != ")":
                    return None
                if_inner, _ = frames.pop()
                norm_if = _validate_flat_if_branches(if_inner)
                if norm_if is None:
                    return None
                frames[-1][0].append(CssToken("function", "if"))
                frames[-1][0].extend(norm_if)
                frames[-1][0].append(tok)
        else:
            frames[-1][0].append(tok)

    if len(frames) != 1 or frames[0][1]:
        return None
    return frames[0][0]


def _validate_and_format_if_function(
    inner_tokens: Sequence[CssToken],
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str | None:
    """Validate `if(...)` inner tokens per CSS Values 5 `<if-args>` and return canonical `if(...)` text."""
    norm_nested = _normalize_tokens_with_if_validation(inner_tokens)
    if norm_nested is None:
        return None
    norm_inner = _validate_flat_if_branches(norm_nested)
    if norm_inner is None:
        return None
    return f"if({format_token_sequence(norm_inner, options=options)})"


def _format_tokens_with_if_validation(
    tokens: Sequence[CssToken],
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str | None:
    """Validate any `if(...)` calls in `tokens` and format the full token sequence."""
    norm = _normalize_tokens_with_if_validation(tokens)
    if norm is None:
        return None
    return format_token_sequence(norm, options=options)


def _contains_global_substitution(tokens: Sequence[CssToken]) -> bool:
    """Return True if `tokens` contains `var(...)`, `env(...)`, `attr(...)`, `first-valid(...)`, `inherit(...)`, or `if(...)`."""
    for tok in tokens:
        if tok.kind == "function" and tok.value.lower() in GLOBAL_SUBSTITUTION_FUNCTIONS:
            return True
    return False
