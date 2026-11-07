"""CSS value wrapper dataclasses (`Keyword`, `GenericFamily`, `Url`, `LocalFont`, `Dimension`, `Percentage`)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from stixis._syntax.serialize import (
    CSS_WIDE_KEYWORDS,
    RESERVED_CUSTOM_IDENT_KEYWORDS,
    serialize_ident,
    serialize_number,
    serialize_string,
    serialize_url,
    serialize_url_modifier,
)
from stixis.errors import StixisValidationError

__all__ = [
    "CSS_WIDE_KEYWORDS",
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

    def __str__(self) -> str:
        return serialize_ident(self.value)


@dataclass(frozen=True, slots=True)
class GenericFamily:
    """Explicit `<generic-family>` value for `font-family` (e.g., `serif`, `generic(kai)`)."""

    name: str

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
        if not math.isfinite(self.value):
            raise StixisValidationError("<dimension>", "<dimension>", self.value)
        if not isinstance(self.unit, str) or not self.unit:
            raise StixisValidationError("<dimension>", "<dimension>", self.unit)

    def __str__(self) -> str:
        return f"{serialize_number(self.value)}{self.unit}"


@dataclass(frozen=True, slots=True)
class Percentage:
    """A numeric percentage value (`<percentage>`)."""

    value: int | float

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise StixisValidationError("<percentage>", "<percentage>", self.value)
        if not math.isfinite(self.value):
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
