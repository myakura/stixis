"""Configuration options and presets for CSS serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SerializationOptions:
    """Controls CSS serialization details across W3C CSSOM and Cleome compatibility modes."""

    quote_char: Literal['"', "'"] = '"'
    always_quote_family_name: bool = True
    normalize_zero_oblique: bool = False
    allow_vendor_prefixes: bool = False
    allow_unknown_properties: bool = False
    font_face_stretch_alias: Literal["font-width", "font-stretch", "both"] = "font-width"
    quote_font_format: bool = False
    uppercase_urange_prefix: bool = True

    @classmethod
    def cssom(cls) -> SerializationOptions:
        """Strict W3C CSSOM serialization mode used by Web Platform Tests."""
        return cls(
            quote_char='"',
            always_quote_family_name=False,
            normalize_zero_oblique=True,
            allow_vendor_prefixes=False,
            allow_unknown_properties=False,
            font_face_stretch_alias="font-width",
            quote_font_format=False,
            uppercase_urange_prefix=True,
        )

    @classmethod
    def cleome(cls) -> SerializationOptions:
        """Cleome-compatible serialization mode (`'` quotes, `font-stretch`, `format('woff2')`)."""
        return cls(
            quote_char="'",
            always_quote_family_name=True,
            normalize_zero_oblique=False,
            allow_vendor_prefixes=False,
            allow_unknown_properties=False,
            font_face_stretch_alias="font-stretch",
            quote_font_format=True,
            uppercase_urange_prefix=True,
        )


DEFAULT_OPTIONS = SerializationOptions()
