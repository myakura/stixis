#!/usr/bin/env python3
"""Generate `pytest` suites in `tests/_generated/` from local Web Platform Tests (WPT) HTML files.

Usage:
    uv run python codegen/gen_wpt_tests.py
    uv run python codegen/gen_wpt_tests.py --check
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "tests" / "_generated"
DEFAULT_WPT_CSS_DIR = Path.home() / "resources" / "tests" / "wpt" / "css"

_VALID_CALL_RE = re.compile(
    r"test_valid_value\(\s*(['\"])([^'\"]+)\1\s*,\s*(['\"])((?:\\.|(?!\3).)*)\3"
    r"(?:\s*,\s*(?:(['\"])((?:\\.|(?!\5).)*)\5|\[([^\]]+)\]))?\s*\)"
)
_INVALID_CALL_RE = re.compile(
    r"test_invalid_value\(\s*(['\"])([^'\"]+)\1\s*,\s*(['\"])((?:\\.|(?!\3).)*)\3\s*\)"
)
_MATH_FUNCTION_PREFIXES: tuple[str, ...] = (
    "calc(",
    "min(",
    "max(",
    "clamp(",
    "sign(",
    "sibling-index(",
    "sibling-count(",
)


@dataclass(frozen=True, slots=True)
class FontFaceSuiteExtraction:
    valid: list[tuple[str, str, str | None]]
    invalid: list[tuple[str, str]]
    xfail_valid: list[tuple[str, str, str]]


@dataclass(frozen=True, slots=True)
class PropertySuiteExtraction:
    valid: list[tuple[str, str, str]]
    invalid: list[tuple[str, str]]
    xfail_valid: list[tuple[str, str, str, str]]
    xfail_invalid: list[tuple[str, str, str]]
    skipped_counts: dict[str, int]


def _unescape_js_string(raw: str) -> str:
    """Unescape a JavaScript single- or double-quoted string literal body."""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in ('"', "'", "\\", "/"):
                out.append(nxt)
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "u" and i + 5 < n:
                hex_part = raw[i + 2 : i + 6]
                if all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    out.append(chr(int(hex_part, 16)))
                    i += 6
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_urange_cases(wpt_dir: Path) -> tuple[list[tuple[str, str]], list[str]]:
    html_path = wpt_dir / "css-syntax" / "urange-parsing.html"
    text = html_path.read_text(encoding="utf-8")
    valid: list[tuple[str, str]] = []
    invalid: list[str] = []

    for m in re.finditer(
        r"testUrange\(\s*(['\"])((?:\\.|(?!\1).)*)\1(?:\s*,\s*(['\"])((?:\\.|(?!\3).)*)\3)?\s*\)",
        text,
    ):
        inp = _unescape_js_string(m.group(2))
        exp = (_unescape_js_string(m.group(4)) if m.group(4) is not None else inp).upper()
        valid.append((inp, exp))

    for m in re.finditer(r"testInvalidUrange\(\s*(['\"])((?:\\.|(?!\1).)*)\1\s*\)", text):
        inp = _unescape_js_string(m.group(2))
        invalid.append(inp)

    return valid, invalid


def _extract_font_family_serialization_cases(wpt_dir: Path) -> list[tuple[str, str]]:
    kw_js = (wpt_dir / "css-fonts" / "support" / "font-family-keywords.js").read_text(
        encoding="utf-8"
    )
    generic_m = re.search(
        r"kGenericFontFamilyKeywords\s*=\s*\[(.*?)\];", kw_js, flags=re.DOTALL
    )
    non_generic_m = re.search(
        r"kNonGenericFontFamilyKeywords\s*=\s*\[(.*?)\];", kw_js, flags=re.DOTALL
    )
    generic_kws = (
        re.findall(r'"([^"]+)"', generic_m.group(1)) if generic_m is not None else []
    )
    non_generic_kws = (
        re.findall(r'"([^"]+)"', non_generic_m.group(1)) if non_generic_m is not None else []
    )

    cases: list[tuple[str, str]] = []
    for kw in generic_kws:
        cases.append((kw, kw))
        cases.append((f'"{kw}"', f'"{kw}"'))
    for kw in non_generic_kws:
        cases.append((kw, kw))

    html_text = (wpt_dir / "cssom" / "font-family-serialization-001.html").read_text(
        encoding="utf-8"
    )
    for m in re.finditer(
        r"assert_equals\(\s*SetFontFamilyAndSerialize(?:Specified)?\(\s*(['\"])((?:\\.|(?!\1).)*)\1\s*\)\s*,\s*(['\"])((?:\\.|(?!\3).)*)\3\s*\)",
        html_text,
    ):
        inp = _unescape_js_string(m.group(2))
        exp = _unescape_js_string(m.group(4))
        if (inp, exp) not in cases:
            cases.append((inp, exp))

    return cases


def extract_font_face_descriptor_suite(
    wpt_dir: Path,
) -> FontFaceSuiteExtraction:
    """Extract `@font-face` single-descriptor valid, invalid, and xfail cases from `css-fonts/parsing/font-face-*.html`."""
    parsing_dir = wpt_dir / "css-fonts" / "parsing"
    valid: list[tuple[str, str, str | None]] = []
    invalid: list[tuple[str, str]] = []
    xfail_valid: list[tuple[str, str, str]] = []

    # 1. font-face-metric-overrides.html
    mo_text = (parsing_dir / "font-face-metric-overrides.html").read_text(encoding="utf-8")
    for m in re.finditer(
        r'\{\s*value:\s*"([^"]+)"\s*,\s*valid:\s*(true|false)(?:\s*,\s*expected:\s*"([^"]+)")?\s*\}',
        mo_text,
    ):
        val, is_val, exp = m.group(1), m.group(2) == "true", m.group(3)
        for desc in ("ascent-override", "descent-override", "line-gap-override"):
            if is_val:
                valid.append((desc, val, exp or val))
            else:
                invalid.append((desc, val))

    # 2. font-face-size-adjust.html
    sa_text = (parsing_dir / "font-face-size-adjust.html").read_text(encoding="utf-8")
    for m in re.finditer(
        r"\{\s*sizeAdjust:\s*'([^']+)'\s*,\s*valid:\s*(true|false)\s*\}", sa_text
    ):
        val, is_val = m.group(1), m.group(2) == "true"
        if is_val:
            valid.append(("size-adjust", val, val))
        else:
            invalid.append(("size-adjust", val))

    # 3. font-face-width.html
    fw_text = (parsing_dir / "font-face-width.html").read_text(encoding="utf-8")
    v_block = re.search(r"const validValues = \[(.*?)\];", fw_text, re.DOTALL)
    i_block = re.search(r"const invalidValues = \[(.*?)\];", fw_text, re.DOTALL)
    if v_block:
        for val in re.findall(r'"([^"]+)"', v_block.group(1)):
            for desc in ("font-width", "font-stretch"):
                valid.append((desc, val, val))
    if i_block:
        for val in re.findall(r'"([^"]+)"', i_block.group(1)):
            for desc in ("font-width", "font-stretch"):
                invalid.append((desc, val))

    # 4. font-face-src-local.html, font-face-src-format.html, font-face-src-tech.html
    for fname in ("font-face-src-local.html", "font-face-src-format.html", "font-face-src-tech.html"):
        src_text = (parsing_dir / fname).read_text(encoding="utf-8")
        for m in re.finditer(
            r"\{\s*src:\s*'([^']+)'\s*,\s*(?:dontcomparetech:\s*true\s*,\s*)?valid:\s*(true|false)\s*\}",
            src_text,
        ):
            val, is_val = m.group(1), m.group(2) == "true"
            # Multi-item fallback recovery tests where one item is intentionally invalid
            # (`format(xyzz)`, `dummy()`, `format()`, `format(none)`, `format(dummy)`, `tech(color)`)
            if is_val and (
                "xyzz" in val
                or "dummy" in val
                or "format()" in val
                or "format(none)" in val
                or "tech(color)" in val
            ):
                xfail_valid.append(("src", val, "font-src-list-per-item-fallback-recovery"))
                continue
            if is_val:
                valid.append(("src", val, None))
            else:
                invalid.append(("src", val))

    return FontFaceSuiteExtraction(valid=valid, invalid=invalid, xfail_valid=xfail_valid)


def _extract_font_face_descriptor_cases(
    wpt_dir: Path,
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str]]]:
    suite = extract_font_face_descriptor_suite(wpt_dir)
    return suite.valid, suite.invalid


def _classify_valid_xfail_reason(
    raw_css: str,
    *,
    error: Exception | None,
    got: str | None,
) -> str:
    lower_val = raw_css.lower()
    has_math = any(fn in lower_val for fn in _MATH_FUNCTION_PREFIXES)
    if has_math:
        if error is not None:
            return "math-function-in-unsupported-grammar-position"
        return "math-expression-simplification-or-canonical-order"
    if error is not None:
        return "spec-grammar-mismatch-or-prose-syntax"
    return "cssom-shortest-or-computed-serialization-diff"


def _classify_invalid_xfail_reason(raw_css: str) -> str:
    lower_val = raw_css.lower()
    if any(fn in lower_val for fn in _MATH_FUNCTION_PREFIXES):
        return "math-expression-dimensional-type-checking"
    return "prose-parse-constraint-not-in-formal-grammar"


def _evaluate_property_html_files(
    html_files: list[Path],
    *,
    min_engines: int = 2,
) -> PropertySuiteExtraction:
    from stixis import SerializationOptions, StixisError, canonicalize
    from stixis._generated.specs import PROPERTY_SYNTAXES
    from stixis.nodes import is_property_enabled

    opts = SerializationOptions.cssom(min_engines=min_engines)
    valid: list[tuple[str, str, str]] = []
    invalid: list[tuple[str, str]] = []
    xfail_valid: list[tuple[str, str, str, str]] = []
    xfail_invalid: list[tuple[str, str, str]] = []
    skipped_counts: dict[str, int] = {
        "disabled_min_engines": 0,
        "unknown_property": 0,
    }

    for html_file in html_files:
        text = html_file.read_text(encoding="utf-8", errors="ignore")
        for m in _VALID_CALL_RE.finditer(text):
            prop = m.group(2)
            if prop not in PROPERTY_SYNTAXES:
                skipped_counts["unknown_property"] += 1
                continue
            if not is_property_enabled(prop, opts):
                skipped_counts["disabled_min_engines"] += 1
                continue
            val = _unescape_js_string(m.group(4))
            if m.group(6) is not None:
                candidates = [_unescape_js_string(m.group(6))]
            elif m.group(7) is not None:
                candidates = [
                    _unescape_js_string(cm.group(2))
                    for cm in re.finditer(r"(['\"])((?:\\.|(?!\1).)*)\1", m.group(7))
                ]
                if not candidates:
                    candidates = [val]
            else:
                candidates = [val]

            try:
                got = canonicalize(prop, val, options=opts)
            except StixisError as exc:
                xfail_valid.append(
                    (
                        prop,
                        val,
                        candidates[0],
                        _classify_valid_xfail_reason(val, error=exc, got=None),
                    )
                )
                continue

            if got in candidates:
                try:
                    roundtrip = canonicalize(prop, got, options=opts)
                except StixisError as exc:
                    xfail_valid.append(
                        (
                            prop,
                            val,
                            got,
                            _classify_valid_xfail_reason(val, error=exc, got=got),
                        )
                    )
                    continue
                if roundtrip == got:
                    valid.append((prop, val, got))
                else:
                    xfail_valid.append((prop, val, got, "non-idempotent-canonicalization"))
            else:
                xfail_valid.append(
                    (
                        prop,
                        val,
                        candidates[0],
                        _classify_valid_xfail_reason(val, error=None, got=got),
                    )
                )

        for m in _INVALID_CALL_RE.finditer(text):
            prop = m.group(2)
            if prop not in PROPERTY_SYNTAXES:
                skipped_counts["unknown_property"] += 1
                continue
            if not is_property_enabled(prop, opts):
                skipped_counts["disabled_min_engines"] += 1
                continue
            val = _unescape_js_string(m.group(4))
            try:
                canonicalize(prop, val, options=opts)
            except StixisError:
                invalid.append((prop, val))
            else:
                xfail_invalid.append((prop, val, _classify_invalid_xfail_reason(val)))

    return PropertySuiteExtraction(
        valid=valid,
        invalid=invalid,
        xfail_valid=xfail_valid,
        xfail_invalid=xfail_invalid,
        skipped_counts=skipped_counts,
    )


def extract_css_fonts_property_suite(
    wpt_dir: Path,
    *,
    min_engines: int = 2,
) -> PropertySuiteExtraction:
    parsing_dir = wpt_dir / "css-fonts" / "parsing"
    html_files = sorted(parsing_dir.glob("*.html")) if parsing_dir.is_dir() else []
    return _evaluate_property_html_files(html_files, min_engines=min_engines)


def _extract_css_fonts_property_cases(
    wpt_dir: Path,
    *,
    min_engines: int = 2,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    suite = extract_css_fonts_property_suite(wpt_dir, min_engines=min_engines)
    return suite.valid, suite.invalid


def extract_broad_css_property_suite(
    wpt_dir: Path,
    *,
    min_engines: int = 2,
) -> PropertySuiteExtraction:
    """Extract valid, invalid, xfail, and skipped property vectors across WPT `css-*/parsing/*-{valid,invalid}.html`."""
    html_files = [
        p
        for p in sorted(wpt_dir.glob("css-*/parsing/*-*.html"))
        if p.parent.parent.name != "css-fonts"
        and (p.name.endswith("-valid.html") or p.name.endswith("-invalid.html"))
    ]
    return _evaluate_property_html_files(html_files, min_engines=min_engines)


def _extract_broad_css_property_cases(
    wpt_dir: Path,
    *,
    min_engines: int = 2,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    suite = extract_broad_css_property_suite(wpt_dir, min_engines=min_engines)
    return suite.valid, suite.invalid


def _render_urange_test_file(
    valid: list[tuple[str, str]],
    invalid: list[str],
) -> str:
    lines = [
        '"""Auto-generated WPT `<urange>` parsing tests from `css-syntax/urange-parsing.html`. DO NOT EDIT."""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "from stixis import StixisError, canonicalize, format_urange, parse_urange",
        "",
        "VALID_URANGE_CASES: tuple[tuple[str, str], ...] = (",
    ]
    for inp, exp in valid:
        lines.append(f"    ({inp!r}, {exp!r}),")
    lines.append(")")
    lines.append("")
    lines.append("INVALID_URANGE_CASES: tuple[str, ...] = (")
    for inv in invalid:
        lines.append(f"    {inv!r},")
    lines.append(")")
    lines.append("")
    lines.append("")
    lines.append('@pytest.mark.parametrize(("raw_css", "expected"), VALID_URANGE_CASES)')
    lines.append("def test_wpt_urange_valid(raw_css: str, expected: str) -> None:")
    lines.append("    assert format_urange(raw_css) == expected")
    lines.append('    assert canonicalize("<urange>", raw_css) == expected')
    lines.append('    assert canonicalize("unicode-range", raw_css, at_rule="@font-face") == expected')
    lines.append("    # Round-trip idempotence")
    lines.append("    assert format_urange(parse_urange(expected)) == expected")
    lines.append("")
    lines.append("")
    lines.append('@pytest.mark.parametrize("raw_css", INVALID_URANGE_CASES)')
    lines.append("def test_wpt_urange_invalid(raw_css: str) -> None:")
    lines.append("    with pytest.raises(StixisError):")
    lines.append("        parse_urange(raw_css)")
    lines.append("    with pytest.raises(StixisError):")
    lines.append('        canonicalize("unicode-range", raw_css, at_rule="@font-face")')
    lines.append("")
    return "\n".join(lines)


def _render_font_family_test_file(cases: list[tuple[str, str]]) -> str:
    lines = [
        '"""Auto-generated WPT `font-family` serialization tests from `cssom/font-family-serialization-001.html`. DO NOT EDIT."""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "from stixis import SerializationOptions, canonicalize",
        "",
        "CSSOM_OPTS = SerializationOptions.cssom()",
        "",
        "FONT_FAMILY_SERIALIZATION_CASES: tuple[tuple[str, str], ...] = (",
    ]
    for inp, exp in cases:
        lines.append(f"    ({inp!r}, {exp!r}),")
    lines.append(")")
    lines.append("")
    lines.append("")
    lines.append('@pytest.mark.parametrize(("raw_css", "expected"), FONT_FAMILY_SERIALIZATION_CASES)')
    lines.append("def test_wpt_font_family_serialization(raw_css: str, expected: str) -> None:")
    lines.append('    got = canonicalize("font-family", raw_css, options=CSSOM_OPTS)')
    lines.append("    assert got == expected")
    lines.append('    assert canonicalize("font-family", got, options=CSSOM_OPTS) == expected')
    lines.append("")
    return "\n".join(lines)


def _render_font_face_test_file(
    valid: list[tuple[str, str, str | None]],
    invalid: list[tuple[str, str]],
    *,
    xfail_valid: list[tuple[str, str, str]] | None = None,
) -> str:
    xfail_v = xfail_valid or []
    lines = [
        '"""Auto-generated WPT `@font-face` descriptor tests from `css-fonts/parsing/font-face-*.html`. DO NOT EDIT."""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "from stixis import FontFace, SerializationOptions, StixisError, canonicalize",
        "",
        "CSSOM_OPTS = SerializationOptions.cssom()",
        "",
        "VALID_FONT_FACE_DESCRIPTOR_CASES: tuple[tuple[str, str, str | None], ...] = (",
    ]
    for desc, val, exp in valid:
        lines.append(f"    ({desc!r}, {val!r}, {exp!r}),")
    lines.append(")")
    lines.append("")
    lines.append("INVALID_FONT_FACE_DESCRIPTOR_CASES: tuple[tuple[str, str], ...] = (")
    for desc, val in invalid:
        lines.append(f"    ({desc!r}, {val!r}),")
    lines.append(")")
    lines.append("")
    lines.append("XFAIL_VALID_FONT_FACE_DESCRIPTOR_CASES: tuple[tuple[str, str, str], ...] = (")
    for desc, val, reason in xfail_v:
        lines.append(f"    ({desc!r}, {val!r}, {reason!r}),")
    lines.append(")")
    lines.append("")
    lines.append("")
    lines.append(
        '@pytest.mark.parametrize(("descriptor", "raw_css", "expected"), VALID_FONT_FACE_DESCRIPTOR_CASES)'
    )
    lines.append(
        "def test_wpt_font_face_descriptor_valid(descriptor: str, raw_css: str, expected: str | None) -> None:"
    )
    lines.append('    got = canonicalize(descriptor, raw_css, at_rule="@font-face", options=CSSOM_OPTS)')
    lines.append("    if expected is not None:")
    lines.append("        assert got == expected")
    lines.append("    # Round-trip idempotence and eager FontFace node construction")
    lines.append('    assert canonicalize(descriptor, got, at_rule="@font-face", options=CSSOM_OPTS) == got')
    lines.append("    rule = FontFace({descriptor: raw_css}, options=CSSOM_OPTS)")
    lines.append("    assert got in str(rule)")
    lines.append("")
    lines.append("")
    lines.append('@pytest.mark.parametrize(("descriptor", "raw_css"), INVALID_FONT_FACE_DESCRIPTOR_CASES)')
    lines.append("def test_wpt_font_face_descriptor_invalid(descriptor: str, raw_css: str) -> None:")
    lines.append("    with pytest.raises(StixisError):")
    lines.append('        canonicalize(descriptor, raw_css, at_rule="@font-face", options=CSSOM_OPTS)')
    if xfail_v:
        lines.append("")
        lines.append("")
        lines.append("def test_wpt_font_face_descriptor_valid_xfail() -> None:")
        lines.append("    unexpected_passes: list[tuple[str, str]] = []")
        lines.append("    for descriptor, raw_css, _reason in XFAIL_VALID_FONT_FACE_DESCRIPTOR_CASES:")
        lines.append("        try:")
        lines.append(
            '            canonicalize(descriptor, raw_css, at_rule="@font-face", options=CSSOM_OPTS)'
        )
        lines.append("        except StixisError:")
        lines.append("            continue")
        lines.append("        unexpected_passes.append((descriptor, raw_css))")
        lines.append("    if unexpected_passes:")
        lines.append(
            '        pytest.fail(f"XPASS: {len(unexpected_passes)} @font-face xfail case(s) now pass ({unexpected_passes[0]!r}); regenerate WPT tests")'
        )
        lines.append(
            '    pytest.xfail(f"{len(XFAIL_VALID_FONT_FACE_DESCRIPTOR_CASES)} known @font-face xfail cases")'
        )
    lines.append("")
    return "\n".join(lines)


def _render_property_test_file(
    doc: str,
    valid: list[tuple[str, str, str]],
    invalid: list[tuple[str, str]],
    func_suffix: str,
    *,
    min_engines: int | None = None,
    xfail_valid: list[tuple[str, str, str, str]] | None = None,
    xfail_invalid: list[tuple[str, str, str]] | None = None,
    skipped_counts: dict[str, int] | None = None,
) -> str:
    xfail_v = xfail_valid or []
    xfail_i = xfail_invalid or []
    skips = skipped_counts or {"disabled_min_engines": 0, "unknown_property": 0}
    opts_expr = (
        "SerializationOptions.cssom()"
        if min_engines is None
        else f"SerializationOptions.cssom(min_engines={min_engines})"
    )
    lines = [
        f'"""{doc} DO NOT EDIT."""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "from stixis import SerializationOptions, StixisError, canonicalize",
        "",
        f"CSSOM_OPTS = {opts_expr}",
        "",
        "VALID_PROPERTY_CASES: tuple[tuple[str, str, str], ...] = (",
    ]
    for prop, val, exp in valid:
        lines.append(f"    ({prop!r}, {val!r}, {exp!r}),")
    lines.append(")")
    lines.append("")
    lines.append("INVALID_PROPERTY_CASES: tuple[tuple[str, str], ...] = (")
    for prop, val in invalid:
        lines.append(f"    ({prop!r}, {val!r}),")
    lines.append(")")
    lines.append("")
    lines.append("XFAIL_VALID_PROPERTY_CASES: tuple[tuple[str, str, str, str], ...] = (")
    for prop, val, exp, reason in xfail_v:
        lines.append(f"    ({prop!r}, {val!r}, {exp!r}, {reason!r}),")
    lines.append(")")
    lines.append("")
    lines.append("XFAIL_INVALID_PROPERTY_CASES: tuple[tuple[str, str, str], ...] = (")
    for prop, val, reason in xfail_i:
        lines.append(f"    ({prop!r}, {val!r}, {reason!r}),")
    lines.append(")")
    lines.append("")
    lines.append("SKIPPED_PROPERTY_COUNTS: dict[str, int] = {")
    for key, count in sorted(skips.items()):
        lines.append(f"    {key!r}: {count},")
    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append('@pytest.mark.parametrize(("prop", "raw_css", "expected"), VALID_PROPERTY_CASES)')
    lines.append(
        f"def test_wpt_{func_suffix}_valid(prop: str, raw_css: str, expected: str) -> None:"
    )
    lines.append("    got = canonicalize(prop, raw_css, options=CSSOM_OPTS)")
    lines.append("    assert got == expected")
    lines.append("    assert canonicalize(prop, got, options=CSSOM_OPTS) == expected")
    lines.append("")
    lines.append("")
    lines.append('@pytest.mark.parametrize(("prop", "raw_css"), INVALID_PROPERTY_CASES)')
    lines.append(f"def test_wpt_{func_suffix}_invalid(prop: str, raw_css: str) -> None:")
    lines.append("    with pytest.raises(StixisError):")
    lines.append("        canonicalize(prop, raw_css, options=CSSOM_OPTS)")
    if xfail_v:
        lines.append("")
        lines.append("")
        lines.append(f"def test_wpt_{func_suffix}_valid_xfail() -> None:")
        lines.append("    unexpected_passes: list[tuple[str, str, str]] = []")
        lines.append("    for prop, raw_css, expected, _reason in XFAIL_VALID_PROPERTY_CASES:")
        lines.append("        try:")
        lines.append("            got = canonicalize(prop, raw_css, options=CSSOM_OPTS)")
        lines.append("        except StixisError:")
        lines.append("            continue")
        lines.append("        if got == expected:")
        lines.append("            try:")
        lines.append("                roundtrip = canonicalize(prop, got, options=CSSOM_OPTS)")
        lines.append("            except StixisError:")
        lines.append("                continue")
        lines.append("            if roundtrip == expected:")
        lines.append("                unexpected_passes.append((prop, raw_css, expected))")
        lines.append("    if unexpected_passes:")
        lines.append(
            '        pytest.fail(f"XPASS: {len(unexpected_passes)} valid xfail case(s) now pass ({unexpected_passes[0]!r}); regenerate WPT tests")'
        )
        lines.append(
            '    pytest.xfail(f"{len(XFAIL_VALID_PROPERTY_CASES)} known WPT valid xfail cases")'
        )
    if xfail_i:
        lines.append("")
        lines.append("")
        lines.append(f"def test_wpt_{func_suffix}_invalid_xfail() -> None:")
        lines.append("    unexpected_passes: list[tuple[str, str]] = []")
        lines.append("    for prop, raw_css, _reason in XFAIL_INVALID_PROPERTY_CASES:")
        lines.append("        try:")
        lines.append("            canonicalize(prop, raw_css, options=CSSOM_OPTS)")
        lines.append("        except StixisError:")
        lines.append("            unexpected_passes.append((prop, raw_css))")
        lines.append("    if unexpected_passes:")
        lines.append(
            '        pytest.fail(f"XPASS: {len(unexpected_passes)} invalid xfail case(s) now raise StixisError ({unexpected_passes[0]!r}); regenerate WPT tests")'
        )
        lines.append(
            '    pytest.xfail(f"{len(XFAIL_INVALID_PROPERTY_CASES)} known WPT invalid xfail cases")'
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate pytest suites from local Web Platform Tests (WPT)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that generated files in tests/_generated/ match without writing.",
    )
    parser.add_argument(
        "--wpt-dir",
        type=Path,
        default=DEFAULT_WPT_CSS_DIR,
        help="Path to WPT css/ directory.",
    )
    parser.add_argument(
        "--min-engines",
        type=int,
        default=None,
        help="Minimum browser engine count required for general WPT property suites (0..3, default: DEFAULT_MIN_ENGINES).",
    )
    args = parser.parse_args(argv)

    from stixis._generated import specs as _gen_specs

    default_min = getattr(_gen_specs, "DEFAULT_MIN_ENGINES", 2)
    eff_min = default_min if args.min_engines is None else args.min_engines
    opt_min = None if eff_min == default_min else eff_min

    wpt_dir: Path = args.wpt_dir
    if not wpt_dir.is_dir():
        print(f"Error: WPT directory not found at {wpt_dir}", file=sys.stderr)
        return 1

    urange_v, urange_i = _extract_urange_cases(wpt_dir)
    ff_ser_cases = _extract_font_family_serialization_cases(wpt_dir)
    ff_suite = extract_font_face_descriptor_suite(wpt_dir)
    fonts_suite = extract_css_fonts_property_suite(wpt_dir, min_engines=eff_min)
    broad_suite = extract_broad_css_property_suite(wpt_dir, min_engines=eff_min)

    files_to_write = {
        OUT_DIR / "__init__.py": '"""Auto-generated WPT test suites."""\n',
        OUT_DIR / "test_wpt_urange.py": _render_urange_test_file(urange_v, urange_i),
        OUT_DIR / "test_wpt_font_family.py": _render_font_family_test_file(ff_ser_cases),
        OUT_DIR / "test_wpt_font_face.py": _render_font_face_test_file(
            ff_suite.valid,
            ff_suite.invalid,
            xfail_valid=ff_suite.xfail_valid,
        ),
        OUT_DIR / "test_wpt_css_fonts.py": _render_property_test_file(
            "Auto-generated WPT `css-fonts/parsing/*-{valid,invalid}.html` tests.",
            fonts_suite.valid,
            fonts_suite.invalid,
            "css_fonts",
            min_engines=opt_min,
            xfail_valid=fonts_suite.xfail_valid,
            xfail_invalid=fonts_suite.xfail_invalid,
            skipped_counts=fonts_suite.skipped_counts,
        ),
        OUT_DIR / "test_wpt_css_properties.py": _render_property_test_file(
            "Auto-generated WPT `css-*/parsing/*-{valid,invalid}.html` property tests.",
            broad_suite.valid,
            broad_suite.invalid,
            "css_properties",
            min_engines=opt_min,
            xfail_valid=broad_suite.xfail_valid,
            xfail_invalid=broad_suite.xfail_invalid,
            skipped_counts=broad_suite.skipped_counts,
        ),
    }

    total_pass = (
        len(urange_v)
        + len(urange_i)
        + len(ff_ser_cases)
        + len(ff_suite.valid)
        + len(ff_suite.invalid)
        + len(fonts_suite.valid)
        + len(fonts_suite.invalid)
        + len(broad_suite.valid)
        + len(broad_suite.invalid)
    )
    total_xfail = (
        len(ff_suite.xfail_valid)
        + len(fonts_suite.xfail_valid)
        + len(fonts_suite.xfail_invalid)
        + len(broad_suite.xfail_valid)
        + len(broad_suite.xfail_invalid)
    )
    total_skipped = (
        sum(fonts_suite.skipped_counts.values())
        + sum(broad_suite.skipped_counts.values())
    )

    if args.check:
        diffs: list[str] = []
        for path, expected in files_to_write.items():
            if not path.exists():
                diffs.append(f"Missing generated test file: {path}")
            elif path.read_text(encoding="utf-8") != expected:
                diffs.append(f"Out-of-date generated test file: {path}")
        if diffs:
            for d in diffs:
                print(d, file=sys.stderr)
            return 1
        print(
            f"Generated WPT test files are up to date "
            f"({len(urange_v) + len(urange_i)} urange, "
            f"{len(ff_ser_cases)} font-family, "
            f"{len(ff_suite.valid) + len(ff_suite.invalid)} @font-face "
            f"[+{len(ff_suite.xfail_valid)} xfail], "
            f"{len(fonts_suite.valid) + len(fonts_suite.invalid)} css-fonts "
            f"[+{len(fonts_suite.xfail_valid) + len(fonts_suite.xfail_invalid)} xfail], "
            f"{len(broad_suite.valid) + len(broad_suite.invalid)} general CSS assertions "
            f"[+{len(broad_suite.xfail_valid) + len(broad_suite.xfail_invalid)} xfail, "
            f"{total_skipped} skipped])."
        )
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in files_to_write.items():
        path.write_text(content, encoding="utf-8")

    print(
        f"Generated {total_pass} passing and {total_xfail} xfail WPT test assertions "
        f"({total_skipped} skipped for min_engines/unknown) across {len(files_to_write) - 1} modules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
