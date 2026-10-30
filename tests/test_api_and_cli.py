"""Comprehensive unit tests for Stixis public API, PEP 750 Templates, AST nodes, microsyntaxes, and CLI."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

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

    # Plain string comment with premature `*/` is rejected eagerly, while consecutive `*` before `*/` is normalized
    with pytest.raises(StixisValidationError):
        CommentNode("premature */ close")
    assert str(CommentNode("/* foo**/")) == "/* foo* */"

    # Comment Template without outer `/* ... */` is evaluated in comment context from the start
    from stixis.syntax import render_css_template, tokenize_css

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


def test_large_number_serialization_and_overflow_rejection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from stixis.primitives import serialize_number

    assert serialize_number(1e20) == "100000000000000000000"
    assert serialize_number(1e22) == "10000000000000000000000"
    assert serialize_number(1e30) == "1" + "0" * 30
    assert serialize_number(-1e30) == "-1" + "0" * 30
    assert canonicalize("width", "1e30px") == "1" + "0" * 30 + "px"
    assert str(StyleRule("a", {"width": t"{1e30}px"})) == f"a {{\n  width: {'1' + '0' * 30}px;\n}}"

    rc = cli_main(["check", "width", "1e22px"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert captured.out.strip() == "10000000000000000000000px"

    # Non-finite overflow tokens (`1e400`, 309+ digit integers) in raw CSS are rejected cleanly with StixisValidationError
    with pytest.raises(StixisValidationError):
        canonicalize("width", "1e400px")
    with pytest.raises(StixisValidationError):
        canonicalize("width", "9" * 309 + "px")
    with pytest.raises(StixisValidationError):
        canonicalize("width", "-" + "9" * 309 + "px")
    with pytest.raises(StixisValidationError):
        canonicalize("<integer>", "1e400")
    with pytest.raises(StixisValidationError):
        canonicalize("<integer>", "9" * 309)
    rc_overflow = cli_main(["check", "width", "1e400px"])
    captured = capsys.readouterr()
    assert rc_overflow == EXIT_VALIDATION_ERROR



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


def test_comment_node_section_spacing_and_options_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stixis.nodes as nodes_mod
    from stixis import using_options

    # 1. Leading newline on a section comment (or `leading_newline=True`) emits a blank line in non-minified output
    sheet = StyleSheet(
        rules=[
            FontFace(font_family="Inter", src=Url("inter-400.woff2"), font_weight=400),
            CommentNode("\n/* === Bold === */"),
            FontFace(font_family="Inter", src=Url("inter-700.woff2"), font_weight=700),
            CommentNode("=== Italic ===", leading_newline=True),
            FontFace(font_family="Inter", src=Url("inter-400i.woff2"), font_style="italic"),
        ],
    )
    rendered = dumps(sheet)
    assert "\n\n/* === Bold === */\n@font-face" in rendered
    assert "\n\n/* === Italic === */\n@font-face" in rendered
    assert "/*" not in dumps(sheet, minify=True)

    # 2. `using_options` / `options.activate()` supplies default options to child rules without repeating `options=`
    cleome_1eng = SerializationOptions.cleome(min_engines=1)
    with using_options(cleome_1eng):
        s_ctx = StyleSheet(
            rules=[
                FontFace(font_family="Inter", src=FontSrc(url="inter.woff2", format="woff2"), font_width=Pct(100)),
                StyleRule("p", {"hanging_punctuation": "first"}),
            ],
        )
    out_ctx = dumps(s_ctx)
    assert "font-family: 'Inter';" in out_ctx
    assert "font-stretch: 100%;" in out_ctx
    assert "hanging-punctuation: first;" in out_ctx

    # 3. `format` / `dumps` skips redundant child rule rebuilds when formatting profile matches and gates are not tightened
    rule = StyleRule("body", {"margin": Px(0)})
    calls = 0
    orig_validate = nodes_mod._validate_and_normalize_declarations

    def _counting_validate(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return orig_validate(*args, **kwargs)

    monkeypatch.setattr(nodes_mod, "_validate_and_normalize_declarations", _counting_validate)
    assert dumps(rule, options=SerializationOptions(min_engines=1)) == "body {\n  margin: 0px;\n}"
    assert calls == 0

    # 4. Child rules constructed with explicit options keep their options when wrapped in `StyleSheet([rule])` without parent `options=`
    child_1eng = StyleRule(
        "p",
        {"hanging_punctuation": "first"},
        options=SerializationOptions.cleome(min_engines=1),
    )
    assert "hanging-punctuation: first;" in dumps(StyleSheet([child_1eng]))

    # 5. Re-validating when `allow_unknown_properties=True` or `allow_vendor_prefixes=True` and a previously disabled property becomes enabled
    raw_unknown_rule = StyleRule(
        "p",
        {"hanging_punctuation": 123},
        options=SerializationOptions(min_engines=3, allow_unknown_properties=True),
    )
    with pytest.raises(StixisValidationError):
        dumps(
            raw_unknown_rule,
            options=SerializationOptions(min_engines=0, allow_unknown_properties=True),
        )

    raw_vendor_rule = StyleRule(
        "p",
        {"-webkit-animation-duration": 123},
        options=SerializationOptions(allow_vendor_prefixes=True),
    )
    with pytest.raises(StixisValidationError):
        dumps(
            raw_vendor_rule,
            options=SerializationOptions(min_engines=0, allow_vendor_prefixes=True),
        )


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
    escaped_custom = StyleRule("body", {r"--foo\+bar": "blue", r"\2d\2d MyVar": "red", r"\43 olor": "green"})
    assert r"--foo\+bar: blue;" in str(escaped_custom)
    assert "--MyVar: red;" in str(escaped_custom)
    assert "color: green;" in str(escaped_custom)

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


def test_upstream_checker_detects_unresolved_refs_and_formats_agent_issue() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import check_upstream

    from stixis._generated.specs import PROPERTY_SYNTAXES, VALUE_SYNTAXES
    from stixis.syntax import compile_value_syntax

    # 1. Resolved syntax produces no unresolved references
    node_ok = compile_value_syntax("normal | <length-percentage>")
    assert check_upstream.find_unresolved_refs(node_ok, PROPERTY_SYNTAXES, VALUE_SYNTAXES) == []

    # 2. Newly introduced upstream non-terminal is flagged for spec_patches.toml
    node_missing = compile_value_syntax("auto | <future-css-type>")
    assert check_upstream.find_unresolved_refs(
        node_missing, PROPERTY_SYNTAXES, VALUE_SYNTAXES
    ) == ["<future-css-type>"]

    # 3. Markdown Issue renderer emits structured agent instructions and unresolved references
    report = check_upstream.UpstreamReport(
        webref_old_commit="1111111111111111111111111111111111111111",
        webref_new_commit="2222222222222222222222222222222222222222",
        wpt_old_commit="3333333333333333333333333333333333333333",
        wpt_new_commit="4444444444444444444444444444444444444444",
        syntax_changes=[
            check_upstream.SyntaxChange(
                target="font-future",
                kind="property",
                change_type="added",
                old_syntax=None,
                new_syntax="auto | <future-css-type>",
                resolvable=False,
                unresolved_refs=["<future-css-type>"],
            )
        ],
        unresolved_targets=[
            check_upstream.SyntaxChange(
                target="font-future",
                kind="property",
                change_type="added",
                old_syntax=None,
                new_syntax="auto | <future-css-type>",
                resolvable=False,
                unresolved_refs=["<future-css-type>"],
            )
        ],
        wpt_failures=[
            check_upstream.WptFailure(
                suite="css-fonts",
                target="font-future",
                raw_css="future-token",
                expected="future-token",
                actual_or_error="raised StixisValidationError",
            )
        ],
    )
    assert report.has_changes
    assert report.needs_agent_intervention
    md = check_upstream.format_markdown_issue(report)
    assert "Action Required" in md
    assert "`<future-css-type>`" in md
    assert "`font-future`" in md
    assert "codegen/spec_patches.toml" in md


def test_upstream_checker_captures_failing_wpt_vectors_without_crashing(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import check_upstream

    # Set up a minimal synthetic WPT directory with:
    # 1. A css-fonts test with candidate array `[...]` on an unsupported/invalid value
    # 2. A non-font WPT property test where `width` has a failing expected value `"2px"` for `"1px"`
    wpt_css = tmp_path / "css"
    (wpt_css / "css-syntax").mkdir(parents=True)
    (wpt_css / "css-syntax" / "urange-parsing.html").write_text(
        'testUrange("U+41", "U+41");\n', encoding="utf-8"
    )
    (wpt_css / "css-fonts" / "support").mkdir(parents=True)
    (wpt_css / "css-fonts" / "support" / "font-family-keywords.js").write_text(
        'const kGenericFontFamilyKeywords = ["serif"];\nconst kNonGenericFontFamilyKeywords = [];\n',
        encoding="utf-8",
    )
    (wpt_css / "cssom").mkdir(parents=True)
    (wpt_css / "cssom" / "font-family-serialization-001.html").write_text("", encoding="utf-8")
    fonts_parsing = wpt_css / "css-fonts" / "parsing"
    fonts_parsing.mkdir(parents=True)
    for fname in (
        "font-face-metric-overrides.html",
        "font-face-size-adjust.html",
        "font-face-width.html",
        "font-face-src-local.html",
        "font-face-src-format.html",
        "font-face-src-tech.html",
    ):
        (fonts_parsing / fname).write_text("", encoding="utf-8")

    (fonts_parsing / "font-weight-valid.html").write_text(
        'test_valid_value("font-weight", "not-a-weight", ["400", "normal"]);\n',
        encoding="utf-8",
    )
    sizing_parsing = wpt_css / "css-sizing" / "parsing"
    sizing_parsing.mkdir(parents=True)
    (sizing_parsing / "width-valid.html").write_text(
        'test_valid_value("width", "1px", "2px");\n',
        encoding="utf-8",
    )
    transforms_parsing = wpt_css / "css-transforms" / "parsing"
    transforms_parsing.mkdir(parents=True)
    (transforms_parsing / "transform-origin-valid.html").write_text(
        'test_valid_value("transform-origin", "top center", ["center top 0px", "center top"]);\n',
        encoding="utf-8",
    )

    _counts, failures, _changed = check_upstream._check_wpt_cases(wpt_css)
    failure_targets = {(f.suite, f.target, f.raw_css) for f in failures}
    assert ("css-fonts", "font-weight", "not-a-weight") in failure_targets
    assert ("css-properties", "width", "1px") in failure_targets
    assert ("css-properties", "transform-origin", "top center") not in failure_targets


def test_engine_support_threshold_toggle_and_extra_properties(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. `hanging-punctuation` (1 engine: WebKit) is rejected by default (`min_engines=2`)
    with pytest.raises(StixisValidationError, match="min_engines=2"):
        StyleRule("p", {"hanging_punctuation": "first"})
    with pytest.raises(StixisValidationError, match="min_engines=2"):
        canonicalize("hanging-punctuation", "first")

    # 2. Enabling 1-engine support (`min_engines=1`) or `SerializationOptions.cleome(min_engines=1)` allows it
    rule_1eng = StyleRule(
        "p",
        {"hanging_punctuation": "first"},
        options=SerializationOptions.cleome(min_engines=1),
    )
    assert "hanging-punctuation: first;" in str(rule_1eng)
    assert (
        canonicalize(
            "hanging-punctuation",
            "first",
            options=SerializationOptions(min_engines=1),
        )
        == "first"
    )

    # 3. Pinpoint opt-in via `extra_properties` keeps `min_engines=2` for everything else
    opts_pinpoint = SerializationOptions(
        min_engines=2,
        extra_properties=frozenset({"hanging-punctuation"}),
    )
    assert serialize_value("hanging-punctuation", "first", options=opts_pinpoint) == "first"
    with pytest.raises(StixisValidationError, match="min_engines=2"):
        serialize_value("block-step-size", "none", options=opts_pinpoint)

    # 4. CLI `--min-engines` flag toggles 1-engine property validation
    rc_default = cli_main(["check", "hanging-punctuation", "first"])
    captured = capsys.readouterr()
    assert rc_default == EXIT_VALIDATION_ERROR
    assert "min_engines=2" in captured.err

    rc_one = cli_main(["check", "hanging-punctuation", "first", "--min-engines", "1"])
    captured = capsys.readouterr()
    assert rc_one == EXIT_OK
    assert captured.out.strip() == "first"


def test_bcd_shipping_unprefixed_filter_and_support_promotion(tmp_path: Path) -> None:
    import tomllib

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import check_upstream
    import gen_specs
    import gen_wpt_tests

    # 1. `_is_shipping_unprefixed_statement` rejects preview, removed, prefixed, and flagged entries
    assert gen_specs._is_shipping_unprefixed_statement({"version_added": "120"})
    assert not gen_specs._is_shipping_unprefixed_statement({"version_added": "preview"})
    assert not gen_specs._is_shipping_unprefixed_statement(
        {"version_added": "1", "version_removed": "50"}
    )
    assert not gen_specs._is_shipping_unprefixed_statement(
        {"version_added": "1", "prefix": "-webkit-"}
    )
    assert not gen_specs._is_shipping_unprefixed_statement(
        {"version_added": "1", "alternative_name": "old-name"}
    )
    assert not gen_specs._is_shipping_unprefixed_statement(
        {"version_added": "1", "flags": [{"name": "flag"}]}
    )

    # 2. `_render_property_test_file` threads `min_engines=1` into `SerializationOptions.cssom(min_engines=1)`
    rendered_1 = gen_wpt_tests._render_property_test_file(
        "Docstring.",
        [("hanging-punctuation", "first", "first")],
        [],
        "custom_1eng",
        min_engines=1,
    )
    assert "SerializationOptions.cssom(min_engines=1)" in rendered_1

    # 3. `analyze_upstream` detects when a BCD property crosses `min_engines`
    bcd_css = tmp_path / "bcd_css"
    (bcd_css / "properties").mkdir(parents=True)
    (bcd_css / "properties" / "hanging-punctuation.json").write_text(
        json.dumps(
            {
                "css": {
                    "properties": {
                        "hanging-punctuation": {
                            "__compat": {
                                "support": {
                                    "chrome": {"version_added": "140"},
                                    "firefox": {"version_added": False},
                                    "safari": {"version_added": "10"},
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    patches = tomllib.loads((repo_root / "codegen" / "spec_patches.toml").read_text(encoding="utf-8"))
    raw_webref = Path(patches["meta"]["webref_css_dir"]).expanduser()
    webref_dir = raw_webref if raw_webref.is_absolute() else (Path.home() / raw_webref)
    if not webref_dir.is_dir():
        pytest.skip(f"Local @webref/css directory not available: {webref_dir}")

    report = check_upstream.analyze_upstream(
        webref_dir,
        gen_wpt_tests.DEFAULT_WPT_CSS_DIR,
        repo_root / "codegen" / "spec_patches.toml",
        repo_root / "codegen" / "upstream.json",
        min_engines=2,
        bcd_dir=bcd_css,
    )
    assert report.has_changes
    assert any("`hanging-punctuation`: 1 -> 2 engines" in s for s in report.support_changes)

    # 4. `_extract_css_fonts_property_cases` filters by `min_engines` (e.g. `min_engines=3` excludes 2-engine `font-language-override`)
    fonts_v_3, _ = gen_wpt_tests._extract_css_fonts_property_cases(
        gen_wpt_tests.DEFAULT_WPT_CSS_DIR, min_engines=3
    )
    assert all(prop != "font-language-override" for prop, _, _ in fonts_v_3)


def test_consumer_skill_and_readme_stay_in_sync_with_public_api() -> None:
    import ast
    import inspect
    import re

    import stixis

    repo_root = Path(__file__).resolve().parent.parent
    skill_path = repo_root / "skills" / "stixis" / "SKILL.md"
    readme_path = repo_root / "README.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    readme_text = readme_path.read_text(encoding="utf-8")

    # 1. Every runtime symbol in `stixis.__all__` (excluding type aliases / constants) must be documented in SKILL.md
    exempt_exports = {
        "DEFAULT_OPTIONS",
        "FontDisplayKeyword",
        "FontFormatKeyword",
        "FontTechKeyword",
        "FontWidthKeyword",
        "Percentage",
        "__version__",
    }
    missing_symbols = [
        sym for sym in stixis.__all__ if sym not in exempt_exports and sym not in skill_text
    ]
    assert not missing_symbols, (
        f"Public exports in stixis.__all__ missing from skills/stixis/SKILL.md: {missing_symbols}"
    )

    # 2. Every public parameter of core entry points and AST constructors must appear in SKILL.md
    core_callables = (
        stixis.dumps,
        stixis.FontFace,
        stixis.StyleRule,
        stixis.AtRule,
        stixis.CommentNode,
        stixis.StyleSheet,
        stixis.serialize_value,
        stixis.canonicalize,
        stixis.format_urange,
        stixis.parse_urange,
        stixis.compact_urange,
        stixis.using_options,
    )
    missing_params: list[str] = []
    for fn in core_callables:
        sig = inspect.signature(fn)
        for param_name in sig.parameters:
            if param_name.startswith("_"):
                continue
            if param_name not in skill_text:
                missing_params.append(f"{fn.__name__}({param_name}=...)")
    assert not missing_params, (
        f"Public parameters missing from skills/stixis/SKILL.md: {missing_params}"
    )

    # 3. Every fenced `python` code block in SKILL.md and README.md must parse and execute cleanly
    code_block_re = re.compile(r"```python\n(.*?)```", re.DOTALL)
    for doc_name, doc_content in (("SKILL.md", skill_text), ("README.md", readme_text)):
        blocks = code_block_re.findall(doc_content)
        assert blocks, f"Expected at least one ```python block in {doc_name}"
        for idx, block in enumerate(blocks):
            tree = ast.parse(block, filename=f"{doc_name}#block-{idx}")
            assert tree.body
            res = subprocess.run(
                [sys.executable, "-c", block],
                capture_output=True,
                text=True,
                check=False,
            )
            assert res.returncode == 0, f"{doc_name}#block-{idx} failed: {res.stderr}"


def test_format_token_sequence_preserves_space_after_hyphen_or_hash_delim() -> None:
    from stixis.syntax import tokenize_css

    out = canonicalize("width", "calc(1px - var(--x))")
    assert out == "calc(1px - var(--x))"
    assert [(t.kind, t.value) for t in tokenize_css(out) if t.kind != "whitespace"][:4] == [
        ("function", "calc"),
        ("dimension", "1"),
        ("delim", "-"),
        ("function", "var"),
    ]

    assert canonicalize("width", "calc(100% - min(10px, 5%))") == "calc(100% - min(10px, 5%))"
    assert canonicalize("--x", "a - b") == "a - b"
    assert canonicalize("--x", "# foo") == "# foo"


def test_format_token_sequence_escapes_unrestricted_hash_and_dimension_unit() -> None:
    from stixis.syntax import tokenize_css

    h = canonicalize("--x", r"#1\;color:red")
    assert h == r"#1\;color: red"
    assert [(t.kind, t.value, t.hash_type) for t in tokenize_css(h) if t.kind != "whitespace"] == [
        ("hash", "1;color", "unrestricted"),
        ("colon", ":", "unrestricted"),
        ("ident", "red", "unrestricted"),
    ]

    d = canonicalize("--x", r"1px\;color:red")
    assert d == r"1px\;color: red"
    assert [(t.kind, t.num, t.unit) for t in tokenize_css(d) if t.kind != "whitespace"] == [
        ("dimension", 1, "px;color"),
        ("colon", None, ""),
        ("ident", None, ""),
    ]

    css = str(StyleRule("a", {"width": r"var(--x, #1\})"}))
    assert css == "a {\n  width: var(--x, #1\\});\n}"


def test_url_modifiers_validated_as_ident_or_functional_notation() -> None:
    u_valid = Url("a.png", modifiers=("crossorigin", "integrity(sha256-abc)"))
    assert str(StyleRule("a", {"background-image": u_valid})) == (
        'a {\n  background-image: url("a.png" crossorigin integrity(sha256-abc));\n}'
    )

    for bad_mod in (
        "x);color:red;b:url(",
        "",
        "two idents",
        "10px",
        "fn(a) extra",
        "fn()",
        "unclosed(",
    ):
        with pytest.raises(StixisValidationError):
            Url("a.png", modifiers=(bad_mod,))


def test_tokenize_css_comment_after_star_cdo_cdc_and_escaped_eof() -> None:
    from stixis.syntax import tokenize_css

    def kinds(s: str) -> list[tuple[str, str]]:
        return [(t.kind, t.value) for t in tokenize_css(s)]

    assert canonicalize("width", "calc(2*/**/3px)") == "calc(2 * 3px)"
    assert canonicalize("width", "calc(2/**/*3px)") == "calc(2 * 3px)"
    with pytest.raises(StixisSyntaxError, match=r"Unexpected comment close `\*/`"):
        tokenize_css("2 */ 3")

    assert kinds("-->") == [("CDC", "-->")]
    assert kinds("<!--") == [("CDO", "<!--")]
    assert kinds("a\\") == [("ident", "a\ufffd")]
    assert canonicalize("--x", "<!-- -->") == "<!-- -->"
    with pytest.raises(StixisValidationError):
        canonicalize("content", "<!--")


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


def test_gen_specs_rejects_missing_bcd_dir_and_disallows_fallback_in_check_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import gen_specs

    fake_webref = tmp_path / "webref"
    fake_webref.mkdir(parents=True)

    rc_explicit = gen_specs.main(
        [
            "--check",
            "--webref-dir",
            str(fake_webref),
            "--bcd-dir",
            str(tmp_path / "nonexistent"),
        ]
    )
    captured = capsys.readouterr()
    assert rc_explicit == 4
    assert "missing bcd_css_dir" in captured.err

    custom_patches = tmp_path / "spec_patches.toml"
    custom_patches.write_text(
        f'[meta]\nwebref_css_dir = "{fake_webref}"\nbcd_css_dir = "{tmp_path / "missing_bcd"}"\n',
        encoding="utf-8",
    )
    rc_check_no_bcd = gen_specs.main(["--check", "--patches", str(custom_patches)])
    captured = capsys.readouterr()
    assert rc_check_no_bcd == 4
    assert "missing bcd_css_dir" in captured.err


def test_upstream_watch_workflow_gates_issue_creation_on_exit_code_3() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    wf_text = (repo_root / ".github" / "workflows" / "upstream-watch.yml").read_text(encoding="utf-8")
    assert "steps.check.outputs.exit_code == '3'" in wf_text
    assert "continue-on-error: true" not in wf_text


def test_version_single_source_of_truth_and_publish_tag_check() -> None:
    import importlib.metadata
    import tomllib

    import stixis

    repo_root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    init_text = (repo_root / "src" / "stixis" / "__init__.py").read_text(encoding="utf-8")
    publish_wf = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert stixis.__version__ == pyproject["project"]["version"]
    assert stixis.__version__ == importlib.metadata.version("stixis")
    assert 'importlib.metadata' in init_text
    assert "GITHUB_REF_NAME" in publish_wf


def test_gen_wpt_tests_preserves_partial_files_math_var_and_tracks_xfails_and_skips(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "codegen"))
    import gen_wpt_tests

    from tests._generated import test_wpt_css_fonts, test_wpt_css_properties

    wpt_css = tmp_path / "css"
    sizing_dir = wpt_css / "css-sizing" / "parsing"
    sizing_dir.mkdir(parents=True)
    (sizing_dir / "width-valid.html").write_text(
        'test_valid_value("width", "10px");\n'
        'test_valid_value("width", "calc(10px + 20px)");\n'
        'test_valid_value("width", "var(--w)");\n'
        'test_valid_value("transform-origin", "top center", ["center top 0px", "center top"]);\n'
        'test_valid_value("width", "1px", "2px");\n'
        'test_valid_value("width", "3px", ["4px", "5px"]);\n'
        'test_valid_value("hanging-punctuation", "first");\n',
        encoding="utf-8",
    )
    (sizing_dir / "width-invalid.html").write_text(
        'test_invalid_value("width", "not-a-width");\n'
        'test_invalid_value("width", "calc(10px + 20px) extra-invalid");\n'
        'test_invalid_value("width", "calc(1 / 2px)");\n'
        'test_invalid_value("nonexistent-prop", "10px");\n',
        encoding="utf-8",
    )

    valid, invalid = gen_wpt_tests._extract_broad_css_property_cases(wpt_css, min_engines=2)
    assert ("width", "10px", "10px") in valid
    assert ("width", "calc(10px + 20px)", "calc(10px + 20px)") in valid
    assert ("width", "var(--w)", "var(--w)") in valid
    assert ("transform-origin", "top center", "center top") in valid
    assert ("width", "not-a-width") in invalid
    assert ("width", "calc(10px + 20px) extra-invalid") in invalid

    suite = gen_wpt_tests.extract_broad_css_property_suite(wpt_css, min_engines=2)
    assert any(prop == "width" and raw == "1px" and exp == "2px" and reason for prop, raw, exp, reason in suite.xfail_valid)
    assert any(prop == "width" and raw == "3px" and exp == ("4px", "5px") and reason for prop, raw, exp, reason in suite.xfail_valid)
    assert any(prop == "width" and raw == "calc(1 / 2px)" and reason for prop, raw, reason in suite.xfail_invalid)
    assert suite.skipped_counts["disabled_min_engines"] == 1
    assert suite.skipped_counts["unknown_property"] == 1

    # Generated property test modules expose xfail and skipped tables and include calc/var cases
    assert any("calc(" in raw or "var(" in raw for _, raw, _ in test_wpt_css_properties.VALID_PROPERTY_CASES)
    assert hasattr(test_wpt_css_properties, "XFAIL_VALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_properties, "XFAIL_INVALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_properties, "SKIPPED_PROPERTY_COUNTS")
    assert hasattr(test_wpt_css_fonts, "XFAIL_VALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_fonts, "XFAIL_INVALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_fonts, "SKIPPED_PROPERTY_COUNTS")


def test_is_property_enabled_defaults_missing_engine_count_to_zero_and_docs_describe_bcd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stixis._generated import specs as gen_specs_mod
    from stixis.nodes import is_property_enabled

    monkeypatch.setattr(gen_specs_mod, "PROPERTY_ENGINE_COUNTS", {})
    assert not is_property_enabled("width", SerializationOptions(min_engines=1))
    with pytest.raises(StixisValidationError, match=r"0 engine\(s\)"):
        canonicalize("width", "10px", options=SerializationOptions(min_engines=1))

    repo_root = Path(__file__).resolve().parent.parent
    readme_text = (repo_root / "README.md").read_text(encoding="utf-8")
    skill_text = (repo_root / "skills" / "stixis" / "SKILL.md").read_text(encoding="utf-8")
    assert "browser-compat-data" in readme_text
    assert "min_engines" in readme_text
    assert "partial_implementation" in readme_text
    assert "min_engines" in skill_text












