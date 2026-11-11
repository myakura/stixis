"""Unit tests for CSS grammar target resolution, descriptor context, and BCD engine support thresholds."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from stixis import (
    AtRule,
    Deg,
    FontFace,
    Keyword,
    SerializationOptions,
    StixisValidationError,
    StyleRule,
    Url,
    canonicalize,
    dumps,
    serialize_value,
)
from stixis.cli import (
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_VALIDATION_ERROR,
)
from stixis.cli import (
    main as cli_main,
)


def test_type_targets_reject_css_wide_keywords(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for type_target in ("<length>", "<color>", "<number>", "<custom-ident>", "length"):
        for kw in ("inherit", "initial", "unset", "revert", "revert-layer", "revert-rule"):
            with pytest.raises(StixisValidationError):
                canonicalize(type_target, kw)
            with pytest.raises(StixisValidationError):
                serialize_value(type_target, kw)
            with pytest.raises(StixisValidationError):
                serialize_value(type_target, Keyword(kw))

    with pytest.raises(StixisValidationError):
        canonicalize("<font-src>", "inherit")
    with pytest.raises(StixisValidationError):
        serialize_value("<font-src>", Keyword("inherit"))

    for prop in ("width", "color", "--custom"):
        for kw in ("inherit", "initial", "unset", "revert", "revert-layer", "revert-rule"):
            assert canonicalize(prop, kw) == kw
            assert serialize_value(prop, kw) == kw
            assert serialize_value(prop, Keyword(kw)) == kw

    rc_bad = cli_main(["check", "<length>", "inherit"])
    captured = capsys.readouterr()
    assert rc_bad == EXIT_VALIDATION_ERROR

    rc_good = cli_main(["check", "width", "inherit"])
    captured = capsys.readouterr()
    assert rc_good == EXIT_OK
    assert captured.out.strip() == "inherit"


def test_shared_type_target_resolution_between_library_and_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from stixis._grammar.targets import resolve_target_syntax

    # 1. Built-in evaluator-supported `<type>` targets work in both library and CLI (angled and bare)
    for type_target, raw_val, expected in (
        ("<custom-ident>", "foo", "foo"),
        ("custom-ident", "foo", "foo"),
        ("<dashed-ident>", r"\--my-var", "--my-var"),
        ("<integer>", "42", "42"),
        ("integer", "42", "42"),
        ("<signless-integer>", "42", "42"),
        ("<signed-integer>", "+42", "42"),
        ("<number>", "3.5", "3.5"),
        ("<percentage>", "50%", "50%"),
        ("<dimension>", "10px", "10px"),
        ("<zero>", "0", "0"),
        ("<string>", '"hello"', '"hello"'),
        ("<hex-color>", "#ff00aa", "#ff00aa"),
        ("<urange>", "u+3040-309f", "U+3040-309F"),
        ("<declaration-value>", "1px solid red", "1px solid red"),
        ("<any-value>", "<!-- foo -->", "<!-- foo -->"),
        ("<rotateX()>", "rotateX(30deg)", "rotatex(30deg)"),
        ("<rotatex()>", "rotateX(30deg)", "rotatex(30deg)"),
        ("image-1D", "stripes(red, blue)", "stripes(red, blue)"),
    ):
        assert canonicalize(type_target, raw_val) == expected
        rc_check = cli_main(["check", type_target, raw_val])
        captured = capsys.readouterr()
        assert rc_check == EXIT_OK
        assert captured.out.strip() == expected

        rc_inspect = cli_main(["inspect", type_target])
        captured = capsys.readouterr()
        assert rc_inspect == EXIT_OK
        assert "(type)" in captured.out

    assert serialize_value("<signless-integer>", 0) == "0"
    with pytest.raises(StixisValidationError):
        serialize_value("<signless-integer>", -1)
    assert serialize_value("<hex-color>", "#00ff00") == "#00ff00"

    # 2. Unknown `<type>` targets, unimplemented token placeholders, and multi-term `<...>` expressions
    #    raise StixisValidationError("Unknown CSS target: ...") at target resolution in Python and exit 4 on CLI
    for unknown_target in (
        "<not-a-type>",
        "<lenght>",
        "<colon-token>",
        "<declaration-list>",
        "<length> | <percentage>",
        "<length [0,∞]>",
    ):
        with pytest.raises(StixisValidationError, match="Unknown CSS target"):
            resolve_target_syntax(unknown_target)
        with pytest.raises(StixisValidationError, match="Unknown CSS target"):
            canonicalize(unknown_target, "10px")
        with pytest.raises(StixisValidationError, match="Unknown CSS target"):
            serialize_value(unknown_target, "10px")

        rc_check_bad = cli_main(["check", unknown_target, "10px"])
        captured = capsys.readouterr()
        assert rc_check_bad == EXIT_NOT_FOUND
        assert "unknown CSS target" in captured.err

        rc_inspect_bad = cli_main(["inspect", unknown_target])
        captured = capsys.readouterr()
        assert rc_inspect_bad == EXIT_NOT_FOUND
        assert "unknown CSS target" in captured.err


def test_security_and_descriptor_resolution_regressions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. Custom property names with injection characters (`--x: red; color`) must be rejected eagerly,
    #    while escaped CSS identifiers (`r"--foo\+bar"`) are preserved and serialized safely.
    with pytest.raises(StixisValidationError):
        StyleRule("body", {"--x: red; color": "blue"})
    with pytest.raises(StixisValidationError):
        StyleRule("body", {"--": "blue"})
    escaped_custom = StyleRule(
        "body", {r"--foo\+bar": "blue", r"\2d\2d MyVar": "red", r"\43 olor": "green"}
    )
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
    nested = AtRule(
        "@media", "screen", rules=[FontFace(font_family="Inter", src=Url("inter.woff2"))]
    )
    cleome_out = dumps(nested, options=SerializationOptions.cleome())
    assert "font-family: 'Inter';" in cleome_out
    assert "src: url('inter.woff2');" in cleome_out

    nested_style = StyleRule(
        ".card",
        {"font_family": "Inter"},
        rules=[StyleRule("&:hover", {"font_family": "Noto Sans JP"})],
    )
    assert "font-family: 'Noto Sans JP';" in dumps(
        nested_style, options=SerializationOptions.cleome()
    )

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
    patches = tomllib.loads(
        (repo_root / "codegen" / "spec_patches.toml").read_text(encoding="utf-8")
    )
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


def test_is_property_enabled_defaults_missing_engine_count_to_zero_and_docs_describe_bcd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stixis._grammar.targets as targets_mod
    from stixis._grammar.targets import is_property_enabled

    monkeypatch.setattr(targets_mod, "PROPERTY_ENGINE_COUNTS", {})
    assert not is_property_enabled("width", SerializationOptions(min_engines=1))
    with pytest.raises(StixisValidationError, match=r"0 engine\(s\)"):
        canonicalize("width", "10px", options=SerializationOptions(min_engines=1))

    repo_root = Path(__file__).resolve().parent.parent
    readme_text = (repo_root / "README.md").read_text(encoding="utf-8")
    skill_text = (repo_root / "skills" / "stixis" / "SKILL.md").read_text(encoding="utf-8")
    assert "browser-compat-data" in readme_text
    assert "min_engines" in readme_text
    assert "partial_implementation" in skill_text
    assert "min_engines" in skill_text


def test_tokenize_vds_preserves_nested_parameterized_nonterminals() -> None:
    """Issue #97: `_tokenize_vds` and `_parse_nonterminal_token` must preserve nested `<...>` and `[...]`."""
    from stixis._grammar.vds import (
        ExclusiveOrNode,
        KeywordNode,
        RefNode,
        _tokenize_vds,
        compile_value_syntax,
    )

    toks = _tokenize_vds("<boolean-expr[ <if-test> ]> | else")
    assert toks == [
        ("NONTERM", "<boolean-expr[ <if-test> ]>"),
        ("|", "|"),
        ("KEYWORD", "else"),
    ]
    assert _tokenize_vds("<[-token> | <]-token>") == [
        ("NONTERM", "<[-token>"),
        ("|", "|"),
        ("NONTERM", "<]-token>"),
    ]
    node = compile_value_syntax("<boolean-expr[ <if-test> ]> | else")
    assert node == ExclusiveOrNode((RefNode("<boolean-expr>"), KeywordNode("else")))
    assert canonicalize("<if-condition>", "else") == "else"


def test_singular_urange_and_baseline_grammar_patches() -> None:
    """Issue #100: singular `<urange>`, non-zero position `<urange>` matching, and 4 baseline grammar patches."""
    from stixis._grammar.matcher import evaluate_canonicalize
    from stixis._grammar.targets import lookup_value_node
    from stixis._grammar.vds import compile_value_syntax

    # 1. Singular `<urange>` and `<unicode-range-token>` reject comma lists and multi-item containers,
    #    while `@font-face/unicode-range` continues to accept `<urange>#` (`css-syntax-3` §8.1).
    assert canonicalize("<urange>", "u+0041") == "U+41"
    assert canonicalize("<unicode-range-token>", "u+3040-309f") == "U+3040-309F"
    assert serialize_value("<urange>", 0x41) == "U+41"
    assert serialize_value("<urange>", (0x3040, 0x309F)) == "U+3040-309F"
    assert canonicalize("@font-face/unicode-range", "U+0041, U+0042") == "U+41, U+42"
    assert canonicalize("<urange>", "u/**/+0/**/-1") == "U+0-1"
    for bad_singular in (
        "U+0041, U+0042",
        "U+0041,U+0042",
        "U+00/**/41",
        r"U+\61",
        r"\75+41",
        r"u+0\61",
        r"u+4\31",
        r"u+0041-\62",
    ):
        with pytest.raises(StixisValidationError):
            canonicalize("<urange>", bad_singular)
        with pytest.raises(StixisValidationError):
            canonicalize("<unicode-range-token>", bad_singular)
    # CSS Syntax Level 3 §8.1: `<urange>` matches token representations (`raw`), so backslash escapes are invalid
    for escaped_urange in (r"U+\61", r"\75+41", r"u+0\61", r"u+4\31", r"u+0041-\62"):
        with pytest.raises(StixisValidationError):
            canonicalize("unicode-range", escaped_urange, at_rule="@font-face")
    for bad_py_singular in ("U+0041, U+0042", [0x41, 0x42], {0x41, 0x42}, True):
        with pytest.raises(StixisValidationError):
            serialize_value("<urange>", bad_py_singular)

    # 2. `_TokenMatcher._match_primitive` matches `<urange>` at non-zero token offsets and inside functions.
    seq_syn = "range <urange> <integer>"
    seq_node = compile_value_syntax(seq_syn)
    assert (
        evaluate_canonicalize(
            "<test-urange-seq>",
            seq_syn,
            seq_node,
            "range u+0041-005a 2",
            specs_lookup=lookup_value_node,
        )
        == "range U+41-5A 2"
    )
    for bad_seq in ("range U+41, U+42 2", "range U+00/**/41 2", "range u +41 2"):
        with pytest.raises(StixisValidationError):
            evaluate_canonicalize(
                "<test-urange-seq>",
                seq_syn,
                seq_node,
                bad_seq,
                specs_lookup=lookup_value_node,
            )

    fn_syn = "fn( <urange> , <urange> )"
    fn_node = compile_value_syntax(fn_syn)
    assert (
        evaluate_canonicalize(
            "<test-urange-fn>",
            fn_syn,
            fn_node,
            "fn(u/**/+/**/a/**/?, u+3040-309f)",
            specs_lookup=lookup_value_node,
        )
        == "fn(U+A0-AF, U+3040-309F)"
    )

    # 3. `box-shadow` (`css-backgrounds-3`: `none | <shadow>#`) rejects `none` inside shadow lists.
    assert canonicalize("box-shadow", "none") == "none"
    assert canonicalize("box-shadow", "0 2px 4px black") == "black 0px 2px 4px"
    with pytest.raises(StixisValidationError):
        canonicalize("box-shadow", "none, 0 2px 4px black")

    # 4. `<position>` uses shipped CSS Values 4 1/2/4-value syntax (`w3c/csswg-drafts#2140`; WPT
    #    `object-position-invalid.html`), while `<bg-position>` / `background-position` accepts 3-value syntax.
    assert canonicalize("<position>", "left 10px top 20px") == "left 10px top 20px"
    for bad_pos in ("left 10px center", "block-start 10px inline-end 20px"):
        with pytest.raises(StixisValidationError):
            canonicalize("<position>", bad_pos)
    assert canonicalize("<bg-position>", "left 10px center") == "left 10px center"
    assert canonicalize("background-position", "left 10px center") == "left 10px center"

    # 5. `white-space` and `vertical-align` preserve shipped multi-engine grammars over unshipped drafts.
    assert canonicalize("white-space", "nowrap") == "nowrap"
    assert canonicalize("white-space", "preserve nowrap") == "preserve nowrap"
    for unshipped_ws in ("none", "discard-before", "discard", "preserve-spaces"):
        with pytest.raises(StixisValidationError):
            canonicalize("white-space", unshipped_ws)

    assert canonicalize("vertical-align", "middle") == "middle"
    assert canonicalize("vertical-align", "10px") == "10px"
    for unshipped_va in ("first sub", "alphabetic", "center"):
        with pytest.raises(StixisValidationError):
            canonicalize("vertical-align", unshipped_va)
