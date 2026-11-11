"""Unit tests for PEP 750 CSS template rendering and context-aware escaping."""

from __future__ import annotations

from string.templatelib import Interpolation, Template

import pytest

from stixis import (
    CommentNode,
    FontFace,
    SerializationOptions,
    StixisSyntaxError,
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

    # Multi-slot unquoted URL concatenation (including spaceless adjacent slots and `/*` path segments)
    base_slash = "https://example.com/"
    filename = "Noto Sans.woff2"
    assert (
        serialize_value("<url>", t"url({base_slash}{filename})")
        == 'url("https://example.com/Noto Sans.woff2")'
    )
    for tail_seg in ("foo(bar", "a) foo(bar", "x\"y'z\\w\n\x00*"):
        assert serialize_value("<url>", t"url({base_slash}{tail_seg})") == serialize_value(
            "<url>", Url(base_slash + tail_seg.replace("\x00", "\ufffd"))
        )
    assert (
        serialize_value("<url>", t"url({base}/*/{slug}.woff2)")
        == 'url("https://example.com/*/Noto Sans.woff2")'
    )
    assert (
        serialize_value("<url>", t"url({base}/*dir*/{slug}.woff2)")
        == 'url("https://example.com/*dir*/Noto Sans.woff2")'
    )
    # Spaceless `t"url({a}{b})"` and `t"url({a}suffix)"` enter unquoted `<url-token>` mode (`css-syntax-3` §4.3.6)
    path = "font.woff2"
    modifier = "type(font)"
    assert serialize_value("<url>", t"url({path}{modifier})") == 'url("font.woff2type(font)")'
    assert serialize_value("<url>", t"url({path}crossorigin)") == 'url("font.woff2crossorigin")'
    assert render_css_template(t"url({path}type(font))") == "url(font.woff2type(font))"
    with pytest.raises(StixisValidationError, match="Bad URL token"):
        serialize_value("<url>", t"url({path}type(font))")

    # Multiple sequential `url(...)` expressions in one template
    assert (
        render_css_template(t"url(https://example.com/{slug}.woff2), url({path})")
        == 'url(https://example.com/Noto\\ Sans.woff2), url("font.woff2")'
    )


def test_template_url_single_slot_modifiers_and_token_boundaries() -> None:
    from stixis._syntax import render_css_template

    site = "https://example.com"
    assert render_css_template(t"url({site})") == 'url("https://example.com")'
    assert render_css_template(t"url( {site} )") == 'url( "https://example.com" )'
    assert (
        render_css_template(t"url({site})", options=SerializationOptions.cleome())
        == "url('https://example.com')"
    )
    assert render_css_template(t"url({site}") == 'url("https://example.com"'

    # Whitespace- or comment-separated `<url-modifier>` forms (`css-values-4` §4.5)
    path = "font.woff2"
    ident_mod = "crossorigin"
    mime = "woff2"
    head = 'type("'
    tail = '")'
    head_bs = "type(\\"
    c1 = "/* a"
    c2 = "b */"
    nested_slug = "Noto Sans"
    assert serialize_value("<url>", t"url({path} type(font))") == 'url("font.woff2" type(font))'
    assert serialize_value("<url>", t"url({path} {ident_mod})") == 'url("font.woff2" crossorigin)'
    assert serialize_value("<url>", t"url({path} type({mime}))") == 'url("font.woff2" type(woff2))'
    assert serialize_value("<url>", t"url({path}/*c*/)") == 'url("font.woff2")'
    assert (
        serialize_value("<url>", t"url({path}/*c*/ crossorigin)") == 'url("font.woff2" crossorigin)'
    )
    assert (
        serialize_value("<url>", t"url({path} {head}woff2{tail})")
        == 'url("font.woff2" type("woff2"))'
    )
    assert (
        serialize_value("<url>", t"url({path} {head}w)off2{tail})")
        == 'url("font.woff2" type("w)off2"))'
    )
    assert (
        serialize_value("<url>", t"url({path} {head_bs})woff2))")
        == 'url("font.woff2" type(\\)woff2))'
    )
    assert (
        serialize_value("<url>", t"url({path} {c1}) {c2} crossorigin)")
        == 'url("font.woff2" crossorigin)'
    )
    assert (
        serialize_value("<url>", t"url({path} foo(url(https://example.com/*/{nested_slug}.woff2)))")
        == 'url("font.woff2" foo(url("https://example.com/*/Noto Sans.woff2")))'
    )

    # Escaped `url` function names (`css-syntax-3` §4.3.4 & §4.3.7)
    injected = "a),url(b"
    spaced_font = "Noto Sans.woff2"
    assert serialize_value("background-image", t"\\url({injected})") == 'url("a),url(b")'
    assert serialize_value("<url>", t"u\\72 l({spaced_font})") == 'url("Noto Sans.woff2")'
    assert serialize_value("<url>", t"\\75{''} rl({spaced_font})") == 'url("Noto Sans.woff2")'
    assert serialize_value("<url>", t"\\{''}url({spaced_font})") == 'url("Noto Sans.woff2")'

    # Complete CSS token boundaries (`css-syntax-3` §4.3.1, §4.3.3, §4.3.4, §4.3.6, §4.3.12)
    value = "red"
    num_one = 1
    fn_word = "url"
    hex_esc_one = "\\31 "
    url_star_num = "url(https://example.com/*a)1"
    assert render_css_template(t"1url({value})") == "1url(red)"
    assert serialize_value("--x", t"1url({value})") == "1url (red)"
    assert render_css_template(t"{num_one}url({value})") == "1url(red)"
    assert render_css_template(t"1{fn_word}({value})") == "1url(red)"
    assert render_css_template(t"{fn_word}({value})") == "url(red)"
    assert render_css_template(t"{hex_esc_one}url({value})") == "\\31 url(red)"
    assert (
        render_css_template(t"{url_star_num}url({value})") == "url(https://example.com/*a)1url(red)"
    )
    assert render_css_template(t"#url({value})") == "#url(red)"
    assert render_css_template(t"@url({value})") == "@url(red)"
    assert render_css_template(t"--url({value})") == "--url(red)"
    assert render_css_template(t"url\\(({value})") == "url\\((red)"
    assert render_css_template(t"--{''}>url({spaced_font})") == '-->url("Noto Sans.woff2")'
    assert render_css_template(t"<!-{''}-url({spaced_font})") == '<!--url("Noto Sans.woff2")'
    assert render_css_template(t"#\\{''}\nurl({spaced_font})") == '#\\\nurl("Noto Sans.woff2")'

    # Unfinished escapes in compressed prefixes across empty slots (`css-syntax-3` §4.3.12)
    long_ident = "x" * 64
    assert render_css_template(t"#a\\{''}(url({value})") == "#a\\(url(red)"
    assert render_css_template(t"@a\\{''}(url({value})") == "@a\\(url(red)"
    assert render_css_template(t"1a\\{''}(url({value})") == "1a\\(url(red)"
    assert render_css_template(t"{long_ident}\\{''}(url({value})") == f"{long_ident}\\(url(red)"
    assert render_css_template(t"#a\\31{''} url({value})") == "#a\\31 url(red)"

    # Single-pass linear evaluation over 200 sequential slots and stateful formatters
    many_urls = Template(
        *[part for _ in range(200) for part in ("url(", Interpolation("a", "path"), ") ")]
    )
    assert render_css_template(many_urls) == 'url("a") ' * 200

    order: list[str] = []

    class OnceValue:
        def __init__(self, label: str, text: str) -> None:
            self._label = label
            self._values = iter([text])

        def __str__(self) -> str:
            order.append(self._label)
            return next(self._values)

        def __format__(self, spec: str) -> str:
            order.append(f"{self._label}:{spec}")
            return format(next(self._values), spec)

    p1 = OnceValue("p1", "font.woff2")
    m1 = OnceValue("m1", "crossorigin")
    assert serialize_value("<url>", t"url({p1} {m1})") == 'url("font.woff2" crossorigin)'
    assert order == ["p1", "m1"]

    order.clear()
    p2 = OnceValue("p2", "https://example.com/")
    f2 = OnceValue("f2", "Noto Sans.woff2")
    assert (
        serialize_value("<url>", t"url({p2}{f2:s})") == 'url("https://example.com/Noto Sans.woff2")'
    )
    assert order == ["p2", "f2:s"]


def test_template_pending_escape_and_split_comment_security() -> None:
    from stixis._syntax import render_css_template, tokenize_css

    # 1. Pending static `\` or unfinished hex escape inside quotes cannot cancel slot escaping
    payload = '"], body /*'
    rule = StyleRule(t'[data-x="a\\{payload}"] /*end*/', {"color": "red"})
    sel_tokens = tokenize_css(rule._selector_str)
    assert "," not in [tok.value for tok in sel_tokens]
    str_tokens = [tok for tok in sel_tokens if tok.kind == "string"]
    assert len(str_tokens) == 1
    assert str_tokens[0].value == 'a\\"], body /*'

    digit_two = "2"
    assert serialize_value("content", t'"\\41{digit_two}"') == '"A2"'

    with pytest.raises(StixisSyntaxError, match="Unclosed CSS string quote"):
        render_css_template(t'"a\\{""}"')
    assert serialize_value("content", t'"a\\{""}"b"') == '"a\\"b"'

    # 2. Pending static `\` or unfinished hex escape inside unquoted `url(...)` cannot break out
    assert serialize_value("<url>", t"url(a\\{')red'})") == 'url("a\\\\)red")'
    assert serialize_value("<url>", t"url(\\41{digit_two})") == 'url("A2")'
    assert serialize_value("<url>", t"url(a\\{''})b)") == 'url("a)b")'

    # 3. Empty or dynamic slots splitting `/*` or `*/` cannot bypass comment neutralization
    comment_break = "*/body/*"
    r_empty = StyleRule(t".ok /{' ' if False else ''}*{comment_break}*/", {"color": "red"})
    assert tokenize_css(r_empty._selector_str)[0].value == "."
    assert "body" not in [tok.value for tok in tokenize_css(r_empty._selector_str)]

    slash = "/"
    r_dyn = StyleRule(t".ok {slash}*{comment_break}*/", {"color": "red"})
    assert "body" not in [tok.value for tok in tokenize_css(r_dyn._selector_str)]

    spaced = "Noto Sans.woff2"
    assert (
        render_css_template(t"/* safe *{''}/ url({spaced})") == '/* safe */ url("Noto Sans.woff2")'
    )

    slash_payload = "/ red"
    with pytest.raises(StixisValidationError):
        # `slash_payload` stays inside `/* ... */`, leaving `<color>` token stream empty
        serialize_value("color", t"/* *{slash_payload} */")
