"""Auto-generated WPT `<urange>` parsing tests from `css-syntax/urange-parsing.html`. DO NOT EDIT."""

from __future__ import annotations

import pytest

from stixis import StixisError, canonicalize, format_urange, parse_urange

VALID_URANGE_CASES: tuple[tuple[str, str], ...] = (
    ('u/**/+/**/a/**/?', 'U+A0-AF'),
    ('u+abc', 'U+ABC'),
    ('U+abc', 'U+ABC'),
    ('u+ABC', 'U+ABC'),
    ('U+ABC', 'U+ABC'),
    ('u+AbC', 'U+ABC'),
    ('u+a', 'U+A'),
    ('u+aa', 'U+AA'),
    ('u+aaa', 'U+AAA'),
    ('u+aaaa', 'U+AAAA'),
    ('u+aaaaa', 'U+AAAAA'),
    ('u+a?', 'U+A0-AF'),
    ('u+a??', 'U+A00-AFF'),
    ('u+a???', 'U+A000-AFFF'),
    ('u+a????', 'U+A0000-AFFFF'),
    ('u/**/+0a/**/?', 'U+A0-AF'),
    ('u+0a', 'U+A'),
    ('U+0a0', 'U+A0'),
    ('u+0aaaaa', 'U+AAAAA'),
    ('u+0a0000', 'U+A0000'),
    ('u+00000a', 'U+A'),
    ('u+0a????', 'U+A0000-AFFFF'),
    ('u+1e9a', 'U+1E9A'),
    ('u/**/+0/**/?', 'U+0-F'),
    ('u+0', 'U+0'),
    ('u+00', 'U+0'),
    ('u+000', 'U+0'),
    ('u+0000', 'U+0'),
    ('u+00000', 'U+0'),
    ('u+000000', 'U+0'),
    ('u+00000?', 'U+0-F'),
    ('u+0?????', 'U+0-FFFFF'),
    ('u+1e3', 'U+1E3'),
    ('u+1e-20', 'U+1E-20'),
    ('u/**/+0/**/-0a', 'U+0-A'),
    ('u+0-0a', 'U+0-A'),
    ('u+000000-0aaaaa', 'U+0-AAAAA'),
    ('u/**/+0/**/-1', 'U+0-1'),
    ('u+0-1', 'U+0-1'),
    ('u+000000-000001', 'U+0-1'),
    ('u+0-10ffff', 'U+0-10FFFF'),
    ('u/**/+/**/?', 'U+0-F'),
    ('u+?', 'U+0-F'),
    ('u+?????', 'U+0-FFFFF'),
)

INVALID_URANGE_CASES: tuple[str, ...] = (
    'u+efg',
    'u+ abc',
    'u +abc',
    'u + abc',
    'U + a b c',
    'u+aaaaaaa',
    'u+aaaaaa?',
    'u+aaaaa??',
    'u+aaaa???',
    'u+aaa????',
    'u+aa?????',
    'u+a??????',
    'u+a?a',
    'u+aaaaaa',
    'u+a?????',
    'u+0aaaaaa',
    'u+0a00000',
    'u+0aaaaa0',
    'u+00000aa',
    'u+00000a0',
    'u+000000a',
    'u+0a?????',
    'u+00a????',
    'u+22222a',
    'u/**/0',
    'u+0000000',
    'u+0?a',
    'u+000000?',
    'u+00000??',
    'u+0??????',
    'u+222222',
    'u+2?????',
    'u+0000000-0a',
    'u+0-0aaaaaa',
    'u+0-000000a',
    'u+0+0a',
    'u+0?-0a',
    'u+0-0a?',
    'u+222222-22222a',
    'u-0-1',
    'u-0+1',
    'u+0+1',
    'u+0000000-1',
    'u+0-0000001',
    'u+0-110000',
    'u+0-222222',
    'u+???????',
    'u+?a',
    'u+??????',
    '()U+123',
)


@pytest.mark.parametrize(("raw_css", "expected"), VALID_URANGE_CASES)
def test_wpt_urange_valid(raw_css: str, expected: str) -> None:
    assert format_urange(raw_css) == expected
    assert canonicalize("<urange>", raw_css) == expected
    assert canonicalize("unicode-range", raw_css, at_rule="@font-face") == expected
    # Round-trip idempotence
    assert format_urange(parse_urange(expected)) == expected


@pytest.mark.parametrize("raw_css", INVALID_URANGE_CASES)
def test_wpt_urange_invalid(raw_css: str) -> None:
    with pytest.raises(StixisError):
        parse_urange(raw_css)
    with pytest.raises(StixisError):
        canonicalize("unicode-range", raw_css, at_rule="@font-face")
