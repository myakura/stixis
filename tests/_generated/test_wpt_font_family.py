"""Auto-generated WPT `font-family` serialization tests from `cssom/font-family-serialization-001.html`. DO NOT EDIT."""

from __future__ import annotations

import pytest

from stixis import SerializationOptions, canonicalize

CSSOM_OPTS = SerializationOptions.cssom()

FONT_FAMILY_SERIALIZATION_CASES: tuple[tuple[str, str], ...] = (
    ('serif', 'serif'),
    ('"serif"', '"serif"'),
    ('sans-serif', 'sans-serif'),
    ('"sans-serif"', '"sans-serif"'),
    ('cursive', 'cursive'),
    ('"cursive"', '"cursive"'),
    ('fantasy', 'fantasy'),
    ('"fantasy"', '"fantasy"'),
    ('monospace', 'monospace'),
    ('"monospace"', '"monospace"'),
    ('system-ui', 'system-ui'),
    ('"system-ui"', '"system-ui"'),
    ('math', 'math'),
    ('"math"', '"math"'),
    ('generic(fangsong)', 'generic(fangsong)'),
    ('"generic(fangsong)"', '"generic(fangsong)"'),
    ('generic(kai)', 'generic(kai)'),
    ('"generic(kai)"', '"generic(kai)"'),
    ('generic(khmer-mul)', 'generic(khmer-mul)'),
    ('"generic(khmer-mul)"', '"generic(khmer-mul)"'),
    ('generic(nastaliq)', 'generic(nastaliq)'),
    ('"generic(nastaliq)"', '"generic(nastaliq)"'),
    ('ui-serif', 'ui-serif'),
    ('"ui-serif"', '"ui-serif"'),
    ('ui-sans-serif', 'ui-sans-serif'),
    ('"ui-sans-serif"', '"ui-sans-serif"'),
    ('ui-monospace', 'ui-monospace'),
    ('"ui-monospace"', '"ui-monospace"'),
    ('ui-rounded', 'ui-rounded'),
    ('"ui-rounded"', '"ui-rounded"'),
    ('NonGenericFontFamilyName', 'NonGenericFontFamilyName'),
    ('-webkit-body', '-webkit-body'),
    ('-webkit-standard', '-webkit-standard'),
    ('-webkit-pictograph', '-webkit-pictograph'),
    ('emoji', 'emoji'),
    ('fangsong', 'fangsong'),
    ('BlinkMacSystemFont', 'BlinkMacSystemFont'),
    ('Twisty Tie', 'Twisty Tie'),
    ("'Twisty Tie'", 'Twisty Tie'),
    ('Times New Roman', 'Times New Roman'),
    ("'Times New Roman'", 'Times New Roman'),
    ('Veronica', 'Veronica'),
    ("'Veronica'", 'Veronica'),
    ("'34J'", '"34J"'),
    ("'1'", '"1"'),
    ('"initial"', '"initial"'),
    ('"inherit"', '"inherit"'),
    ('"unset"', '"unset"'),
    ('"default"', '"default"'),
    ('"revert"', '"revert"'),
    ('"revert-layer"', '"revert-layer"'),
    ('"revert-rule"', '"revert-rule"'),
    ("'A  B'", '"A  B"'),
    ('Twisty Tie, \'34J\', "serif", Veronica, sans-serif', 'Twisty Tie, "34J", "serif", Veronica, sans-serif'),
)


@pytest.mark.parametrize(("raw_css", "expected"), FONT_FAMILY_SERIALIZATION_CASES)
def test_wpt_font_family_serialization(raw_css: str, expected: str) -> None:
    got = canonicalize("font-family", raw_css, options=CSSOM_OPTS)
    assert got == expected
    assert canonicalize("font-family", got, options=CSSOM_OPTS) == expected
