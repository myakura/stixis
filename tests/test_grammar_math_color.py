"""Unit tests for CSS math functions, calc() simplification, anchor positioning, and relative colors."""

from __future__ import annotations

import pytest

from stixis import (
    Keyword,
    SerializationOptions,
    StixisValidationError,
    Url,
    canonicalize,
    serialize_value,
)
from stixis.cli import (
    EXIT_OK,
    EXIT_VALIDATION_ERROR,
)
from stixis.cli import (
    main as cli_main,
)


def test_math_function_calc_sum_structural_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for invalid_expr in (
        "calc(1px + )",
        "calc(foo bar)",
        "calc(1px+2px)",
        "calc(1px +2px)",
        "calc(1px+ 2px)",
        "calc(1px-2px)",
        "calc(1px -2px)",
        "calc(1px * )",
        "min(1px, )",
        "clamp(1px, 2px)",
    ):
        with pytest.raises(StixisValidationError):
            canonicalize("width", invalid_expr)
        with pytest.raises(StixisValidationError):
            serialize_value("width", invalid_expr)

    assert canonicalize("width", "calc(1px + 2px)") == "calc(3px)"
    assert canonicalize("width", "calc((1px + 2px) * 2)") == "calc(6px)"
    assert canonicalize("width", "min(1px, 2em)") == "min(1px, 2em)"
    assert canonicalize("opacity", "round(1.5)") == "round(1.5)"
    assert canonicalize("opacity", "round(up, 1.5)") == "round(up, 1.5)"
    assert canonicalize("width", "calc-size(auto, min(size, 100px))") == (
        "calc-size(auto, min(size, 100px))"
    )
    assert canonicalize("width", "calc(100px * progress(1, 0, 2))") == (
        "calc(100px * progress(1, 0, 2))"
    )
    assert canonicalize("width", "calc(2 * random(--foo, 0px, 100px))") == (
        "calc(2 * random(--foo, 0px, 100px))"
    )
    assert canonicalize("width", "calc(2 * random(0px, 100px))") == ("calc(2 * random(0px, 100px))")
    assert canonicalize("left", "calc(inherit(--x) + 1px)") == "calc(inherit(--x) + 1px)"
    assert canonicalize("color", "alpha(from currentcolor / calc(alpha * 0.5))") == (
        "alpha(from currentcolor / calc(0.5 * alpha))"
    )
    assert canonicalize("color", "ictcp(from red calc(i) calc(ct) calc(cp))") == (
        "ictcp(from red calc(i) calc(ct) calc(cp))"
    )
    assert canonicalize("color", "jzazbz(from red calc(j) calc(a) calc(b))") == (
        "jzazbz(from red calc(j) calc(a) calc(b))"
    )
    assert canonicalize("color", "jzczhz(from red calc(j) calc(c) calc(h))") == (
        "jzczhz(from red calc(j) calc(c) calc(h))"
    )
    assert canonicalize("color", "lab(from red calc(l) calc(a) calc(b))") == (
        "lab(from red calc(l) calc(a) calc(b))"
    )
    for invalid_color in (
        "jzazbz(from red calc(jz) calc(az) calc(bz))",
        "jzczhz(from red calc(jz) calc(cz) calc(hz))",
        "jzazbz(from red calc(jz) calc(a) calc(b))",
        "jzazbz(from red calc(j) calc(az) calc(b))",
        "jzazbz(from red calc(j) calc(a) calc(bz))",
        "jzczhz(from red calc(jz) calc(c) calc(h))",
        "jzczhz(from red calc(j) calc(cz) calc(h))",
        "jzczhz(from red calc(j) calc(c) calc(hz))",
    ):
        with pytest.raises(StixisValidationError):
            canonicalize("color", invalid_color)
        with pytest.raises(StixisValidationError):
            serialize_value("color", invalid_color)
    with pytest.raises(StixisValidationError):
        canonicalize("color", "alpha(from red / calc(r * 0.5))")
    with pytest.raises(StixisValidationError):
        canonicalize("width", "calc(alpha)")

    rc_bad = cli_main(["check", "width", "calc(1px + )"])
    captured = capsys.readouterr()
    assert rc_bad == EXIT_VALIDATION_ERROR

    rc_good = cli_main(["check", "width", "calc((1px + 2px) * 2)"])
    captured = capsys.readouterr()
    assert rc_good == EXIT_OK
    assert captured.out.strip() == "calc(6px)"


def test_context_free_math_expression_simplification(
    capsys: pytest.CaptureFixture[str],
) -> None:
    opts = SerializationOptions(min_engines=0)

    # 1. Folding numbers and same-unit terms
    assert canonicalize("font-weight", "calc(100 + 100)") == "calc(200)"
    assert canonicalize("width", "calc(1px + 2px)") == "calc(3px)"
    assert canonicalize("width", "calc(100px + 100px)") == "calc(200px)"
    assert canonicalize("opacity", "calc(-50% - 50%)") == "calc(-100%)"
    assert canonicalize("font-style", "oblique calc(10deg - 10deg)") == "oblique calc(0deg)"
    assert canonicalize("left", "calc(20px + calc(80px))") == "calc(100px)"
    assert canonicalize("left", "calc(calc(2 * calc(calc(3)) + 4) * 10px)") == "calc(100px)"

    # 2. Sorting terms of a sum (numbers, percentages, dimensions alphabetically, then other sub-expressions)
    assert canonicalize("animation-range-end", "contain calc(10px + 10%)") == (
        "contain calc(10% + 10px)"
    )
    assert canonicalize("background-position-x", "calc(10px - 0.5em)") == "calc(-0.5em + 10px)"
    assert canonicalize("padding-right", "calc(2em + 3%)") == "calc(3% + 2em)"
    assert canonicalize("letter-spacing", "calc(2ch - 30%)") == "calc(-30% + 2ch)"
    assert canonicalize("color", "alpha(from currentcolor / calc(alpha + 0.1))", options=opts) == (
        "alpha(from currentcolor / calc(0.1 + alpha))"
    )
    assert canonicalize("grid-column-start", "span calc(sibling-index() - 2)") == (
        "span calc(-2 + sibling-index())"
    )
    assert canonicalize("top", "calc(anchor(--foo left, 1px) + 10%)", options=opts) == (
        "calc(10% + anchor(--foo left, 1px))"
    )

    # 3. Division by a number rewritten as multiplication and numeric factor placed first
    assert (
        canonicalize(
            "top",
            "calc((anchor(--foo top) + anchor(--bar bottom)) / 2)",
            options=opts,
        )
        == "calc(0.5 * (anchor(--foo top) + anchor(--bar bottom)))"
    )
    assert canonicalize("color", "alpha(from currentcolor / calc(alpha * 0.5))", options=opts) == (
        "alpha(from currentcolor / calc(0.5 * alpha))"
    )
    assert (
        canonicalize(
            "color",
            "alpha(from green / calc(sibling-index() * 0.2))",
            options=opts,
        )
        == "alpha(from green / calc(0.2 * sibling-index()))"
    )
    assert canonicalize("text-decoration-inset", "calc(1em / 4) calc(-1ch)", options=opts) == (
        "calc(0.25em) calc(-1ch)"
    )
    assert canonicalize("animation-range-start", "cover calc(sign(100em - 1px) * 1%)") == (
        "cover calc(1% * sign(100em - 1px))"
    )
    assert (
        canonicalize(
            "flex",
            "1 1 calc(10px + (sign(20cqw - 10px) * 5px))",
        )
        == "1 1 calc(10px + (5px * sign(20cqw - 10px)))"
    )
    assert canonicalize("height", "calc-size(min-content, size * 2)") == (
        "calc-size(min-content, 2 * size)"
    )
    assert canonicalize("max-width", "calc-size(max-content, size / 2)") == (
        "calc-size(max-content, 0.5 * size)"
    )
    assert canonicalize("max-height", "calc-size(fit-content, 30px + size / 2)") == (
        "calc-size(fit-content, 30px + (0.5 * size))"
    )

    # 4. Preserving `-` when already in canonical order and keeping out-of-scope units untouched
    assert canonicalize("width", "calc(10% - 0.5em)") == "calc(10% - 0.5em)"
    assert canonicalize("width", "calc(1in + 1px)") == "calc(1in + 1px)"
    assert (
        canonicalize("width", "calc((10000000px + 100%) / 10000000)")
        == "calc((100% + 10000000px) / 10000000)"
    )
    assert (
        canonicalize("width", "calc((1000000px + 100%) / 600000)")
        == "calc((100% + 1000000px) / 600000)"
    )
    assert (
        canonicalize("width", "calc(0.0001 * 0.0001 * (1000000px + 100%))")
        == "calc(0.0001 * 0.0001 * (100% + 1000000px))"
    )
    assert canonicalize("width", "calc(10000000px / 10000000)") == "calc(1px)"

    # 5. CLI check
    rc = cli_main(["check", "background-position-x", "calc(10px - 0.5em)"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert captured.out.strip() == "calc(-0.5em + 10px)"


def test_anchor_and_anchor_size_on_inset_and_in_math_functions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    opts = SerializationOptions(min_engines=0)
    for prop in (
        "top",
        "right",
        "bottom",
        "left",
        "inset-block-start",
        "inset-block-end",
        "inset-inline-start",
        "inset-inline-end",
        "inset",
        "inset-block",
        "inset-inline",
    ):
        assert canonicalize(prop, "anchor(--foo top)", options=opts) == "anchor(--foo top)"
        assert canonicalize(prop, "anchor-size(--foo width)", options=opts) == (
            "anchor-size(--foo width)"
        )

    for value in (
        "anchor(--foo top)",
        "anchor(top --foo)",
        "anchor(--foo 50%, 10px)",
        "calc((anchor(--foo top) + anchor(--bar bottom)) / 2)",
        "calc(0.5 * (anchor(--foo top) + anchor(--bar bottom)))",
        "min(100px, 10%, anchor(--foo top), anchor(--bar bottom))",
        "calc(anchor(--foo left, 1px) + 10%)",
        "anchor(--foo top, calc(0.5 * anchor(--bar bottom)))",
        "anchor-size(width, anchor(top))",
    ):
        out = canonicalize("top", value, options=opts)
        assert out

    for size_prop in (
        "width",
        "height",
        "min-width",
        "max-width",
        "block-size",
        "inline-size",
        "margin-top",
        "margin-inline-start",
    ):
        assert canonicalize(size_prop, "anchor-size(--foo width)", options=opts) == (
            "anchor-size(--foo width)"
        )
        assert (
            canonicalize(
                size_prop,
                "calc(0.5 * (anchor-size(--foo width) + anchor-size(--bar height)))",
                options=opts,
            )
            == "calc(0.5 * (anchor-size(--foo width) + anchor-size(--bar height)))"
        )
        with pytest.raises(StixisValidationError):
            canonicalize(size_prop, "anchor(--foo top)", options=opts)
        with pytest.raises(StixisValidationError):
            canonicalize(size_prop, "anchor-size(width, anchor(top))", options=opts)
        with pytest.raises(StixisValidationError):
            canonicalize(size_prop, "calc(anchor(--foo top) + 10px)", options=opts)

    with pytest.raises(StixisValidationError):
        canonicalize("font-size", "calc(anchor(--foo top) + 10px)", options=opts)
    with pytest.raises(StixisValidationError):
        canonicalize("font-size", "calc(anchor-size(--foo width) + 10px)", options=opts)
    with pytest.raises(StixisValidationError):
        canonicalize("flex-basis", "anchor-size(--a width, 10px)", options=opts)
    with pytest.raises(StixisValidationError):
        canonicalize("flex-basis", "calc(anchor-size(--a width, 10px) + 1px)", options=opts)

    rc1 = cli_main(
        [
            "check",
            "top",
            "calc(0.5 * (anchor(--foo top) + anchor(--bar bottom)))",
            "--min-engines",
            "0",
        ]
    )
    captured = capsys.readouterr()
    assert rc1 == EXIT_OK
    assert captured.out.strip() == "calc(0.5 * (anchor(--foo top) + anchor(--bar bottom)))"

    rc2 = cli_main(["check", "top", "anchor(--foo top)", "--min-engines", "0"])
    captured = capsys.readouterr()
    assert rc2 == EXIT_OK
    assert captured.out.strip() == "anchor(--foo top)"


def test_relative_color_bare_channel_keywords(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Issue #70: relative color functions accept bare channel keywords as operands in channel positions."""
    assert canonicalize("color", "rgb(from rebeccapurple r g b)") == "rgb(from rebeccapurple r g b)"
    assert (
        canonicalize("color", "rgb(from rebeccapurple r g b / alpha)")
        == "rgb(from rebeccapurple r g b / alpha)"
    )
    assert canonicalize("color", "lab(from red l a b)") == "lab(from red l a b)"
    assert canonicalize("color", "oklab(from red l a b / alpha)") == "oklab(from red l a b / alpha)"
    assert canonicalize("color", "lch(from red l c h)") == "lch(from red l c h)"
    assert canonicalize("color", "oklch(from red l c h)") == "oklch(from red l c h)"
    assert canonicalize("color", "hsl(from red h s l)") == "hsl(from red h s l)"
    assert canonicalize("color", "hwb(from red h w b)") == "hwb(from red h w b)"
    assert canonicalize("color", "jzazbz(from red j a b)") == "jzazbz(from red j a b)"
    assert canonicalize("color", "jzczhz(from red j c h)") == "jzczhz(from red j c h)"
    assert canonicalize("color", "ictcp(from red i ct cp)") == "ictcp(from red i ct cp)"
    assert canonicalize("color", "alpha(from red / alpha)") == "alpha(from red / alpha)"
    assert (
        canonicalize("color", "color(from color(srgb 0.7 0.5 0.3) srgb r g b / alpha)")
        == "color(from color(srgb 0.7 0.5 0.3) srgb r g b / alpha)"
    )
    assert (
        canonicalize("color", "color(from color(xyz-d65 7 -20.5 100) xyz-d65 x y z / alpha)")
        == "color(from color(xyz-d65 7 -20.5 100) xyz-d65 x y z / alpha)"
    )

    # Reject channel keywords in absolute color functions (without `from <color>`)
    for invalid_abs in (
        "rgb(r g b)",
        "rgb(0 0 0 / alpha)",
        "hsl(h s l)",
        "lab(l a b)",
        "color(srgb r g b)",
        "color(xyz x y z)",
        "rgb(from rgb(r g b) r g b)",
    ):
        with pytest.raises(StixisValidationError):
            canonicalize("color", invalid_abs)

    # Reject wrong color-space channel keywords in `color(from ...)` and legacy comma syntax
    for invalid_cs in (
        "rgb(from red, r)",
        "rgba(from red, r)",
        "color(from color(srgb 0.7 0.5 0.3) srgb x y z)",
        "color(from color(srgb 0.7 0.5 0.3) srgb calc(x) g b)",
        "color(from color(srgb 0.7 0.5 0.3) srgb 0 0 0 / x)",
        "color(from color(srgb 0.7 0.5 0.3) srgb 0 0 0 / calc(x))",
        "color(from color(xyz 7 -20.5 100) xyz r g b)",
        "color(from color(xyz 7 -20.5 100) xyz calc(r) y z)",
        "color(from color(xyz 7 -20.5 100) xyz 0 0 0 / r)",
        "color(from color(xyz 7 -20.5 100) xyz 0 0 0 / calc(r))",
    ):
        with pytest.raises(StixisValidationError):
            canonicalize("color", invalid_cs)

    # CLI check
    rc = cli_main(["check", "color", "rgb(from rebeccapurple r g b)"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert captured.out.strip() == "rgb(from rebeccapurple r g b)"

    rc_lab = cli_main(["check", "color", "lab(from red l a b)"])
    captured_lab = capsys.readouterr()
    assert rc_lab == EXIT_OK
    assert captured_lab.out.strip() == "lab(from red l a b)"

    rc_err = cli_main(["check", "color", "rgb(r g b)"])
    _ = capsys.readouterr()
    assert rc_err == EXIT_VALIDATION_ERROR


def test_math_functions_and_token_formatting_have_no_dead_entries() -> None:
    from stixis._grammar.calc import MATH_FUNCTIONS
    from stixis._syntax import format_token_sequence, serialize_url, tokenize_css

    assert all(fn == fn.strip() for fn in MATH_FUNCTIONS)
    assert "sibling-index" in MATH_FUNCTIONS
    assert " sibling-index" not in MATH_FUNCTIONS
    assert format_token_sequence(tokenize_css("calc(10px + 20px / 2)")) == "calc(10px + 20px / 2)"
    assert serialize_url("https://example.com/\x01a") == r'url("https://example.com/\1 a")'


def test_canonical_grammars_not_overwritten_by_unshipped_or_umbrella_drafts() -> None:
    """Issue #101: canonical shipped/base grammars must not be overwritten by excluded or superseded drafts."""
    from stixis._generated.specs import PROPERTY_SYNTAXES, VALUE_SYNTAXES

    opts_all = SerializationOptions(min_engines=0)
    assert canonicalize("color", "contrast-color(white)") == "contrast-color(white)"
    assert (
        canonicalize("color", "if(style(--dark: 1): black; else: white)")
        == "if(style(--dark: 1): black; else: white)"
    )
    assert (
        canonicalize("color", "if(style(--dark: 1): black; else: white;)")
        == "if(style(--dark: 1): black; else: white)"
    )
    assert canonicalize("color", "if(style(): red; else: blue)") == "if(style(): red; else: blue)"
    assert (
        canonicalize("color", "if(media(): red; supports(): green; else: blue)")
        == "if(media(): red; supports(): green; else: blue)"
    )
    assert (
        canonicalize("width", "calc(10px + if(style(--x: 1): 5px; else: 20px))")
        == "calc(10px + if(style(--x: 1): 5px; else: 20px))"
    )
    deep_font_style = "var(--x, " + "(" * 1100 + "normal" + ")" * 1100 + ")"
    assert canonicalize("font-style", deep_font_style) == deep_font_style
    deep_nested_if = "if(else: " * 1100 + "red" + ")" * 1100
    assert canonicalize("color", deep_nested_if) == deep_nested_if
    with pytest.raises(StixisValidationError):
        canonicalize("font-style", "oblique calc(" + "(" * 1100 + "10deg" + ")" * 1100 + ")")
    assert canonicalize("content", "content()") == "content()"
    assert canonicalize("content", "content(marker)") == "content(marker)"
    assert canonicalize("stroke-opacity", "50%", options=opts_all) == "50%"
    assert canonicalize("stroke-width", "2", options=opts_all) == "2"
    assert canonicalize("fill", "rebeccapurple", options=opts_all) == "rebeccapurple"
    assert canonicalize("stroke", "none", options=opts_all) == "none"

    assert VALUE_SYNTAXES["<contrast-color()>"] == "contrast-color( <color> )"
    assert VALUE_SYNTAXES["<if()>"] == "if( [ <if-branch> ; ]* <if-branch> ;? )"
    assert (
        VALUE_SYNTAXES["<content()>"]
        == "content( [ text | before | after | first-letter | marker ]? )"
    )
    assert PROPERTY_SYNTAXES["border-start-start-radius"] == "<border-radius>"
    assert PROPERTY_SYNTAXES["border-block-start-width"] == "<line-width>"

    for prop, bad_val in (
        ("color", "contrast-color(white tbd-fg)"),
        ("color", "if(true, black, white)"),
        ("color", "if(true then black else white)"),
        ("color", "if()"),
        ("color", "if(style())"),
        ("color", "if(: white)"),
        ("color", "if(style(--x: 3) !)"),
        ("color", "if(style(--x: 3): black; else: white!)"),
        ("stroke-opacity", "1px"),
    ):
        with pytest.raises(StixisValidationError):
            canonicalize(prop, bad_val, options=opts_all)


def test_vds_implicit_comma_elision_and_gradients() -> None:
    """Issue #97: CSS Values 4 §2.2.1 implicit comma elision in VDS juxtapositions."""
    for prop, val, expected in (
        ("color", "rgb(0, 0, 255)", "rgb(0, 0, 255)"),
        ("color", "rgb(0, 0, 255, 0.5)", "rgb(0, 0, 255, 0.5)"),
        ("color", "rgba(0, 0, 255)", "rgba(0, 0, 255)"),
        ("color", "hsl(120, 100%, 50%)", "hsl(120, 100%, 50%)"),
        ("color", "hsl(120, 100%, 50%, 0.5)", "hsl(120, 100%, 50%, 0.5)"),
        ("background-image", "linear-gradient(red, blue)", "linear-gradient(red, blue)"),
        (
            "background-image",
            "linear-gradient(45deg, red, 30%, blue)",
            "linear-gradient(45deg, red, 30%, blue)",
        ),
        ("background-image", "radial-gradient(red, blue)", "radial-gradient(red, blue)"),
        ("background-image", "conic-gradient(red, blue)", "conic-gradient(red, blue)"),
        ("color", "color-mix(in srgb, red, blue)", "color-mix(in srgb, red, blue)"),
        ("background", "red", "red"),
        ("background", 'url("a.png"), red', 'url("a.png"), red'),
        ("transition-timing-function", "steps(4)", "steps(4)"),
        ("transition-timing-function", "steps(4, jump-start)", "steps(4, jump-start)"),
        ("transform", "translate(10px)", "translate(10px)"),
        ("transform", "translate(10px, 20px)", "translate(10px, 20px)"),
    ):
        assert canonicalize(prop, val) == expected

    assert serialize_value("background", Keyword("red")) == "red"
    assert (
        serialize_value(
            "background",
            [Url("a.png"), Keyword("red")],
            options=SerializationOptions.cleome(),
        )
        == "url('a.png'), red"
    )

    for prop, bad_val in (
        ("color", "rgb(0, 0, 255, )"),
        ("color", "rgb(, 0, 0, 255)"),
        ("color", "rgb(0, , 255)"),
        ("color", "hsl(120, 100%, 50%, )"),
        ("background-image", "linear-gradient(, red, blue)"),
        ("background-image", "linear-gradient(red, , blue)"),
        ("background-image", "linear-gradient(red, blue, )"),
        ("background-image", "linear-gradient(45deg red, blue)"),
        ("background", ", red"),
        ("background", "red, "),
        ("cursor", 'url("a.png") pointer'),
        ("transition-timing-function", "steps(4, )"),
        ("transform", "translate(10px, )"),
    ):
        with pytest.raises(StixisValidationError):
            canonicalize(prop, bad_val)
