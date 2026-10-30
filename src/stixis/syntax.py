"""CSS Syntax Level 3 tokenizer and W3C Value Definition Syntax compiler & evaluator."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from string.templatelib import Interpolation, Template, convert
from typing import Any

from stixis._generated.units import LOWER_TO_CANONICAL_UNIT, UNITS_BY_TYPE
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.microsyntax.family_name import (
    GENERIC_FAMILY_KEYWORDS,
    GENERIC_SCRIPT_KEYWORDS,
    serialize_family_name,
    serialize_font_family_list,
    serialize_generic_family,
)
from stixis.microsyntax.font_src import (
    FONT_TECH_KEYWORDS,
    serialize_font_format,
    serialize_font_src_list,
    serialize_font_tech,
    serialize_single_font_src,
)
from stixis.microsyntax.opentype import (
    serialize_font_feature_settings,
    serialize_font_language_override,
    serialize_font_variation_settings,
    serialize_opentype_tag,
)
from stixis.microsyntax.ranges import (
    angle_to_deg,
    serialize_font_style,
    serialize_font_weight,
    serialize_font_width,
    serialize_metric_override,
)
from stixis.microsyntax.urange import format_urange
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.primitives import (
    CSS_WIDE_KEYWORDS,
    Dimension,
    GenericFamily,
    Keyword,
    LocalFont,
    Percentage,
    Url,
    serialize_custom_ident,
    serialize_dashed_ident,
    serialize_dimension,
    serialize_ident,
    serialize_integer,
    serialize_number,
    serialize_percentage,
    serialize_string,
    serialize_unrestricted_ident,
    serialize_url,
)

# CSS-wide keywords + CSS Variables / substitution functions that are valid on any property
GLOBAL_SUBSTITUTION_FUNCTIONS: frozenset[str] = frozenset({"var", "env", "attr", "first-valid"})
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
        " sibling-index",
        "sibling-index",
        "sibling-count",
    }
)


@dataclass(frozen=True, slots=True)
class CssToken:
    """A single token produced by the CSS Syntax Level 3 tokenizer."""

    kind: str  # "ident", "function", "at-keyword", "hash", "string", "url", "number", "percentage", "dimension", "whitespace", "comma", "colon", "semicolon", "delim", "(", ")", "[", "]", "{", "}"
    value: str
    num: int | float | None = None
    is_integer: bool = False
    unit: str = ""
    hash_type: str = "unrestricted"  # "id" or "unrestricted"


def _consume_escape(text: str, pos: int) -> tuple[str, int]:
    """Consume an escaped code point after `\\` at `pos` (`css-syntax-3` §4.3.7)."""
    n = len(text)
    if pos >= n:
        return "\ufffd", pos
    ch = text[pos]
    if ch in "0123456789abcdefABCDEF":
        hex_digits = [ch]
        pos += 1
        while pos < n and len(hex_digits) < 6 and text[pos] in "0123456789abcdefABCDEF":
            hex_digits.append(text[pos])
            pos += 1
        if pos < n and text[pos] in (" ", "\t", "\n", "\r", "\f"):
            if text[pos : pos + 2] == "\r\n":
                pos += 2
            else:
                pos += 1
        cp = int("".join(hex_digits), 16)
        if cp == 0 or (0xD800 <= cp <= 0xDFFF) or cp > 0x10FFFF:
            return "\ufffd", pos
        return chr(cp), pos
    return ch, pos + 1


def _is_ident_start_char(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x80 or ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_"


def _is_ident_code_point(ch: str) -> bool:
    return _is_ident_start_char(ch) or ("0" <= ch <= "9") or ch == "-"


def _starts_with_valid_escape(text: str, pos: int) -> bool:
    if pos >= len(text) or text[pos] != "\\":
        return False
    if pos + 1 >= len(text):
        return False
    return text[pos + 1] not in ("\n", "\r", "\f")


def _starts_an_ident_sequence(text: str, pos: int) -> bool:
    n = len(text)
    if pos >= n:
        return False
    ch = text[pos]
    if ch == "-":
        return bool(
            pos + 1 < n
            and (
                _is_ident_start_char(text[pos + 1])
                or text[pos + 1] == "-"
                or _starts_with_valid_escape(text, pos + 1)
            )
        )
    if _is_ident_start_char(ch):
        return True
    if ch == "\\":
        return _starts_with_valid_escape(text, pos)
    return False


def _consume_ident_sequence(text: str, pos: int) -> tuple[str, int]:
    n = len(text)
    chars: list[str] = []
    while pos < n:
        ch = text[pos]
        if _is_ident_code_point(ch):
            chars.append(ch)
            pos += 1
        elif _starts_with_valid_escape(text, pos):
            esc_ch, pos = _consume_escape(text, pos + 1)
            chars.append(esc_ch)
        else:
            break
    return "".join(chars), pos


def _starts_a_number(text: str, pos: int) -> bool:
    n = len(text)
    if pos >= n:
        return False
    ch = text[pos]
    if ch in ("+", "-"):
        if pos + 1 >= n:
            return False
        nxt = text[pos + 1]
        if "0" <= nxt <= "9":
            return True
        return bool(nxt == "." and pos + 2 < n and ("0" <= text[pos + 2] <= "9"))
    if ch == ".":
        return pos + 1 < n and ("0" <= text[pos + 1] <= "9")
    return "0" <= ch <= "9"


def _consume_number(text: str, pos: int) -> tuple[str, int | float, bool, int]:
    n = len(text)
    start = pos
    is_int = True
    if pos < n and text[pos] in ("+", "-"):
        pos += 1
    while pos < n and ("0" <= text[pos] <= "9"):
        pos += 1
    if pos + 1 < n and text[pos] == "." and ("0" <= text[pos + 1] <= "9"):
        is_int = False
        pos += 2
        while pos < n and ("0" <= text[pos] <= "9"):
            pos += 1
    if pos + 1 < n and text[pos] in ("e", "E"):
        if "0" <= text[pos + 1] <= "9":
            is_int = False
            pos += 2
            while pos < n and ("0" <= text[pos] <= "9"):
                pos += 1
        elif pos + 2 < n and text[pos + 1] in ("+", "-") and ("0" <= text[pos + 2] <= "9"):
            is_int = False
            pos += 3
            while pos < n and ("0" <= text[pos] <= "9"):
                pos += 1
    repr_str = text[start:pos]
    if is_int:
        try:
            int_val = int(repr_str)
        except ValueError:
            int_val = None
        if int_val is not None:
            try:
                float(int_val)
            except OverflowError:
                return repr_str, (-math.inf if int_val < 0 else math.inf), False, pos
            return repr_str, int_val, True, pos
    num_val = float(repr_str)
    return repr_str, num_val, is_int, pos


def _consume_unquoted_url(text: str, pos: int) -> tuple[str, int]:
    n = len(text)
    while pos < n and text[pos] in (" ", "\t", "\n", "\r", "\f"):
        pos += 1
    chars: list[str] = []
    while pos < n:
        ch = text[pos]
        if ch == ")":
            return "".join(chars), pos + 1
        if ch in (" ", "\t", "\n", "\r", "\f"):
            while pos < n and text[pos] in (" ", "\t", "\n", "\r", "\f"):
                pos += 1
            if pos < n and text[pos] == ")":
                return "".join(chars), pos + 1
            raise StixisSyntaxError("Bad URL token: whitespace inside unquoted url()")
        if ch in ('"', "'", "(") or ord(ch) <= 0x1F or ord(ch) == 0x7F:
            raise StixisSyntaxError(f"Bad URL token: invalid character {ch!r} inside unquoted url()")
        if ch == "\\":
            if _starts_with_valid_escape(text, pos):
                esc_ch, pos = _consume_escape(text, pos + 1)
                chars.append(esc_ch)
            else:
                raise StixisSyntaxError("Bad URL token: invalid escape inside unquoted url()")
        else:
            chars.append(ch)
            pos += 1
    raise StixisSyntaxError("Unclosed url() token")


def tokenize_css(text: str, *, preserve_whitespace: bool = False) -> list[CssToken]:
    """Tokenize a CSS string according to W3C CSS Syntax Module Level 3 §4."""
    # Preprocess newlines and NULL characters per §3.3
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").replace("\x00", "\ufffd")
    tokens: list[CssToken] = []
    pos = 0
    n = len(normalized)

    while pos < n:
        ch = normalized[pos]

        # Comments
        if ch == "/" and pos + 1 < n and normalized[pos + 1] == "*":
            end_comment = normalized.find("*/", pos + 2)
            if end_comment == -1:
                raise StixisSyntaxError("Unclosed CSS comment `/*`")
            pos = end_comment + 2
            continue

        # Stray closing comment
        if ch == "*" and pos + 1 < n and normalized[pos + 1] == "/":
            raise StixisSyntaxError("Unexpected comment close `*/`")

        # Whitespace
        if ch in (" ", "\t", "\n"):
            start = pos
            while pos < n and normalized[pos] in (" ", "\t", "\n"):
                pos += 1
            if preserve_whitespace:
                tokens.append(CssToken("whitespace", normalized[start:pos]))
            continue

        # Strings
        if ch in ('"', "'"):
            quote = ch
            pos += 1
            chars: list[str] = []
            closed = False
            while pos < n:
                c = normalized[pos]
                if c == quote:
                    pos += 1
                    closed = True
                    break
                if c == "\n":
                    raise StixisSyntaxError("Unescaped newline inside CSS string literal")
                if c == "\\":
                    if pos + 1 >= n:
                        break
                    if normalized[pos + 1] == "\n":
                        pos += 2
                        continue
                    esc_ch, pos = _consume_escape(normalized, pos + 1)
                    chars.append(esc_ch)
                else:
                    chars.append(c)
                    pos += 1
            if not closed:
                raise StixisSyntaxError(f"Unclosed CSS string literal starting with {quote!r}")
            tokens.append(CssToken("string", "".join(chars)))
            continue

        # Hash `#`
        if ch == "#":
            if pos + 1 < n and (
                _is_ident_code_point(normalized[pos + 1])
                or _starts_with_valid_escape(normalized, pos + 1)
            ):
                hash_type = "id" if _starts_an_ident_sequence(normalized, pos + 1) else "unrestricted"
                ident, pos = _consume_ident_sequence(normalized, pos + 1)
                tokens.append(CssToken("hash", ident, hash_type=hash_type))
            else:
                tokens.append(CssToken("delim", "#"))
                pos += 1
            continue

        # Numeric tokens
        if _starts_a_number(normalized, pos):
            repr_str, num_val, is_int, pos = _consume_number(normalized, pos)
            if _starts_an_ident_sequence(normalized, pos):
                unit, pos = _consume_ident_sequence(normalized, pos)
                tokens.append(
                    CssToken("dimension", repr_str, num=num_val, is_integer=is_int, unit=unit)
                )
            elif pos < n and normalized[pos] == "%":
                pos += 1
                tokens.append(CssToken("percentage", repr_str, num=num_val, is_integer=is_int))
            else:
                tokens.append(CssToken("number", repr_str, num=num_val, is_integer=is_int))
            continue

        # Ident-like tokens
        if _starts_an_ident_sequence(normalized, pos):
            ident, pos = _consume_ident_sequence(normalized, pos)
            if pos < n and normalized[pos] == "(":
                pos += 1
                if ident.lower() == "url":
                    look = pos
                    while look < n and normalized[look] in (" ", "\t", "\n"):
                        look += 1
                    if look < n and normalized[look] not in ('"', "'"):
                        url_val, pos = _consume_unquoted_url(normalized, look)
                        tokens.append(CssToken("url", url_val))
                        continue
                tokens.append(CssToken("function", ident))
            else:
                tokens.append(CssToken("ident", ident))
            continue

        # At-keyword
        if ch == "@":
            if _starts_an_ident_sequence(normalized, pos + 1):
                ident, pos = _consume_ident_sequence(normalized, pos + 1)
                tokens.append(CssToken("at-keyword", ident))
            else:
                tokens.append(CssToken("delim", "@"))
                pos += 1
            continue

        # Single-character punctuation tokens
        if ch in ("(", ")", "[", "]", "{", "}"):
            tokens.append(CssToken(ch, ch))
            pos += 1
            continue
        if ch == ",":
            tokens.append(CssToken("comma", ","))
            pos += 1
            continue
        if ch == ":":
            tokens.append(CssToken("colon", ":"))
            pos += 1
            continue
        if ch == ";":
            tokens.append(CssToken("semicolon", ";"))
            pos += 1
            continue

        tokens.append(CssToken("delim", ch))
        pos += 1

    return tokens


def render_css_template(
    tpl: Template | str,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    start_in_comment: bool = False,
) -> str:
    """Render a PEP 750 `Template` (`t"..."`) with context-aware escaping for CSS comments, strings, URLs, and numbers."""
    if isinstance(tpl, str):
        return tpl
    if not isinstance(tpl, Template):
        raise TypeError(f"Expected str or string.templatelib.Template, got {type(tpl).__name__}")

    out: list[str] = []
    in_comment = start_in_comment
    in_quote: str | None = None
    in_unquoted_url = False

    for item in tpl:
        if isinstance(item, str):
            out.append(item)
            i = 0
            n = len(item)
            while i < n:
                ch = item[i]
                if in_comment:
                    if not start_in_comment and ch == "*" and i + 1 < n and item[i + 1] == "/":
                        in_comment = False
                        i += 2
                        continue
                elif in_quote is not None:
                    if ch == "\\" and i + 1 < n:
                        i += 2
                        continue
                    if ch == in_quote:
                        in_quote = None
                else:
                    if ch == "/" and i + 1 < n and item[i + 1] == "*":
                        in_comment = True
                        i += 2
                        continue
                    if ch in ('"', "'"):
                        in_quote = ch
                        in_unquoted_url = False
                    elif ch == ")":
                        in_unquoted_url = False
                    elif (
                        i >= 3
                        and item[i - 3 : i + 1].lower() == "url("
                        and (i < 4 or not _is_ident_code_point(item[i - 4]))
                    ):
                        in_unquoted_url = True
                i += 1
        elif isinstance(item, Interpolation):
            val = convert(item.value, item.conversion)
            if in_comment:
                text = format(val, item.format_spec) if item.format_spec else str(val)
                cleaned = (
                    text.replace("\r\n", " ")
                    .replace("\r", " ")
                    .replace("\n", " ")
                    .replace("\f", " ")
                )
                while "*/" in cleaned:
                    cleaned = cleaned.replace("*/", "* /")
                out.append(cleaned)
            elif in_quote is not None:
                text = format(val, item.format_spec) if item.format_spec else str(val)
                q_opts = (
                    options
                    if options.quote_char == in_quote
                    else SerializationOptions(quote_char=in_quote)  # type: ignore[arg-type]
                )
                # Strip the outer quotes added by serialize_string
                escaped = serialize_string(text, options=q_opts)[1:-1]
                out.append(escaped)
            elif in_unquoted_url:
                text = format(val, item.format_spec) if item.format_spec else str(val)
                # Render as a quoted string inside url("...")
                out.append(serialize_string(text, options=options))
                in_unquoted_url = False
            elif not item.format_spec and not isinstance(val, bool) and isinstance(val, (int, float)):
                out.append(serialize_number(val))
            else:
                text = format(val, item.format_spec) if item.format_spec else str(val)
                out.append(text)

    if in_comment and not start_in_comment:
        raise StixisSyntaxError("Unclosed CSS comment `/*` in Template")
    if in_quote is not None:
        raise StixisSyntaxError(f"Unclosed CSS string quote {in_quote!r} in Template")

    return "".join(out)


def validate_token_stream_safety(
    text: str,
    *,
    forbid_top_level_semicolon: bool = True,
    forbid_top_level_bang: bool = False,
    forbid_top_level_braces: bool = True,
    allow_empty: bool = False,
    target: str = "<declaration-value>",
) -> list[CssToken]:
    """Validate bracket/quote balance and top-level token safety (`<declaration-value>` / selector / prelude)."""
    try:
        tokens = tokenize_css(text, preserve_whitespace=True)
    except StixisSyntaxError as exc:
        raise StixisValidationError(target, target, text, str(exc)) from exc

    non_ws = [t for t in tokens if t.kind != "whitespace"]
    if not non_ws and not allow_empty:
        raise StixisValidationError(target, target, text, f"Empty token stream for {target}")

    stack: list[str] = []
    matching = {"(": ")", "[": "]", "{": "}"}
    closing = {")", "]", "}"}

    for tok in tokens:
        if tok.kind == "function":
            stack.append(")")
        elif tok.kind in matching:
            if not stack and tok.kind == "{" and forbid_top_level_braces:
                raise StixisValidationError(
                    target, target, text, "Unexpected top-level `{` token"
                )
            stack.append(matching[tok.kind])
        elif tok.kind in closing:
            if not stack or stack[-1] != tok.kind:
                raise StixisValidationError(
                    target, target, text, f"Unmatched closing `{tok.kind}` token"
                )
            stack.pop()
        elif not stack:
            if forbid_top_level_semicolon and tok.kind == "semicolon":
                raise StixisValidationError(
                    target, target, text, "Unexpected top-level `;` token"
                )
            if forbid_top_level_bang and tok.kind == "delim" and tok.value == "!":
                raise StixisValidationError(
                    target, target, text, "Unexpected top-level `!` token"
                )

    if stack:
        raise StixisValidationError(
            target, target, text, f"Unclosed bracket expecting `{stack[-1]}`"
        )

    return tokens


def format_token_sequence(
    tokens: Sequence[CssToken],
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Serialize a validated sequence of `CssToken`s into canonical CSSOM string form."""
    out: list[str] = []
    prev: CssToken | None = None
    in_calc_depth = 0
    paren_stack: list[bool] = []  # True if the '(' belongs to a calc/math function

    for idx, tok in enumerate(tokens):
        if tok.kind == "whitespace":
            continue

        # Determine if space is needed before this token
        need_space = False
        if prev is not None:
            if prev.kind in ("function", "(", "[") or tok.kind in (")", "]", "comma", "semicolon", "colon"):
                need_space = False
            elif prev.kind == "colon" or prev.kind == "comma":  # noqa: SIM114
                need_space = True
            elif in_calc_depth > 0 and (
                (tok.kind == "delim" and tok.value in ("+", "-", "*", "/"))
                or (prev.kind == "delim" and prev.value in ("+", "-", "*", "/"))
            ) or tok.kind == "delim" and tok.value == "/" or prev.kind == "delim" and prev.value == "/":
                need_space = True
            else:
                need_space = True

        if need_space:
            out.append(" ")

        if tok.kind == "ident":
            out.append(serialize_ident(tok.value))
        elif tok.kind == "function":
            fn_lower = tok.value.lower()
            is_math = fn_lower in MATH_FUNCTIONS
            paren_stack.append(is_math)
            if is_math:
                in_calc_depth += 1
            out.append(f"{serialize_ident(fn_lower)}(")
        elif tok.kind == "(":
            paren_stack.append(False)
            out.append("(")
        elif tok.kind == ")":
            if paren_stack:
                was_math = paren_stack.pop()
                if was_math:
                    in_calc_depth -= 1
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
            val = int(tok.num) if tok.is_integer and math.isfinite(tok.num) else tok.num
            out.append(serialize_number(val))
        elif tok.kind == "percentage":
            assert tok.num is not None
            val = int(tok.num) if tok.is_integer and math.isfinite(tok.num) else tok.num
            num_s = serialize_number(val)
            out.append(f"{num_s}%")
        elif tok.kind == "dimension":
            assert tok.num is not None
            assert tok.unit is not None
            val = int(tok.num) if tok.is_integer and math.isfinite(tok.num) else tok.num
            num_s = serialize_number(val)
            out.append(f"{num_s}{serialize_ident(tok.unit)}")
        else:
            out.append(tok.value)

        prev = tok

    return "".join(out)


# ---------------------------------------------------------------------------
# Value Definition Syntax AST (`ValueNode`) & Compiler
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValueNode:
    """Base class for compiled W3C Value Definition Syntax combinator nodes."""


@dataclass(frozen=True, slots=True)
class KeywordNode(ValueNode):
    """Matches a case-insensitive CSS keyword and serializes it in ASCII lowercase."""

    name: str


@dataclass(frozen=True, slots=True)
class DelimNode(ValueNode):
    """Matches a literal delimiter or punctuation token (such as `/`, `,`, `:`, `(`, `)`)."""

    delim: str


@dataclass(frozen=True, slots=True)
class PrimitiveNode(ValueNode):
    """Matches a primitive or microsyntax type (`<number>`, `<length>`, `<string>`, `<urange>`, etc.)."""

    kind: str  # e.g., "<number>", "<integer>", "<length>", "<percentage>", "<angle>", "<string>", "<url>", "<custom-ident>", "<dashed-ident>", "<ident>", "<urange>", "<opentype-tag>", "<family-name>", "<generic-family>", "<font-src>", "<font-src-list>", "<font-format>", "<font-tech>", "<hex-color>", "<any-value>", "<declaration-value>"
    min_val: float | None = None
    max_val: float | None = None
    min_unit: str = ""
    max_unit: str = ""


@dataclass(frozen=True, slots=True)
class RefNode(ValueNode):
    """References a named value definition (`<color>`, `<font-weight-absolute>`, etc.) or property (`<'font-width'>`)."""

    name: str
    is_property: bool = False
    min_val: float | None = None
    max_val: float | None = None


@dataclass(frozen=True, slots=True)
class FunctionNode(ValueNode):
    """Matches a CSS function `name(arg_node)`."""

    name: str
    arg: ValueNode | None


@dataclass(frozen=True, slots=True)
class JuxtaposeNode(ValueNode):
    """Ordered juxtaposition `A B C`."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class AllOfNode(ValueNode):
    """Unordered conjunction `A && B` (all required, serialized in canonical grammar order)."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class AnyOrderNode(ValueNode):
    """Unordered inclusive-or `A || B` (at least one required, serialized in canonical grammar order)."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class ExclusiveOrNode(ValueNode):
    """Exclusive choice `A | B`."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class MultiplierNode(ValueNode):
    """Multiplied node (`?`, `*`, `+`, `{min,max}`, `#`, `#{min,max}`)."""

    child: ValueNode
    min_count: int
    max_count: int | None
    comma_separated: bool = False


@dataclass(frozen=True, slots=True)
class NonEmptyGroupNode(ValueNode):
    """Required non-empty group `[ ... ]!`."""

    child: ValueNode


_BUILTIN_PRIMITIVES: frozenset[str] = frozenset(
    {
        "<ident>",
        "<custom-ident>",
        "<dashed-ident>",
        "<string>",
        "<url>",
        "<url-token>",
        "<number>",
        "<integer>",
        "<percentage>",
        "<dimension>",
        "<zero>",
        "<length>",
        "<angle>",
        "<time>",
        "<frequency>",
        "<resolution>",
        "<flex>",
        "<decibel>",
        "<semitones>",
        "<length-percentage>",
        "<angle-percentage>",
        "<time-percentage>",
        "<frequency-percentage>",
        "<unicode-range-token>",
        "<urange>",
        "<opentype-tag>",
        "<feature-tag-value>",
        "<family-name>",
        "<font-family-name>",
        "<generic-family>",
        "<generic-font-family>",
        "<font-src>",
        "<font-src-list>",
        "<font-format>",
        "<font-tech>",
        "<hex-color>",
        "<declaration-value>",
        "<any-value>",
        "<ident-token>",
        "<function-token>",
        "<at-keyword-token>",
        "<hash-token>",
        "<string-token>",
        "<delim-token>",
        "<number-token>",
        "<percentage-token>",
        "<dimension-token>",
        "<whitespace-token>",
        "<colon-token>",
        "<semicolon-token>",
        "<comma-token>",
        "<[-token>",
        "<]-token>",
        "<(-token>",
        "<)-token>",
        "<{-token>",
        "<}-token>",
        "<an+b>",
        "<a-n-plus-b>",
        "<signless-integer>",
        "<signed-integer>",
        "<n-dimension>",
        "<ndash-dimension>",
        "<ndashdigit-dimension>",
        "<ndashdigit-ident>",
        "<dashndashdigit-ident>",
        "<declaration-list>",
        "<rule-list>",
        "<block-contents>",
        "<qualified-rule-list>",
        "<at-rule-list>",
        "<declaration-rule-list>",
    }
)

_BOUND_VAL_RE = re.compile(
    r"^([+-]?(?:∞|infinity|\d+(?:\.\d+)?))([a-zA-Z%]*)$", re.IGNORECASE
)


def _parse_bound(raw: str) -> tuple[float | None, str]:
    s = raw.strip()
    if s in ("-∞", "-infinity", "-inf"):
        return None, ""
    if s in ("∞", "+∞", "infinity", "+infinity", "inf", "+inf"):
        return None, ""
    m = _BOUND_VAL_RE.match(s)
    if not m:
        return None, ""
    return float(m.group(1)), m.group(2)


def _tokenize_vds(syntax: str) -> list[tuple[str, str]]:
    """Tokenize a W3C Value Definition Syntax string."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    n = len(syntax)

    while pos < n:
        ch = syntax[pos]
        if ch.isspace():
            pos += 1
            continue

        # Multi-character combinators
        if syntax[pos : pos + 2] == "||":
            tokens.append(("||", "||"))
            pos += 2
            continue
        if syntax[pos : pos + 2] == "&&":
            tokens.append(("&&", "&&"))
            pos += 2
            continue
        if ch == "|":
            tokens.append(("|", "|"))
            pos += 1
            continue

        # Quoted literal token: '+' or '<' or ','
        if ch == "'":
            end = syntax.find("'", pos + 1)
            if end != -1:
                lit = syntax[pos + 1 : end]
                tokens.append(("LITERAL", lit))
                pos = end + 1
                continue

        # Non-terminal `<...>`
        if ch == "<":
            # Find matching `>` (accounting for `[min,max]` or `'<prop>'`)
            end = syntax.find(">", pos + 1)
            if end != -1:
                inner = syntax[pos : end + 1]
                if inner == "<function-token>":
                    tokens.append(("FUNC_START", "<function-token>"))
                else:
                    tokens.append(("NONTERM", inner))
                pos = end + 1
                continue

        # Grouping brackets `[` and `]`
        if ch == "[":
            tokens.append(("[", "["))
            pos += 1
            continue
        if ch == "]":
            tokens.append(("]", "]"))
            pos += 1
            continue

        # Braces `{A,B}` range multiplier vs literal `{` / `}`
        if ch == "{":
            end = syntax.find("}", pos + 1)
            if end != -1 and re.match(
                r"^\{\s*\d+\s*(?:,\s*(?:\d+|∞|inf|infinity)?\s*)?\}$",
                syntax[pos : end + 1],
                re.IGNORECASE,
            ):
                tokens.append(("RANGE_MULT", syntax[pos : end + 1]))
                pos = end + 1
                continue
            tokens.append(("LITERAL", "{"))
            pos += 1
            continue
        if ch == "}":
            tokens.append(("LITERAL", "}"))
            pos += 1
            continue

        # Multipliers and `!`
        if ch in ("?", "*", "+", "#", "!"):
            tokens.append(("MULT_CHAR", ch))
            pos += 1
            continue

        # Punctuation literals inside grammar (`,`, `/`, `:`, `;`, `(`, `)`)
        if ch in (",", "/", ":", ";", "(", ")", "="):
            tokens.append(("LITERAL", ch))
            pos += 1
            continue

        # Keyword or function start `fn(`
        start = pos
        while pos < n and not syntax[pos].isspace() and syntax[pos] not in (
            "|",
            "&",
            "[",
            "]",
            "{",
            "}",
            "?",
            "*",
            "+",
            "#",
            "!",
            ",",
            "/",
            ":",
            ";",
            "(",
            ")",
            "<",
            ">",
            "'",
        ):
            # Allow `+` or `-` inside keywords like `<an+b>` or `-webkit-` or `n-`
            pos += 1
        if pos == start:
            tokens.append(("LITERAL", syntax[pos]))
            pos += 1
            continue
        word = syntax[start:pos]
        if pos < n and syntax[pos] == "(":
            pos += 1
            tokens.append(("FUNC_START", word))
        else:
            tokens.append(("KEYWORD", word))

    return tokens


class _VdsParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_expr(self) -> ValueNode:
        return self.parse_exclusive_or()

    def parse_exclusive_or(self) -> ValueNode:
        items = [self.parse_any_order()]
        while (tok := self.peek()) is not None and tok[0] == "|":
            self.consume()
            items.append(self.parse_any_order())
        if len(items) == 1:
            return items[0]
        return ExclusiveOrNode(tuple(items))

    def parse_any_order(self) -> ValueNode:
        items = [self.parse_all_of()]
        while (tok := self.peek()) is not None and tok[0] == "||":
            self.consume()
            items.append(self.parse_all_of())
        if len(items) == 1:
            return items[0]
        return AnyOrderNode(tuple(items))

    def parse_all_of(self) -> ValueNode:
        items = [self.parse_juxtapose()]
        while (tok := self.peek()) is not None and tok[0] == "&&":
            self.consume()
            items.append(self.parse_juxtapose())
        if len(items) == 1:
            return items[0]
        return AllOfNode(tuple(items))

    def parse_juxtapose(self) -> ValueNode:
        items: list[ValueNode] = []
        while (tok := self.peek()) is not None:
            if tok[0] in ("|", "||", "&&", "]"):
                break
            if tok[0] == "LITERAL" and tok[1] == ")":
                break
            items.append(self.parse_multiplied())
        if not items:
            return KeywordNode("")
        if len(items) == 1:
            return items[0]
        return JuxtaposeNode(tuple(items))

    def parse_multiplied(self) -> ValueNode:
        node = self.parse_primary()
        while (tok := self.peek()) is not None and tok[0] in ("MULT_CHAR", "RANGE_MULT"):
            kind, val = self.consume()
            if kind == "MULT_CHAR":
                if val == "!":
                    node = NonEmptyGroupNode(node)
                elif val == "?":
                    node = MultiplierNode(node, 0, 1, comma_separated=False)
                elif val == "*":
                    node = MultiplierNode(node, 0, None, comma_separated=False)
                elif val == "+":
                    node = MultiplierNode(node, 1, None, comma_separated=False)
                elif val == "#":
                    # Check if followed by {A,B} or ?
                    nxt = self.peek()
                    if nxt is not None and nxt[0] == "RANGE_MULT":
                        _, r_str = self.consume()
                        lo, hi = _parse_range_mult(r_str)
                        node = MultiplierNode(node, lo, hi, comma_separated=True)
                    elif nxt is not None and nxt == ("MULT_CHAR", "?"):
                        self.consume()
                        node = MultiplierNode(node, 0, None, comma_separated=True)
                    else:
                        node = MultiplierNode(node, 1, None, comma_separated=True)
            elif kind == "RANGE_MULT":
                lo, hi = _parse_range_mult(val)
                node = MultiplierNode(node, lo, hi, comma_separated=False)
        return node

    def parse_primary(self) -> ValueNode:
        tok = self.peek()
        if tok is None:
            return KeywordNode("")
        kind, val = self.consume()

        if kind == "[":
            inner = self.parse_expr()
            if self.peek() == ("]", "]"):
                self.consume()
            return inner

        if kind == "FUNC_START":
            if self.peek() == ("LITERAL", ")"):
                self.consume()
                return FunctionNode(val, None)
            inner = self.parse_expr()
            if self.peek() == ("LITERAL", ")"):
                self.consume()
            return FunctionNode(val, inner)

        if kind == "LITERAL" and val == "(":
            if self.peek() == ("LITERAL", ")"):
                self.consume()
                return JuxtaposeNode((DelimNode("("), DelimNode(")")))
            inner = self.parse_expr()
            if self.peek() == ("LITERAL", ")"):
                self.consume()
            return JuxtaposeNode((DelimNode("("), inner, DelimNode(")")))

        if kind == "NONTERM":
            return _parse_nonterminal_token(val)

        if kind == "LITERAL":
            return DelimNode(val)

        if kind == "KEYWORD":
            return KeywordNode(val)

        return KeywordNode(val)


def _parse_range_mult(r_str: str) -> tuple[int, int | None]:
    inner = r_str[1:-1].strip()
    if "," not in inner:
        n = int(inner)
        return n, n
    left, right = inner.split(",", 1)
    lo = int(left.strip())
    right_s = right.strip()
    hi = None if right_s in ("", "∞", "inf", "infinity") else int(right_s)
    return lo, hi


def _parse_nonterminal_token(raw: str) -> ValueNode:
    inner = raw[1:-1].strip()
    min_val: float | None = None
    max_val: float | None = None
    min_unit = ""
    max_unit = ""

    if "[" in inner and inner.endswith("]"):
        b_idx = inner.index("[")
        bracket = inner[b_idx + 1 : -1]
        inner = inner[:b_idx].strip()
        if "," in bracket:
            lo_s, hi_s = bracket.split(",", 1)
            min_val, min_unit = _parse_bound(lo_s)
            max_val, max_unit = _parse_bound(hi_s)

    if inner.startswith("'") and inner.endswith("'"):
        prop_name = inner[1:-1].strip()
        return RefNode(prop_name, is_property=True, min_val=min_val, max_val=max_val)

    type_name = f"<{inner}>"
    if type_name in _BUILTIN_PRIMITIVES:
        if type_name == "<signless-integer>":
            return PrimitiveNode("<integer>", min_val=0, max_val=max_val)
        if type_name == "<signed-integer>":
            return PrimitiveNode("<integer>", min_val=min_val, max_val=max_val)
        return PrimitiveNode(
            type_name,
            min_val=min_val,
            max_val=max_val,
            min_unit=min_unit,
            max_unit=max_unit,
        )

    return RefNode(type_name, is_property=False, min_val=min_val, max_val=max_val)


@lru_cache(maxsize=4096)
def compile_value_syntax(syntax: str) -> ValueNode:
    """Compile a W3C Value Definition Syntax string into an immutable `ValueNode` tree."""
    tokens = _tokenize_vds(syntax)
    parser = _VdsParser(tokens)
    return parser.parse_expr()


# ---------------------------------------------------------------------------
# Token Stream & Structured Value Evaluator against `ValueNode`
# ---------------------------------------------------------------------------


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


def _is_math_function_token(tokens: Sequence[CssToken], pos: int) -> tuple[str, int] | None:
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
    if not inner_tokens:
        return None
    # Verify balanced non-empty interior without top-level semicolon or bang
    for t in inner_tokens:
        if t.kind == "semicolon":
            return None
    inner_str = format_token_sequence(inner_tokens)
    return f"{fn_name}({inner_str})", next_pos


def _contains_global_substitution(tokens: Sequence[CssToken]) -> bool:
    """Return True if `tokens` contains `var(...)`, `env(...)`, or `first-valid(...)`."""
    for tok in tokens:
        if tok.kind == "function" and tok.value.lower() in GLOBAL_SUBSTITUTION_FUNCTIONS:
            return True
    return False


def _collect_grammar_keywords(
    node: ValueNode,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    visited: set[tuple[str, bool]] | None = None,
) -> frozenset[str]:
    """Collect all explicit `KeywordNode` names in a grammar tree to exclude them from `<custom-ident>` when needed."""
    if visited is None:
        visited = set()
    if isinstance(node, KeywordNode):
        return frozenset({node.name.lower()}) if node.name else frozenset()
    if isinstance(node, (JuxtaposeNode, AllOfNode, AnyOrderNode, ExclusiveOrNode)):
        out: set[str] = set()
        for child in node.items:
            out.update(_collect_grammar_keywords(child, specs_lookup, visited))
        return frozenset(out)
    if isinstance(node, (MultiplierNode, NonEmptyGroupNode)):
        return _collect_grammar_keywords(node.child, specs_lookup, visited)
    if isinstance(node, RefNode):
        key = (node.name, node.is_property)
        if key in visited:
            return frozenset()
        visited.add(key)
        resolved = specs_lookup(node.name, node.is_property)
        if resolved is not None:
            return _collect_grammar_keywords(resolved, specs_lookup, visited)
    return frozenset()


class _TokenMatcher:
    """Backtracking matcher and CSSOM canonicalizer for `CssToken` streams against `ValueNode`."""

    def __init__(
        self,
        tokens: Sequence[CssToken],
        specs_lookup: Callable[[str, bool], ValueNode | None],
        options: SerializationOptions,
        forbidden_custom_idents: frozenset[str] = frozenset(),
        raw_text: str = "",
        allow_css_wide_in_multiword_family: bool = False,
    ) -> None:
        self.tokens = [t for t in tokens if t.kind != "whitespace"]
        self.specs_lookup = specs_lookup
        self.options = options
        self.forbidden_custom_idents = forbidden_custom_idents
        self.raw_text = raw_text
        self.allow_css_wide_in_multiword_family = allow_css_wide_in_multiword_family
        self._active_refs: set[tuple[str, bool, int]] = set()

    def match_root(self, node: ValueNode) -> str | None:
        if len(self.tokens) == 1 and self.tokens[0].kind == "ident":
            kw_low = self.tokens[0].value.lower()
            if kw_low in CSS_WIDE_KEYWORDS:
                return kw_low

        if _contains_global_substitution(self.tokens):
            validate_token_stream_safety(
                self.raw_text or format_token_sequence(self.tokens, options=self.options),
                forbid_top_level_bang=True,
                forbid_top_level_semicolon=True,
            )
            return format_token_sequence(self.tokens, options=self.options)

        for pos, rendered in self.match_node(node, 0):
            if pos == len(self.tokens) and rendered != "":
                return rendered
        return None

    def match_node(
        self,
        node: ValueNode,
        pos: int,
        min_override: float | None = None,
        max_override: float | None = None,
    ) -> list[tuple[int, str]]:
        n_toks = len(self.tokens)

        if isinstance(node, KeywordNode):
            if not node.name:
                return [(pos, "")]
            if (
                pos < n_toks
                and self.tokens[pos].kind == "ident"
                and self.tokens[pos].value.lower() == node.name.lower()
            ):
                return [(pos + 1, node.name.lower())]
            return []

        if isinstance(node, DelimNode):
            if pos >= n_toks:
                return []
            tok = self.tokens[pos]
            if node.delim == "," and tok.kind == "comma":
                return [(pos + 1, ",")]
            if node.delim == ":" and tok.kind == "colon":
                return [(pos + 1, ":")]
            if node.delim == ";" and tok.kind == "semicolon":
                return [(pos + 1, ";")]
            if node.delim in ("(", ")", "[", "]", "{", "}") and tok.kind == node.delim:
                return [(pos + 1, node.delim)]
            if tok.kind == "delim" and tok.value == node.delim:
                return [(pos + 1, node.delim)]
            return []

        if isinstance(node, PrimitiveNode):
            return self._match_primitive(node, pos, min_override, max_override)

        if isinstance(node, RefNode):
            return self._match_ref(node, pos, min_override, max_override)

        if isinstance(node, FunctionNode):
            return self._match_function(node, pos)

        if isinstance(node, NonEmptyGroupNode):
            results = self.match_node(node.child, pos, min_override, max_override)
            return [(p, s) for p, s in results if p > pos and s != ""]

        if isinstance(node, ExclusiveOrNode):
            out: list[tuple[int, str]] = []
            seen: set[tuple[int, str]] = set()
            for branch in node.items:
                for res in self.match_node(branch, pos, min_override, max_override):
                    if res not in seen:
                        seen.add(res)
                        out.append(res)
            return out

        if isinstance(node, JuxtaposeNode):
            return self._match_juxtapose(node.items, pos, min_override, max_override)

        if isinstance(node, AllOfNode):
            return self._match_unordered(
                node.items, pos, require_all=True, min_override=min_override, max_override=max_override
            )

        if isinstance(node, AnyOrderNode):
            return self._match_unordered(
                node.items, pos, require_all=False, min_override=min_override, max_override=max_override
            )

        if isinstance(node, MultiplierNode):
            return self._match_multiplier(node, pos, min_override, max_override)

        return []

    def _match_ref(
        self,
        node: RefNode,
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        eff_min = node.min_val if node.min_val is not None else min_override
        eff_max = node.max_val if node.max_val is not None else max_override

        # Special microsyntax fast-paths for font properties when referenced via `<'prop'>`
        if node.is_property and node.name == "font-style":
            res = self._match_font_style_tokens(pos, is_descriptor=False)
            if res:
                return res

        guard_key = (node.name, node.is_property, pos)
        if guard_key in self._active_refs:
            return []
        target_node = self.specs_lookup(node.name, node.is_property)
        if target_node is None:
            return []
        self._active_refs.add(guard_key)
        try:
            return self.match_node(target_node, pos, eff_min, eff_max)
        finally:
            self._active_refs.discard(guard_key)

    def _match_function(self, node: FunctionNode, pos: int) -> list[tuple[int, str]]:
        if pos >= len(self.tokens) or self.tokens[pos].kind != "function":
            return []
        tok = self.tokens[pos]
        if node.name != "<function-token>" and tok.value.lower() != node.name.lower():
            return []
        grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
        if grouped is None:
            return []
        inner_tokens, next_pos = grouped
        fn_name = serialize_ident(tok.value.lower())

        if node.arg is None:
            if not inner_tokens:
                return [(next_pos, f"{fn_name}()")]
            return []

        sub_matcher = _TokenMatcher(
            inner_tokens,
            self.specs_lookup,
            self.options,
            self.forbidden_custom_idents,
        )
        results: list[tuple[int, str]] = []
        for end_p, rendered in sub_matcher.match_node(node.arg, 0):
            if end_p == len(sub_matcher.tokens):
                results.append((next_pos, f"{fn_name}({rendered})"))
        return results

    def _match_juxtapose(
        self,
        items: Sequence[ValueNode],
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        states: list[tuple[int, list[str]]] = [(pos, [])]
        for child in items:
            next_states: list[tuple[int, list[str]]] = []
            seen: set[tuple[int, tuple[str, ...]]] = set()
            for cur_pos, parts in states:
                for nxt_pos, rendered in self.match_node(child, cur_pos, min_override, max_override):
                    new_parts = parts + ([rendered] if rendered != "" else [])
                    key = (nxt_pos, tuple(new_parts))
                    if key not in seen:
                        seen.add(key)
                        next_states.append((nxt_pos, new_parts))
            states = next_states
            if not states:
                return []

        out: list[tuple[int, str]] = []
        for end_pos, parts in states:
            joined = _join_serialized_parts(parts)
            out.append((end_pos, joined))
        return out

    def _match_unordered(
        self,
        items: Sequence[ValueNode],
        pos: int,
        *,
        require_all: bool,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        k = len(items)
        required_indices = {
            idx
            for idx, b in enumerate(items)
            if not (isinstance(b, MultiplierNode) and b.min_count == 0)
        }
        # States: (cur_pos, matched_dict {branch_idx: rendered_str})
        results: list[tuple[int, dict[int, str]]] = []
        seen_states: set[tuple[int, tuple[tuple[int, str], ...]]] = set()

        def dfs(cur_pos: int, matched: dict[int, str]) -> None:
            state_key = (cur_pos, tuple(sorted(matched.items())))
            if state_key in seen_states:
                return
            seen_states.add(state_key)

            if (require_all and required_indices.issubset(matched.keys()) and len(matched) >= 1) or (
                not require_all and len(matched) >= 1
            ):
                results.append((cur_pos, dict(matched)))
                if len(matched) == k:
                    return

            for idx, branch in enumerate(items):
                if idx in matched:
                    continue
                for nxt_pos, rendered in self.match_node(branch, cur_pos, min_override, max_override):
                    if nxt_pos > cur_pos and rendered != "":
                        matched[idx] = rendered
                        dfs(nxt_pos, matched)
                        del matched[idx]
                    elif not require_all and nxt_pos == cur_pos:
                        # Avoid zero-width infinite recursion in || / &&
                        continue

        dfs(pos, {})

        # Sort results to prefer longer token matches first
        out: list[tuple[int, str]] = []
        seen_out: set[tuple[int, str]] = set()
        for end_pos, matched in sorted(results, key=lambda x: -x[0]):
            # Assemble in canonical grammar definition order (0 .. k-1)!
            canon_parts = [matched[i] for i in range(k) if i in matched and matched[i] != ""]
            joined = _join_serialized_parts(canon_parts)
            pair = (end_pos, joined)
            if pair not in seen_out:
                seen_out.add(pair)
                out.append(pair)
        return out

    def _match_multiplier(
        self,
        node: MultiplierNode,
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        # Fast path for `?` (0 or 1)
        if not node.comma_separated and node.min_count == 0 and node.max_count == 1:
            child_matches = self.match_node(node.child, pos, min_override, max_override)
            out = [(p, s) for p, s in child_matches if p > pos]
            out.append((pos, ""))
            return out

        results: list[tuple[int, list[str]]] = []
        if node.min_count == 0:
            results.append((pos, []))

        # BFS/DFS over occurrences
        queue: list[tuple[int, list[str]]] = [(pos, [])]
        seen: set[tuple[int, tuple[str, ...]]] = set()

        while queue:
            cur_pos, acc = queue.pop()
            count = len(acc)
            if node.max_count is not None and count >= node.max_count:
                continue

            next_start = cur_pos
            if count > 0 and node.comma_separated:
                if next_start >= len(self.tokens) or self.tokens[next_start].kind != "comma":
                    continue
                next_start += 1

            for nxt_pos, rendered in self.match_node(node.child, next_start, min_override, max_override):
                if nxt_pos == next_start:
                    continue
                new_acc = acc + [rendered]
                key = (nxt_pos, tuple(new_acc))
                if key in seen:
                    continue
                seen.add(key)
                if len(new_acc) >= node.min_count:
                    results.append((nxt_pos, new_acc))
                queue.append((nxt_pos, new_acc))

        sep = ", " if node.comma_separated else " "
        out_pairs: list[tuple[int, str]] = []
        seen_pairs: set[tuple[int, str]] = set()
        for end_pos, items in sorted(results, key=lambda x: -x[0]):
            joined = sep.join(s for s in items if s != "")
            pair = (end_pos, joined)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                out_pairs.append(pair)
        return out_pairs

    def _match_font_style_tokens(self, pos: int, *, is_descriptor: bool) -> list[tuple[int, str]]:
        if pos >= len(self.tokens) or self.tokens[pos].kind != "ident":
            return []
        kw = self.tokens[pos].value.lower()
        if kw in ("normal", "italic", "left", "right"):
            return [(pos + 1, kw)]
        if is_descriptor and kw == "auto":
            return [(pos + 1, "auto")]
        if kw == "oblique":
            results: list[tuple[int, str]] = [(pos + 1, "oblique")]
            angle_prim = PrimitiveNode("<angle>", min_val=-90.0, max_val=90.0, min_unit="deg", max_unit="deg")
            for p1, a1 in self._match_primitive(angle_prim, pos + 1, -90.0, 90.0):
                if (
                    not is_descriptor
                    and self.options.normalize_zero_oblique
                    and a1 == "0deg"
                ):
                    results.insert(0, (p1, "normal"))
                else:
                    results.insert(0, (p1, f"oblique {a1}"))
                if is_descriptor:
                    for p2, a2 in self._match_primitive(angle_prim, p1, -90.0, 90.0):
                        results.insert(0, (p2, f"oblique {a1} {a2}"))
            return results
        return []

    def _match_primitive(
        self,
        node: PrimitiveNode,
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        kind = node.kind
        eff_min = node.min_val if node.min_val is not None else min_override
        eff_max = node.max_val if node.max_val is not None else max_override

        if kind in ("<declaration-value>", "<any-value>"):
            if pos >= len(self.tokens):
                return []
            rest = self.tokens[pos:]
            return [(len(self.tokens), format_token_sequence(rest, options=self.options))]

        if kind in ("<unicode-range-token>", "<urange>"):
            # `unicode-range` uses raw_text parsing via `format_urange` at the root
            if pos == 0 and self.raw_text:
                try:
                    return [(len(self.tokens), format_urange(self.raw_text, options=self.options))]
                except StixisValidationError:
                    return []
            return []

        if pos >= len(self.tokens):
            return []

        tok = self.tokens[pos]

        # Numeric & dimension math function support (`calc(...)`, `min(...)`, `max(...)`, `clamp(...)`, etc.)
        if kind in (
            "<number>",
            "<integer>",
            "<percentage>",
            "<dimension>",
            "<length>",
            "<angle>",
            "<time>",
            "<frequency>",
            "<resolution>",
            "<flex>",
            "<length-percentage>",
            "<angle-percentage>",
            "<time-percentage>",
            "<frequency-percentage>",
        ):
            math_res = _is_math_function_token(self.tokens, pos)
            if math_res is not None:
                rendered_math, next_pos = math_res
                return [(next_pos, rendered_math)]

        if kind == "<zero>":
            if tok.kind == "number" and tok.num == 0:
                return [(pos + 1, "0")]
            return []

        if kind == "<integer>":
            if (
                tok.kind == "number"
                and tok.is_integer
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                return [(pos + 1, str(int(tok.num)))]
            return []

        if kind == "<number>":
            if (
                tok.kind == "number"
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, serialize_number(val))]
            return []

        if kind == "<percentage>":
            if (
                tok.kind == "percentage"
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, f"{serialize_number(val)}%")]
            return []

        if kind in ("<length>", "<angle>", "<time>", "<frequency>", "<resolution>", "<flex>", "<decibel>", "<semitones>"):
            allowed_units = UNITS_BY_TYPE.get(kind, frozenset())
            lower_map = LOWER_TO_CANONICAL_UNIT.get(kind, {})
            if kind == "<length>" and tok.kind == "number" and tok.num == 0:
                if (eff_min is None or 0 >= eff_min) and (eff_max is None or 0 <= eff_max):
                    return [(pos + 1, "0px")]
                return []
            if tok.kind == "dimension" and tok.num is not None and math.isfinite(tok.num):
                canon_u = lower_map.get(tok.unit.lower())
                if canon_u is None or canon_u not in allowed_units:
                    return []
                check_val = tok.num
                if kind == "<angle>" and (node.min_unit or node.max_unit):
                    check_val = angle_to_deg(tok.num, canon_u)
                if eff_min is not None and check_val < eff_min - 1e-9:
                    return []
                if eff_max is not None and check_val > eff_max + 1e-9:
                    return []
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, f"{serialize_number(val)}{canon_u}")]
            return []

        if kind == "<dimension>":
            if (
                tok.kind == "dimension"
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, f"{serialize_number(val)}{tok.unit}")]
            return []

        if kind in ("<length-percentage>", "<angle-percentage>", "<time-percentage>", "<frequency-percentage>"):
            base_kind = f"<{kind[1:].split('-')[0]}>"
            dim_matches = self._match_primitive(
                PrimitiveNode(base_kind, eff_min, eff_max, node.min_unit, node.max_unit),
                pos,
                eff_min,
                eff_max,
            )
            pct_matches = self._match_primitive(
                PrimitiveNode("<percentage>", eff_min, eff_max),
                pos,
                eff_min,
                eff_max,
            )
            return dim_matches + pct_matches

        if kind in ("<string>", "<string-token>"):
            if tok.kind == "string":
                return [(pos + 1, serialize_string(tok.value, options=self.options))]
            return []

        if kind == "<opentype-tag>":
            if tok.kind == "string":
                try:
                    return [(pos + 1, serialize_opentype_tag(tok.value, options=self.options))]
                except StixisValidationError:
                    return []
            return []

        if kind == "<feature-tag-value>":
            if tok.kind == "string":
                try:
                    tag_s = serialize_opentype_tag(tok.value, options=self.options)
                except StixisValidationError:
                    return []
                results: list[tuple[int, str]] = [(pos + 1, tag_s)]
                if pos + 1 < len(self.tokens):
                    nxt = self.tokens[pos + 1]
                    if nxt.kind == "ident":
                        low = nxt.value.lower()
                        if low == "on":
                            results.insert(0, (pos + 2, tag_s))
                        elif low == "off":
                            results.insert(0, (pos + 2, f"{tag_s} 0"))
                    elif nxt.kind == "number" and nxt.is_integer and nxt.num is not None and nxt.num >= 0:
                        iv = int(nxt.num)
                        if iv == 1:
                            results.insert(0, (pos + 2, tag_s))
                        else:
                            results.insert(0, (pos + 2, f"{tag_s} {iv}"))
                return results
            return []

        if kind in ("<url>", "<url-token>"):
            if tok.kind == "url":
                return [(pos + 1, serialize_url(tok.value, options=self.options))]
            if tok.kind == "function" and tok.value.lower() in ("url", "src"):
                grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
                if grouped is not None:
                    inner_toks, next_pos = grouped
                    if len(inner_toks) == 1 and inner_toks[0].kind == "string":
                        fn_low = tok.value.lower()
                        str_s = serialize_string(inner_toks[0].value, options=self.options)
                        return [(next_pos, f"{fn_low}({str_s})")]
            return []

        if kind in ("<ident>", "<ident-token>"):
            if tok.kind == "ident":
                return [(pos + 1, serialize_ident(tok.value))]
            return []

        if kind == "<dashed-ident>":
            if tok.kind == "ident" and tok.value.startswith("--") and len(tok.value) >= 2:
                return [(pos + 1, serialize_dashed_ident(tok.value))]
            return []

        if kind == "<custom-ident>":
            if tok.kind == "ident":
                low = tok.value.lower()
                if low in CSS_WIDE_KEYWORDS or low in self.forbidden_custom_idents:
                    return []
                return [(pos + 1, serialize_ident(tok.value))]
            return []

        if kind in ("<generic-family>", "<generic-font-family>"):
            if tok.kind == "ident":
                low = tok.value.lower()
                if low in GENERIC_FAMILY_KEYWORDS or (
                    low.startswith("-webkit-") and low[8:] in GENERIC_FAMILY_KEYWORDS
                ):
                    return [(pos + 1, low)]
            elif tok.kind == "function" and tok.value.lower() == "generic":
                grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
                if grouped is not None:
                    inner_toks, next_pos = grouped
                    if len(inner_toks) == 1 and inner_toks[0].kind == "ident":
                        arg_low = inner_toks[0].value.lower()
                        if arg_low in GENERIC_SCRIPT_KEYWORDS:
                            return [(next_pos, f"generic({arg_low})")]
            return []

        if kind in ("<family-name>", "<font-family-name>"):
            if tok.kind == "string":
                return [
                    (
                        pos + 1,
                        serialize_family_name(
                            tok.value, options=self.options, is_quoted_token=True
                        ),
                    )
                ]
            if tok.kind == "ident":
                # Consume one or more consecutive <custom-ident> tokens
                idents: list[str] = []
                cur = pos
                while cur < len(self.tokens) and self.tokens[cur].kind == "ident":
                    cand = self.tokens[cur].value
                    idents.append(cand)
                    cur += 1
                if not idents:
                    return []
                # Single ident cannot be a CSS-wide keyword or a generic-family keyword
                if len(idents) == 1:
                    low = idents[0].lower()
                    if low in CSS_WIDE_KEYWORDS or low in GENERIC_FAMILY_KEYWORDS:
                        return []
                else:
                    # In a multi-word unquoted `<family-name>` (`<custom-ident>+`),
                    # CSS-wide keywords are never valid `<custom-ident>`s (except inside `local(default A)`),
                    # and `<generic-family>` keywords are not valid inside `font-family`.
                    if any(w.lower() in GENERIC_FAMILY_KEYWORDS for w in idents):
                        return []
                    if not self.allow_css_wide_in_multiword_family and any(
                        w.lower() in CSS_WIDE_KEYWORDS for w in idents
                    ):
                        return []
                joined_name = " ".join(idents)
                if self.options.always_quote_family_name:
                    return [(cur, serialize_string(joined_name, options=self.options))]
                return [(cur, " ".join(serialize_ident(w) for w in idents))]
            return []

        if kind == "<font-format>":
            if tok.kind == "string":
                try:
                    s = serialize_font_format(
                        tok.value, is_quoted_token=True, options=self.options
                    )
                    # Strip outer `format(` and `)`
                    return [(pos + 1, s[7:-1])]
                except StixisValidationError:
                    return []
            if tok.kind == "ident":
                try:
                    s = serialize_font_format(
                        Keyword(tok.value), is_quoted_token=False, options=self.options
                    )
                    return [(pos + 1, s[7:-1])]
                except StixisValidationError:
                    return []
            return []

        if kind == "<font-tech>":
            if tok.kind == "ident" and tok.value.lower() in FONT_TECH_KEYWORDS:
                return [(pos + 1, tok.value.lower())]
            return []

        if kind in ("<font-src>", "<font-src-list>"):
            return self._match_font_src(pos, is_list=(kind == "<font-src-list>"))

        if kind == "<hex-color>":
            if (
                tok.kind == "hash"
                and len(tok.value) in (3, 4, 6, 8)
                and all(c in "0123456789abcdefABCDEF" for c in tok.value)
            ):
                return [(pos + 1, f"#{tok.value.lower()}")]
            return []

        return []

    def _match_font_src(self, pos: int, *, is_list: bool) -> list[tuple[int, str]]:
        if not is_list:
            return self._match_single_font_src_tokens(pos)
        # Comma-separated list of `<font-src>`
        mult = MultiplierNode(PrimitiveNode("<font-src>"), 1, None, comma_separated=True)
        return self._match_multiplier(mult, pos, None, None)

    def _match_single_font_src_tokens(self, pos: int) -> list[tuple[int, str]]:
        if pos >= len(self.tokens):
            return []
        tok = self.tokens[pos]
        if tok.kind == "function" and tok.value.lower() == "local":
            grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
            if grouped is None:
                return []
            inner_toks, next_pos = grouped
            if not inner_toks:
                return []
            sub = _TokenMatcher(
                inner_toks,
                self.specs_lookup,
                self.options,
                allow_css_wide_in_multiword_family=True,
            )
            fam_matches = sub._match_primitive(PrimitiveNode("<family-name>"), 0, None, None)
            out: list[tuple[int, str]] = []
            for end_p, fam_str in fam_matches:
                if end_p == len(sub.tokens):
                    out.append((next_pos, f"local({fam_str})"))
            return out

        url_matches = self._match_primitive(PrimitiveNode("<url>"), pos, None, None)
        if not url_matches:
            return []

        results: list[tuple[int, str]] = []
        for after_url, url_str in url_matches:
            cur = after_url
            parts = [url_str]

            # Optional `format(<font-format>)`
            if cur < len(self.tokens) and self.tokens[cur].kind == "function" and self.tokens[cur].value.lower() == "format":
                grouped = _consume_balanced_group(self.tokens, cur + 1, ")")
                if grouped is None:
                    continue
                fmt_toks, after_fmt = grouped
                if len(fmt_toks) != 1:
                    continue
                sub = _TokenMatcher(fmt_toks, self.specs_lookup, self.options)
                fmt_m = sub._match_primitive(PrimitiveNode("<font-format>"), 0, None, None)
                if not fmt_m or fmt_m[0][0] != 1:
                    continue
                parts.append(f"format({fmt_m[0][1]})")
                cur = after_fmt

            # Optional `tech(<font-tech>#)`
            if cur < len(self.tokens) and self.tokens[cur].kind == "function" and self.tokens[cur].value.lower() == "tech":
                grouped = _consume_balanced_group(self.tokens, cur + 1, ")")
                if grouped is None:
                    continue
                tech_toks, after_tech = grouped
                if not tech_toks:
                    continue
                sub = _TokenMatcher(tech_toks, self.specs_lookup, self.options)
                tech_mult = MultiplierNode(PrimitiveNode("<font-tech>"), 1, None, comma_separated=True)
                tech_m = sub._match_multiplier(tech_mult, 0, None, None)
                valid_tech = [s for end_p, s in tech_m if end_p == len(sub.tokens)]
                if not valid_tech:
                    continue
                parts.append(f"tech({valid_tech[0]})")
                cur = after_tech

            results.append((cur, " ".join(parts)))
        return results


def _join_serialized_parts(parts: Sequence[str]) -> str:
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if not out:
            out.append(p)
            continue
        prev = out[-1]
        if p in (",", ":", ";", ")", "]") or prev in ("(", "["):
            out.append(p)
        else:
            out.append(" " + p)
    return "".join(out)


def _node_matches_single_value(
    node: ValueNode,
    value: Any,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None = None,
    max_override: float | None = None,
    active_refs: set[tuple[str, bool]] | None = None,
) -> str | None:
    """Try to serialize a structured Python `value` directly against `node`, returning `None` if it does not match."""
    if active_refs is None:
        active_refs = set()

    if isinstance(node, KeywordNode):
        if isinstance(value, Keyword):
            raw = value.value
        elif isinstance(value, str):
            raw = value
        else:
            return None
        if raw.strip().lower() == node.name.lower():
            return node.name.lower()
        return None

    if isinstance(node, DelimNode):
        if isinstance(value, str) and value.strip() == node.delim:
            return node.delim
        return None

    if isinstance(node, PrimitiveNode):
        return _serialize_primitive_python_value(
            node,
            value,
            specs_lookup,
            options,
            forbidden_custom_idents,
            min_override,
            max_override,
        )

    if isinstance(node, RefNode):
        eff_min = node.min_val if node.min_val is not None else min_override
        eff_max = node.max_val if node.max_val is not None else max_override
        key = (node.name, node.is_property)
        if key in active_refs:
            return None
        target_node = specs_lookup(node.name, node.is_property)
        if target_node is None:
            return None
        active_refs.add(key)
        try:
            return _node_matches_single_value(
                target_node,
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                eff_min,
                eff_max,
                active_refs,
            )
        finally:
            active_refs.discard(key)

    if isinstance(node, NonEmptyGroupNode):
        res = _node_matches_single_value(
            node.child,
            value,
            specs_lookup,
            options,
            forbidden_custom_idents,
            min_override,
            max_override,
            active_refs,
        )
        return res if res else None

    if isinstance(node, ExclusiveOrNode):
        # If `value` is a plain `str` (and not `Keyword`/`GenericFamily`) and one of the branches
        # is `<string>` or `<family-name>` / `<font-family-name>`, check whether it matches a keyword first
        # ONLY when neither `<family-name>` nor `<font-family-name>` is a branch!
        has_family_branch = any(
            isinstance(b, PrimitiveNode) and b.kind in ("<family-name>", "<font-family-name>")
            or (isinstance(b, RefNode) and b.name in ("<family-name>", "<font-family-name>"))
            for b in node.items
        )
        if isinstance(value, str) and has_family_branch:
            for branch in node.items:
                if (
                    isinstance(branch, PrimitiveNode)
                    and branch.kind in ("<family-name>", "<font-family-name>")
                ) or (
                    isinstance(branch, RefNode)
                    and branch.name in ("<family-name>", "<font-family-name>")
                ):
                    res = _node_matches_single_value(
                        branch,
                        value,
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    if res is not None:
                        return res

        for branch in node.items:
            res = _node_matches_single_value(
                branch,
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                min_override,
                max_override,
                active_refs,
            )
            if res is not None:
                return res
        return None

    if isinstance(node, MultiplierNode):
        # A single item is valid if min_count <= 1
        if node.min_count <= 1:
            single = _node_matches_single_value(
                node.child,
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                min_override,
                max_override,
                active_refs,
            )
            if single is not None:
                return single
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            count = len(value)
            if count < node.min_count:
                return None
            if node.max_count is not None and count > node.max_count:
                return None
            rendered_items: list[str] = []
            for elem in value:
                r = _node_matches_single_value(
                    node.child,
                    elem,
                    specs_lookup,
                    options,
                    forbidden_custom_idents,
                    min_override,
                    max_override,
                    active_refs,
                )
                if r is None:
                    return None
                rendered_items.append(r)
            sep = ", " if node.comma_separated else " "
            return sep.join(rendered_items)
        return None

    if isinstance(node, JuxtaposeNode):
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            # Try matching sequence items via token stream or positional matching
            if len(value) == len(node.items):
                parts: list[str] = []
                ok = True
                for sub_node, sub_val in zip(node.items, value, strict=True):
                    r = _node_matches_single_value(
                        sub_node,
                        sub_val,
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    if r is None:
                        ok = False
                        break
                    parts.append(r)
                if ok:
                    return _join_serialized_parts(parts)
            # Or convert sequence elements to CSS tokens and match through _TokenMatcher
            return _match_sequence_via_tokens(
                node, value, specs_lookup, options, forbidden_custom_idents
            )
        return None

    if isinstance(node, (AllOfNode, AnyOrderNode)):
        require_all = isinstance(node, AllOfNode)
        if isinstance(value, Mapping):
            # Match mapping keys/values or values against branches in canonical order
            items_to_match = [v for k, v in value.items() if v is not None and v is not False]
            return _match_unordered_python_items(
                node.items,
                items_to_match,
                require_all=require_all,
                specs_lookup=specs_lookup,
                options=options,
                forbidden_custom_idents=forbidden_custom_idents,
                min_override=min_override,
                max_override=max_override,
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            res = _match_unordered_python_items(
                node.items,
                list(value),
                require_all=require_all,
                specs_lookup=specs_lookup,
                options=options,
                forbidden_custom_idents=forbidden_custom_idents,
                min_override=min_override,
                max_override=max_override,
            )
            if res is not None:
                return res
            return _match_sequence_via_tokens(
                node, value, specs_lookup, options, forbidden_custom_idents
            )
        if not require_all:
            # Single item matching one branch of `A || B`
            for branch in node.items:
                r = _node_matches_single_value(
                    branch,
                    value,
                    specs_lookup,
                    options,
                    forbidden_custom_idents,
                    min_override,
                    max_override,
                    active_refs,
                )
                if r is not None:
                    return r
        return None

    return None


def _match_unordered_python_items(
    branches: Sequence[ValueNode],
    values: Sequence[Any],
    *,
    require_all: bool,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None,
    max_override: float | None,
) -> str | None:
    if not values:
        return None
    k = len(branches)
    if len(values) > k:
        return None
    if require_all and len(values) != k:
        return None

    # Find assignment of `values` to distinct branch indices `0..k-1`
    matched: dict[int, str] = {}

    def backtrack(v_idx: int) -> bool:
        if v_idx == len(values):
            return True
        val = values[v_idx]
        for b_idx, branch in enumerate(branches):
            if b_idx in matched:
                continue
            r = _node_matches_single_value(
                branch,
                val,
                specs_lookup,
                options,
                forbidden_custom_idents,
                min_override,
                max_override,
            )
            if r is not None:
                matched[b_idx] = r
                if backtrack(v_idx + 1):
                    return True
                del matched[b_idx]
        return False

    if not backtrack(0):
        return None
    return _join_serialized_parts([matched[i] for i in range(k) if i in matched])


def _value_item_to_css_fragment(item: Any, options: SerializationOptions) -> str | None:
    if isinstance(item, Template):
        return render_css_template(item, options=options)
    if isinstance(item, (Keyword, GenericFamily, Url, LocalFont, Dimension, Percentage)):
        return str(item)
    if not isinstance(item, bool) and isinstance(item, (int, float)):
        return serialize_number(item)
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and not isinstance(item[0], bool)
        and isinstance(item[0], (int, float))
        and isinstance(item[1], str)
    ):
        return f"{serialize_number(item[0])}{item[1]}"
    if isinstance(item, str):
        return item
    return None


def _match_sequence_via_tokens(
    node: ValueNode,
    seq: Sequence[Any],
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
) -> str | None:
    fragments: list[str] = []
    for elem in seq:
        frag = _value_item_to_css_fragment(elem, options)
        if frag is None:
            return None
        fragments.append(frag)
    joined = " ".join(fragments)
    try:
        tokens = tokenize_css(joined)
    except StixisSyntaxError:
        return None
    matcher = _TokenMatcher(tokens, specs_lookup, options, forbidden_custom_idents, raw_text=joined)
    return matcher.match_root(node)


def _serialize_primitive_python_value(
    node: PrimitiveNode,
    value: Any,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None,
    max_override: float | None,
) -> str | None:
    kind = node.kind
    eff_min = node.min_val if node.min_val is not None else min_override
    eff_max = node.max_val if node.max_val is not None else max_override

    try:
        if kind == "<zero>":
            if not isinstance(value, bool) and isinstance(value, (int, float)) and value == 0:
                return "0"
            return None

        if kind == "<integer>":
            return serialize_integer(value, min_val=eff_min, max_val=eff_max)

        if kind == "<number>":
            return serialize_number(value, min_val=eff_min, max_val=eff_max)

        if kind == "<percentage>":
            return serialize_percentage(value, min_val=eff_min, max_val=eff_max)

        if kind in (
            "<length>",
            "<angle>",
            "<time>",
            "<frequency>",
            "<resolution>",
            "<flex>",
            "<decibel>",
            "<semitones>",
        ):
            allowed = UNITS_BY_TYPE.get(kind, frozenset())
            lower_map = LOWER_TO_CANONICAL_UNIT.get(kind, {})
            if kind == "<angle>" and (node.min_unit or node.max_unit):
                if isinstance(value, Dimension):
                    deg = angle_to_deg(float(value.value), value.unit)
                elif (
                    isinstance(value, tuple)
                    and len(value) == 2
                    and not isinstance(value[0], bool)
                    and isinstance(value[0], (int, float))
                    and isinstance(value[1], str)
                ):
                    deg = angle_to_deg(float(value[0]), value[1])
                else:
                    return None
                if eff_min is not None and deg < eff_min - 1e-9:
                    return None
                if eff_max is not None and deg > eff_max + 1e-9:
                    return None
                return serialize_dimension(
                    value,
                    allowed_units=allowed,
                    lower_to_canonical=lower_map,
                    allow_unitless_zero=False,
                )
            return serialize_dimension(
                value,
                allowed_units=allowed,
                lower_to_canonical=lower_map,
                allow_unitless_zero=(kind == "<length>"),
                min_val=eff_min,
                max_val=eff_max,
            )

        if kind in (
            "<length-percentage>",
            "<angle-percentage>",
            "<time-percentage>",
            "<frequency-percentage>",
        ):
            base_kind = f"<{kind[1:].split('-')[0]}>"
            if isinstance(value, Percentage) or (
                isinstance(value, tuple) and len(value) == 2 and value[1] == "%"
            ):
                return serialize_percentage(value, min_val=eff_min, max_val=eff_max)
            return _serialize_primitive_python_value(
                PrimitiveNode(base_kind, eff_min, eff_max, node.min_unit, node.max_unit),
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                eff_min,
                eff_max,
            )

        if kind in ("<string>", "<string-token>"):
            if isinstance(value, str):
                return serialize_string(value, options=options)
            return None

        if kind == "<opentype-tag>":
            if isinstance(value, str):
                return serialize_opentype_tag(value, options=options)
            return None

        if kind in ("<url>", "<url-token>"):
            if isinstance(value, Url):
                return serialize_url(value, options=options)
            return None

        if kind in ("<ident>", "<ident-token>"):
            if isinstance(value, Keyword):
                return serialize_ident(value.value)
            if isinstance(value, str):
                return serialize_ident(value)
            return None

        if kind == "<custom-ident>":
            raw = value.value if isinstance(value, Keyword) else value
            if isinstance(raw, str):
                return serialize_custom_ident(raw, forbidden_keywords=forbidden_custom_idents)
            return None

        if kind == "<dashed-ident>":
            raw = value.value if isinstance(value, Keyword) else value
            if isinstance(raw, str):
                return serialize_dashed_ident(raw)
            return None

        if kind in ("<family-name>", "<font-family-name>"):
            if isinstance(value, str):
                return serialize_family_name(value, options=options, is_quoted_token=True)
            return None

        if kind in ("<generic-family>", "<generic-font-family>"):
            if isinstance(value, (GenericFamily, Keyword)):
                return serialize_generic_family(value)
            return None

        if kind in ("<unicode-range-token>", "<urange>"):
            return format_urange(value, options=options)

        if kind == "<font-src>":
            return serialize_single_font_src(value, options=options)

        if kind == "<font-src-list>":
            return serialize_font_src_list(value, options=options)

        if kind == "<font-format>":
            s = serialize_font_format(value, options=options)
            return s[7:-1]

        if kind == "<font-tech>":
            s = serialize_font_tech(value)
            return s[5:-1]

        if kind in ("<declaration-value>", "<any-value>"):
            if isinstance(value, Template):
                rendered = render_css_template(value, options=options)
                validate_token_stream_safety(
                    rendered,
                    forbid_top_level_semicolon=(kind == "<declaration-value>"),
                    forbid_top_level_bang=(kind == "<declaration-value>"),
                    target=kind,
                )
                return rendered.strip()
            if isinstance(value, str):
                validate_token_stream_safety(
                    value,
                    forbid_top_level_semicolon=(kind == "<declaration-value>"),
                    forbid_top_level_bang=(kind == "<declaration-value>"),
                    target=kind,
                )
                return value.strip()
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                return serialize_number(value)
            if isinstance(value, (Dimension, Percentage, Keyword, Url)):
                return str(value)
            return None

    except StixisValidationError:
        return None

    return None


def evaluate_canonicalize(
    target: str,
    syntax: str,
    node: ValueNode,
    css_text: str,
    *,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Validate raw `css_text` against `node` and return its canonical CSSOM string."""
    if not isinstance(css_text, str) or not css_text.strip():
        raise StixisValidationError(target, syntax, css_text)

    # Dedicated microsyntax fast-paths for exact WPT conformance
    if target in ("unicode-range", "<urange>", "<unicode-range-token>"):
        return format_urange(css_text, options=options)

    tokens = validate_token_stream_safety(
        css_text,
        forbid_top_level_semicolon=True,
        forbid_top_level_bang=True,
        forbid_top_level_braces=True,
        target=target,
    )
    non_ws = [t for t in tokens if t.kind != "whitespace"]

    if target == "font-style":
        matcher = _TokenMatcher(non_ws, specs_lookup, options, raw_text=css_text)
        if len(non_ws) == 1 and non_ws[0].kind == "ident" and non_ws[0].value.lower() in CSS_WIDE_KEYWORDS and at_rule is None:
            return non_ws[0].value.lower()
        if _contains_global_substitution(non_ws) and at_rule is None:
            return format_token_sequence(non_ws, options=options)
        matches = matcher._match_font_style_tokens(0, is_descriptor=(at_rule == "@font-face"))
        for end_p, rendered in matches:
            if end_p == len(non_ws):
                return rendered
        raise StixisValidationError(target, syntax, css_text)

    if target == "font-language-override":
        if len(non_ws) == 1 and non_ws[0].kind == "ident":
            kw_low = non_ws[0].value.lower()
            if kw_low in CSS_WIDE_KEYWORDS and at_rule is None:
                return kw_low
            return serialize_font_language_override(
                kw_low, options=options, target=target, is_keyword_token=True
            )
        if len(non_ws) == 1 and non_ws[0].kind == "string":
            return serialize_font_language_override(
                non_ws[0].value, options=options, target=target, is_keyword_token=False
            )
        raise StixisValidationError(target, syntax, css_text)

    forbidden_idents = _collect_grammar_keywords(node, specs_lookup)
    matcher = _TokenMatcher(
        non_ws,
        specs_lookup,
        options,
        forbidden_custom_idents=forbidden_idents,
        raw_text=css_text,
    )
    if (
        at_rule is not None
        and len(non_ws) == 1
        and non_ws[0].kind == "ident"
        and non_ws[0].value.lower() in CSS_WIDE_KEYWORDS
    ):
        # CSS-wide keywords (`initial`, `inherit`, `unset`, `revert`, `default`) are NOT valid on @font-face descriptors
        raise StixisValidationError(target, syntax, css_text)

    res = matcher.match_root(node)
    if res is None:
        raise StixisValidationError(target, syntax, css_text)
    if target == "font-size-adjust" and res.startswith("ex-height "):
        res = res[len("ex-height ") :]
    return res


def evaluate_serialize_value(
    target: str,
    syntax: str,
    node: ValueNode,
    value: Any,
    *,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Validate and serialize a structured Python `value` (or PEP 750 `Template`) against `node`."""
    if isinstance(value, Template):
        rendered_tpl = render_css_template(value, options=options)
        return evaluate_canonicalize(
            target,
            syntax,
            node,
            rendered_tpl,
            specs_lookup=specs_lookup,
            at_rule=at_rule,
            options=options,
        )

    is_font_face = at_rule == "@font-face"

    # Dedicated microsyntax handlers for structured Python inputs
    if target in ("unicode-range", "<urange>", "<unicode-range-token>"):
        return format_urange(value, options=options)

    if target in ("font-family", "<family-name>", "<font-family-name>"):
        if target in ("<family-name>", "<font-family-name>") or is_font_face:
            if isinstance(value, str):
                return serialize_family_name(value, options=options, target=target, is_quoted_token=True)
            raise StixisValidationError(target, syntax, value)
        return serialize_font_family_list(value, options=options, target=target)

    if target in ("<generic-family>", "<generic-font-family>"):
        return serialize_generic_family(value, target=target)

    if target == "font-feature-settings" or target == "<feature-tag-value>":
        if isinstance(value, str) and ("," in value or " " in value or value.startswith(('"', "'"))):
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_font_feature_settings(value, options=options, target=target)

    if target == "font-variation-settings":
        if isinstance(value, str) and value.strip().lower() != "normal":
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_font_variation_settings(value, options=options, target=target)

    if target == "<opentype-tag>":
        return serialize_opentype_tag(value, options=options, target=target)

    if target == "font-language-override":
        if isinstance(value, str) and value.strip().startswith(('"', "'")):
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        if isinstance(value, str) and value.strip().lower() == "normal":
            return "normal"
        return serialize_font_language_override(value, options=options, target=target)

    if (target == "src" and is_font_face) or target == "<font-src-list>":
        if isinstance(value, str) and (
            value.strip().lower().startswith(("url(", "local(")) or "," in value
        ):
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_font_src_list(value, options=options, target=target)

    if target == "<font-src>":
        if isinstance(value, str) and value.strip().lower().startswith(("url(", "local(")):
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_single_font_src(value, options=options, target=target)

    if target == "font-weight":
        if isinstance(value, str) and (" " in value.strip() or value.strip().lower().startswith("calc(")):
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_font_weight(value, is_descriptor=is_font_face, target=target)

    if target in ("font-width", "font-stretch"):
        if isinstance(value, str) and (" " in value.strip() or value.strip().lower().startswith("calc(")):
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_font_width(value, is_descriptor=is_font_face, target=target)

    if target == "font-style":
        if isinstance(value, str) and "calc(" in value.lower():
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_font_style(
            value, is_descriptor=is_font_face, options=options, target=target
        )

    if is_font_face and target in ("ascent-override", "descent-override", "line-gap-override"):
        if isinstance(value, str) and " " in value.strip():
            return evaluate_canonicalize(
                target, syntax, node, value, specs_lookup=specs_lookup, at_rule=at_rule, options=options
            )
        return serialize_metric_override(value, allow_from_font=False, min_val=0.0, target=target)

    if is_font_face and target in ("superscript-size-override", "subscript-size-override"):
        return serialize_metric_override(value, allow_from_font=True, min_val=0.0, target=target)

    if is_font_face and target in ("superscript-position-override", "subscript-position-override"):
        return serialize_metric_override(value, allow_from_font=True, min_val=None, target=target)

    # General ValueNode evaluation
    if at_rule is None and isinstance(value, (str, Keyword)):
        raw_kw = value.value if isinstance(value, Keyword) else value
        if raw_kw.strip().lower() in CSS_WIDE_KEYWORDS:
            return raw_kw.strip().lower()

    forbidden_idents = _collect_grammar_keywords(node, specs_lookup)
    direct = _node_matches_single_value(
        node, value, specs_lookup, options, forbidden_idents
    )
    if direct is not None:
        return direct

    # Fallback: if `value` is a CSS string expression (e.g. `"10px 20px"` or `"calc(100% - 20px)"`),
    # canonicalize it through the token matcher!
    if isinstance(value, str):
        return evaluate_canonicalize(
            target,
            syntax,
            node,
            value,
            specs_lookup=specs_lookup,
            at_rule=at_rule,
            options=options,
        )

    raise StixisValidationError(target, syntax, value)



