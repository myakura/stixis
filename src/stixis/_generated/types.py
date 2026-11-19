"""Auto-generated `TypedDict` and `Literal` type definitions for `@font-face` and CSS properties."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

from stixis.microsyntax.font_src import FontSrc
from stixis.values import Dimension, Keyword, LocalFont, Percentage, Url

type FontDisplayKeyword = Literal['auto', 'block', 'swap', 'fallback', 'optional']
type FontWidthKeyword = Literal['normal', 'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded']
type FontFormatKeyword = Literal['collection', 'embedded-opentype', 'opentype', 'svg', 'truetype', 'woff', 'woff2']
type FontTechKeyword = Literal['features-opentype', 'features-aat', 'features-graphite', 'avar2', 'color-colrv0', 'color-colrv1', 'color-svg', 'color-sbix', 'color-cbdt', 'variations', 'palettes', 'incremental']

_FontFaceDescriptorsHyphen = TypedDict(
    "_FontFaceDescriptorsHyphen",
    {
        'ascent-override': Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'descent-override': Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'font-display': Literal['auto', 'block', 'swap', 'fallback', 'optional'] | str | Keyword | None,
        'font-family': str | Keyword | Sequence[Any] | None,
        'font-feature-settings': Literal['normal'] | str | Keyword | Sequence[Any] | Mapping[str, Any] | None,
        'font-language-override': Literal['normal'] | str | Keyword | None,
        'font-named-instance': Literal['auto'] | str | Keyword | None,
        'font-size': Literal['auto'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'font-stretch': Literal['auto', 'normal', 'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded'] | str | Keyword | Sequence[Any] | None,
        'font-style': Literal['auto', 'normal', 'italic', 'left', 'right'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'font-variation-settings': Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | Mapping[str, Any] | None,
        'font-weight': Literal['auto', 'normal', 'bold'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'font-width': Literal['auto', 'normal', 'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded'] | str | Keyword | Sequence[Any] | None,
        'line-gap-override': Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'size-adjust': str | Keyword | int | float | Dimension | Percentage | None,
        'subscript-position-override': Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'subscript-size-override': Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'superscript-position-override': Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'superscript-size-override': Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None,
        'unicode-range': str | Keyword | Sequence[Any] | set[int] | frozenset[int] | tuple[int, int] | None,
    },
    total=False,
)


class FontFaceDescriptors(_FontFaceDescriptorsHyphen, total=False):
    """TypedDict of `@font-face` descriptors (accepts both `snake_case` and `kebab-case` keys)."""

    ascent_override: Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    descent_override: Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    font_display: Literal['auto', 'block', 'swap', 'fallback', 'optional'] | str | Keyword | None
    font_family: str | Keyword | Sequence[Any] | None
    font_feature_settings: Literal['normal'] | str | Keyword | Sequence[Any] | Mapping[str, Any] | None
    font_language_override: Literal['normal'] | str | Keyword | None
    font_named_instance: Literal['auto'] | str | Keyword | None
    font_size: Literal['auto'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    font_stretch: Literal['auto', 'normal', 'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded'] | str | Keyword | Sequence[Any] | None
    font_style: Literal['auto', 'normal', 'italic', 'left', 'right'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    font_variation_settings: Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | Mapping[str, Any] | None
    font_weight: Literal['auto', 'normal', 'bold'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    font_width: Literal['auto', 'normal', 'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded'] | str | Keyword | Sequence[Any] | None
    line_gap_override: Literal['normal'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    size_adjust: str | Keyword | int | float | Dimension | Percentage | None
    src: str | Keyword | Sequence[Any] | FontSrc | Url | LocalFont | Mapping[str, Any] | None
    subscript_position_override: Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    subscript_size_override: Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    superscript_position_override: Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    superscript_size_override: Literal['normal', 'from-font'] | str | Keyword | int | float | Dimension | Percentage | Sequence[Any] | None
    unicode_range: str | Keyword | Sequence[Any] | set[int] | frozenset[int] | tuple[int, int] | None
