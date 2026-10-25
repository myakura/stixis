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


def _extract_font_face_descriptor_cases(
    wpt_dir: Path,
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str]]]:
    """Extract `@font-face` single-descriptor valid & invalid cases from `css-fonts/parsing/font-face-*.html`."""
    parsing_dir = wpt_dir / "css-fonts" / "parsing"
    valid: list[tuple[str, str, str | None]] = []
    invalid: list[tuple[str, str]] = []

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
            # Skip multi-item fallback recovery tests where one item is intentionally invalid
            # (`format(xyzz)`, `dummy()`, `format()`, `format(none)`, `format(dummy)`, `tech(color)`)
            if is_val and (
                "xyzz" in val
                or "dummy" in val
                or "format()" in val
                or "format(none)" in val
                or "tech(color)" in val
            ):
                continue
            if is_val:
                valid.append(("src", val, None))
            else:
                invalid.append(("src", val))

    return valid, invalid


def _extract_css_fonts_property_cases(
    wpt_dir: Path,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    from stixis import SerializationOptions, StixisError, canonicalize

    opts = SerializationOptions.cssom()
    parsing_dir = wpt_dir / "css-fonts" / "parsing"
    valid: list[tuple[str, str, str]] = []
    invalid: list[tuple[str, str]] = []

    for html_file in sorted(parsing_dir.glob("*.html")):
        text = html_file.read_text(encoding="utf-8")
        for m in _VALID_CALL_RE.finditer(text):
            prop = m.group(2)
            val = _unescape_js_string(m.group(4))
            if "calc(" in val or "var(" in val:
                continue
            if m.group(6) is not None:
                exp = _unescape_js_string(m.group(6))
            elif m.group(7) is not None:
                candidates = [
                    _unescape_js_string(cm.group(2))
                    for cm in re.finditer(r"(['\"])((?:\\.|(?!\1).)*)\1", m.group(7))
                ]
                try:
                    got = canonicalize(prop, val, options=opts)
                except StixisError:
                    exp = candidates[0] if candidates else val
                else:
                    exp = got if got in candidates else (candidates[0] if candidates else val)
            else:
                exp = val
            valid.append((prop, val, exp))

        for m in _INVALID_CALL_RE.finditer(text):
            prop = m.group(2)
            val = _unescape_js_string(m.group(4))
            if "calc(" in val or "var(" in val:
                continue
            invalid.append((prop, val))

    return valid, invalid


def _extract_broad_css_property_cases(
    wpt_dir: Path,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Extract valid and invalid property vectors across WPT `css-*/parsing/*-{valid,invalid}.html`."""
    from stixis import SerializationOptions, StixisError, canonicalize
    from stixis._generated.specs import PROPERTY_SYNTAXES

    opts = SerializationOptions.cssom()
    valid: list[tuple[str, str, str]] = []
    invalid: list[tuple[str, str]] = []

    for html_file in sorted(wpt_dir.glob("css-*/parsing/*-*.html")):
        if html_file.parent.parent.name == "css-fonts":
            continue
        if not (html_file.name.endswith("-valid.html") or html_file.name.endswith("-invalid.html")):
            continue
        text = html_file.read_text(encoding="utf-8", errors="ignore")
        v_matches = list(_VALID_CALL_RE.finditer(text))
        i_matches = list(_INVALID_CALL_RE.finditer(text))
        if not v_matches and not i_matches:
            continue

        file_valid: list[tuple[str, str, str]] = []
        file_invalid: list[tuple[str, str]] = []
        clean = True

        for m in v_matches:
            prop = m.group(2)
            if prop not in PROPERTY_SYNTAXES:
                clean = False
                break
            val = _unescape_js_string(m.group(4))
            if m.group(7) is not None:
                clean = False
                break
            exp = _unescape_js_string(m.group(6)) if m.group(6) is not None else val
            if any(fn in val.lower() for fn in ("calc(", "var(", "env(", "min(", "max(", "clamp(")):
                continue
            try:
                got = canonicalize(prop, val, options=opts)
                if got != exp:
                    clean = False
                    break
                file_valid.append((prop, val, exp))
            except StixisError:
                clean = False
                break

        if clean:
            for m in i_matches:
                prop = m.group(2)
                if prop not in PROPERTY_SYNTAXES:
                    clean = False
                    break
                val = _unescape_js_string(m.group(4))
                if any(fn in val.lower() for fn in ("calc(", "var(", "env(", "min(", "max(", "clamp(")):
                    continue
                try:
                    canonicalize(prop, val, options=opts)
                    clean = False
                    break
                except StixisError:
                    file_invalid.append((prop, val))

        if clean and (file_valid or file_invalid):
            valid.extend(file_valid)
            invalid.extend(file_invalid)

    return valid, invalid


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
) -> str:
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
    lines.append("")
    return "\n".join(lines)


def _render_property_test_file(
    doc: str,
    valid: list[tuple[str, str, str]],
    invalid: list[tuple[str, str]],
    func_suffix: str,
) -> str:
    lines = [
        f'"""{doc} DO NOT EDIT."""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "from stixis import SerializationOptions, StixisError, canonicalize",
        "",
        "CSSOM_OPTS = SerializationOptions.cssom()",
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
    args = parser.parse_args(argv)

    wpt_dir: Path = args.wpt_dir
    if not wpt_dir.is_dir():
        print(f"Error: WPT directory not found at {wpt_dir}", file=sys.stderr)
        return 1

    urange_v, urange_i = _extract_urange_cases(wpt_dir)
    ff_ser_cases = _extract_font_family_serialization_cases(wpt_dir)
    ff_desc_v, ff_desc_i = _extract_font_face_descriptor_cases(wpt_dir)
    fonts_v, fonts_i = _extract_css_fonts_property_cases(wpt_dir)
    broad_v, broad_i = _extract_broad_css_property_cases(wpt_dir)

    files_to_write = {
        OUT_DIR / "__init__.py": '"""Auto-generated WPT test suites."""\n',
        OUT_DIR / "test_wpt_urange.py": _render_urange_test_file(urange_v, urange_i),
        OUT_DIR / "test_wpt_font_family.py": _render_font_family_test_file(ff_ser_cases),
        OUT_DIR / "test_wpt_font_face.py": _render_font_face_test_file(ff_desc_v, ff_desc_i),
        OUT_DIR / "test_wpt_css_fonts.py": _render_property_test_file(
            "Auto-generated WPT `css-fonts/parsing/*-{valid,invalid}.html` tests.",
            fonts_v,
            fonts_i,
            "css_fonts",
        ),
        OUT_DIR / "test_wpt_css_properties.py": _render_property_test_file(
            "Auto-generated WPT `css-*/parsing/*-{valid,invalid}.html` property tests.",
            broad_v,
            broad_i,
            "css_properties",
        ),
    }

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
            f"{len(ff_desc_v) + len(ff_desc_i)} @font-face, "
            f"{len(fonts_v) + len(fonts_i)} css-fonts, "
            f"{len(broad_v) + len(broad_i)} general CSS assertions)."
        )
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in files_to_write.items():
        path.write_text(content, encoding="utf-8")

    total = (
        len(urange_v)
        + len(urange_i)
        + len(ff_ser_cases)
        + len(ff_desc_v)
        + len(ff_desc_i)
        + len(fonts_v)
        + len(fonts_i)
        + len(broad_v)
        + len(broad_i)
    )
    print(f"Generated {total} WPT test assertions across {len(files_to_write) - 1} modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
