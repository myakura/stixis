"""Unit tests for PEP 750 CSS template rendering and context-aware escaping."""

from __future__ import annotations

from string.templatelib import Interpolation, Template

import pytest

from stixis import (
    CommentNode,
    FontFace,
    SerializationOptions,
    StixisValidationError,
    StyleRule,
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


def test_template_partial_and_multi_slot_url_interpolations() -> None:
    from stixis._syntax import render_css_template

    # Issue #103 acceptance cases (`css-syntax-3` §4.3.6 `<url-token>` & `cssom-1` §2.1 `serialize a URL`)
    slug = "Noto Sans"
    base = "https://example.com"
    assert (
        serialize_value("<url>", t"url(https://example.com/{slug}.woff2)")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
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
    assert (
        str(StyleRule("div", {"background-image": t"url(https://example.com/{slug}.woff2)"}))
        == 'div {\n  background-image: url("https://example.com/Noto Sans.woff2");\n}'
    )
    ff = FontFace(font_family="Inter", src=t"url(/fonts/{slug}.woff2) format(woff2)")
    assert 'src: url("/fonts/Noto Sans.woff2") format(woff2);' in str(ff)

    # Adjacent URL path slots whose trailing tokens are not `<url-modifier>` (`css-values-4` §4.5)
    base_slash = "https://example.com/"
    filename = "Noto Sans.woff2"
    assert (
        serialize_value("<url>", t"url({base_slash}{filename})")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
    unbalanced_tail = "a) foo(bar"
    assert (
        serialize_value("<url>", t"url({base_slash}{unbalanced_tail})")
        == 'url("https://example.com/a) foo(bar")'
    )

    # Unsafe `<url-token>` characters & comment delimiters (`css-syntax-3` §3.3, §4.3.6, §4.3.7)
    weird = "a\"b'c(d)e\\f g\th\ni/*j*/\x00*"
    rendered_weird = render_css_template(t"url(https://example.com/{weird}/k.woff2)")
    assert "/*" not in rendered_weird
    assert "*/" not in rendered_weird
    assert serialize_value("<url>", t"url(https://example.com/{weird}/k.woff2)") == serialize_value(
        "<url>", Url(f"https://example.com/{weird}/k.woff2")
    )

    slash_after_star = "/sub"
    rendered_star_slash = render_css_template(t"url(https://example.com/*{slash_after_star})")
    assert "*/" not in rendered_star_slash
    assert (
        serialize_value("<url>", t"url(https://example.com/*{slash_after_star})")
        == 'url("https://example.com/*/sub")'
    )

    slash_before_star = "sub/"
    rendered_slash_star = render_css_template(t"url(https://example.com/{slash_before_star}*x)")
    assert "/*" not in rendered_slash_star
    assert (
        serialize_value("<url>", t"url(https://example.com/{slash_before_star}*x)")
        == 'url("https://example.com/sub/*x")'
    )

    # Escaped `\)` and literal `/*` in static unquoted URL prefix, plus multiple `url(...)` tokens (`css-syntax-3` §4.3.6)
    assert (
        serialize_value("<url>", t"url(https://example.com/v\\)1/{slug}.woff2)")
        == 'url("https://example.com/v)1/Noto Sans.woff2")'
    )
    assert (
        serialize_value("<url>", t"url(https://example.com/*/{slug}.woff2)")
        == 'url("https://example.com/*/Noto Sans.woff2")'
    )
    file_b = "Noto Sans.woff2"
    assert (
        serialize_value("background-image", t"url(a.woff2), url({file_b})")
        == 'url("a.woff2"), url("Noto Sans.woff2")'
    )


def test_template_url_single_slot_modifiers_and_token_boundaries() -> None:
    from stixis._syntax import render_css_template

    # Single-slot quoted `url(...)` and `SerializationOptions` quote styles (`cssom-1` §2.1 & existing behavior)
    site = "https://example.com"
    assert render_css_template(t"url({site})") == 'url("https://example.com")'
    assert (
        render_css_template(t"url({site})", options=SerializationOptions.cleome())
        == "url('https://example.com')"
    )
    assert render_css_template(t"url({site}") == 'url("https://example.com"'

    # Static, adjacent, dynamic, nested-slot, and commented `<url-modifier>` forms (`css-values-4` §4.5 & existing behavior)
    path = "font.woff2"
    modifier = "type(font)"
    ident_mod = "crossorigin"
    mime = "woff2"
    assert serialize_value("<url>", t"url({path} type(font))") == 'url("font.woff2" type(font))'
    assert serialize_value("<url>", t"url({path}type(font))") == 'url("font.woff2" type(font))'
    assert serialize_value("<url>", t"url({path}{modifier})") == 'url("font.woff2" type(font))'
    assert serialize_value("<url>", t"url({path} {ident_mod})") == 'url("font.woff2" crossorigin)'
    assert serialize_value("<url>", t"url({path} type({mime}))") == 'url("font.woff2" type(woff2))'
    assert serialize_value("<url>", t"url({path}/*c*/)") == 'url("font.woff2")'

    # Escaped `url` function names (`css-syntax-3` §4.3.4 & §4.3.7)
    injected = "a),url(b"
    spaced_font = "Noto Sans.woff2"
    assert serialize_value("background-image", t"\\url({injected})") == 'url("a),url(b")'
    assert serialize_value("<url>", t"u\\72 l({spaced_font})") == 'url("Noto Sans.woff2")'

    # Complete CSS token boundaries (`css-syntax-3` §4.3.3 numeric/dimension & §4.3.4 ident/hash/at-keyword)
    value = "red"
    num_one = 1
    fn_word = "url"
    hex_esc_one = "\\31 "
    assert render_css_template(t"1url({value})") == "1url(red)"
    assert serialize_value("--x", t"1url({value})") == "1url (red)"
    assert render_css_template(t"{num_one}url({value})") == "1url(red)"
    assert render_css_template(t"1{fn_word}({value})") == "1url(red)"
    assert render_css_template(t"{hex_esc_one}url({value})") == "\\31 url(red)"
    assert render_css_template(t"#url({value})") == "#url(red)"
    assert render_css_template(t"@url({value})") == "@url(red)"
    assert render_css_template(t"--url({value})") == "--url(red)"
    assert render_css_template(t"url\\(({value})") == "url\\((red)"

    # Bounded per-`url()` lookahead over 200 sequential slots
    many_urls = Template(
        *[part for _ in range(200) for part in ("url(", Interpolation("a", "path"), ") ")]
    )
    assert render_css_template(many_urls) == 'url("a") ' * 200
