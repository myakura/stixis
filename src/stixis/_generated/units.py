"""Auto-generated CSS unit tables extracted from `@webref/css`."""

from __future__ import annotations

LENGTH_UNITS: frozenset[str] = frozenset({'Q', 'cap', 'ch', 'cm', 'cqb', 'cqh', 'cqi', 'cqmax', 'cqmin', 'cqw', 'dvb', 'dvh', 'dvi', 'dvmax', 'dvmin', 'dvw', 'em', 'ex', 'ic', 'in', 'lh', 'lvb', 'lvh', 'lvi', 'lvmax', 'lvmin', 'lvw', 'mm', 'pc', 'pt', 'px', 'rcap', 'rch', 'rem', 'rex', 'ric', 'rlh', 'svb', 'svh', 'svi', 'svmax', 'svmin', 'svw', 'vb', 'vh', 'vi', 'vmax', 'vmin', 'vw'})
ANGLE_UNITS: frozenset[str] = frozenset({'deg', 'grad', 'rad', 'turn'})
TIME_UNITS: frozenset[str] = frozenset({'ms', 's'})
FREQUENCY_UNITS: frozenset[str] = frozenset({'Hz', 'kHz'})
RESOLUTION_UNITS: frozenset[str] = frozenset({'dpcm', 'dpi', 'dppx', 'x'})
FLEX_UNITS: frozenset[str] = frozenset({'fr'})
DECIBEL_UNITS: frozenset[str] = frozenset({'dB'})
SEMITONES_UNITS: frozenset[str] = frozenset({'st'})

UNITS_BY_TYPE: dict[str, frozenset[str]] = {
    '<length>': LENGTH_UNITS,
    '<angle>': ANGLE_UNITS,
    '<time>': TIME_UNITS,
    '<frequency>': FREQUENCY_UNITS,
    '<resolution>': RESOLUTION_UNITS,
    '<flex>': FLEX_UNITS,
    '<decibel>': DECIBEL_UNITS,
    '<semitones>': SEMITONES_UNITS,
}

LOWER_TO_CANONICAL_UNIT: dict[str, dict[str, str]] = {
    type_name: {u.lower(): u for u in units}
    for type_name, units in UNITS_BY_TYPE.items()
}
