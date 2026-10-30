"""OpenType 4-byte tag (`<opentype-tag>`) validation and `font-feature-settings` / `font-variation-settings` serialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stixis.errors import StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.primitives import Keyword, serialize_integer, serialize_number, serialize_string


def validate_opentype_tag(
    tag: str,
    *,
    target: str = "<opentype-tag>",
    expected_syntax: str = "<opentype-tag>",
) -> str:
    """Validate that `tag` is a 4-character printable ASCII string (`0x20` to `0x7E`)."""
    if not isinstance(tag, str) or len(tag) != 4:
        raise StixisValidationError(target, expected_syntax, tag)
    for ch in tag:
        cp = ord(ch)
        if not (0x20 <= cp <= 0x7E):
            raise StixisValidationError(target, expected_syntax, tag)
    return tag


def serialize_opentype_tag(
    tag: str,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "<opentype-tag>",
    expected_syntax: str = "<opentype-tag>",
) -> str:
    """Validate and serialize an `<opentype-tag>` as a quoted CSS string."""
    validated = validate_opentype_tag(tag, target=target, expected_syntax=expected_syntax)
    return serialize_string(validated, options=options)


def serialize_font_language_override(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-language-override",
    is_keyword_token: bool = False,
) -> str:
    """Validate and serialize `font-language-override` (`normal | <string>` with 1..4 printable ASCII chars, right-trimmed)."""
    expected_syntax = "normal | <string>"
    if isinstance(value, Keyword):
        if value.value.lower() == "normal":
            return "normal"
        raise StixisValidationError(target, expected_syntax, value)
    if not isinstance(value, str):
        raise StixisValidationError(target, expected_syntax, value)
    if is_keyword_token:
        if value.strip().lower() == "normal":
            return "normal"
        raise StixisValidationError(target, expected_syntax, value)
    if len(value) < 1 or len(value) > 4:
        raise StixisValidationError(
            target,
            expected_syntax,
            value,
            "OpenType language system tag must be 1 to 4 printable ASCII characters",
        )
    for ch in value:
        code = ord(ch)
        if code < 0x20 or code > 0x7E:
            raise StixisValidationError(
                target,
                expected_syntax,
                value,
                f"Non-printable or non-ASCII character U+{code:04X} in language tag",
            )
    trimmed = value.rstrip(" ")
    if not trimmed:
        raise StixisValidationError(target, expected_syntax, value)
    return serialize_string(trimmed, options=options)


def _format_single_feature_tag_value(
    tag: str,
    val: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-feature-settings",
    expected_syntax: str = "normal | <feature-tag-value>#",
) -> str:
    tag_str = serialize_opentype_tag(
        tag, options=options, target=target, expected_syntax=expected_syntax
    )
    if val is None or val is True:
        return tag_str
    if val is False:
        return f"{tag_str} 0"
    if isinstance(val, Keyword):
        val = val.value
    if isinstance(val, str):
        lower = val.lower()
        if lower == "on":
            return tag_str
        if lower == "off":
            return f"{tag_str} 0"
        raise StixisValidationError(target, expected_syntax, (tag, val))
    int_str = serialize_integer(
        val,
        min_val=0,
        target=target,
        expected_syntax=expected_syntax,
    )
    if int_str == "1":
        return tag_str
    return f"{tag_str} {int_str}"


def serialize_font_feature_settings(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-feature-settings",
) -> str:
    """Serialize `font-feature-settings` from Python dicts, tuples, lists, or keywords."""
    expected_syntax = "normal | <feature-tag-value>#"

    if isinstance(value, Keyword):
        if value.value.lower() == "normal":
            return "normal"
        raise StixisValidationError(target, expected_syntax, value)

    if isinstance(value, str):
        if value.lower() == "normal":
            return "normal"
        return serialize_opentype_tag(
            value, options=options, target=target, expected_syntax=expected_syntax
        )

    if isinstance(value, Mapping):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        parts: list[str] = []
        for k, v in value.items():
            if not isinstance(k, str):
                raise StixisValidationError(target, expected_syntax, value)
            parts.append(
                _format_single_feature_tag_value(
                    k, v, options=options, target=target, expected_syntax=expected_syntax
                )
            )
        return ", ".join(parts)

    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and not isinstance(value[1], (tuple, list, dict))
        and not (
            isinstance(value[1], str)
            and len(value[1]) == 4
            and value[1].lower() not in ("on", "off")
        )
    ):
        return _format_single_feature_tag_value(
            value[0], value[1], options=options, target=target, expected_syntax=expected_syntax
        )

    if isinstance(value, Sequence):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        parts = []
        for item in value:
            if isinstance(item, str):
                if item.lower() == "normal":
                    raise StixisValidationError(target, expected_syntax, value)
                parts.append(
                    _format_single_feature_tag_value(
                        item, None, options=options, target=target, expected_syntax=expected_syntax
                    )
                )
            elif isinstance(item, tuple) and 1 <= len(item) <= 2 and isinstance(item[0], str):
                v = item[1] if len(item) == 2 else None
                parts.append(
                    _format_single_feature_tag_value(
                        item[0], v, options=options, target=target, expected_syntax=expected_syntax
                    )
                )
            else:
                raise StixisValidationError(target, expected_syntax, value)
        return ", ".join(parts)

    raise StixisValidationError(target, expected_syntax, value)


def _format_single_variation_tag_value(
    tag: str,
    val: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-variation-settings",
    expected_syntax: str = "normal | [ <opentype-tag> <number> ]#",
) -> str:
    tag_str = serialize_opentype_tag(
        tag, options=options, target=target, expected_syntax=expected_syntax
    )
    num_str = serialize_number(val, target=target, expected_syntax=expected_syntax)
    return f"{tag_str} {num_str}"


def serialize_font_variation_settings(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-variation-settings",
) -> str:
    """Serialize `font-variation-settings` from Python dicts, tuples, lists, or `'normal'`."""
    expected_syntax = "normal | [ <opentype-tag> <number> ]#"

    if isinstance(value, Keyword):
        if value.value.lower() == "normal":
            return "normal"
        raise StixisValidationError(target, expected_syntax, value)

    if isinstance(value, str):
        if value.lower() == "normal":
            return "normal"
        raise StixisValidationError(target, expected_syntax, value)

    if isinstance(value, Mapping):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        parts: list[str] = []
        for k, v in value.items():
            if not isinstance(k, str):
                raise StixisValidationError(target, expected_syntax, value)
            parts.append(
                _format_single_variation_tag_value(
                    k, v, options=options, target=target, expected_syntax=expected_syntax
                )
            )
        return ", ".join(parts)

    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and not isinstance(value[1], bool)
        and isinstance(value[1], (int, float))
    ):
        return _format_single_variation_tag_value(
            value[0], value[1], options=options, target=target, expected_syntax=expected_syntax
        )

    if isinstance(value, Sequence):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        parts = []
        for item in value:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                parts.append(
                    _format_single_variation_tag_value(
                        item[0],
                        item[1],
                        options=options,
                        target=target,
                        expected_syntax=expected_syntax,
                    )
                )
            else:
                raise StixisValidationError(target, expected_syntax, value)
        return ", ".join(parts)

    raise StixisValidationError(target, expected_syntax, value)
