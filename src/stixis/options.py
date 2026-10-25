"""Configuration options and presets for CSS serialization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from stixis._generated import specs as _gen_specs

_DEFAULT_MIN_ENGINES: int = getattr(_gen_specs, "DEFAULT_MIN_ENGINES", 2)


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
    min_engines: int = _DEFAULT_MIN_ENGINES
    extra_properties: frozenset[str] = frozenset()

    @classmethod
    def cssom(cls, *, min_engines: int | None = None) -> SerializationOptions:
        """Strict W3C CSSOM serialization mode used by Web Platform Tests."""
        eff_min = _DEFAULT_MIN_ENGINES if min_engines is None else min_engines
        return cls(
            quote_char='"',
            always_quote_family_name=False,
            normalize_zero_oblique=True,
            allow_vendor_prefixes=False,
            allow_unknown_properties=False,
            font_face_stretch_alias="font-width",
            quote_font_format=False,
            uppercase_urange_prefix=True,
            min_engines=eff_min,
        )

    @classmethod
    def cleome(cls, *, min_engines: int | None = None) -> SerializationOptions:
        """Cleome-compatible serialization mode (`'` quotes, `font-stretch`, `format('woff2')`)."""
        eff_min = _DEFAULT_MIN_ENGINES if min_engines is None else min_engines
        return cls(
            quote_char="'",
            always_quote_family_name=True,
            normalize_zero_oblique=False,
            allow_vendor_prefixes=False,
            allow_unknown_properties=False,
            font_face_stretch_alias="font-stretch",
            quote_font_format=True,
            uppercase_urange_prefix=True,
            min_engines=eff_min,
        )

    @contextmanager
    def activate(self) -> Iterator[SerializationOptions]:
        """Temporarily set this `SerializationOptions` as the default for newly constructed AST nodes."""
        token = _ACTIVE_OPTIONS.set(self)
        try:
            yield self
        finally:
            _ACTIVE_OPTIONS.reset(token)


DEFAULT_OPTIONS = SerializationOptions()

_ACTIVE_OPTIONS: ContextVar[SerializationOptions] = ContextVar(
    "_ACTIVE_OPTIONS", default=DEFAULT_OPTIONS
)


def current_options(options: SerializationOptions | None = None) -> SerializationOptions:
    """Return `options` if provided, otherwise the active context options or `DEFAULT_OPTIONS`."""
    if options is not None:
        return options
    return _ACTIVE_OPTIONS.get()


@contextmanager
def using_options(options: SerializationOptions) -> Iterator[SerializationOptions]:
    """Context manager that sets default `SerializationOptions` for AST nodes constructed in the block."""
    with options.activate() as active:
        yield active
