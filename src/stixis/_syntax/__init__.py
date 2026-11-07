"""CSS Syntax Level 3 layer: tokenization, CSSOM primitive serialization, and PEP 750 templates."""

from __future__ import annotations

from stixis._syntax.serialize import (
    CSS_WIDE_KEYWORDS,
    RESERVED_CUSTOM_IDENT_KEYWORDS,
    _serialize_dimension_unit,
    canonicalize_unit,
    check_numeric_bounds,
    escape_char,
    escape_code_point,
    format_token_sequence,
    is_ident_char,
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
    serialize_url_modifier,
)
from stixis._syntax.template import render_css_template
from stixis._syntax.tokenizer import (
    CssToken,
    tokenize_css,
    validate_token_stream_safety,
)

__all__ = [
    "CSS_WIDE_KEYWORDS",
    "RESERVED_CUSTOM_IDENT_KEYWORDS",
    "CssToken",
    "_serialize_dimension_unit",
    "canonicalize_unit",
    "check_numeric_bounds",
    "escape_char",
    "escape_code_point",
    "format_token_sequence",
    "is_ident_char",
    "render_css_template",
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
    "tokenize_css",
    "validate_token_stream_safety",
]
