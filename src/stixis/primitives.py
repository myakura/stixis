"""W3C CSSOM primitive serialization and range validation (`cssom-1` §2.1 & §6.7.2)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

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


@dataclass(frozen=True, slots=True)
class Keyword:
    """Explicit CSS identifier or keyword (disambiguates keywords from `<string>` or `<family-name>`)."""

    value: str

    def __str__(self) -> str:
        return serialize_ident(self.value)


@dataclass(frozen=True, slots=True)
class GenericFamily:
    """Explicit `<generic-family>` value for `font-family` (e.g., `serif`, `generic(kai)`)."""

    name: str

    def __str__(self) -> str:
        lower = self.name.strip().lower()
        if lower.startswith("generic(") and lower.endswith(")"):
            inner = lower[8:-1].strip()
            return f"generic({serialize_ident(inner)})"
        return serialize_ident(lower)


def generic(name: str) -> GenericFamily:
    """Construct an explicit `<generic-family>` value (such as `generic('serif')` or `generic('generic(kai)')`)."""
    return GenericFamily(name)


@dataclass(frozen=True, slots=True)
class Url:
    """Explicit `<url>` value."""

    value: str
    modifiers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise StixisValidationError("<url>", "<url>", self.value)
        if not isinstance(self.modifiers, tuple):
            raise StixisValidationError("<url-modifier>", "<url-modifier>", self.modifiers)
        for mod in self.modifiers:
            serialize_url_modifier(mod)

    def __str__(self) -> str:
        return serialize_url(self.value, modifiers=self.modifiers)



@dataclass(frozen=True, slots=True)
class LocalFont:
    """Explicit `local(<font-family-name>)` value for `@font-face` `src`."""

    family: str

    def __str__(self) -> str:
        return f"local({serialize_string(self.family)})"


@dataclass(frozen=True, slots=True)
class Dimension:
    """A numeric value paired with a CSS unit (`<length>`, `<angle>`, `<time>`, `<frequency>`, `<resolution>`, `<flex>`)."""

    value: int | float
    unit: str

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise StixisValidationError("<dimension>", "<dimension>", self.value)
        if not math.isfinite(self.value):
            raise StixisValidationError("<dimension>", "<dimension>", self.value)
        if not isinstance(self.unit, str) or not self.unit:
            raise StixisValidationError("<dimension>", "<dimension>", self.unit)

    def __str__(self) -> str:
        return f"{serialize_number(self.value)}{self.unit}"


@dataclass(frozen=True, slots=True)
class Percentage:
    """A numeric percentage value (`<percentage>`)."""

    value: int | float

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise StixisValidationError("<percentage>", "<percentage>", self.value)
        if not math.isfinite(self.value):
            raise StixisValidationError("<percentage>", "<percentage>", self.value)

    def __str__(self) -> str:
        return f"{serialize_number(self.value)}%"


def Pct(value: float) -> Percentage:
    """Shorthand constructor for `Percentage(value)`."""
    return Percentage(value)


def Deg(value: float) -> Dimension:
    """Shorthand constructor for `Dimension(value, 'deg')`."""
    return Dimension(value, "deg")


def Px(value: float) -> Dimension:
    """Shorthand constructor for `Dimension(value, 'px')`."""
    return Dimension(value, "px")


def Rem(value: float) -> Dimension:
    """Shorthand constructor for `Dimension(value, 'rem')`."""
    return Dimension(value, "rem")


def Em(value: float) -> Dimension:
    """Shorthand constructor for `Dimension(value, 'em')`."""
    return Dimension(value, "em")


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
        elif (0x0001 <= cp <= 0x001F) or cp == 0x007F or idx == 0 and "0" <= ch <= "9" or idx == 1 and "0" <= ch <= "9" and value[0] == "-":
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
    from stixis.syntax import format_token_sequence, validate_token_stream_safety

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
                    raise StixisValidationError(
                        "<url-modifier>", "<url-modifier>", modifier
                    )
        return format_token_sequence(non_ws, options=options)
    raise StixisValidationError("<url-modifier>", "<url-modifier>", modifier)


def serialize_url(
    value: str | Url,
    *,
    modifiers: tuple[str, ...] = (),
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Serialize a CSS `<url>` according to `cssom-1` §2.1 `serialize a URL`."""
    if isinstance(value, Url):
        raw = value.value
        mods = value.modifiers or modifiers
    elif isinstance(value, str):
        raw = value
        mods = modifiers
    else:
        raise StixisValidationError("<url>", "<url>", value)

    if any(ord(c) <= 0x1F or ord(c) == 0x7F for c in raw):
        # Control characters inside URL strings are still escaped by serialize_string,
        # which is valid for url("...") string form.
        pass
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
    if not math.isfinite(value):
        raise StixisValidationError(target, expected_syntax, value)

    check_numeric_bounds(value, min_val, max_val, target, expected_syntax)

    if isinstance(value, int):
        return str(value)

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
    if isinstance(value, Percentage):
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
    if isinstance(value, Dimension):
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
