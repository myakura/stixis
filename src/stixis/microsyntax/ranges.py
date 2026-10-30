"""1-to-2 value range (`{1,2}`) serialization for variable font descriptors and properties."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Any

from stixis.errors import StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.primitives import (
    Dimension,
    Keyword,
    Percentage,
    serialize_dimension,
    serialize_number,
    serialize_percentage,
)

ANGLE_UNITS: frozenset[str] = frozenset({"deg", "grad", "rad", "turn"})
ANGLE_UNITS_LOWER: dict[str, str] = {u.lower(): u for u in ANGLE_UNITS}

FONT_WIDTH_KEYWORDS: frozenset[str] = frozenset(
    {
        "normal",
        "ultra-condensed",
        "extra-condensed",
        "condensed",
        "semi-condensed",
        "semi-expanded",
        "expanded",
        "extra-expanded",
        "ultra-expanded",
    }
)

_ANGLE_STR_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)(deg|grad|rad|turn)$",
    re.IGNORECASE,
)
_PCT_STR_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)%$")
_NUM_STR_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)$")


def angle_to_deg(val: float, unit: str) -> float:
    """Convert an angle value in `deg`, `grad`, `rad`, or `turn` to degrees for range validation."""
    u = unit.lower()
    if u == "deg":
        return val
    if u == "grad":
        return val * 0.9
    if u == "rad":
        return val * 180.0 / math.pi
    if u == "turn":
        return val * 360.0
    raise ValueError(f"Unknown angle unit: {unit}")


def serialize_angle_value(
    value: Any,
    *,
    min_deg: float | None = None,
    max_deg: float | None = None,
    target: str = "<angle>",
    expected_syntax: str = "<angle>",
) -> tuple[str, float]:
    """Validate and serialize an `<angle>` (from `int`/`float` degrees, `Dimension`, `(val, unit)`, or string)."""
    num: float
    unit: str
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        num = value
        unit = "deg"
    elif isinstance(value, Dimension):
        num = value.value
        unit = value.unit
    elif (
        isinstance(value, tuple)
        and len(value) == 2
        and not isinstance(value[0], bool)
        and isinstance(value[0], (int, float))
        and isinstance(value[1], str)
    ):
        num = value[0]
        unit = value[1]
    elif isinstance(value, str):
        m = _ANGLE_STR_RE.match(value.strip())
        if not m:
            raise StixisValidationError(target, expected_syntax, value)
        num = float(m.group(1))
        unit = m.group(2).lower()
    else:
        raise StixisValidationError(target, expected_syntax, value)

    if unit.lower() not in ANGLE_UNITS_LOWER:
        raise StixisValidationError(target, expected_syntax, value)

    deg = angle_to_deg(float(num), unit)
    if min_deg is not None and deg < min_deg - 1e-9:
        raise StixisValidationError(target, expected_syntax, value)
    if max_deg is not None and deg > max_deg + 1e-9:
        raise StixisValidationError(target, expected_syntax, value)

    serialized = serialize_dimension(
        (num, unit.lower()),
        allowed_units=ANGLE_UNITS,
        lower_to_canonical=ANGLE_UNITS_LOWER,
        target=target,
        expected_syntax=expected_syntax,
    )
    return serialized, deg


def serialize_one_or_two(
    value: Any,
    item_serializer: Callable[[Any], str],
    *,
    target: str,
    expected_syntax: str,
) -> str:
    """Serialize a `{1,2}` range value from a single item or a 2-element sequence."""
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and not (
            isinstance(value, tuple)
            and len(value) == 2
            and not isinstance(value[0], bool)
            and isinstance(value[0], (int, float))
            and isinstance(value[1], str)
            and (value[1] == "%" or value[1].lower() in ANGLE_UNITS_LOWER)
        )
    ):
        if not (1 <= len(value) <= 2):
            raise StixisValidationError(target, expected_syntax, value)
        return " ".join(item_serializer(v) for v in value)
    return item_serializer(value)


def _serialize_weight_item(
    item: Any,
    *,
    allow_relative: bool,
    target: str,
    expected_syntax: str,
) -> str:
    if isinstance(item, Keyword):
        item = item.value
    if isinstance(item, str):
        lower = item.strip().lower()
        if lower in ("normal", "bold"):
            return lower
        if allow_relative and lower in ("bolder", "lighter"):
            return lower
        m = _NUM_STR_RE.match(lower)
        if m:
            num = float(m.group(1))
            return serialize_number(
                num, min_val=1, max_val=1000, target=target, expected_syntax=expected_syntax
            )
        raise StixisValidationError(target, expected_syntax, item)
    return serialize_number(
        item, min_val=1, max_val=1000, target=target, expected_syntax=expected_syntax
    )


def serialize_font_weight(
    value: Any,
    *,
    is_descriptor: bool = False,
    target: str = "font-weight",
) -> str:
    """Serialize `font-weight` for either a style rule or `@font-face` descriptor (`{1,2}`)."""
    if is_descriptor:
        expected_syntax = "auto | <font-weight-absolute>{1,2}"
        if isinstance(value, (str, Keyword)) and str(value).strip().lower() == "auto":
            return "auto"
        return serialize_one_or_two(
            value,
            lambda item: _serialize_weight_item(
                item, allow_relative=False, target=target, expected_syntax=expected_syntax
            ),
            target=target,
            expected_syntax=expected_syntax,
        )
    expected_syntax = "<font-weight-absolute> | bolder | lighter"
    return _serialize_weight_item(
        value, allow_relative=True, target=target, expected_syntax=expected_syntax
    )


def _serialize_width_item(
    item: Any,
    *,
    target: str,
    expected_syntax: str,
) -> str:
    if isinstance(item, Keyword):
        item = item.value
    if isinstance(item, str):
        lower = item.strip().lower()
        if lower in FONT_WIDTH_KEYWORDS:
            return lower
        m = _PCT_STR_RE.match(lower)
        if m:
            num = float(m.group(1))
            return serialize_percentage(
                Percentage(num), min_val=0, target=target, expected_syntax=expected_syntax
            )
        raise StixisValidationError(target, expected_syntax, item)
    if not isinstance(item, bool) and isinstance(item, (int, float)):
        return serialize_percentage(
            Percentage(item), min_val=0, target=target, expected_syntax=expected_syntax
        )
    return serialize_percentage(item, min_val=0, target=target, expected_syntax=expected_syntax)


def serialize_font_width(
    value: Any,
    *,
    is_descriptor: bool = False,
    target: str = "font-width",
) -> str:
    """Serialize `font-width` / `font-stretch` for either a style rule or `@font-face` descriptor (`{1,2}`)."""
    if is_descriptor:
        expected_syntax = "auto | <'font-width'>{1,2}"
        if isinstance(value, (str, Keyword)) and str(value).strip().lower() == "auto":
            return "auto"
        return serialize_one_or_two(
            value,
            lambda item: _serialize_width_item(
                item, target=target, expected_syntax=expected_syntax
            ),
            target=target,
            expected_syntax=expected_syntax,
        )
    expected_syntax = "normal | <percentage [0,∞]> | ultra-condensed | extra-condensed | condensed | semi-condensed | semi-expanded | expanded | extra-expanded | ultra-expanded"
    return _serialize_width_item(value, target=target, expected_syntax=expected_syntax)


def serialize_font_style(
    value: Any,
    *,
    is_descriptor: bool = False,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "font-style",
) -> str:
    """Serialize `font-style` for either a style rule or `@font-face` descriptor (`oblique <angle [-90deg,90deg]>{1,2}`)."""
    expected_syntax = (
        "auto | normal | italic | left | right | oblique [ <angle [-90deg,90deg]>{1,2} ]?"
        if is_descriptor
        else "normal | italic | left | right | oblique <angle [-90deg,90deg]>?"
    )

    if isinstance(value, Keyword):
        value = value.value

    if isinstance(value, str):
        raw = value.strip()
        lower = raw.lower()
        if lower in ("normal", "italic", "left", "right", "oblique"):
            return lower
        if is_descriptor and lower == "auto":
            return "auto"
        if lower.startswith("oblique") and len(lower) > 7 and lower[7].isspace():
            rest = raw[7:].strip().split()
            if not rest or len(rest) > (2 if is_descriptor else 1):
                raise StixisValidationError(target, expected_syntax, value)
            angles = [
                serialize_angle_value(
                    part,
                    min_deg=-90.0,
                    max_deg=90.0,
                    target=target,
                    expected_syntax=expected_syntax,
                )
                for part in rest
            ]
            if (
                not is_descriptor
                and len(angles) == 1
                and angles[0][1] == 0.0
                and options.normalize_zero_oblique
            ):
                return "normal"
            return "oblique " + " ".join(a[0] for a in angles)
        raise StixisValidationError(target, expected_syntax, value)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            raise StixisValidationError(target, expected_syntax, value)
        first = value[0].value if isinstance(value[0], Keyword) else value[0]
        if isinstance(first, str) and first.strip().lower() == "oblique":
            tail = list(value[1:])
            if not tail:
                return "oblique"
            if (
                len(tail) == 1
                and isinstance(tail[0], (tuple, list))
                and not (
                    len(tail[0]) == 2
                    and isinstance(tail[0][1], str)
                    and tail[0][1].lower() in ANGLE_UNITS_LOWER
                )
            ):
                tail = list(tail[0])
            if len(tail) > (2 if is_descriptor else 1):
                raise StixisValidationError(target, expected_syntax, value)
            angles = [
                serialize_angle_value(
                    part,
                    min_deg=-90.0,
                    max_deg=90.0,
                    target=target,
                    expected_syntax=expected_syntax,
                )
                for part in tail
            ]
            if (
                not is_descriptor
                and len(angles) == 1
                and angles[0][1] == 0.0
                and options.normalize_zero_oblique
            ):
                return "normal"
            return "oblique " + " ".join(a[0] for a in angles)
        # Or a 2-tuple of angles `(lo, hi)` directly for `@font-face` oblique range
        if is_descriptor and 1 <= len(value) <= 2:
            angles = [
                serialize_angle_value(
                    part,
                    min_deg=-90.0,
                    max_deg=90.0,
                    target=target,
                    expected_syntax=expected_syntax,
                )
                for part in value
            ]
            return "oblique " + " ".join(a[0] for a in angles)

    raise StixisValidationError(target, expected_syntax, value)


def _serialize_metric_override_item(
    item: Any,
    *,
    allow_from_font: bool,
    min_val: float | None,
    target: str,
    expected_syntax: str,
) -> str:
    if isinstance(item, Keyword):
        item = item.value
    if isinstance(item, str):
        lower = item.strip().lower()
        if lower == "normal":
            return "normal"
        if allow_from_font and lower == "from-font":
            return "from-font"
        m = _PCT_STR_RE.match(lower)
        if m:
            num = float(m.group(1))
            return serialize_percentage(
                Percentage(num), min_val=min_val, target=target, expected_syntax=expected_syntax
            )
        raise StixisValidationError(target, expected_syntax, item)
    if not isinstance(item, bool) and isinstance(item, (int, float)):
        return serialize_percentage(
            Percentage(item), min_val=min_val, target=target, expected_syntax=expected_syntax
        )
    return serialize_percentage(
        item, min_val=min_val, target=target, expected_syntax=expected_syntax
    )


def serialize_metric_override(
    value: Any,
    *,
    allow_from_font: bool = False,
    min_val: float | None = 0.0,
    target: str = "ascent-override",
) -> str:
    """Serialize `[ normal | <percentage [0,∞]> ]{1,2}` metric override descriptors (`css-fonts-4` & `css-fonts-5`)."""
    expected_syntax = (
        "[ normal | from-font | <percentage> ]{1,2}"
        if allow_from_font and min_val is None
        else "[ normal | from-font | <percentage [0,∞]> ]{1,2}"
        if allow_from_font
        else "[ normal | <percentage [0,∞]> ]{1,2}"
    )
    return serialize_one_or_two(
        value,
        lambda item: _serialize_metric_override_item(
            item,
            allow_from_font=allow_from_font,
            min_val=min_val,
            target=target,
            expected_syntax=expected_syntax,
        ),
        target=target,
        expected_syntax=expected_syntax,
    )
