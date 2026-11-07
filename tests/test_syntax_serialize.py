"""Unit tests for CSS tokenizer, token sequence formatting, and primitive serializers."""

from __future__ import annotations

import math

import pytest

from stixis import (
    Pct,
    Px,
    StixisSyntaxError,
    StixisValidationError,
    StyleRule,
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
    from stixis._syntax.serialize import serialize_number

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

    # Issue #87: Python `int` values too large to convert to a finite float raise StixisValidationError
    # on `<number>`, dimension, and percentage targets (and in `Px` / `Pct` wrappers), while `<integer>`
    # and `z-index` keep accepting arbitrary-precision integers.
    with pytest.raises(StixisValidationError):
        serialize_value("<number>", 10**400)
    with pytest.raises(StixisValidationError):
        Px(10**400)
    with pytest.raises(StixisValidationError):
        serialize_value("width", Px(10**400))
    with pytest.raises(StixisValidationError):
        serialize_value("width", (10**400, "px"))
    with pytest.raises(StixisValidationError):
        Pct(10**400)
    with pytest.raises(StixisValidationError):
        serialize_value("<percentage>", Pct(10**400))
    with pytest.raises(StixisValidationError):
        serialize_value("<percentage>", (10**400, "%"))
    with pytest.raises(StixisValidationError):
        serialize_value("font-style", ("oblique", (10**400, "deg")))

    expected_digits = "1" + "0" * 400
    assert serialize_value("<integer>", 10**400) == expected_digits
    assert serialize_value("z-index", 10**400) == expected_digits
    assert serialize_value("<integer>", 9007199254740993) == "9007199254740993"


def test_format_token_sequence_preserves_space_after_hyphen_or_hash_delim() -> None:
    from stixis._syntax.tokenizer import tokenize_css

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
    from stixis._syntax.tokenizer import tokenize_css

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


def test_dimension_unit_starting_with_exponent_is_escaped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from stixis._syntax.tokenizer import tokenize_css

    for target in ("--x", "<dimension>"):
        for raw_css, expected_unit in (
            (r"1\65 3", "e3"),
            (r"1\45 3", "E3"),
            (r"1\65 -3", "e-3"),
            (r"1\45 -3", "E-3"),
        ):
            out = canonicalize(target, raw_css)
            toks = [t for t in tokenize_css(out) if t.kind != "whitespace"]
            assert len(toks) == 1
            assert toks[0].kind == "dimension"
            assert toks[0].num == 1
            assert toks[0].unit == expected_unit

    rc = cli_main(["check", "--", "--x", r"1\65 3"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    cli_toks = [t for t in tokenize_css(captured.out.strip()) if t.kind != "whitespace"]
    assert len(cli_toks) == 1
    assert cli_toks[0].kind == "dimension"
    assert cli_toks[0].num == 1
    assert cli_toks[0].unit == "e3"


def test_tokenize_css_comment_after_star_cdo_cdc_and_escaped_eof() -> None:
    from stixis._syntax.tokenizer import tokenize_css

    def kinds(s: str) -> list[tuple[str, str]]:
        return [(t.kind, t.value) for t in tokenize_css(s)]

    assert canonicalize("width", "calc(2*/**/3px)") == "calc(6px)"
    assert canonicalize("width", "calc(2/**/*3px)") == "calc(6px)"
    with pytest.raises(StixisSyntaxError, match=r"Unexpected comment close `\*/`"):
        tokenize_css("2 */ 3")

    assert kinds("-->") == [("CDC", "-->")]
    assert kinds("<!--") == [("CDO", "<!--")]
    assert kinds("a\\") == [("ident", "a\ufffd")]
    assert canonicalize("--x", "<!-- -->") == "<!-- -->"
    with pytest.raises(StixisValidationError):
        canonicalize("content", "<!--")
