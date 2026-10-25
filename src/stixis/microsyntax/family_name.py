"""`<family-name>` and `<generic-family>` serialization and quoting (`css-fonts-4` §2.1 & WPT `font-family-serialization-001.html`)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from stixis.errors import StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.primitives import (
    CSS_WIDE_KEYWORDS,
    GenericFamily,
    Keyword,
    serialize_ident,
    serialize_string,
)

GENERIC_FAMILY_KEYWORDS: frozenset[str] = frozenset(
    {
        "serif",
        "sans-serif",
        "cursive",
        "fantasy",
        "monospace",
        "system-ui",
        "math",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
    }
)

GENERIC_SCRIPT_KEYWORDS: frozenset[str] = frozenset(
    {
        "fangsong",
        "kai",
        "khmer-mul",
        "nastaliq",
    }
)

_UNQUOTED_WORD_RE = re.compile(r"^-?[a-zA-Z_\x80-\U0010ffff][a-zA-Z0-9_\-\x80-\U0010ffff]*$")


def is_valid_unquoted_family_name(name: str) -> bool:
    """Return True if `name` can be serialized as an unquoted `<custom-ident>+` sequence without ambiguity."""
    if not name or name != name.strip() or "  " in name:
        return False
    if any(ch in name for ch in ("\t", "\n", "\r", "\f")):
        return False
    words = name.split(" ")
    if not words:
        return False

    for w in words:
        if not _UNQUOTED_WORD_RE.match(w):
            return False
        if w.startswith("--"):
            return False

    if len(words) == 1:
        lower = words[0].lower()
        if lower in CSS_WIDE_KEYWORDS or lower in GENERIC_FAMILY_KEYWORDS or lower == "default":
            return False
    return True


def serialize_generic_family(
    value: str | GenericFamily | Keyword,
    *,
    allow_webkit_prefix: bool = False,
    target: str = "<generic-family>",
) -> str:
    """Validate and serialize a `<generic-family>` keyword or `generic(...)` function."""
    expected_syntax = "<generic-family>"
    if isinstance(value, GenericFamily):
        raw = value.name
    elif isinstance(value, Keyword):
        raw = value.value
    elif isinstance(value, str):
        raw = value
    else:
        raise StixisValidationError(target, expected_syntax, value)

    lower = raw.strip().lower()
    if lower in GENERIC_FAMILY_KEYWORDS:
        return lower
    if allow_webkit_prefix and lower.startswith("-webkit-"):
        tail = lower[len("-webkit-") :]
        if tail in GENERIC_FAMILY_KEYWORDS:
            return lower
    if lower.startswith("generic(") and lower.endswith(")"):
        inner = lower[8:-1].strip()
        if inner in GENERIC_SCRIPT_KEYWORDS:
            return f"generic({inner})"
    raise StixisValidationError(target, expected_syntax, value)


def serialize_family_name(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "<family-name>",
    is_quoted_token: bool = True,
) -> str:
    """Validate and serialize a `<family-name>` (`<font-family-name>`).

    In `serialize_value`, a Python `str` passed to `<family-name>` or `font-family` is always
    treated as a font family name (not a `<generic-family>` keyword).
    When `options.always_quote_family_name` is True (the default), it is always quoted;
    when False (`SerializationOptions.cssom()`), valid `<custom-ident>+` names are unquoted
    while digit-starting, multi-space, or keyword-colliding names remain quoted per WPT
    `font-family-serialization-001.html`.
    """
    expected_syntax = "<family-name>"
    if not isinstance(value, str):
        raise StixisValidationError(target, expected_syntax, value)

    if value == "" and not is_quoted_token:
        raise StixisValidationError(target, expected_syntax, value)

    if options.always_quote_family_name:
        return serialize_string(value, options=options)

    if is_valid_unquoted_family_name(value):
        return " ".join(serialize_ident(w) for w in value.split(" "))

    return serialize_string(value, options=options)


def serialize_font_family_item(
    item: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-family",
) -> str:
    """Serialize a single item in a `font-family` list (`<family-name> | <generic-family>`)."""
    if isinstance(item, (GenericFamily, Keyword)):
        return serialize_generic_family(item, target=target)
    if isinstance(item, str):
        return serialize_family_name(item, options=options, target=target, is_quoted_token=True)
    raise StixisValidationError(target, "[ <family-name> | <generic-family> ]#", item)


def serialize_font_family_list(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-family",
) -> str:
    """Serialize the `font-family` property (`[ <family-name> | <generic-family> ]#`) from Python values."""
    expected_syntax = "[ <family-name> | <generic-family> ]#"
    if isinstance(value, (str, GenericFamily, Keyword)):
        return serialize_font_family_item(value, options=options, target=target)
    if isinstance(value, Sequence):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        return ", ".join(
            serialize_font_family_item(elem, options=options, target=target) for elem in value
        )
    raise StixisValidationError(target, expected_syntax, value)
