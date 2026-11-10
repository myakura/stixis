"""Unit tests for structured value wrappers, SerializationOptions, microsyntaxes, and top-level API."""

from __future__ import annotations

import pytest

from stixis import (
    Deg,
    FontFace,
    FontSrc,
    GenericFamily,
    Keyword,
    LocalFont,
    Pct,
    RawDeclaration,
    SerializationOptions,
    StixisSyntaxError,
    StixisValidationError,
    StyleRule,
    Url,
    canonicalize,
    compact_urange,
    dumps,
    generic,
    serialize,
    serialize_value,
)
from stixis.cli import (
    EXIT_OK,
    EXIT_VALIDATION_ERROR,
)
from stixis.cli import (
    main as cli_main,
)


def test_top_level_aliases_and_exports() -> None:
    assert serialize is dumps
    rule = StyleRule("body", {"margin": 0, "font_family": ["Inter", generic("sans-serif")]})
    expected = 'body {\n  margin: 0px;\n  font-family: "Inter", sans-serif;\n}'
    assert dumps(rule) == expected
    assert str(rule) == expected
    assert dumps(rule, minify=True) == 'body{margin:0px;font-family:"Inter", sans-serif}'


def test_family_name_vs_generic_family_boundary() -> None:
    # A plain Python `str` passed to `font-family` or `<family-name>` is ALWAYS treated as a
    # font family name (quoted by default), even if its text matches a generic family keyword!
    assert serialize_value("font-family", "serif") == '"serif"'
    assert serialize_value("<family-name>", "serif") == '"serif"'
    assert serialize_value("font-family", "Noto Sans JP") == '"Noto Sans JP"'

    # Generic families are explicitly marked via `generic(...)`, `GenericFamily(...)`, or `Keyword(...)`
    assert serialize_value("font-family", generic("serif")) == "serif"
    assert serialize_value("font-family", GenericFamily("system-ui")) == "system-ui"
    assert serialize_value("font-family", Keyword("monospace")) == "monospace"
    assert (
        serialize_value("font-family", ["Noto Sans JP", "serif", generic("sans-serif")])
        == '"Noto Sans JP", "serif", sans-serif'
    )


def test_cssom_multiword_family_name_with_reserved_or_generic_keyword_stays_quoted() -> None:
    cssom = SerializationOptions.cssom()
    for fam in ("foo inherit", "initial bar", "foo default", "foo serif", "Noto sans-serif"):
        expected = f'"{fam}"'
        assert serialize_value("font-family", fam, options=cssom) == expected
        got = canonicalize("font-family", expected, options=cssom)
        assert got == expected
        assert canonicalize("font-family", got, options=cssom) == expected


def test_value_wrappers_eager_validation_and_dimension_unit_formatting() -> None:
    from stixis import Dimension
    from stixis._syntax.tokenizer import tokenize_css

    for bad_unit in ("", "px;color:red", "px ", " px", "px{}", "2px", "-2px", "-", "%"):
        with pytest.raises(StixisValidationError):
            Dimension(10, bad_unit)

    assert str(Dimension(10, "px")) == "10px"
    assert str(Dimension(1, "e3")) == r"1\65 3"
    toks = [t for t in tokenize_css(str(Dimension(1, "e3"))) if t.kind != "whitespace"]
    assert len(toks) == 1 and toks[0].kind == "dimension" and toks[0].unit == "e3"

    with pytest.raises(StixisValidationError):
        serialize_value("<dimension>", (10, "px;color:red"))

    for bad_kw in ("", 123):
        with pytest.raises(StixisValidationError):
            Keyword(bad_kw)  # type: ignore[arg-type]
    assert str(Keyword("auto")) == "auto"
    assert str(Keyword("   ")) == r"\ \ \ "

    for bad_fam in ("", 123):
        with pytest.raises(StixisValidationError):
            LocalFont(bad_fam)  # type: ignore[arg-type]
    assert str(LocalFont("Inter")) == 'local("Inter")'
    assert str(LocalFont("   ")) == 'local("   ")'

    for bad_gen in ("", "   ", "not-a-generic", "generic(unknown)", 123):
        with pytest.raises(StixisValidationError):
            GenericFamily(bad_gen)  # type: ignore[arg-type]
    assert str(GenericFamily("serif")) == "serif"
    assert str(GenericFamily("generic(kai)")) == "generic(kai)"


def test_value_wrapper_subclasses_accepted() -> None:
    from stixis import Dimension, Percentage

    class CustomDimension(Dimension):
        pass

    class CustomPercentage(Percentage):
        pass

    class CustomUrl(Url):
        pass

    assert serialize_value("width", CustomDimension(10, "px")) == "10px"
    assert serialize_value("<percentage>", CustomPercentage(50)) == "50%"
    assert serialize_value("<url>", CustomUrl("a.png")) == 'url("a.png")'


def test_serialization_options_validates_fields_in_post_init() -> None:
    with pytest.raises(ValueError, match="quote_char"):
        SerializationOptions(quote_char="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="quote_char"):
        SerializationOptions(quote_char="")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="font_face_stretch_alias"):
        SerializationOptions(font_face_stretch_alias="invalid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="min_engines"):
        SerializationOptions(min_engines="2")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="min_engines"):
        SerializationOptions(min_engines=True)
    with pytest.raises(ValueError, match="min_engines"):
        SerializationOptions(min_engines=-1)
    with pytest.raises(ValueError, match="min_engines"):
        SerializationOptions(min_engines=4)
    with pytest.raises(ValueError, match="extra_properties"):
        SerializationOptions(extra_properties={"width"})  # type: ignore[arg-type]


def test_cleome_and_cssom_serialization_options_presets() -> None:
    ff = FontFace(
        font_family="Noto Sans JP",
        font_style=("oblique", Deg(-10), Deg(20)),
        font_weight=(100, 900),
        font_width=(Pct(75), Pct(125)),
        font_display="swap",
        src=[
            FontSrc(url="sub-0.woff2", format="woff2", tech=("variations", "color-COLRv1")),
            LocalFont("Noto Sans JP"),
        ],
        unicode_range=[(0x3040, 0x309F), 0x3000],
    )

    default_css = dumps(ff)
    assert 'font-family: "Noto Sans JP";' in default_css
    assert "font-width: 75% 125%;" in default_css
    assert (
        'src: url("sub-0.woff2") format(woff2) tech(variations, color-colrv1), local("Noto Sans JP");'
        in default_css
    )

    cleome_css = dumps(ff, options=SerializationOptions.cleome())
    assert "font-family: 'Noto Sans JP';" in cleome_css
    assert "font-stretch: 75% 125%;" in cleome_css
    assert (
        "src: url('sub-0.woff2') format('woff2') tech(variations, color-colrv1), local('Noto Sans JP');"
        in cleome_css
    )

    cssom_css = dumps(ff, options=SerializationOptions.cssom())
    assert "font-family: Noto Sans JP;" in cssom_css


def test_opentype_feature_and_variation_settings() -> None:
    feat = serialize_value(
        "font-feature-settings",
        {"liga": True, "dlig": False, "swsh": 2, "smcp": "on"},
    )
    assert feat == '"liga", "dlig" 0, "swsh" 2, "smcp"'

    var = serialize_value(
        "font-variation-settings",
        [("wght", 700), ("wdth", 87.5), ("slnt", -10.0)],
    )
    assert var == '"wght" 700, "wdth" 87.5, "slnt" -10'

    # Reject non-4-char or non-ASCII tags
    with pytest.raises(StixisValidationError):
        serialize_value("font-feature-settings", {"lig": 1})
    with pytest.raises(StixisValidationError):
        serialize_value("font-variation-settings", {"wgh\x7f": 400})


def test_snake_case_and_custom_properties_and_vendor_prefixes() -> None:
    rule = StyleRule(
        ":root",
        {
            "--main_color": "#123456",
            "--accent-gap": "12px",
            "font_optical_sizing": "auto",
        },
    )
    rendered = str(rule)
    # Custom properties preserve `--main_color` verbatim, while regular properties become `kebab-case`
    assert "--main_color: #123456;" in rendered
    assert "--accent-gap: 12px;" in rendered
    assert "font-optical-sizing: auto;" in rendered

    # Custom property rejects top-level `;` or unbalanced brackets
    with pytest.raises((StixisValidationError, StixisSyntaxError)):
        StyleRule(":root", {"--bad": "10px; color: red"})
    with pytest.raises((StixisValidationError, StixisSyntaxError)):
        StyleRule(":root", {"--bad": "calc(10px + 5px"})

    # Vendor-prefixed properties require `allow_vendor_prefixes=True` or `RawDeclaration`
    with pytest.raises(StixisValidationError):
        StyleRule(".text", {"-webkit-font-smoothing": "antialiased"})

    vendor_opts = SerializationOptions(allow_vendor_prefixes=True)
    v_rule = StyleRule(".text", {"-webkit-font-smoothing": "antialiased"}, options=vendor_opts)
    assert "-webkit-font-smoothing: antialiased;" in str(v_rule)

    raw_rule = StyleRule(".text", [RawDeclaration("-moz-osx-font-smoothing", "grayscale")])
    assert "-moz-osx-font-smoothing: grayscale;" in str(raw_rule)


def test_compact_urange_cascade_shadowing() -> None:
    from stixis import format_urange, parse_urange

    target = [0x3041, 0x3042, 0x3045]
    # Without forbidden codepoints, gap of 2 (0x3043..0x3044) is bridged when max_gap >= 2
    assert compact_urange(target, [], max_gap=0x10) == "U+3041-3045"
    # With 0x3043 in forbidden_sorted, bridging across 0x3043 is prevented to avoid cascade shadowing!
    assert compact_urange(target, [0x3043], max_gap=0x10) == "U+3041-3042, U+3045"

    # Reject bool and non-int values in sorted_target_cps and forbidden_sorted, and invalid max_gap
    with pytest.raises(StixisValidationError):
        compact_urange([True])
    with pytest.raises(StixisValidationError):
        compact_urange([False])
    with pytest.raises(StixisValidationError):
        compact_urange([0x3041, 0x3042], forbidden_sorted=[False])
    with pytest.raises(StixisValidationError):
        compact_urange([0x3041, 0x3042], forbidden_sorted=["bad"])  # type: ignore[list-item]
    with pytest.raises(StixisValidationError):
        compact_urange([0x3041], max_gap=-1)
    with pytest.raises(StixisValidationError):
        compact_urange([0x3041], max_gap=True)

    # Pure integer sequences (`list[int]`, `tuple[int, ...]`) coalesce contiguous code points like `set[int]`
    assert format_urange({0x3041, 0x3042, 0x3043}) == "U+3041-3043"
    assert format_urange([0x3041, 0x3042, 0x3043]) == "U+3041-3043"
    assert format_urange((0x3043, 0x3041, 0x3042, 0x3045)) == "U+3041-3043, U+3045"
    assert parse_urange([0x3041, 0x3042, 0x3043]) == [(0x3041, 0x3043)]

    # Mixed interval/codepoint sequences preserve explicit segments by default and merge when coalesce=True
    assert format_urange([(0x3040, 0x309F), 0x3000]) == "U+3040-309F, U+3000"
    assert format_urange([(0x3040, 0x3042), 0x3043], coalesce=True) == "U+3040-3043"


def test_comma_separated_multiplier_scales_linearly() -> None:
    import time

    value = ", ".join(["1s ease-in 2s infinite alternate both running foo"] * 40)
    t0 = time.perf_counter()
    canonicalize("animation", value)
    assert time.perf_counter() - t0 < 1.0


def test_deeply_nested_value_raises_stixis_validation_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    nested = "color-mix(in srgb, red, " * 70 + "blue" + ")" * 70
    with pytest.raises(StixisValidationError):
        canonicalize("color", nested)
    with pytest.raises(StixisValidationError):
        serialize_value("color", nested)

    rc = cli_main(["check", "color", nested])
    captured = capsys.readouterr()
    assert rc == EXIT_VALIDATION_ERROR
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err


def test_empty_custom_property_values_accepted_regular_properties_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert canonicalize("--x", "") == ""
    assert canonicalize("--x", " ") == ""
    assert serialize_value("--x", "") == ""
    assert serialize_value("--x", " ") == ""

    rc_ok = cli_main(["check", "--", "--x", ""])
    captured = capsys.readouterr()
    assert rc_ok == EXIT_OK
    assert captured.out == "\n"

    with pytest.raises(StixisValidationError):
        canonicalize("width", "")
    with pytest.raises(StixisValidationError):
        serialize_value("width", "")

    rc_err = cli_main(["check", "width", ""])
    captured = capsys.readouterr()
    assert rc_err == EXIT_VALIDATION_ERROR


def test_default_is_rejected_as_css_wide_keyword_but_excluded_from_custom_ident(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for prop in ("width", "color", "font-style", "font-language-override"):
        with pytest.raises(StixisValidationError):
            canonicalize(prop, "default")
        with pytest.raises(StixisValidationError):
            serialize_value(prop, "default")

    rc = cli_main(["check", "width", "default"])
    capsys.readouterr()
    assert rc == EXIT_VALIDATION_ERROR

    assert canonicalize("cursor", "default") == "default"
    assert serialize_value("cursor", "default") == "default"

    with pytest.raises(StixisValidationError):
        canonicalize("<custom-ident>", "default")
    with pytest.raises(StixisValidationError):
        serialize_value("<custom-ident>", "default")
