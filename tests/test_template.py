"""Unit tests for comment-only PEP 750 Template rendering and CSS-position rejection."""

from __future__ import annotations

import pytest

from stixis import (
    AtRule,
    CommentNode,
    FontFace,
    LocalFont,
    Px,
    RawDeclaration,
    Rem,
    SerializationOptions,
    StixisValidationError,
    StyleRule,
    StyleSheet,
    Url,
    dumps,
    serialize_value,
)
from stixis._syntax.tokenizer import tokenize_css


def test_comment_template_sanitization_and_all_comment_paths() -> None:
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

    with pytest.raises(StixisValidationError):
        CommentNode("premature */ close")
    assert str(CommentNode("/* foo**/")) == "/* foo* */"

    name = "header"
    assert str(CommentNode(t"Don't edit: {name}")) == "/* Don't edit: header */"
    assert str(CommentNode(t"Section '{name}'")) == "/* Section 'header' */"

    multiline = "line1\nline2\r\nline3\fline4"
    rendered_multi = str(CommentNode(t"Generated: {multiline}"))
    assert "\n" not in rendered_multi
    assert rendered_multi == "/* Generated: line1 line2 line3 line4 */"

    val_star = "foo*"
    node_star = CommentNode(t"/* {val_star}*/")
    assert str(node_star) == "/* foo* */"
    assert tokenize_css(str(node_star)) == []

    slash_payload = "/ body { display: none }"
    star = "*"
    for boundary_node in (
        CommentNode(t"*{slash_payload}"),
        CommentNode(t"/* {star}/ body */"),
        CommentNode(t"/*/"),
    ):
        rendered = str(boundary_node)
        assert rendered.startswith("/*")
        assert rendered.endswith("*/")
        assert tokenize_css(rendered) == []

    sheet = StyleSheet(
        header_comment=t"Header {malicious_desc}",
        rules=[
            t"Top {malicious_desc}",
            StyleRule("body", {"margin": Px(0)}, comment=t"Rule {malicious_desc}"),
            AtRule(
                "@media",
                "(min-width: 48rem)",
                comment=t"Media {malicious_desc}",
                rules=[
                    t"Nested {malicious_desc}",
                    StyleRule("p", {"color": "red"}),
                ],
            ),
            ff,
        ],
    )
    sheet_css = dumps(sheet)
    assert "*/ body" not in sheet_css
    assert "/* Header injected * / body { display: none } /* */" in sheet_css
    assert "/* Top injected * / body { display: none } /* */" in sheet_css
    assert "/* Rule injected * / body { display: none } /* */" in sheet_css
    assert "/* Media injected * / body { display: none } /* */" in sheet_css
    assert "  /* Nested injected * / body { display: none } /* */" in sheet_css


def test_comment_template_snapshots_mutable_and_custom_interpolations() -> None:
    items = ["alpha"]
    cleome = SerializationOptions.cleome()

    class CustomUrl(Url):
        def __str__(self) -> str:
            return "custom-url"

    class CustomLocal(LocalFont):
        def __str__(self) -> str:
            return "custom-local"

    class InheritedUrl(Url):
        pass

    class InheritedLocal(LocalFont):
        pass

    c_node = CommentNode(
        t"{items} {Url('a.woff2')} {LocalFont('Inter')} {InheritedUrl('c.woff2')} {InheritedLocal('Serif')} {CustomUrl('b.woff2')} {CustomLocal('Mono')}"
    )
    s_rule = StyleRule("a", {"color": "red"}, comment=t"{items} {Url('a.woff2')}")
    sheet = StyleSheet([], header_comment=t"{items} {Url('a.woff2')}")
    items.append("beta")

    assert (
        c_node.format(options=cleome)
        == "/* ['alpha'] url('a.woff2') local('Inter') url('c.woff2') local('Serif') custom-url custom-local */"
    )
    assert "/* ['alpha'] url('a.woff2') */" in s_rule.format(options=cleome)
    assert "/* ['alpha'] url('a.woff2') */" in sheet.format(options=cleome)


def test_comment_template_evaluates_interpolations_once_before_rendering() -> None:
    cleome = SerializationOptions.cleome()

    class CountingStr:
        def __init__(self, label: str) -> None:
            self.label = label
            self.str_calls = 0
            self.format_calls = 0

        def __str__(self) -> str:
            self.str_calls += 1
            return f"{self.label}#{self.str_calls}"

        def __format__(self, spec: str) -> str:
            self.format_calls += 1
            return f"{self.label}:{spec}#{self.format_calls}"

    class OneShotStr:
        def __init__(self, value: str) -> None:
            self._it = iter((value,))

        def __str__(self) -> str:
            return next(self._it)

        def __format__(self, spec: str) -> str:
            return f"{next(self._it)}:{spec}"

    c_str = CountingStr("s")
    c_fmt = CountingStr("f")
    c_node = CommentNode(t"{c_str} {c_fmt:tag} {Url('a.woff2')}")
    assert c_str.str_calls == 1
    assert c_fmt.format_calls == 1
    assert str(c_node) == '/* s#1 f:tag#1 url("a.woff2") */'
    assert c_node.format(options=cleome) == "/* s#1 f:tag#1 url('a.woff2') */"
    assert c_str.str_calls == 1
    assert c_fmt.format_calls == 1

    o_node = CommentNode(t"{OneShotStr('once-node')} {Url('a.woff2')}")
    o_rule = StyleRule("a", {"color": "red"}, comment=t"{OneShotStr('once-rule')} {Url('a.woff2')}")
    o_at = AtRule(
        "@media",
        "(min-width: 48rem)",
        comment=t"{OneShotStr('once-at')}",
        rules=[t"{OneShotStr('once-child')}"],
    )
    o_ff = FontFace(
        font_family="Inter",
        src=Url("inter.woff2"),
        comment=t"{OneShotStr('once-ff')}",
    )
    o_sheet = StyleSheet(
        rules=[t"{OneShotStr('once-top')}", o_rule, o_at, o_ff],
        header_comment=t"{OneShotStr('once-hdr')} {Url('b.woff2')}",
    )

    assert str(o_node) == '/* once-node url("a.woff2") */'
    assert o_node.format(options=cleome) == "/* once-node url('a.woff2') */"
    assert "/* once-rule url('a.woff2') */" in o_rule.format(options=cleome)
    assert "/* once-at */" in o_at.format(options=cleome)
    assert "/* once-child */" in o_at.format(options=cleome)
    assert "/* once-ff */" in o_ff.format(options=cleome)
    cleome_sheet = o_sheet.format(options=cleome)
    assert "/* once-hdr url('b.woff2') */" in cleome_sheet
    assert "/* once-top */" in cleome_sheet


def test_css_syntax_positions_reject_template_including_pr116_edge_cases() -> None:
    payload = '"], body /*'
    digit_two = "2"
    with pytest.raises(StixisValidationError):
        StyleRule(t'[data-x="a\\{payload}"] /*end*/', {"color": "red"})
    with pytest.raises(StixisValidationError):
        serialize_value("content", t'"\\41{digit_two}"')
    with pytest.raises(StixisValidationError):
        serialize_value("content", t'"a\\{""}"b"')

    with pytest.raises(StixisValidationError):
        serialize_value("<url>", t"url(a\\{')red'})")
    with pytest.raises(StixisValidationError):
        serialize_value("<url>", t"url(\\41{digit_two})")
    with pytest.raises(StixisValidationError):
        serialize_value("background-image", t"\\url({'a),url(b'})")
    with pytest.raises(StixisValidationError):
        serialize_value("<url>", t"u\\72 l({'Noto Sans.woff2'})")

    comment_break = "*/body/*"
    slash = "/"
    slash_payload = "/ body { display: none }"
    with pytest.raises(StixisValidationError):
        StyleRule(t".ok /{' ' if False else ''}*{comment_break}*/", {"color": "red"})
    with pytest.raises(StixisValidationError):
        StyleRule(t".ok {slash}*{comment_break}*/", {"color": "red"})
    with pytest.raises(StixisValidationError):
        serialize_value("color", t"/* *{slash_payload} */")

    base = "https://example.com"
    slug = "Noto Sans"
    url_val = "https://example.com/fonts/Noto Sans (v1).woff2"
    lo, hi = -10, 20
    name = "accent"

    for target, val, at_rule in (
        ("<url>", t"url({base}/{slug}.woff2)", None),
        ("<any-value>", t"url({base}/{slug}.woff2)", None),
        ("src", t"url({url_val}) format(woff2) tech(variations)", "@font-face"),
        ("font-style", t"oblique {lo}deg {hi}deg", "@font-face"),
        ("margin", [Px(10), t"{Px(20)}"], None),
    ):
        with pytest.raises(StixisValidationError):
            serialize_value(target, val, at_rule=at_rule)

    with pytest.raises(StixisValidationError):
        FontFace(font_family="Inter", src=t"url(/fonts/{slug}.woff2) format(woff2)")
    with pytest.raises(StixisValidationError):
        FontFace(font_family="Inter", src=[t"url(/fonts/{slug}.woff2)"])
    with pytest.raises(StixisValidationError):
        RawDeclaration("--t", t"url({'a.woff2'})")
    with pytest.raises(StixisValidationError):
        StyleRule(":root", {"--t": t"{Url('b.woff2')}"})
    with pytest.raises(StixisValidationError):
        StyleRule("div", {"margin": [Px(10), t"{Px(20)}"]})
    with pytest.raises(StixisValidationError):
        StyleRule(t".a", {"color": "red"})
    with pytest.raises(StixisValidationError):
        StyleRule([t".a"], {"color": "red"})
    with pytest.raises(StixisValidationError):
        AtRule("@media", t"(min-width: {Rem(48)})")
    with pytest.raises(StixisValidationError):
        AtRule("@media", [t"(min-width: {Rem(48)})"])
    with pytest.raises(StixisValidationError):
        AtRule("@property", t"--{name}", descriptors={"syntax": '"*"', "inherits": "false"})
    with pytest.raises(StixisValidationError):
        AtRule("@layer", [t"base"])
