"""W3C CSSOM primitive and token-sequence serialization (`cssom-1` §2.1 & §6.7.2)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

from stixis._syntax.tokenizer import CssToken, validate_token_stream_safety
from stixis.errors import StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

CSS_WIDE_KEYWORDS: frozenset[str] = frozenset(
    {
        "initial",
        "inherit",
        "unset",
        "revert",
        "revert-layer",
        "revert-rule",
    }
)

RESERVED_CUSTOM_IDENT_KEYWORDS: frozenset[str] = CSS_WIDE_KEYWORDS | frozenset({"default"})

__all__ = [
    "CSS_WIDE_KEYWORDS",
    "RESERVED_CUSTOM_IDENT_KEYWORDS",
    "_serialize_dimension_unit",
    "canonicalize_unit",
    "check_numeric_bounds",
    "escape_char",
    "escape_code_point",
    "format_token_sequence",
    "is_ident_char",
    "serialize_custom_ident",
    "serialize_dashed_ident",
    "serialize_dimension",
    "serialize_ident",
    "serialize_integer",
    "serialize_number",
    "serialize_percentage",
    "serialize_string",
    "serialize_unrestricted_ident",
    "serialize_url",
    "serialize_url_modifier",
]


def escape_code_point(cp: int) -> str:
    """Escape a character as a hexadecimal code point followed by a single space (`cssom-1` §2.1)."""
    return f"\\{cp:x} "


def escape_char(ch: str) -> str:
    """Escape a character with a leading backslash (`cssom-1` §2.1)."""
    return f"\\{ch}"


def is_ident_char(ch: str) -> bool:
    """Return True if `ch` is an unescaped CSS identifier code point (`cssom-1` §2.1)."""
    cp = ord(ch)
    return (
        cp >= 0x0080
        or ch in ("-", "_")
        or ("0" <= ch <= "9")
        or ("A" <= ch <= "Z")
        or ("a" <= ch <= "z")
    )


def serialize_ident(value: str) -> str:
    """Serialize a CSS identifier according to `cssom-1` §2.1 `serialize an identifier`."""
    if not isinstance(value, str) or value == "":
        raise StixisValidationError("<ident>", "<ident>", value)

    out: list[str] = []
    length = len(value)
    for idx, ch in enumerate(value):
        cp = ord(ch)
        if cp == 0x0000:
            out.append("\ufffd")
        elif (
            (0x0001 <= cp <= 0x001F)
            or cp == 0x007F
            or idx == 0
            and "0" <= ch <= "9"
            or idx == 1
            and "0" <= ch <= "9"
            and value[0] == "-"
        ):
            out.append(escape_code_point(cp))
        elif idx == 0 and ch == "-" and length == 1:
            out.append(escape_char(ch))
        elif is_ident_char(ch):
            out.append(ch)
        else:
            out.append(escape_char(ch))
    return "".join(out)


def serialize_custom_ident(
    value: str,
    *,
    forbidden_keywords: frozenset[str] = frozenset(),
) -> str:
    """Validate and serialize a `<custom-ident>`, rejecting CSS-wide and context-forbidden keywords."""
    if not isinstance(value, str) or value == "":
        raise StixisValidationError("<custom-ident>", "<custom-ident>", value)
    lower = value.lower()
    if lower in RESERVED_CUSTOM_IDENT_KEYWORDS or lower in forbidden_keywords:
        raise StixisValidationError("<custom-ident>", "<custom-ident>", value)
    return serialize_ident(value)


def serialize_dashed_ident(value: str) -> str:
    """Validate and serialize a `<dashed-ident>` (`--*`)."""
    if not isinstance(value, str) or not value.startswith("--") or len(value) < 2:
        raise StixisValidationError("<dashed-ident>", "<dashed-ident>", value)
    if value == "--":
        return "--"
    # Serialize the identifier after the leading `--`
    return f"--{serialize_unrestricted_ident(value[2:])}"


def serialize_unrestricted_ident(value: str) -> str:
    """Escape code points inside an unrestricted hash value without leading-digit rules."""
    out: list[str] = []
    for ch in value:
        cp = ord(ch)
        if cp == 0x0000:
            out.append("\ufffd")
        elif (0x0001 <= cp <= 0x001F) or cp == 0x007F:
            out.append(escape_code_point(cp))
        elif is_ident_char(ch):
            out.append(ch)
        else:
            out.append(escape_char(ch))
    return "".join(out)


def serialize_string(
    value: str,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Serialize a CSS `<string>` according to `cssom-1` §2.1 `serialize a string`."""
    if not isinstance(value, str):
        raise StixisValidationError("<string>", "<string>", value)
    q = options.quote_char
    out: list[str] = [q]
    for ch in value:
        cp = ord(ch)
        if cp == 0x0000:
            out.append("\ufffd")
        elif (0x0001 <= cp <= 0x001F) or cp == 0x007F:
            out.append(escape_code_point(cp))
        elif ch == q or ch == "\\":
            out.append(escape_char(ch))
        else:
            out.append(ch)
    out.append(q)
    return "".join(out)


def serialize_url_modifier(
    modifier: str,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Validate and serialize a `<url-modifier>` (`<ident> | <function-token> <any-value> )`)."""
    if not isinstance(modifier, str):
        raise StixisValidationError("<url-modifier>", "<url-modifier>", modifier)

    tokens = validate_token_stream_safety(
        modifier,
        forbid_top_level_semicolon=True,
        forbid_top_level_bang=True,
        forbid_top_level_braces=True,
        allow_empty=False,
        target="<url-modifier>",
    )
    non_ws = [t for t in tokens if t.kind != "whitespace"]
    if len(non_ws) == 1 and non_ws[0].kind == "ident":
        return serialize_ident(non_ws[0].value)
    if len(non_ws) >= 2 and non_ws[0].kind == "function" and non_ws[-1].kind == ")":
        if len(non_ws) == 2:
            raise StixisValidationError("<url-modifier>", "<url-modifier>", modifier)
        depth = 0
        matching = {"(": ")", "[": "]", "{": "}"}
        closing = {")", "]", "}"}
        for idx, tok in enumerate(non_ws):
            if tok.kind == "function" or tok.kind in matching:
                depth += 1
            elif tok.kind in closing:
                depth -= 1
                if depth == 0 and idx != len(non_ws) - 1:
                    raise StixisValidationError("<url-modifier>", "<url-modifier>", modifier)
        return format_token_sequence(non_ws, options=options)
    raise StixisValidationError("<url-modifier>", "<url-modifier>", modifier)


def _is_values_instance(value: object, class_name: str) -> bool:
    """Return True if `value` is an instance or subclass instance of `stixis.values.<class_name>`."""
    return any(
        cls.__name__ == class_name and getattr(cls, "__module__", "") == "stixis.values"
        for cls in type(value).__mro__
    )


def serialize_url(
    value: Any,
    *,
    modifiers: tuple[str, ...] = (),
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Serialize a CSS `<url>` according to `cssom-1` §2.1 `serialize a URL`."""
    if _is_values_instance(value, "Url"):
        raw = value.value
        mods = value.modifiers or modifiers
    elif isinstance(value, str):
        raw = value
        mods = modifiers
    else:
        raise StixisValidationError("<url>", "<url>", value)

    # Control characters inside URL strings are escaped by serialize_string,
    # which is valid for url("...") string form.
    inner = serialize_string(raw, options=options)
    if mods:
        if not isinstance(mods, tuple):
            raise StixisValidationError("<url-modifier>", "<url-modifier>", mods)
        mod_str = " ".join(serialize_url_modifier(m, options=options) for m in mods)
        return f"url({inner} {mod_str})"
    return f"url({inner})"


def check_numeric_bounds(
    value: float,
    min_val: float | None,
    max_val: float | None,
    target: str,
    expected_syntax: str,
) -> None:
    """Check whether `value` satisfies `[min_val, max_val]`."""
    if min_val is not None and value < min_val:
        raise StixisValidationError(target, expected_syntax, value)
    if max_val is not None and value > max_val:
        raise StixisValidationError(target, expected_syntax, value)


def serialize_integer(
    value: Any,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
    target: str = "<integer>",
    expected_syntax: str = "<integer>",
) -> str:
    """Validate and serialize a CSS `<integer>` (`cssom-1` §6.7.2)."""
    if isinstance(value, bool):
        raise StixisValidationError(target, expected_syntax, value)
    if isinstance(value, int):
        int_val = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise StixisValidationError(target, expected_syntax, value)
        int_val = int(value)
    else:
        raise StixisValidationError(target, expected_syntax, value)

    check_numeric_bounds(int_val, min_val, max_val, target, expected_syntax)
    return str(int_val)


def serialize_number(
    value: Any,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
    target: str = "<number>",
    expected_syntax: str = "<number>",
) -> str:
    """Validate and serialize a CSS `<number>` (`cssom-1` §6.7.2: shortest base-10, max 6 decimals, no scientific notation)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StixisValidationError(target, expected_syntax, value)
    if isinstance(value, int):
        check_numeric_bounds(value, min_val, max_val, target, expected_syntax)
        return str(value)
    if not math.isfinite(value):
        raise StixisValidationError(target, expected_syntax, value)

    check_numeric_bounds(value, min_val, max_val, target, expected_syntax)

    if value == 0.0:
        return "0"

    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))

    # Round to at most 6 decimal places using Decimal for exact base-10 formatting without scientific notation
    raw_dec = Decimal(str(value))
    prec = max(28, raw_dec.adjusted() + 8)
    dec = Context(prec=prec, rounding=ROUND_HALF_UP).quantize(raw_dec, Decimal("0.000001"))
    if dec == 0:
        return "0"
    formatted = format(dec, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    if formatted in ("-0", ""):
        return "0"
    return formatted


def serialize_percentage(
    value: Any,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
    target: str = "<percentage>",
    expected_syntax: str = "<percentage>",
) -> str:
    """Validate and serialize a CSS `<percentage>` from `Percentage`, `(num, '%')`, or `%` string."""
    num: int | float
    if _is_values_instance(value, "Percentage"):
        num = value.value
    elif (
        isinstance(value, tuple)
        and len(value) == 2
        and value[1] == "%"
        and not isinstance(value[0], bool)
        and isinstance(value[0], (int, float))
    ):
        num = value[0]
    else:
        raise StixisValidationError(target, expected_syntax, value)

    num_str = serialize_number(
        num,
        min_val=min_val,
        max_val=max_val,
        target=target,
        expected_syntax=expected_syntax,
    )
    return f"{num_str}%"


def canonicalize_unit(
    unit: str,
    allowed_units: frozenset[str],
    lower_to_canonical: dict[str, str],
    target: str,
    expected_syntax: str,
    raw_value: Any,
) -> str:
    """Validate a unit against `allowed_units` (case-insensitively) and return its canonical casing."""
    if unit in allowed_units:
        return unit
    canon = lower_to_canonical.get(unit.lower())
    if canon is not None:
        return canon
    raise StixisValidationError(target, expected_syntax, raw_value)


def serialize_dimension(
    value: Any,
    *,
    allowed_units: frozenset[str],
    lower_to_canonical: dict[str, str],
    allow_unitless_zero: bool = False,
    min_val: float | None = None,
    max_val: float | None = None,
    target: str = "<dimension>",
    expected_syntax: str = "<dimension>",
) -> str:
    """Validate and serialize a dimension value (`Dimension(n, unit)`, `(n, unit)`, or unitless `0` for `<length>`)."""
    if (
        allow_unitless_zero
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value == 0
    ):
        check_numeric_bounds(0, min_val, max_val, target, expected_syntax)
        return "0px"

    num: int | float
    unit: str
    if _is_values_instance(value, "Dimension"):
        num = value.value
        unit = value.unit
    elif (
        isinstance(value, tuple)
        and len(value) == 2
        and not isinstance(value[0], bool)
        and isinstance(value[0], (int, float))
        and isinstance(value[1], str)
    ):
        num = value[0]
        unit = value[1]
    else:
        raise StixisValidationError(target, expected_syntax, value)

    canon_unit = canonicalize_unit(
        unit, allowed_units, lower_to_canonical, target, expected_syntax, value
    )
    num_str = serialize_number(
        num,
        min_val=min_val,
        max_val=max_val,
        target=target,
        expected_syntax=expected_syntax,
    )
    return f"{num_str}{canon_unit}"


def _serialize_dimension_unit(unit: str) -> str:
    """Serialize a dimension unit identifier, escaping a leading `e`/`E` when followed by a digit or `-[0-9]`."""
    if (
        len(unit) >= 2
        and unit[0] in ("e", "E")
        and ("0" <= unit[1] <= "9" or (len(unit) >= 3 and unit[1] == "-" and "0" <= unit[2] <= "9"))
    ):
        return f"{escape_code_point(ord(unit[0]))}{serialize_unrestricted_ident(unit[1:])}"
    return serialize_ident(unit)


def format_token_sequence(
    tokens: Sequence[CssToken],
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Serialize a validated sequence of `CssToken`s into canonical CSSOM string form."""
    out: list[str] = []
    prev: CssToken | None = None

    for tok in tokens:
        if tok.kind == "whitespace":
            continue

        # Determine if space is needed before this token
        need_space = prev is not None and not (
            prev.kind in ("function", "(", "[")
            or tok.kind in (")", "]", "comma", "semicolon", "colon")
        )

        if need_space:
            out.append(" ")

        if tok.kind == "ident":
            out.append(serialize_ident(tok.value))
        elif tok.kind == "function":
            fn_lower = tok.value.lower()
            out.append(f"{serialize_ident(fn_lower)}(")
        elif tok.kind == "(":
            out.append("(")
        elif tok.kind == ")":
            out.append(")")
        elif tok.kind == "at-keyword":
            out.append(f"@{serialize_ident(tok.value)}")
        elif tok.kind == "hash":
            if tok.hash_type == "id":
                out.append(f"#{serialize_ident(tok.value)}")
            else:
                out.append(f"#{serialize_unrestricted_ident(tok.value)}")
        elif tok.kind == "string":
            out.append(serialize_string(tok.value, options=options))
        elif tok.kind == "url":
            out.append(serialize_url(tok.value, options=options))
        elif tok.kind == "number":
            assert tok.num is not None
            val = (
                int(tok.num)
                if tok.is_integer and (isinstance(tok.num, int) or math.isfinite(tok.num))
                else tok.num
            )
            out.append(serialize_number(val))
        elif tok.kind == "percentage":
            assert tok.num is not None
            val = (
                int(tok.num)
                if tok.is_integer and (isinstance(tok.num, int) or math.isfinite(tok.num))
                else tok.num
            )
            num_s = serialize_number(val)
            out.append(f"{num_s}%")
        elif tok.kind == "dimension":
            assert tok.num is not None
            assert tok.unit is not None
            val = (
                int(tok.num)
                if tok.is_integer and (isinstance(tok.num, int) or math.isfinite(tok.num))
                else tok.num
            )
            num_s = serialize_number(val)
            out.append(f"{num_s}{_serialize_dimension_unit(tok.unit)}")
        else:
            out.append(tok.value)

        prev = tok

    return "".join(out)
