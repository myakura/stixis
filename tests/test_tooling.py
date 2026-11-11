"""Unit tests for codegen scripts, WPT test extraction, upstream checker, and release workflows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_upstream_checker_detects_unresolved_refs_and_formats_agent_issue() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import check_upstream

    from stixis._generated.specs import PROPERTY_SYNTAXES, VALUE_SYNTAXES
    from stixis._grammar.vds import compile_value_syntax

    # 1. Resolved syntax produces no unresolved references
    node_ok = compile_value_syntax("normal | <length-percentage>")
    assert check_upstream.find_unresolved_refs(node_ok, PROPERTY_SYNTAXES, VALUE_SYNTAXES) == []

    # 2. Newly introduced upstream non-terminal is flagged for spec_patches.toml
    node_missing = compile_value_syntax("auto | <future-css-type>")
    assert check_upstream.find_unresolved_refs(node_missing, PROPERTY_SYNTAXES, VALUE_SYNTAXES) == [
        "<future-css-type>"
    ]

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
    assert "`action-required`" in md
    assert "`<future-css-type>`" in md
    assert "`font-future`" in md
    assert "## Syntax changes (1)" in md
    assert "## Unresolved targets (1)" in md
    assert "## WPT failures (1)" in md


def test_upstream_markdown_issue_truncates_large_reports_below_github_limit() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import check_upstream

    long_syntax = " | ".join(f"keyword-{i}-with-extra-text" for i in range(40))
    syntax_changes = [
        check_upstream.SyntaxChange(
            target=f"<type-{i}>",
            kind="value",
            change_type="modified",
            old_syntax=long_syntax,
            new_syntax=long_syntax + " | extra",
            resolvable=False,
            unresolved_refs=[f"<missing-{i}>"],
        )
        for i in range(120)
    ]
    unresolved_targets = [
        check_upstream.SyntaxChange(
            target=f"prop-{i}",
            kind="property",
            change_type="modified",
            old_syntax="auto",
            new_syntax=long_syntax,
            resolvable=False,
            unresolved_refs=[f"<missing-{i}>"],
        )
        for i in range(120)
    ]
    wpt_failures = [
        check_upstream.WptFailure(
            suite="css-properties",
            target=f"prop-{i}",
            raw_css=long_syntax,
            expected=long_syntax,
            actual_or_error=f"raised Invalid value: {long_syntax}",
        )
        for i in range(120)
    ]
    report = check_upstream.UpstreamReport(
        webref_old_commit="1111111111111111111111111111111111111111",
        webref_new_commit="2222222222222222222222222222222222222222",
        wpt_old_commit="3333333333333333333333333333333333333333",
        wpt_new_commit="4444444444444444444444444444444444444444",
        syntax_changes=syntax_changes,
        unresolved_targets=unresolved_targets,
        wpt_failures=wpt_failures,
    )
    md = check_upstream.format_markdown_issue(report, run_id="42607512900")
    assert len(md) <= check_upstream._MAX_ISSUE_BODY_CHARS
    assert "* ... (+70 more)" in md
    assert "gh run download 42607512900 -n upstream-report -D .upstream" in md

    import json

    full_json = json.loads(check_upstream.format_json_report(report))
    assert len(full_json["syntax_changes"]) == 120
    assert len(full_json["unresolved_targets"]) == 120
    assert len(full_json["wpt_failures"]) == 120
    assert full_json["syntax_changes"][0]["old_syntax"] == long_syntax

    # Non-numeric run_id is ignored so arbitrary text cannot be injected into the artifact line
    md_bad_run = check_upstream.format_markdown_issue(report, run_id="123; rm -rf /")
    assert "artifact:" not in md_bad_run


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


def test_upstream_watch_workflow_gates_issue_creation_on_exit_code_3() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    wf_text = (repo_root / ".github" / "workflows" / "upstream-watch.yml").read_text(
        encoding="utf-8"
    )
    assert "steps.check.outputs.exit_code == '3'" in wf_text
    assert "continue-on-error: true" not in wf_text


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
        'test_valid_value("width", "calc(10px + 20px)", "calc(30px)");\n'
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
    assert ("width", "calc(10px + 20px)", "calc(30px)") in valid
    assert ("width", "var(--w)", "var(--w)") in valid
    assert ("transform-origin", "top center", "center top") in valid
    assert ("width", "not-a-width") in invalid
    assert ("width", "calc(10px + 20px) extra-invalid") in invalid

    suite = gen_wpt_tests.extract_broad_css_property_suite(wpt_css, min_engines=2)
    assert any(
        prop == "width" and raw == "1px" and exp == "2px" and reason
        for prop, raw, exp, reason in suite.xfail_valid
    )
    assert any(
        prop == "width" and raw == "3px" and exp == ("4px", "5px") and reason
        for prop, raw, exp, reason in suite.xfail_valid
    )
    assert any(
        prop == "width" and raw == "calc(1 / 2px)" and reason
        for prop, raw, reason in suite.xfail_invalid
    )
    assert suite.skipped_counts["disabled_min_engines"] == 1
    assert suite.skipped_counts["unknown_property"] == 1

    # Generated property test modules expose xfail and skipped tables and include calc/var cases
    assert any(
        "calc(" in raw or "var(" in raw
        for _, raw, _ in test_wpt_css_properties.VALID_PROPERTY_CASES
    )
    assert hasattr(test_wpt_css_properties, "XFAIL_VALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_properties, "XFAIL_INVALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_properties, "SKIPPED_PROPERTY_COUNTS")
    assert hasattr(test_wpt_css_fonts, "XFAIL_VALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_fonts, "XFAIL_INVALID_PROPERTY_CASES")
    assert hasattr(test_wpt_css_fonts, "SKIPPED_PROPERTY_COUNTS")


def test_check_upstream_shares_wpt_extraction_and_expected_files_with_gen_wpt_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "codegen"))
    import check_upstream
    import gen_wpt_tests

    assert hasattr(gen_wpt_tests, "extract_all_wpt_suites")
    assert hasattr(gen_wpt_tests, "build_expected_wpt_test_files")

    fake_suites = gen_wpt_tests.WptSuitesExtraction(
        urange_valid=[],
        urange_invalid=[],
        font_family=[],
        font_face=gen_wpt_tests.FontFaceSuiteExtraction(valid=[], invalid=[], xfail_valid=[]),
        css_fonts=gen_wpt_tests.PropertySuiteExtraction(
            valid=[],
            invalid=[],
            xfail_valid=[],
            xfail_invalid=[],
            skipped_counts={"disabled_min_engines": 0, "unknown_property": 0},
        ),
        broad_css=gen_wpt_tests.PropertySuiteExtraction(
            valid=[],
            invalid=[],
            xfail_valid=[],
            xfail_invalid=[],
            skipped_counts={"disabled_min_engines": 0, "unknown_property": 0},
        ),
    )
    expected_map = gen_wpt_tests.build_expected_wpt_test_files(fake_suites)
    assert (gen_wpt_tests.OUT_DIR / "__init__.py") in expected_map

    monkeypatch.setattr(
        gen_wpt_tests,
        "extract_all_wpt_suites",
        lambda _wpt_dir, *, min_engines=2: fake_suites,
    )
    # Matching current files on disk when build_expected_wpt_test_files returns only __init__.py
    init_path = gen_wpt_tests.OUT_DIR / "__init__.py"
    monkeypatch.setattr(
        gen_wpt_tests,
        "build_expected_wpt_test_files",
        lambda _suites, *, min_engines=2, default_min_engines=2, out_dir=gen_wpt_tests.OUT_DIR: {
            init_path: init_path.read_text(encoding="utf-8") + "# modified\n"
        },
    )
    _counts, _failures, wpt_changed = check_upstream._check_wpt_cases(
        Path("/unused"), min_engines=2
    )
    assert wpt_changed is True


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
    assert "importlib.metadata" in init_text
    assert "Verify release tag matches pyproject.toml version" in publish_wf
    assert "tomllib" in publish_wf


def test_gen_specs_and_gen_wpt_tests_prefer_upstream_snapshot_and_honor_explicit_patches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "codegen"))
    import gen_specs
    import gen_wpt_tests

    fake_upstream_webref = tmp_path / "upstream_webref"
    fake_upstream_webref.mkdir()
    fake_upstream_bcd = tmp_path / "upstream_bcd"
    (fake_upstream_bcd / "properties").mkdir(parents=True)
    fake_meta_webref = tmp_path / "meta_webref"
    fake_meta_webref.mkdir()
    fake_meta_bcd = tmp_path / "meta_bcd"
    (fake_meta_bcd / "properties").mkdir(parents=True)

    patches = {
        "meta": {
            "webref_css_dir": str(fake_meta_webref),
            "bcd_css_dir": str(fake_meta_bcd),
        }
    }

    monkeypatch.setattr(gen_specs, "UPSTREAM_WEBREF_CSS_DIR", fake_upstream_webref)
    monkeypatch.setattr(gen_specs, "UPSTREAM_BCD_CSS_DIR", fake_upstream_bcd)
    assert (
        gen_specs.resolve_default_webref_dir(patches, explicit_patches=False)
        == fake_upstream_webref
    )
    assert gen_specs.resolve_default_bcd_dir(patches, explicit_patches=False) == fake_upstream_bcd
    assert gen_specs.resolve_default_webref_dir(patches, explicit_patches=True) == fake_meta_webref
    assert gen_specs.resolve_default_bcd_dir(patches, explicit_patches=True) == fake_meta_bcd

    # When `.upstream/` is absent, default resolution falls back to `[meta]`
    monkeypatch.setattr(gen_specs, "UPSTREAM_WEBREF_CSS_DIR", tmp_path / "no_upstream_webref")
    monkeypatch.setattr(gen_specs, "UPSTREAM_BCD_CSS_DIR", tmp_path / "no_upstream_bcd")
    assert gen_specs.resolve_default_webref_dir(patches, explicit_patches=False) == fake_meta_webref
    assert gen_specs.resolve_default_bcd_dir(patches, explicit_patches=False) == fake_meta_bcd

    # Explicit `--patches` still fails with exit 4 when `[meta]` directories are missing, even if `.upstream/` exists
    monkeypatch.setattr(gen_specs, "UPSTREAM_WEBREF_CSS_DIR", fake_upstream_webref)
    monkeypatch.setattr(gen_specs, "UPSTREAM_BCD_CSS_DIR", fake_upstream_bcd)
    missing_patches = tmp_path / "missing_webref_patches.toml"
    missing_patches.write_text(
        f'[meta]\nwebref_css_dir = "{tmp_path / "missing_webref"}"\nbcd_css_dir = "{fake_meta_bcd}"\n',
        encoding="utf-8",
    )
    rc_missing_webref = gen_specs.main(["--check", "--patches", str(missing_patches)])
    captured = capsys.readouterr()
    assert rc_missing_webref == 4
    assert "missing webref_css_dir" in captured.err

    # `gen_wpt_tests` prefers `.upstream/wpt/css` when present and falls back when absent
    fake_upstream_wpt = tmp_path / "upstream_wpt"
    fake_upstream_wpt.mkdir()
    fake_fallback_wpt = tmp_path / "fallback_wpt"
    fake_fallback_wpt.mkdir()
    monkeypatch.setattr(gen_wpt_tests, "UPSTREAM_WPT_CSS_DIR", fake_upstream_wpt)
    monkeypatch.setattr(gen_wpt_tests, "FALLBACK_WPT_CSS_DIR", fake_fallback_wpt)
    assert gen_wpt_tests.resolve_default_wpt_css_dir() == fake_upstream_wpt

    seen_wpt_dirs: list[Path] = []
    empty_suites = gen_wpt_tests.WptSuitesExtraction(
        urange_valid=[],
        urange_invalid=[],
        font_family=[],
        font_face=gen_wpt_tests.FontFaceSuiteExtraction(valid=[], invalid=[], xfail_valid=[]),
        css_fonts=gen_wpt_tests.PropertySuiteExtraction(
            valid=[],
            invalid=[],
            xfail_valid=[],
            xfail_invalid=[],
            skipped_counts={"disabled_min_engines": 0, "unknown_property": 0},
        ),
        broad_css=gen_wpt_tests.PropertySuiteExtraction(
            valid=[],
            invalid=[],
            xfail_valid=[],
            xfail_invalid=[],
            skipped_counts={"disabled_min_engines": 0, "unknown_property": 0},
        ),
    )
    monkeypatch.setattr(
        gen_wpt_tests,
        "extract_all_wpt_suites",
        lambda wpt_dir, *, min_engines=2: seen_wpt_dirs.append(wpt_dir) or empty_suites,
    )
    monkeypatch.setattr(
        gen_wpt_tests,
        "build_expected_wpt_test_files",
        lambda *_a, **_kw: {},
    )
    assert gen_wpt_tests.main(["--check"]) == 0
    _ = capsys.readouterr()
    assert seen_wpt_dirs == [fake_upstream_wpt]

    monkeypatch.setattr(gen_wpt_tests, "UPSTREAM_WPT_CSS_DIR", tmp_path / "no_upstream_wpt")
    assert gen_wpt_tests.resolve_default_wpt_css_dir() == fake_fallback_wpt


def test_load_and_merge_specs_applies_exclude_specs_and_supersedes(tmp_path: Path) -> None:
    import json

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import gen_specs

    (tmp_path / "css-color-5.json").write_text(
        json.dumps(
            {
                "values": [
                    {
                        "name": "contrast-color()",
                        "type": "function",
                        "value": "contrast-color( <color> )",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "css-color-6.json").write_text(
        json.dumps(
            {
                "values": [
                    {
                        "name": "contrast-color()",
                        "type": "function",
                        "value": "contrast-color( <color> && tbd-fg )",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "css-borders.json").write_text(
        json.dumps(
            {
                "properties": [
                    {"name": "border-start-start-radius", "value": "<border-radius>"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "css-logical.json").write_text(
        json.dumps(
            {
                "properties": [
                    {
                        "name": "border-start-start-radius",
                        "value": "<'border-top-left-radius'>",
                    },
                    {"name": "margin-block-start", "value": "<'margin-top'>"},
                ]
            }
        ),
        encoding="utf-8",
    )

    patches = {
        "support": {"exclude_specs": ["css-color-6.json"]},
        "delta_merges": {"supersedes": {"css-borders.json": ["css-logical.json"]}},
    }
    _units, props, vals, _atrules, _channels = gen_specs.load_and_merge_specs(tmp_path, patches)
    assert vals["<contrast-color()>"] == "contrast-color( <color> )"
    assert props["border-start-start-radius"] == "<border-radius>"
    assert props["margin-block-start"] == "<'margin-top'>"


def test_load_and_merge_specs_excludes_query_features_from_atrule_descriptors(
    tmp_path: Path,
) -> None:
    import json

    from stixis._generated.specs import ATRULE_DESCRIPTORS, VALUE_SYNTAXES

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "codegen"))
    import gen_specs

    (tmp_path / "mediaqueries-5.json").write_text(
        json.dumps(
            {
                "atrules": [
                    {
                        "name": "@media",
                        "value": "@media <media-query-list> { <rule-list> }",
                        "descriptors": [
                            {"name": "hover", "type": "discrete", "value": "none | hover"},
                            {
                                "name": "grid",
                                "type": "discrete",
                                "value": "<mq-boolean>",
                                "values": [
                                    {
                                        "name": "<mq-boolean>",
                                        "type": "type",
                                        "value": "<integer [0,1]>",
                                    }
                                ],
                            },
                            {"name": "width", "type": "range", "value": "<length>"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "css-conditional-5.json").write_text(
        json.dumps(
            {
                "atrules": [
                    {
                        "name": "@container",
                        "value": "@container <container-query-list> { <rule-list> }",
                        "descriptors": [
                            {"name": "inline-size", "type": "range", "value": "<length>"},
                            {
                                "name": "orientation",
                                "type": "discrete",
                                "value": "portrait | landscape",
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "css-fonts.json").write_text(
        json.dumps(
            {
                "atrules": [
                    {
                        "name": "@font-face",
                        "value": "@font-face { <declaration-list> }",
                        "descriptors": [
                            {"name": "font-display", "value": "auto | block | swap"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    _units, _props, vals, atrules, _channels = gen_specs.load_and_merge_specs(tmp_path, {})
    assert atrules["@media"]["descriptors"] == {}
    assert atrules["@container"]["descriptors"] == {}
    assert atrules["@font-face"]["descriptors"] == {"font-display": "auto | block | swap"}
    assert vals["<mq-boolean>"] == "<integer [0,1]>"

    assert ATRULE_DESCRIPTORS["@media"] == {}
    assert ATRULE_DESCRIPTORS["@container"] == {}
    assert "<mq-boolean>" in VALUE_SYNTAXES
