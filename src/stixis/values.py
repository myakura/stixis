"""CSS value wrapper dataclasses (`Keyword`, `GenericFamily`, `Url`, `LocalFont`, `Dimension`, `Percentage`)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from stixis._syntax.serialize import (
    CSS_WIDE_KEYWORDS,
    RESERVED_CUSTOM_IDENT_KEYWORDS,
    _is_valid_dimension_unit,
    _serialize_dimension_unit,
    serialize_ident,
    serialize_number,
    serialize_string,
    serialize_url,
    serialize_url_modifier,
)
from stixis.errors import StixisValidationError

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

__all__ = [
    "CSS_WIDE_KEYWORDS",
    "GENERIC_FAMILY_KEYWORDS",
    "GENERIC_SCRIPT_KEYWORDS",
    "RESERVED_CUSTOM_IDENT_KEYWORDS",
    "Deg",
    "Dimension",
    "Em",
    "GenericFamily",
    "Keyword",
    "LocalFont",
    "Pct",
    "Percentage",
    "Px",
    "Rem",
    "Url",
    "generic",
]


@dataclass(frozen=True, slots=True)
class Keyword:
    """Explicit CSS identifier or keyword (disambiguates keywords from `<string>` or `<family-name>`)."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise StixisValidationError("<ident>", "<ident>", self.value)

    def __str__(self) -> str:
        return serialize_ident(self.value)


@dataclass(frozen=True, slots=True)
class GenericFamily:
    """Explicit `<generic-family>` value for `font-family` (e.g., `serif`, `generic(kai)`)."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise StixisValidationError("<generic-family>", "<generic-family>", self.name)
        lower = self.name.strip().lower()
        if lower in GENERIC_FAMILY_KEYWORDS:
            return
        if lower.startswith("generic(") and lower.endswith(")"):
            inner = lower[8:-1].strip()
            if inner in GENERIC_SCRIPT_KEYWORDS:
                return
        raise StixisValidationError("<generic-family>", "<generic-family>", self.name)

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

    def __post_init__(self) -> None:
        if not isinstance(self.family, str) or not self.family.strip():
            raise StixisValidationError("<font-src>", "<font-src>", self.family)

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
        try:
            is_finite = (
                math.isfinite(float(self.value))
                if isinstance(self.value, int)
                else math.isfinite(self.value)
            )
        except OverflowError as exc:
            raise StixisValidationError("<dimension>", "<dimension>", self.value) from exc
        if not is_finite:
            raise StixisValidationError("<dimension>", "<dimension>", self.value)
        if not _is_valid_dimension_unit(self.unit):
            raise StixisValidationError("<dimension>", "<dimension>", self.unit)

    def __str__(self) -> str:
        return f"{serialize_number(self.value)}{_serialize_dimension_unit(self.unit)}"


@dataclass(frozen=True, slots=True)
class Percentage:
    """A numeric percentage value (`<percentage>`)."""

    value: int | float

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise StixisValidationError("<percentage>", "<percentage>", self.value)
        try:
            is_finite = (
                math.isfinite(float(self.value))
                if isinstance(self.value, int)
                else math.isfinite(self.value)
            )
        except OverflowError as exc:
            raise StixisValidationError("<percentage>", "<percentage>", self.value) from exc
        if not is_finite:
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
