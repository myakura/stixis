"""`@font-face` `src` (`<font-src-list>`) serialization and validation (`css-fonts-4` §4.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from stixis.errors import StixisValidationError
from stixis.microsyntax.family_name import serialize_family_name
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.primitives import Keyword, LocalFont, Url, serialize_string, serialize_url

FONT_FORMAT_KEYWORDS: frozenset[str] = frozenset(
    {
        "collection",
        "embedded-opentype",
        "opentype",
        "svg",
        "truetype",
        "woff",
        "woff2",
    }
)

FONT_TECH_KEYWORDS: frozenset[str] = frozenset(
    {
        "features-opentype",
        "features-aat",
        "features-graphite",
        "avar2",
        "color-colrv0",
        "color-colrv1",
        "color-svg",
        "color-sbix",
        "color-cbdt",
        "variations",
        "palettes",
        "incremental",
    }
)


@dataclass(frozen=True, slots=True)
class FontSrc:
    """Structured representation of a single `<font-src>` entry in `@font-face` `src`."""

    url: str | Url | None = None
    local: str | None = None
    format: str | Keyword | None = None
    tech: str | Sequence[str | Keyword] | None = None

    def __post_init__(self) -> None:
        if (self.url is None) == (self.local is None):
            raise StixisValidationError(
                "src",
                "<font-src-list>",
                self,
                "FontSrc requires exactly one of `url` or `local`.",
            )
        if self.local is not None and (self.format is not None or self.tech is not None):
            raise StixisValidationError(
                "src",
                "<font-src-list>",
                self,
                "local() font sources cannot have format() or tech() modifiers.",
            )


def serialize_font_format(
    fmt: str | Keyword,
    *,
    is_quoted_token: bool = False,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "src",
) -> str:
    """Validate and serialize a `format(<font-format>)` modifier."""
    expected_syntax = "<font-format>"
    if isinstance(fmt, Keyword):
        raw = fmt.value
        is_quoted = False
    elif isinstance(fmt, str):
        raw = fmt
        is_quoted = is_quoted_token
    else:
        raise StixisValidationError(target, expected_syntax, fmt)

    if not raw:
        raise StixisValidationError(target, expected_syntax, fmt)

    lower = raw.lower()
    if is_quoted:
        # In CSS Fonts 4, <font-format> = [ <string> | collection | ... ]
        # Any non-empty <string> is syntactically valid in format("..."),
        # though standard format keywords are serialized according to options.
        return f"format({serialize_string(raw, options=options)})"

    if lower not in FONT_FORMAT_KEYWORDS:
        raise StixisValidationError(target, expected_syntax, fmt)

    if options.quote_font_format:
        return f"format({serialize_string(lower, options=options)})"
    return f"format({lower})"


def serialize_font_tech(
    tech: str | Keyword | Sequence[str | Keyword],
    *,
    target: str = "src",
) -> str:
    """Validate and serialize a `tech(<font-tech>#)` modifier (keywords only, ASCII-lowercased)."""
    expected_syntax = "tech( <font-tech># )"
    items: Sequence[str | Keyword]
    if isinstance(tech, (str, Keyword)):
        items = (tech,)
    elif isinstance(tech, Sequence):
        items = tech
    else:
        raise StixisValidationError(target, expected_syntax, tech)

    if not items:
        raise StixisValidationError(target, expected_syntax, tech)

    normalized: list[str] = []
    for item in items:
        if isinstance(item, Keyword):
            raw = item.value
        elif isinstance(item, str):
            raw = item
        else:
            raise StixisValidationError(target, expected_syntax, tech)
        lower = raw.strip().lower()
        if lower not in FONT_TECH_KEYWORDS:
            raise StixisValidationError(target, expected_syntax, item)
        normalized.append(lower)

    return f"tech({', '.join(normalized)})"


def serialize_single_font_src(
    item: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "src",
) -> str:
    """Validate and serialize a single `<font-src>` entry from structured Python data."""
    expected_syntax = "<font-src>"

    if isinstance(item, LocalFont):
        if not item.family:
            raise StixisValidationError(target, expected_syntax, item)
        fam_str = serialize_family_name(
            item.family, options=options, target=target, is_quoted_token=True
        )
        return f"local({fam_str})"

    if isinstance(item, Url):
        return serialize_url(item, options=options)

    if isinstance(item, str):
        if not item:
            raise StixisValidationError(target, expected_syntax, item)
        return serialize_url(item, options=options)

    if isinstance(item, FontSrc):
        if item.local is not None:
            if not item.local:
                raise StixisValidationError(target, expected_syntax, item)
            fam_str = serialize_family_name(
                item.local, options=options, target=target, is_quoted_token=True
            )
            return f"local({fam_str})"
        assert item.url is not None
        parts = [serialize_url(item.url, options=options)]
        if item.format is not None:
            parts.append(serialize_font_format(item.format, options=options, target=target))
        if item.tech is not None:
            parts.append(serialize_font_tech(item.tech, target=target))
        return " ".join(parts)

    if isinstance(item, Mapping):
        allowed_keys = {"url", "local", "format", "tech"}
        if not item or any(k not in allowed_keys for k in item):
            raise StixisValidationError(target, expected_syntax, item)
        src_obj = FontSrc(
            url=item.get("url"),
            local=item.get("local"),
            format=item.get("format"),
            tech=item.get("tech"),
        )
        return serialize_single_font_src(src_obj, options=options, target=target)

    if isinstance(item, tuple) and 1 <= len(item) <= 3:
        url_val = item[0]
        fmt_val = item[1] if len(item) >= 2 else None
        tech_val = item[2] if len(item) == 3 else None
        if not isinstance(url_val, (str, Url)):
            raise StixisValidationError(target, expected_syntax, item)
        src_obj = FontSrc(url=url_val, format=fmt_val, tech=tech_val)
        return serialize_single_font_src(src_obj, options=options, target=target)

    raise StixisValidationError(target, expected_syntax, item)


def serialize_font_src_list(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "src",
) -> str:
    """Validate and serialize a `<font-src-list>` (`<font-src>#`)."""
    expected_syntax = "<font-src-list>"
    if isinstance(value, (str, Url, LocalFont, FontSrc, Mapping)):
        return serialize_single_font_src(value, options=options, target=target)

    if (
        isinstance(value, tuple)
        and 1 <= len(value) <= 3
        and isinstance(value[0], (str, Url))
        and (
            len(value) == 1
            or value[1] is None
            or (
                isinstance(value[1], (str, Keyword))
                and str(value[1]).lower() in FONT_FORMAT_KEYWORDS
            )
        )
    ):
        return serialize_single_font_src(value, options=options, target=target)

    if isinstance(value, Sequence):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        return ", ".join(
            serialize_single_font_src(elem, options=options, target=target) for elem in value
        )

    raise StixisValidationError(target, expected_syntax, value)
