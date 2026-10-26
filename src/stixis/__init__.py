"""Stixis: Spec-driven general-purpose CSS serializer and validator for Python 3.14+."""

from __future__ import annotations

from stixis._generated.types import (
    FontDisplayKeyword,
    FontFaceDescriptors,
    FontFormatKeyword,
    FontTechKeyword,
    FontWidthKeyword,
)
from stixis.errors import StixisError, StixisSyntaxError, StixisValidationError
from stixis.microsyntax.font_src import FontSrc
from stixis.microsyntax.urange import (
    compact_urange,
    format_urange,
    parse_urange,
    parse_urange_codepoints,
)
from stixis.nodes import (
    AtRule,
    CommentNode,
    FontFace,
    RawDeclaration,
    Rule,
    StyleRule,
    StyleSheet,
    canonicalize,
    dumps,
    serialize,
    serialize_declarations,
    serialize_value,
)
from stixis.options import DEFAULT_OPTIONS, SerializationOptions, using_options
from stixis.primitives import (
    Deg,
    Dimension,
    Em,
    GenericFamily,
    Keyword,
    LocalFont,
    Pct,
    Percentage,
    Px,
    Rem,
    Url,
    generic,
)

__version__ = "0.1.2"

__all__ = [
    "DEFAULT_OPTIONS",
    "AtRule",
    "CommentNode",
    "Deg",
    "Dimension",
    "Em",
    "FontDisplayKeyword",
    "FontFace",
    "FontFaceDescriptors",
    "FontFormatKeyword",
    "FontSrc",
    "FontTechKeyword",
    "FontWidthKeyword",
    "GenericFamily",
    "Keyword",
    "LocalFont",
    "Pct",
    "Percentage",
    "Px",
    "RawDeclaration",
    "Rem",
    "Rule",
    "SerializationOptions",
    "StixisError",
    "StixisSyntaxError",
    "StixisValidationError",
    "StyleRule",
    "StyleSheet",
    "Url",
    "__version__",
    "canonicalize",
    "compact_urange",
    "dumps",
    "format_urange",
    "generic",
    "parse_urange",
    "parse_urange_codepoints",
    "serialize",
    "serialize_declarations",
    "serialize_value",
    "using_options",
]
