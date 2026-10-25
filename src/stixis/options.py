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
    min_engines: int = 2
    extra_properties: frozenset[str] = frozenset()

    @classmethod
    def cssom(cls, *, min_engines: int = 2) -> SerializationOptions:
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
            min_engines=min_engines,
        )

    @classmethod
    def cleome(cls, *, min_engines: int = 2) -> SerializationOptions:
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
            min_engines=min_engines,
        )


DEFAULT_OPTIONS = SerializationOptions()
