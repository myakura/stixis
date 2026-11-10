"""Unit tests for PEP 750 CSS template rendering and context-aware escaping."""

from __future__ import annotations

import pytest

from stixis import (
    CommentNode,
    FontFace,
    StixisValidationError,
    Url,
    dumps,
    serialize_value,
)


def test_pep750_template_integration_and_context_escaping() -> None:
    url_val = "https://example.com/fonts/Noto Sans (v1).woff2"
    src_css = serialize_value(
        "src",
        t"url({url_val}) format(woff2) tech(variations)",
        at_rule="@font-face",
    )
    assert (
        src_css
        == 'url("https://example.com/fonts/Noto Sans (v1).woff2") format(woff2) tech(variations)'
    )

    lo, hi = -10, 20
    style_css = serialize_value("font-style", t"oblique {lo}deg {hi}deg", at_rule="@font-face")
    assert style_css == "oblique -10deg 20deg"

    # Comment injection prevention (`*/` inside interpolated slot is neutralized to `* /`)
    slice_id = "latin-ext"
    malicious_desc = "injected */ body { display: none } /*"
    kb = 14.256
    ff = FontFace(
        font_family="Inter",
        src=Url("inter.woff2"),
        comment=t"/* [{slice_id}] {malicious_desc} ({kb:.1f} KB) */",
    )
    out = dumps(ff)
    assert "*/ body" not in out
    assert "/* [latin-ext] injected * / body { display: none } /* (14.3 KB) */" in out

    # Plain string comment with premature `*/` is rejected eagerly, while consecutive `*` before `*/` is normalized
    with pytest.raises(StixisValidationError):
        CommentNode("premature */ close")
    assert str(CommentNode("/* foo**/")) == "/* foo* */"

    # Comment Template without outer `/* ... */` is evaluated in comment context from the start
    from stixis._syntax import render_css_template, tokenize_css

    name = "header"
    assert str(CommentNode(t"Don't edit: {name}")) == "/* Don't edit: header */"
    assert str(CommentNode(t"Section '{name}'")) == "/* Section 'header' */"

    multiline = "line1\nline2\r\nline3\fline4"
    rendered_multi = str(CommentNode(t"Generated: {multiline}"))
    assert "\n" not in rendered_multi
    assert rendered_multi == "/* Generated: line1 line2 line3 line4 */"

    # Trailing `*` before closing `*/` in a comment Template is separated from the closing `*/`
    val_star = "foo*"
    node_star = CommentNode(t"/* {val_star}*/")
    assert str(node_star) == "/* foo* */"
    assert tokenize_css(str(node_star)) == []

    # Hyphenated function names ending in `url(` do not trigger unquoted `url()` string wrapping
    arg = "test"
    assert render_css_template(t"image-url({arg})") == "image-url(test)"
    assert render_css_template(t"my-url({arg})") == "my-url(test)"
    assert render_css_template(t"url({arg})") == 'url("test")'


def test_pep750_template_partial_and_multi_slot_url_escaping() -> None:
    from stixis import SerializationOptions, StyleRule
    from stixis._syntax import render_css_template

    slug = "Noto Sans"
    base = "https://example.com"

    # Single-slot url(...) with optional whitespace, <url-modifier>, or trailing comments stays quoted
    assert render_css_template(t"url({base})") == 'url("https://example.com")'
    assert render_css_template(t"url( {base} )") == 'url( "https://example.com" )'
    assert serialize_value("<url>", t"url( {base} )") == 'url("https://example.com")'
    font_path = "font.woff2"
    assert (
        serialize_value("<url>", t"url({font_path} type(font))") == 'url("font.woff2" type(font))'
    )
    assert (
        serialize_value("<url>", t"url({font_path} crossorigin)") == 'url("font.woff2" crossorigin)'
    )
    assert serialize_value("<url>", t"url({font_path} /* comment */)") == 'url("font.woff2")'
    assert serialize_value("<url>", t"url({font_path}/* comment */)") == 'url("font.woff2")'

    # Static prefix/suffix around an interpolation inside unquoted url(...)
    assert (
        serialize_value("<url>", t"url(https://example.com/{slug}.woff2)")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
    assert serialize_value("<url>", t"url({slug}.woff2)") == 'url("Noto Sans.woff2")'
    assert (
        serialize_value("<url>", t"url(https://example.com/{slug})")
        == 'url("https://example.com/Noto Sans")'
    )

    # Multiple interpolations inside unquoted url(...)
    assert (
        serialize_value("<url>", t"url({base}/{slug}.woff2)")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
    assert (
        serialize_value("<any-value>", t"url({base}/{slug}.woff2)")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
    assert (
        serialize_value(
            "<url>",
            t"url({base}/{slug}.woff2)",
            options=SerializationOptions.cleome(),
        )
        == "url('https://example.com/Noto Sans.woff2')"
    )

    # StyleRule and FontFace integration
    rule = StyleRule("div", {"background-image": t"url(https://example.com/{slug}.woff2)"})
    assert 'background-image: url("https://example.com/Noto Sans.woff2");' in dumps(rule)

    ff = FontFace(font_family="Inter", src=t"url(/fonts/{slug}.woff2) format(woff2)")
    assert 'src: url("/fonts/Noto Sans.woff2") format(woff2);' in dumps(ff)

    # Unsafe <url-token> characters across slots (quotes, parens, backslashes, whitespace, controls, comments)
    tricky_dir = "a\"b'c(d)e\\f"
    tricky_file = "g h\ti\nj/*k*/l\x00\x1f\x7f"
    rendered = render_css_template(t"url(/assets/{tricky_dir}/{tricky_file}.woff2)")
    assert "/*" not in rendered
    assert "*/" not in rendered
    assert serialize_value("<url>", t"url(/assets/{tricky_dir}/{tricky_file}.woff2)") == (
        'url("/assets/a\\"b\'c(d)e\\\\f/g h\\9 i\\a j/*k*/l\ufffd\\1f \\7f .woff2")'
    )

    # Explicitly quoted url("...") and url('...') with multiple slots
    assert (
        serialize_value("<url>", t'url("{base}/{slug}.woff2")')
        == 'url("https://example.com/Noto Sans.woff2")'
    )
    assert (
        serialize_value("<url>", t"url('{base}/{slug}.woff2')")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
