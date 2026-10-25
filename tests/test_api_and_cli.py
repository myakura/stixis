"""Comprehensive unit tests for Stixis public API, PEP 750 Templates, AST nodes, microsyntaxes, and CLI."""

from __future__ import annotations

import json
import math
import subprocess
import sys

import pytest

from stixis import (
    AtRule,
    CommentNode,
    Deg,
    FontFace,
    FontSrc,
    GenericFamily,
    Keyword,
    LocalFont,
    Pct,
    Px,
    RawDeclaration,
    SerializationOptions,
    StixisSyntaxError,
    StixisValidationError,
    StyleRule,
    StyleSheet,
    Url,
    canonicalize,
    compact_urange,
    dumps,
    generic,
    serialize,
    serialize_declarations,
    serialize_value,
)
from stixis.cli import (
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE_ERROR,
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


def test_pep750_template_integration_and_context_escaping() -> None:
    url_val = "https://example.com/fonts/Noto Sans (v1).woff2"
    src_css = serialize_value(
        "src",
        t"url({url_val}) format(woff2) tech(variations)",
        at_rule="@font-face",
    )
    assert src_css == 'url("https://example.com/fonts/Noto Sans (v1).woff2") format(woff2) tech(variations)'

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

    # Plain string comment with premature `*/` is rejected eagerly
    with pytest.raises(StixisValidationError):
        CommentNode("premature */ close")


def test_eager_validation_on_all_rule_nodes() -> None:
    # StyleRule rejects unknown property, invalid value, or injection in selector at __post_init__
    with pytest.raises(StixisValidationError) as exc_info:
        StyleRule(".box", {"not_a_css_property": "10px"})
    assert exc_info.value.target == "not-a-css-property"

    with pytest.raises(StixisValidationError) as exc_info2:
        StyleRule(".box", {"font_weight": 2000})
    assert exc_info2.value.target == "font-weight"
    assert exc_info2.value.invalid_value == 2000

    with pytest.raises((StixisValidationError, StixisSyntaxError)):
        StyleRule(".box { color: red } .evil", {"color": "blue"})

    # AtRule validates typed preludes (`@font-palette-values` requires `<dashed-ident>`)
    valid_palette = AtRule(
        "@font-palette-values",
        "--cool-palette",
        descriptors={"font_family": "Bungee", "base_palette": 1},
    )
    assert "--cool-palette" in str(valid_palette)
    assert 'font-family: "Bungee";' in str(valid_palette)

    with pytest.raises(StixisValidationError):
        AtRule("@font-palette-values", "not-a-dashed-ident", descriptors={"font_family": "Bungee"})

    with pytest.raises(StixisValidationError):
        AtRule("@font-face", "unexpected-prelude", descriptors={"font_family": "Inter"})


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
    assert 'src: url("sub-0.woff2") format(woff2) tech(variations, color-colrv1), local("Noto Sans JP");' in default_css

    cleome_css = dumps(ff, options=SerializationOptions.cleome())
    assert "font-family: 'Noto Sans JP';" in cleome_css
    assert "font-stretch: 75% 125%;" in cleome_css
    assert "src: url('sub-0.woff2') format('woff2') tech(variations, color-colrv1), local('Noto Sans JP');" in cleome_css

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


def test_number_primitive_rejects_bool_and_non_finite() -> None:
    with pytest.raises(StixisValidationError):
        serialize_value("opacity", True)
    with pytest.raises(StixisValidationError):
        serialize_value("opacity", False)
    with pytest.raises(StixisValidationError):
        serialize_value("opacity", math.nan)
    with pytest.raises(StixisValidationError):
        serialize_value("opacity", math.inf)


def test_compact_urange_cascade_shadowing() -> None:
    target = [0x3041, 0x3042, 0x3045]
    # Without forbidden codepoints, gap of 2 (0x3043..0x3044) is bridged when max_gap >= 2
    assert compact_urange(target, [], max_gap=0x10) == "U+3041-3045"
    # With 0x3043 in forbidden_sorted, bridging across 0x3043 is prevented to avoid cascade shadowing!
    assert compact_urange(target, [0x3043], max_gap=0x10) == "U+3041-3042, U+3045"


def test_stylesheet_and_nested_rules_formatting() -> None:
    sheet = StyleSheet(
        rules=[
            FontFace(
                font_family="Inter",
                src=Url("inter.woff2"),
                font_display="swap",
            ),
            AtRule(
                "@media",
                "(min-width: 768px)",
                rules=[
                    StyleRule(
                        ".container",
                        {"max_width": Px(720), "margin": (Px(0), Keyword("auto"))},
                    )
                ],
            ),
        ],
        header_comment="Generated by Stixis",
    )
    pretty = dumps(sheet)
    assert pretty.startswith("/* Generated by Stixis */\n@font-face {\n")
    assert "@media (min-width: 768px) {\n  .container {\n    max-width: 720px;\n    margin: 0px auto;\n  }\n}" in pretty

    minified = dumps(sheet, minify=True)
    assert "/* Generated by Stixis */" not in minified
    assert minified == '@font-face{font-family:"Inter";src:url("inter.woff2");font-display:swap}@media (min-width: 768px){.container{max-width:720px;margin:0px auto}}'


def test_serialize_declarations_helper() -> None:
    text = serialize_declarations(
        {"font_family": "Inter", "font_weight": (100, 900)},
        at_rule="@font-face",
    )
    assert text == 'font-family: "Inter"; font-weight: 100 900;'
    assert (
        serialize_declarations(
            {"font_family": "Inter", "font_weight": 400},
            at_rule="@font-face",
            minify=True,
        )
        == 'font-family:"Inter";font-weight:400'
    )


def test_cli_inspect_and_check_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    # 1. `stixis inspect font-style` -> EXIT_OK (0)
    rc = cli_main(["inspect", "font-style"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert "font-style (property)" in captured.out
    assert "syntax:" in captured.out

    # 2. `stixis inspect @font-face --json` -> EXIT_OK (0) with valid JSON
    rc = cli_main(["inspect", "@font-face", "--json"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    payload = json.loads(captured.out)
    assert payload["kind"] == "at-rule"
    assert payload["name"] == "@font-face"
    assert "unicode-range" in payload["descriptors"]
    assert "font-stretch" in payload["descriptors"]

    # 3. `stixis inspect <font-src>` -> EXIT_OK (0)
    rc = cli_main(["inspect", "<font-src>"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert "<font-src> (type)" in captured.out

    # 4. `stixis inspect unknown-target` -> EXIT_NOT_FOUND (4)
    rc = cli_main(["inspect", "unknown-nonexistent-target"])
    captured = capsys.readouterr()
    assert rc == EXIT_NOT_FOUND
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1

    # 5. `stixis check font-weight "100 900" --at-rule @font-face` -> EXIT_OK (0)
    rc = cli_main(["check", "font-weight", "100 900", "--at-rule", "@font-face"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert captured.out.strip() == "100 900"

    # 6. `stixis check font-weight "2000"` -> EXIT_VALIDATION_ERROR (1)
    rc = cli_main(["check", "font-weight", "2000"])
    captured = capsys.readouterr()
    assert rc == EXIT_VALIDATION_ERROR
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1

    # 7. `stixis check unknown-prop "10px"` -> EXIT_NOT_FOUND (4)
    rc = cli_main(["check", "unknown-prop", "10px"])
    captured = capsys.readouterr()
    assert rc == EXIT_NOT_FOUND
    assert len(captured.err.strip().splitlines()) == 1

    # 8. Bad CLI arguments -> EXIT_USAGE_ERROR (2)
    rc = cli_main(["check"])
    captured = capsys.readouterr()
    assert rc == EXIT_USAGE_ERROR
    assert len(captured.err.strip().splitlines()) == 1


def test_cli_subprocess_entrypoint() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "stixis.cli", "check", "unicode-range", "u+3040-309f", "--at-rule", "@font-face"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "U+3040-309F"


def test_security_and_descriptor_resolution_regressions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. Custom property names with injection characters (`--x: red; color`) must be rejected eagerly,
    #    while escaped CSS identifiers (`r"--foo\+bar"`) are preserved and serialized safely.
    with pytest.raises(StixisValidationError):
        StyleRule("body", {"--x: red; color": "blue"})
    with pytest.raises(StixisValidationError):
        StyleRule("body", {"--": "blue"})
    escaped_custom = StyleRule("body", {r"--foo\+bar": "blue"})
    assert r"--foo\+bar: blue;" in str(escaped_custom)

    # 2. At-rule names with comment or non-ident characters (`media/*`) must be rejected eagerly,
    #    while valid double-hyphen at-rule names (`--custom`) are accepted.
    with pytest.raises(StixisValidationError):
        AtRule("media/*", "screen")
    with pytest.raises(StixisValidationError):
        AtRule("@123invalid", "screen")
    custom_at = AtRule("--custom", "screen")
    assert str(custom_at) == "@--custom screen {}"

    # 3. Nested rules preserve overridden SerializationOptions across `dumps` / `format`
    nested = AtRule("@media", "screen", rules=[FontFace(font_family="Inter", src=Url("inter.woff2"))])
    cleome_out = dumps(nested, options=SerializationOptions.cleome())
    assert "font-family: 'Inter';" in cleome_out
    assert "src: url('inter.woff2');" in cleome_out

    nested_style = StyleRule(
        ".card",
        {"font_family": "Inter"},
        rules=[StyleRule("&:hover", {"font_family": "Noto Sans JP"})],
    )
    assert "font-family: 'Noto Sans JP';" in dumps(nested_style, options=SerializationOptions.cleome())

    # 4. Slash-qualified targets (`@font-face/font-style`, `@font-face/font-family`) preserve `@font-face` descriptor context
    assert (
        serialize_value("@font-face/font-style", ("oblique", Deg(-10), Deg(20)))
        == "oblique -10deg 20deg"
    )
    assert canonicalize("@font-face/font-weight", "100 900") == "100 900"
    with pytest.raises(StixisValidationError):
        serialize_value("@font-face/font-family", ["Inter", "Roboto"])

    # 5. CLI check with slash-qualified descriptor and empty/whitespace target
    rc = cli_main(["check", "@font-face/font-weight", "100 900"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert captured.out.strip() == "100 900"

    rc_empty = cli_main(["check", "", "red"])
    captured = capsys.readouterr()
    assert rc_empty == EXIT_NOT_FOUND
    assert len(captured.err.strip().splitlines()) == 1

    rc_bad_desc = cli_main(["inspect", "bad;name", "--at-rule", "@font-face"])
    captured = capsys.readouterr()
    assert rc_bad_desc == EXIT_NOT_FOUND
    assert len(captured.err.strip().splitlines()) == 1

