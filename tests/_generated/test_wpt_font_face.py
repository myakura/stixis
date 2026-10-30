"""Auto-generated WPT `@font-face` descriptor tests from `css-fonts/parsing/font-face-*.html`. DO NOT EDIT."""

from __future__ import annotations

import pytest

from stixis import FontFace, SerializationOptions, StixisError, canonicalize

CSSOM_OPTS = SerializationOptions.cssom()

VALID_FONT_FACE_DESCRIPTOR_CASES: tuple[tuple[str, str, str | None], ...] = (
    ('ascent-override', 'normal', 'normal'),
    ('descent-override', 'normal', 'normal'),
    ('line-gap-override', 'normal', 'normal'),
    ('ascent-override', '0%', '0%'),
    ('descent-override', '0%', '0%'),
    ('line-gap-override', '0%', '0%'),
    ('ascent-override', '50%', '50%'),
    ('descent-override', '50%', '50%'),
    ('line-gap-override', '50%', '50%'),
    ('ascent-override', '110%', '110%'),
    ('descent-override', '110%', '110%'),
    ('line-gap-override', '110%', '110%'),
    ('ascent-override', '100000000000%', '100000000000%'),
    ('descent-override', '100000000000%', '100000000000%'),
    ('line-gap-override', '100000000000%', '100000000000%'),
    ('size-adjust', '100%', '100%'),
    ('size-adjust', '0%', '0%'),
    ('size-adjust', '110%', '110%'),
    ('size-adjust', '100000000000%', '100000000000%'),
    ('font-width', 'auto', 'auto'),
    ('font-stretch', 'auto', 'auto'),
    ('font-width', 'normal', 'normal'),
    ('font-stretch', 'normal', 'normal'),
    ('font-width', 'condensed', 'condensed'),
    ('font-stretch', 'condensed', 'condensed'),
    ('font-width', '125%', '125%'),
    ('font-stretch', '125%', '125%'),
    ('font-width', '75% 125%', '75% 125%'),
    ('font-stretch', '75% 125%', '75% 125%'),
    ('src', 'local(  A  )', None),
    ('src', 'local(A B)', None),
    ('src', 'local(A    B)', None),
    ('src', 'local(   A  B   )', None),
    ('src', 'local(default A)', None),
    ('src', 'local(inherit A)', None),
    ('src', 'local(revert A)', None),
    ('src', 'local(unset A)', None),
    ('src', 'local("default")', None),
    ('src', 'local("inherit")', None),
    ('src', 'local("revert")', None),
    ('src', 'local("unset")', None),
    ('src', 'url("foo.ttf")', None),
    ('src', 'url("foo.ttf"), url("bar.ttf")', None),
    ('src', 'url("foo.ttf") format("collection")', None),
    ('src', 'url("foo.ttf") format("opentype")', None),
    ('src', 'url("foo.ttf") format("truetype")', None),
    ('src', 'url("foo.ttf") format("woff")', None),
    ('src', 'url("foo.ttf") format("woff2")', None),
    ('src', 'url("foo.ttf") format(collection)', None),
    ('src', 'url("foo.ttf") format(opentype)', None),
    ('src', 'url("foo.ttf") format(truetype)', None),
    ('src', 'url("foo.ttf") format(woff)', None),
    ('src', 'url("foo.ttf") format(woff2)', None),
    ('src', 'url("foo.ttf") format("embedded-opentype"), url("bar.html")', None),
    ('src', 'url("foo.ttf") format(embedded-opentype), url("bar.html")', None),
    ('src', 'url("foo.ttf") format("svg"), url("bar.html")', None),
    ('src', 'url("foo.ttf") format(svg), url("bar.html")', None),
    ('src', 'url("foo.ttf")', None),
    ('src', 'url("foo.ttf") tech(features-opentype)', None),
    ('src', 'url("foo.ttf") tech(features-aat)', None),
    ('src', 'url("foo.ttf") tech(color-COLRv0)', None),
    ('src', 'url("foo.ttf") tech(color-COLRv1)', None),
    ('src', 'url("foo.ttf") tech(color-sbix)', None),
    ('src', 'url("foo.ttf") tech(color-CBDT)', None),
    ('src', 'url("foo.ttf") tech(variations)', None),
    ('src', 'url("foo.ttf") tech(palettes)', None),
    ('src', 'url("foo.ttf") tech(features-opentype, color-COLRv0, variations, palettes)', None),
    ('src', 'url("foo.ttf") format(opentype) tech(features-opentype)', None),
    ('src', 'url("foo.ttf") tech(incremental), url("bar.html")', None),
    ('src', 'url("foo.ttf") tech(incremental, color-SVG, features-graphite, features-aat), url("bar.html")', None),
    ('src', 'url("foo.ttf") tech(color-SVG, features-graphite), url("bar.html")', None),
    ('src', 'url("foo.ttf") tech(color-SVG), url("bar.html")', None),
    ('src', 'url("foo.ttf") tech(features-graphite), url("bar.html")', None),
)

INVALID_FONT_FACE_DESCRIPTOR_CASES: tuple[tuple[str, str], ...] = (
    ('ascent-override', '-1%'),
    ('descent-override', '-1%'),
    ('line-gap-override', '-1%'),
    ('ascent-override', '10px'),
    ('descent-override', '10px'),
    ('line-gap-override', '10px'),
    ('size-adjust', '-100%'),
    ('size-adjust', '-1%'),
    ('font-width', '-1%'),
    ('font-stretch', '-1%'),
    ('font-width', '125'),
    ('font-stretch', '125'),
    ('font-width', 'condensed expanded normal'),
    ('font-stretch', 'condensed expanded normal'),
    ('font-width', 'normal, expanded'),
    ('font-stretch', 'normal, expanded'),
    ('src', 'local(A) dummy()'),
    ('src', 'dummy() local(A)'),
    ('src', 'local(default)'),
    ('src', 'local(inherit)'),
    ('src', 'local(revert)'),
    ('src', 'local(unset)'),
    ('src', 'url("foo.ttf") format()'),
    ('src', 'url("foo.ttf") dummy()'),
    ('src', 'url("foo.ttf") format("woff") dummy()'),
    ('src', 'url("foo.ttf") dummy() format("woff")'),
    ('src', 'url("foo.ttf") format("opentype", "truetype")'),
    ('src', 'url("foo.ttf") format(opentype, truetype)'),
    ('src', 'url("foo.ttf") format(opentype truetype)'),
    ('src', 'url("foo.ttf") format(auto)'),
    ('src', 'url("foo.ttf") format(default)'),
    ('src', 'url("foo.ttf") format(inherit)'),
    ('src', 'url("foo.ttf") format(initial)'),
    ('src', 'url("foo.ttf") format(none)'),
    ('src', 'url("foo.ttf") format(normal)'),
    ('src', 'url("foo.ttf") format(xyzzy)'),
    ('src', 'url("foo.ttf") tech()'),
    ('src', 'url("foo.ttf") tech("features-opentype")'),
    ('src', 'url("foo.ttf") tech("color-COLRv0")'),
    ('src', 'url("foo.ttf") tech("variations")'),
    ('src', 'url("foo.ttf") tech(features-opentype color-COLRv0 variations palettes)'),
    ('src', 'url("foo.ttf") tech(feature-opentype)'),
    ('src', 'url("foo.ttf") tech(feature-aat)'),
    ('src', 'url("foo.ttf") tech(feature-graphite)'),
    ('src', 'url("foo.ttf") tech(auto)'),
    ('src', 'url("foo.ttf") tech(default)'),
    ('src', 'url("foo.ttf") tech(inherit)'),
    ('src', 'url("foo.ttf") tech(initial)'),
    ('src', 'url("foo.ttf") tech(none)'),
    ('src', 'url("foo.ttf") tech(normal)'),
    ('src', 'url("foo.ttf") tech(xyzzy)'),
    ('src', 'url("foo.ttf") tech(xyzzy, features-opentype)'),
    ('src', 'url("foo.ttf") tech(features-opentype, xyzzy)'),
    ('src', 'url("foo.ttf") tech(features-opentype) format(opentype)'),
    ('src', 'url("foo.ttf") dummy("opentype") tech(variations)'),
    ('src', 'url("foo.ttf") dummy("opentype") dummy(variations)'),
    ('src', 'url("foo.ttf") format(opentype) tech(features-opentype) dummy(something)'),
)

XFAIL_VALID_FONT_FACE_DESCRIPTOR_CASES: tuple[tuple[str, str, str], ...] = (
    ('src', 'url("foo.ttf") format(xyzz 200px), url("bar.html")', 'font-src-list-per-item-fallback-recovery'),
    ('src', 'url("foo.ttf") format(xyzz), url("bar.html")', 'font-src-list-per-item-fallback-recovery'),
    ('src', 'url("foo.ttf") dummy(xyzzy), url("bar.html")', 'font-src-list-per-item-fallback-recovery'),
    ('src', 'url("foo.ttf") format(), url("bar.html")', 'font-src-list-per-item-fallback-recovery'),
    ('src', 'url("foo.ttf") format(none), url("bar.html")', 'font-src-list-per-item-fallback-recovery'),
    ('src', 'url("foo.ttf") format(dummy), url("foo.ttf") tech(variations)', 'font-src-list-per-item-fallback-recovery'),
    ('src', 'url("foo.ttf") tech(color), url("bar.html")', 'font-src-list-per-item-fallback-recovery'),
)


@pytest.mark.parametrize(("descriptor", "raw_css", "expected"), VALID_FONT_FACE_DESCRIPTOR_CASES)
def test_wpt_font_face_descriptor_valid(descriptor: str, raw_css: str, expected: str | None) -> None:
    got = canonicalize(descriptor, raw_css, at_rule="@font-face", options=CSSOM_OPTS)
    if expected is not None:
        assert got == expected
    # Round-trip idempotence and eager FontFace node construction
    assert canonicalize(descriptor, got, at_rule="@font-face", options=CSSOM_OPTS) == got
    rule = FontFace({descriptor: raw_css}, options=CSSOM_OPTS)
    assert got in str(rule)


@pytest.mark.parametrize(("descriptor", "raw_css"), INVALID_FONT_FACE_DESCRIPTOR_CASES)
def test_wpt_font_face_descriptor_invalid(descriptor: str, raw_css: str) -> None:
    with pytest.raises(StixisError):
        canonicalize(descriptor, raw_css, at_rule="@font-face", options=CSSOM_OPTS)


def test_wpt_font_face_descriptor_valid_xfail() -> None:
    unexpected_passes: list[tuple[str, str]] = []
    for descriptor, raw_css, _reason in XFAIL_VALID_FONT_FACE_DESCRIPTOR_CASES:
        try:
            canonicalize(descriptor, raw_css, at_rule="@font-face", options=CSSOM_OPTS)
        except StixisError:
            continue
        unexpected_passes.append((descriptor, raw_css))
    if unexpected_passes:
        pytest.fail(f"XPASS: {len(unexpected_passes)} @font-face xfail case(s) now pass ({unexpected_passes[0]!r}); regenerate WPT tests")
    pytest.xfail(f"{len(XFAIL_VALID_FONT_FACE_DESCRIPTOR_CASES)} known @font-face xfail cases")
