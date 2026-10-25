#!/usr/bin/env python3
"""Compare upstream `w3c/webref` and `web-platform-tests/wpt` against Stixis's generated baseline
and emit an agent-ready triage report / GitHub Issue specification.

Exit codes:
    0: Upstream matches current generated baseline (or `--update-baseline` succeeded)
    1: Error (missing files or invalid arguments)
    3: Upstream changes or action-required items detected
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "codegen"))
sys.path.insert(0, str(REPO_ROOT))

import gen_specs
import gen_wpt_tests

from stixis import SerializationOptions, StixisError, canonicalize
from stixis._generated import specs as current_specs
from stixis._generated import units as current_units
from stixis.syntax import (
    _BUILTIN_PRIMITIVES,
    AllOfNode,
    AnyOrderNode,
    DelimNode,
    ExclusiveOrNode,
    FunctionNode,
    JuxtaposeNode,
    KeywordNode,
    MultiplierNode,
    NonEmptyGroupNode,
    PrimitiveNode,
    RefNode,
    ValueNode,
    compile_value_syntax,
)


@dataclass(slots=True)
class SyntaxChange:
    target: str
    kind: str  # "property" | "descriptor" | "value" | "prelude"
    change_type: str  # "added" | "modified" | "removed"
    old_syntax: str | None
    new_syntax: str | None
    resolvable: bool = True
    unresolved_refs: list[str] = field(default_factory=list)
    compile_error: str | None = None


@dataclass(slots=True)
class WptFailure:
    suite: str
    target: str
    raw_css: str
    expected: str | None
    actual_or_error: str


@dataclass(slots=True)
class UpstreamReport:
    webref_old_commit: str
    webref_new_commit: str
    wpt_old_commit: str
    wpt_new_commit: str
    bcd_old_commit: str = "unknown"
    bcd_new_commit: str = "unknown"
    min_engines: int = 2
    unit_changes: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    syntax_changes: list[SyntaxChange] = field(default_factory=list)
    experimental_syntax_changes: list[SyntaxChange] = field(default_factory=list)
    support_changes: list[str] = field(default_factory=list)
    unresolved_targets: list[SyntaxChange] = field(default_factory=list)
    wpt_counts_old: dict[str, int] = field(default_factory=dict)
    wpt_counts_new: dict[str, int] = field(default_factory=dict)
    wpt_failures: list[WptFailure] = field(default_factory=list)
    specs_files_changed: bool = False
    wpt_files_changed: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(
            self.unit_changes
            or self.syntax_changes
            or self.support_changes
            or self.unresolved_targets
            or self.wpt_failures
            or self.wpt_files_changed
        )

    @property
    def needs_agent_intervention(self) -> bool:
        return bool(self.unresolved_targets or self.wpt_failures)


def _detect_git_commit(directory: Path) -> str:
    """Resolve the current git commit SHA for `directory` without requiring network access."""
    cur = directory.resolve()
    for candidate in (cur, *cur.parents):
        git_dir = candidate / ".git"
        if git_dir.is_dir():
            head_file = git_dir / "HEAD"
            if head_file.is_file():
                head_txt = head_file.read_text(encoding="utf-8").strip()
                if head_txt.startswith("ref: "):
                    ref_path = git_dir / head_txt[5:]
                    if ref_path.is_file():
                        return ref_path.read_text(encoding="utf-8").strip()
                elif len(head_txt) == 40:
                    return head_txt
            break
    try:
        res = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except OSError:
        return "unknown"
    return "unknown"


def find_unresolved_refs(
    node: ValueNode,
    properties: dict[str, str],
    values: dict[str, str],
    visited: set[tuple[str, bool]] | None = None,
) -> list[str]:
    """Return a sorted list of unresolved `<type>` or `<'prop'>` references in `node`."""
    if visited is None:
        visited = set()
    missing: set[str] = set()

    if isinstance(node, (KeywordNode, DelimNode, PrimitiveNode)):
        return []
    if isinstance(node, FunctionNode):
        if node.arg is not None:
            missing.update(find_unresolved_refs(node.arg, properties, values, visited))
        return sorted(missing)
    if isinstance(node, (MultiplierNode, NonEmptyGroupNode)):
        return find_unresolved_refs(node.child, properties, values, visited)
    if isinstance(node, (JuxtaposeNode, AllOfNode, AnyOrderNode, ExclusiveOrNode)):
        for c in node.items:
            missing.update(find_unresolved_refs(c, properties, values, visited))
        return sorted(missing)
    if isinstance(node, RefNode):
        key = (node.name, node.is_property)
        if key in visited:
            return []
        visited.add(key)
        if node.is_property:
            syn = properties.get(node.name)
            if not syn:
                return [f"<'{node.name}'>"]
            try:
                compiled = compile_value_syntax(syn)
            except StixisError:
                return [f"<'{node.name}'>"]
            return find_unresolved_refs(compiled, properties, values, visited)
        if node.name in _BUILTIN_PRIMITIVES:
            return []
        syn = values.get(node.name) or values.get(node.name.strip("<>"))
        if not syn:
            return [node.name]
        try:
            compiled = compile_value_syntax(syn)
        except StixisError:
            return [node.name]
        return find_unresolved_refs(compiled, properties, values, visited)
    return []


def _expect_invalid_raises(
    suite: str,
    target: str,
    raw_css: str,
    *,
    at_rule: str | None = None,
    options: SerializationOptions | None = None,
) -> WptFailure | None:
    """Return a `WptFailure` if `raw_css` is unexpectedly accepted instead of raising `StixisError`."""
    try:
        got = canonicalize(target, raw_css, at_rule=at_rule, options=options)
    except StixisError as exc:
        if not str(exc):
            return WptFailure(suite, target, raw_css, None, "raised empty StixisError")
        return None
    return WptFailure(suite, target, raw_css, None, f"accepted as {got!r}")


def _check_wpt_cases(
    wpt_dir: Path,
    *,
    min_engines: int = 2,
) -> tuple[dict[str, int], list[WptFailure], bool]:
    """Run WPT extraction and verify every extracted vector against current Stixis."""
    from stixis.nodes import is_property_enabled

    failures: list[WptFailure] = []
    opts = SerializationOptions.cssom(min_engines=min_engines)

    urange_v, urange_i = gen_wpt_tests._extract_urange_cases(wpt_dir)
    for raw_css, expected in urange_v:
        try:
            got = canonicalize("<urange>", raw_css)
            if got != expected:
                failures.append(
                    WptFailure("urange", "<urange>", raw_css, expected, f"got {got!r}")
                )
        except StixisError as exc:
            failures.append(WptFailure("urange", "<urange>", raw_css, expected, f"raised {exc}"))
    for raw_css in urange_i:
        failure = _expect_invalid_raises("urange-invalid", "<urange>", raw_css)
        if failure is not None:
            failures.append(failure)

    ff_ser = gen_wpt_tests._extract_font_family_serialization_cases(wpt_dir)
    for raw_css, expected in ff_ser:
        try:
            got = canonicalize("font-family", raw_css, options=opts)
            if got != expected:
                failures.append(
                    WptFailure("font-family", "font-family", raw_css, expected, f"got {got!r}")
                )
        except StixisError as exc:
            failures.append(
                WptFailure("font-family", "font-family", raw_css, expected, f"raised {exc}")
            )

    ff_desc_v, ff_desc_i = gen_wpt_tests._extract_font_face_descriptor_cases(wpt_dir)
    for desc, raw_css, expected in ff_desc_v:
        try:
            got = canonicalize(desc, raw_css, at_rule="@font-face", options=opts)
            if expected is not None and got != expected:
                failures.append(
                    WptFailure(
                        "@font-face", f"@font-face/{desc}", raw_css, expected, f"got {got!r}"
                    )
                )
        except StixisError as exc:
            failures.append(
                WptFailure(
                    "@font-face", f"@font-face/{desc}", raw_css, expected, f"raised {exc}"
                )
            )
    for desc, raw_css in ff_desc_i:
        failure = _expect_invalid_raises(
            "@font-face-invalid",
            desc,
            raw_css,
            at_rule="@font-face",
            options=opts,
        )
        if failure is not None:
            failures.append(failure)

    fonts_v, fonts_i = gen_wpt_tests._extract_css_fonts_property_cases(
        wpt_dir, min_engines=min_engines
    )
    for prop, raw_css, expected in fonts_v:
        if prop in current_specs.PROPERTY_SYNTAXES and not is_property_enabled(prop, opts):
            continue
        try:
            got = canonicalize(prop, raw_css, options=opts)
            if got != expected:
                failures.append(
                    WptFailure("css-fonts", prop, raw_css, expected, f"got {got!r}")
                )
        except StixisError as exc:
            failures.append(WptFailure("css-fonts", prop, raw_css, expected, f"raised {exc}"))
    for prop, raw_css in fonts_i:
        if prop in current_specs.PROPERTY_SYNTAXES and not is_property_enabled(prop, opts):
            continue
        failure = _expect_invalid_raises("css-fonts-invalid", prop, raw_css, options=opts)
        if failure is not None:
            failures.append(failure)

    broad_v, broad_i = gen_wpt_tests._extract_broad_css_property_cases(
        wpt_dir, min_engines=min_engines
    )

    # Also verify that all previously committed general CSS property vectors in
    # `tests/_generated/test_wpt_css_properties.py` still pass and were not silently dropped,
    # and diagnose any failing vector in WPT `css-*/parsing/*-{valid,invalid}.html` files
    # when either a previously clean file regresses or `wpt_dir` contains only failing files.
    from tests._generated import test_wpt_css_properties as committed_broad

    new_broad_v_set = set(broad_v)
    new_broad_i_set = set(broad_i)
    dropped_props: set[str] = set()
    for prop, raw_css, expected in committed_broad.VALID_PROPERTY_CASES:
        if not is_property_enabled(prop, opts):
            continue
        if (prop, raw_css, expected) not in new_broad_v_set:
            dropped_props.add(prop)
            try:
                got = canonicalize(prop, raw_css, options=opts)
                if got != expected:
                    failures.append(
                        WptFailure("css-properties", prop, raw_css, expected, f"got {got!r}")
                    )
            except StixisError as exc:
                failures.append(
                    WptFailure("css-properties", prop, raw_css, expected, f"raised {exc}")
                )
    for prop, raw_css in committed_broad.INVALID_PROPERTY_CASES:
        if not is_property_enabled(prop, opts):
            continue
        if (prop, raw_css) not in new_broad_i_set:
            dropped_props.add(prop)
            failure = _expect_invalid_raises("css-properties-invalid", prop, raw_css, options=opts)
            if failure is not None:
                failures.append(failure)

    # Scan candidate WPT files directly for any failing vector on enabled properties when
    # either a committed property was dropped or no clean broad cases were extracted.
    if dropped_props or (not broad_v and not broad_i):
        for html_file in sorted(wpt_dir.glob("css-*/parsing/*-*.html")):
            if html_file.parent.parent.name == "css-fonts":
                continue
            if not (
                html_file.name.endswith("-valid.html") or html_file.name.endswith("-invalid.html")
            ):
                continue
            text = html_file.read_text(encoding="utf-8", errors="ignore")
            for m in gen_wpt_tests._VALID_CALL_RE.finditer(text):
                prop = m.group(2)
                if dropped_props and prop not in dropped_props:
                    continue
                if prop not in current_specs.PROPERTY_SYNTAXES or not is_property_enabled(
                    prop, opts
                ):
                    continue
                val = gen_wpt_tests._unescape_js_string(m.group(4))
                if any(
                    fn in val.lower() for fn in ("calc(", "var(", "env(", "min(", "max(", "clamp(")
                ):
                    continue
                if m.group(6) is not None:
                    candidates = [gen_wpt_tests._unescape_js_string(m.group(6))]
                elif m.group(7) is not None:
                    candidates = [
                        gen_wpt_tests._unescape_js_string(cm.group(2))
                        for cm in re.finditer(r"(['\"])((?:\\.|(?!\1).)*)\1", m.group(7))
                    ]
                    if not candidates:
                        candidates = [val]
                else:
                    candidates = [val]
                try:
                    got = canonicalize(prop, val, options=opts)
                    if got not in candidates:
                        failures.append(
                            WptFailure(
                                "css-properties", prop, val, candidates[0], f"got {got!r}"
                            )
                        )
                except StixisError as exc:
                    failures.append(
                        WptFailure(
                            "css-properties", prop, val, candidates[0], f"raised {exc}"
                        )
                    )
            for m in gen_wpt_tests._INVALID_CALL_RE.finditer(text):
                prop = m.group(2)
                if dropped_props and prop not in dropped_props:
                    continue
                if prop not in current_specs.PROPERTY_SYNTAXES or not is_property_enabled(
                    prop, opts
                ):
                    continue
                val = gen_wpt_tests._unescape_js_string(m.group(4))
                if any(
                    fn in val.lower() for fn in ("calc(", "var(", "env(", "min(", "max(", "clamp(")
                ):
                    continue
                failure = _expect_invalid_raises(
                    "css-properties-invalid", prop, val, options=opts
                )
                if failure is not None:
                    failures.append(failure)

    counts = {
        "urange_cases": len(urange_v) + len(urange_i),
        "font_family_cases": len(ff_ser),
        "font_face_cases": len(ff_desc_v) + len(ff_desc_i),
        "css_fonts_cases": len(fonts_v) + len(fonts_i),
        "css_properties_cases": len(broad_v) + len(broad_i),
    }

    opt_min = None if min_engines == getattr(current_specs, "DEFAULT_MIN_ENGINES", 2) else min_engines
    expected_files = {
        gen_wpt_tests.OUT_DIR / "test_wpt_urange.py": gen_wpt_tests._render_urange_test_file(
            urange_v, urange_i
        ),
        gen_wpt_tests.OUT_DIR / "test_wpt_font_family.py": (
            gen_wpt_tests._render_font_family_test_file(ff_ser)
        ),
        gen_wpt_tests.OUT_DIR / "test_wpt_font_face.py": gen_wpt_tests._render_font_face_test_file(
            ff_desc_v, ff_desc_i
        ),
        gen_wpt_tests.OUT_DIR / "test_wpt_css_fonts.py": gen_wpt_tests._render_property_test_file(
            "Auto-generated WPT `css-fonts/parsing/*-{valid,invalid}.html` tests.",
            fonts_v,
            fonts_i,
            "css_fonts",
            min_engines=opt_min,
        ),
        gen_wpt_tests.OUT_DIR / "test_wpt_css_properties.py": (
            gen_wpt_tests._render_property_test_file(
                "Auto-generated WPT `css-*/parsing/*-{valid,invalid}.html` property tests.",
                broad_v,
                broad_i,
                "css_properties",
                min_engines=opt_min,
            )
        ),
    }
    wpt_changed = any(
        not p.is_file() or p.read_text(encoding="utf-8") != content
        for p, content in expected_files.items()
    )

    return counts, failures, wpt_changed


def analyze_upstream(
    webref_dir: Path,
    wpt_dir: Path,
    patches_path: Path,
    baseline_path: Path,
    *,
    min_engines: int = 2,
    bcd_dir: Path | None = None,
) -> UpstreamReport:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    patches = tomllib.loads(patches_path.read_text(encoding="utf-8"))

    units_by_type, properties, values, atrules = gen_specs.load_and_merge_specs(webref_dir, patches)
    old_engine_counts = dict(getattr(current_specs, "PROPERTY_ENGINE_COUNTS", {}))
    if bcd_dir is not None and (bcd_dir / "properties").is_dir():
        engine_counts = gen_specs.load_bcd_engine_counts(bcd_dir, properties)
        bcd_new_commit = _detect_git_commit(bcd_dir)
    else:
        engine_counts = {p: old_engine_counts.get(p, 0) for p in properties}
        bcd_new_commit = baseline.get("bcd", {}).get("commit", "unknown")

    allow_props = set(patches.get("support", {}).get("allow_properties", []))
    support_changes: list[str] = []
    for pname in sorted(set(old_engine_counts) | set(engine_counts)):
        old_c = old_engine_counts.get(pname, 0)
        new_c = engine_counts.get(pname, 0)
        if old_c != new_c:
            old_en = min_engines <= 0 or pname in allow_props or old_c >= min_engines
            new_en = min_engines <= 0 or pname in allow_props or new_c >= min_engines
            if old_en != new_en:
                state_str = "ENABLED" if new_en else "DISABLED"
                support_changes.append(
                    f"`{pname}`: {old_c} -> {new_c} engines (**{state_str}** at `min_engines={min_engines}`)"
                )
            elif max(old_c, new_c) >= min_engines:
                support_changes.append(f"`{pname}`: {old_c} -> {new_c} engines")

    def _is_prop_monitored(pname: str) -> bool:
        if min_engines <= 0:
            return True
        if pname in allow_props:
            return True
        return max(
            engine_counts.get(pname, 0),
            old_engine_counts.get(pname, 0),
        ) >= min_engines

    # Compare units
    unit_changes: dict[str, dict[str, list[str]]] = {}
    for utype, new_set in sorted(units_by_type.items()):
        old_set = set(current_units.UNITS_BY_TYPE.get(utype, frozenset()))
        added = sorted(new_set - old_set)
        removed = sorted(old_set - new_set)
        if added or removed:
            unit_changes[utype] = {"added": added, "removed": removed}

    syntax_changes: list[SyntaxChange] = []
    experimental_syntax_changes: list[SyntaxChange] = []
    unresolved_targets: list[SyntaxChange] = []

    def _inspect_target(
        target: str,
        kind: str,
        old_syn: str | None,
        new_syn: str | None,
        *,
        monitored: bool = True,
    ) -> None:
        if new_syn is None and old_syn is not None:
            entry = SyntaxChange(
                target=target,
                kind=kind,
                change_type="removed",
                old_syntax=old_syn,
                new_syntax=None,
            )
            if monitored:
                syntax_changes.append(entry)
            else:
                experimental_syntax_changes.append(entry)
            return
        if new_syn is None:
            return

        resolvable = True
        missing: list[str] = []
        compile_err: str | None = None
        if new_syn != "":
            try:
                node = compile_value_syntax(new_syn)
                missing = find_unresolved_refs(node, properties, values)
                if missing or not gen_specs.is_node_resolvable(node, properties, values):
                    resolvable = False
            except StixisError as exc:
                resolvable = False
                compile_err = str(exc)

        change_type = (
            "added"
            if old_syn is None
            else ("modified" if old_syn != new_syn else "unchanged")
        )
        entry = SyntaxChange(
            target=target,
            kind=kind,
            change_type=change_type,
            old_syntax=old_syn,
            new_syntax=new_syn,
            resolvable=resolvable,
            unresolved_refs=missing,
            compile_error=compile_err,
        )
        if change_type != "unchanged":
            if monitored:
                syntax_changes.append(entry)
            else:
                experimental_syntax_changes.append(entry)
        if not resolvable and kind in ("property", "descriptor", "prelude") and monitored:
            unresolved_targets.append(entry)

    # 1. Properties
    all_props = sorted(set(current_specs.PROPERTY_SYNTAXES) | set(properties))
    for pname in all_props:
        _inspect_target(
            pname,
            "property",
            current_specs.PROPERTY_SYNTAXES.get(pname),
            properties.get(pname),
            monitored=_is_prop_monitored(pname),
        )

    # 2. At-rule descriptors
    old_descs = {
        f"{aname}/{dname}": dsyn
        for aname, dmap in current_specs.ATRULE_DESCRIPTORS.items()
        for dname, dsyn in dmap.items()
    }
    new_descs = {
        f"{aname}/{dname}": dsyn
        for aname, ainfo in atrules.items()
        for dname, dsyn in ainfo["descriptors"].items()
    }
    for dkey in sorted(set(old_descs) | set(new_descs)):
        _inspect_target(dkey, "descriptor", old_descs.get(dkey), new_descs.get(dkey))

    # 3. Values (only canonical `<name>` keys to avoid duplicate non-bracketed entries)
    old_vals = {k: v for k, v in current_specs.VALUE_SYNTAXES.items() if k.startswith("<")}
    new_vals = {k: v for k, v in values.items() if k.startswith("<")}
    for vkey in sorted(set(old_vals) | set(new_vals)):
        _inspect_target(vkey, "value", old_vals.get(vkey), new_vals.get(vkey))

    # 4. At-rule preludes
    old_preludes = getattr(current_specs, "ATRULE_PRELUDE_SYNTAXES", {})
    new_preludes = {aname: ainfo.get("prelude") for aname, ainfo in atrules.items()}
    for aname in sorted(set(old_preludes) | set(new_preludes)):
        _inspect_target(aname, "prelude", old_preludes.get(aname), new_preludes.get(aname))

    gen_dir = REPO_ROOT / "src" / "stixis" / "_generated"
    specs_changed = (
        (gen_dir / "units.py").read_text(encoding="utf-8") != gen_specs.format_units_py(units_by_type)
        or (gen_dir / "specs.py").read_text(encoding="utf-8")
        != gen_specs.format_specs_py(
            properties,
            values,
            atrules,
            engine_counts=engine_counts,
            support_cfg=patches.get("support", {}),
        )
        or (gen_dir / "types.py").read_text(encoding="utf-8")
        != gen_specs.format_types_py(properties, values, atrules)
    )

    saved_counts = getattr(current_specs, "PROPERTY_ENGINE_COUNTS", {})
    try:
        current_specs.PROPERTY_ENGINE_COUNTS = engine_counts
        wpt_counts_new, wpt_failures, wpt_changed = _check_wpt_cases(
            wpt_dir, min_engines=min_engines
        )
    finally:
        current_specs.PROPERTY_ENGINE_COUNTS = saved_counts

    wpt_counts_old = {
        k: baseline["wpt"][k]
        for k in (
            "urange_cases",
            "font_family_cases",
            "font_face_cases",
            "css_fonts_cases",
            "css_properties_cases",
        )
        if k in baseline.get("wpt", {})
    }

    return UpstreamReport(
        webref_old_commit=baseline["webref"]["commit"],
        webref_new_commit=_detect_git_commit(webref_dir),
        wpt_old_commit=baseline["wpt"]["commit"],
        wpt_new_commit=_detect_git_commit(wpt_dir),
        bcd_old_commit=baseline.get("bcd", {}).get("commit", "unknown"),
        bcd_new_commit=bcd_new_commit,
        min_engines=min_engines,
        unit_changes=unit_changes,
        syntax_changes=syntax_changes,
        experimental_syntax_changes=experimental_syntax_changes,
        support_changes=support_changes,
        unresolved_targets=unresolved_targets,
        wpt_counts_old=wpt_counts_old,
        wpt_counts_new=wpt_counts_new,
        wpt_failures=wpt_failures,
        specs_files_changed=specs_changed,
        wpt_files_changed=wpt_changed,
    )


def format_markdown_issue(report: UpstreamReport) -> str:
    """Format an agent-ready GitHub Issue / triage report in Markdown."""
    status_badge = (
        "Action Required (Spec Patch / Kernel Extension Needed)"
        if report.needs_agent_intervention
        else (
            "Ready for Regeneration (`gen_specs.py` / `gen_wpt_tests.py`)"
            if report.has_changes
            else "Up to Date"
        )
    )

    lines = [
        "# Upstream Specification & WPT Sync Report",
        "",
        f"* **Status**: {status_badge}",
        f"* **Support Threshold**: `min_engines = {report.min_engines}` (pass `--min-engines 1` to include single-engine prototypes)",
        f"* **`w3c/webref` (`ed/css`)**: `{report.webref_old_commit[:12]}` -> `{report.webref_new_commit[:12]}`",
        f"* **`web-platform-tests/wpt` (`css`)**: `{report.wpt_old_commit[:12]}` -> `{report.wpt_new_commit[:12]}`",
        f"* **`mdn/browser-compat-data` (`css`)**: `{report.bcd_old_commit[:12]}` -> `{report.bcd_new_commit[:12]}`",
        "",
        "## What Changed Upstream?",
        "",
    ]

    if (
        not report.unit_changes
        and not report.syntax_changes
        and not report.support_changes
        and not report.wpt_files_changed
    ):
        lines.append("* No syntax, unit, engine-support, or WPT test vector changes detected for enabled targets (`min_engines >= " + str(report.min_engines) + "`).")
        if report.experimental_syntax_changes:
            lines.append(
                f"* ({len(report.experimental_syntax_changes)} experimental `< {report.min_engines}`-engine property change(s) ignored; run with `--min-engines 1` or `--min-engines 0` to inspect.)"
            )
        lines.append("")
    else:
        if report.support_changes:
            lines.append("### Browser Engine Support Changes (`@mdn/browser-compat-data`)")
            lines.append("")
            for sc_line in report.support_changes:
                lines.append(f"* {sc_line}")
            lines.append("")

        if report.unit_changes:
            lines.append("### Unit Table Changes (`src/stixis/_generated/units.py`)")
            lines.append("")
            for utype, diff in report.unit_changes.items():
                lines.append(
                    f"* `{utype}`: added `{diff['added']}`, removed `{diff['removed']}`"
                )
            lines.append("")

        if report.syntax_changes:
            lines.append("### CSS Grammar Changes (`@webref/css`)")
            lines.append("")
            for sc in report.syntax_changes:
                if sc.change_type == "added":
                    lines.append(
                        f"* **[ADDED]** `{sc.target}` ({sc.kind}): `{sc.new_syntax}` "
                        f"({'resolvable' if sc.resolvable else 'UNRESOLVED'})"
                    )
                elif sc.change_type == "modified":
                    lines.append(
                        f"* **[MODIFIED]** `{sc.target}` ({sc.kind}): `{sc.old_syntax}` -> `{sc.new_syntax}` "
                        f"({'resolvable' if sc.resolvable else 'UNRESOLVED'})"
                    )
                elif sc.change_type == "removed":
                    lines.append(
                        f"* **[REMOVED]** `{sc.target}` ({sc.kind}): was `{sc.old_syntax}`"
                    )
            lines.append("")

        lines.append("### WPT Vector Counts (`tests/_generated/`)")
        lines.append("")
        for key, new_val in report.wpt_counts_new.items():
            old_val = report.wpt_counts_old.get(key, new_val)
            delta = new_val - old_val
            delta_str = f" ({delta:+d})" if delta else ""
            lines.append(f"* `{key}`: {old_val} -> {new_val}{delta_str}")
        lines.append("")

    lines.extend(
        [
            "## What Needs Agent Implementation?",
            "",
        ]
    )

    if not report.needs_agent_intervention:
        lines.append(
            "* All properties, descriptors, and value definitions resolve cleanly, and all extracted WPT vectors pass."
        )
        lines.append("")
    else:
        if report.unresolved_targets:
            lines.append("### 1. Unresolved Non-Terminals or Grammar Constructs")
            lines.append("")
            lines.append(
                "Add missing prose non-terminals to `[values]` in `codegen/spec_patches.toml`, "
                "or extend `src/stixis/syntax.py` if a new Value Definition Syntax combinator was introduced:"
            )
            lines.append("")
            for ut in report.unresolved_targets:
                if ut.compile_error:
                    lines.append(
                        f"* `{ut.target}` (`{ut.new_syntax}`): compile error `{ut.compile_error}`"
                    )
                else:
                    refs = ", ".join(f"`{r}`" for r in ut.unresolved_refs)
                    lines.append(
                        f"* `{ut.target}` (`{ut.new_syntax}`): unresolved references {refs}"
                    )
            lines.append("")

        if report.wpt_failures:
            lines.append("### 2. Failing WPT Conformance Vectors")
            lines.append("")
            lines.append(
                "Inspect the target grammar with `uv run stixis inspect <target> --json` and reproduce with "
                "`uv run stixis check <target> <raw_css>`:"
            )
            lines.append("")
            for wf in report.wpt_failures[:50]:
                exp_str = f"expected `{wf.expected}`" if wf.expected is not None else "expected `StixisError`"
                lines.append(
                    f"* `[{wf.suite}]` `{wf.target}` input `{wf.raw_css}`: {exp_str}, {wf.actual_or_error}"
                )
            if len(report.wpt_failures) > 50:
                lines.append(f"* ... and {len(report.wpt_failures) - 50} more WPT failures.")
            lines.append("")

    lines.extend(
        [
            "## How Should an Agent Complete This Update?",
            "",
            "1. Read `AGENTS.md` and `.agents/docs/grammar-and-codegen.md`.",
            "2. If unresolved references are listed above, add declarative entries to `codegen/spec_patches.toml` (under `[values]`, `[properties]`, or `[atrule_descriptors]`) or update `src/stixis/syntax.py` / `src/stixis/microsyntax/`.",
            "3. Regenerate the specification tables, `TypedDict` definitions, WPT test suites, and baseline metadata:",
            "",
            "```bash",
            "uv run python codegen/gen_specs.py --webref-dir <path-to-webref-ed-css>",
            "uv run python codegen/gen_wpt_tests.py --wpt-dir <path-to-wpt-css>",
            "uv run python codegen/check_upstream.py --webref-dir <path-to-webref-ed-css> --wpt-dir <path-to-wpt-css> --update-baseline",
            "```",
            "",
            "4. Verify all checks pass before opening a pull request:",
            "",
            "```bash",
            "uv run python codegen/gen_specs.py --check --webref-dir <path-to-webref-ed-css>",
            "uv run python codegen/gen_wpt_tests.py --check --wpt-dir <path-to-wpt-css>",
            "uv run pytest",
            "uv run ruff check",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_upstream.py",
        description="Analyze upstream @webref/css and WPT changes and generate an agent-ready Issue report.",
    )
    parser.add_argument(
        "--webref-dir",
        type=Path,
        default=None,
        help="Path to @webref/css ed/css/ directory.",
    )
    parser.add_argument(
        "--wpt-dir",
        type=Path,
        default=gen_wpt_tests.DEFAULT_WPT_CSS_DIR,
        help="Path to WPT css/ directory.",
    )
    parser.add_argument(
        "--patches",
        type=Path,
        default=REPO_ROOT / "codegen" / "spec_patches.toml",
        help="Path to spec_patches.toml.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=REPO_ROOT / "codegen" / "upstream.json",
        help="Path to codegen/upstream.json baseline file.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown Issue body or json).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional file path to write the report to.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Update codegen/upstream.json with the current upstream commits and counts.",
    )
    parser.add_argument(
        "--fail-on-changes",
        action="store_true",
        help="Exit with code 3 when upstream changes or unresolved targets are detected.",
    )
    parser.add_argument(
        "--min-engines",
        type=int,
        default=None,
        help="Minimum browser engine count to monitor (0..3; defaults to support.default_min_engines in spec_patches.toml).",
    )
    parser.add_argument(
        "--bcd-dir",
        type=Path,
        default=None,
        help="Optional path to @mdn/browser-compat-data css/ directory.",
    )
    args = parser.parse_args(argv)

    if not args.patches.is_file():
        print(f"Missing patches file: {args.patches}", file=sys.stderr)
        return 1
    if not args.baseline.is_file():
        print(f"Missing baseline file: {args.baseline}", file=sys.stderr)
        return 1

    patches = tomllib.loads(args.patches.read_text(encoding="utf-8"))
    effective_min_engines = (
        args.min_engines
        if args.min_engines is not None
        else int(patches.get("support", {}).get("default_min_engines", 2))
    )
    if args.webref_dir is not None:
        webref_dir = args.webref_dir.expanduser()
    else:
        raw_webref = Path(patches["meta"]["webref_css_dir"]).expanduser()
        webref_dir = raw_webref if raw_webref.is_absolute() else (Path.home() / raw_webref)

    bcd_dir: Path | None = None
    if args.bcd_dir is not None:
        bcd_dir = args.bcd_dir.expanduser()
    elif "bcd_css_dir" in patches.get("meta", {}):
        raw_bcd = Path(patches["meta"]["bcd_css_dir"]).expanduser()
        candidate_bcd = raw_bcd if raw_bcd.is_absolute() else (Path.home() / raw_bcd)
        if candidate_bcd.is_dir():
            bcd_dir = candidate_bcd

    wpt_dir = args.wpt_dir.expanduser()
    if not webref_dir.is_dir():
        print(f"@webref/css directory not found: {webref_dir}", file=sys.stderr)
        return 1
    if not wpt_dir.is_dir():
        print(f"WPT directory not found: {wpt_dir}", file=sys.stderr)
        return 1

    report = analyze_upstream(
        webref_dir,
        wpt_dir,
        args.patches,
        args.baseline,
        min_engines=effective_min_engines,
        bcd_dir=bcd_dir,
    )

    if args.update_baseline:
        if report.needs_agent_intervention:
            print(
                "Cannot update baseline while unresolved targets or WPT failures remain.",
                file=sys.stderr,
            )
            return 1
        _units_by_type, properties, values, atrules = gen_specs.load_and_merge_specs(
            webref_dir, patches
        )
        total_descs = sum(len(ainfo["descriptors"]) for ainfo in atrules.values())
        new_baseline = {
            "webref": {
                "repository": "https://github.com/w3c/webref.git",
                "branch": "main",
                "commit": report.webref_new_commit,
                "subpath": "ed/css",
                "json_files": len(list(webref_dir.glob("*.json"))),
                "properties": len(properties),
                "descriptors": total_descs,
                "values": len(values),
            },
            "wpt": {
                "repository": "https://github.com/web-platform-tests/wpt.git",
                "branch": "master",
                "commit": report.wpt_new_commit,
                "subpath": "css",
                **report.wpt_counts_new,
            },
            "bcd": {
                "repository": "https://github.com/mdn/browser-compat-data.git",
                "branch": "main",
                "commit": report.bcd_new_commit,
                "subpath": "css",
            },
        }
        args.baseline.write_text(
            json.dumps(new_baseline, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report = analyze_upstream(
            webref_dir,
            wpt_dir,
            args.patches,
            args.baseline,
            min_engines=effective_min_engines,
            bcd_dir=bcd_dir,
        )

    if args.format == "json":
        payload = asdict(report)
        payload["has_changes"] = report.has_changes
        payload["needs_agent_intervention"] = report.needs_agent_intervention
        rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    else:
        rendered = format_markdown_issue(report)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    if args.fail_on_changes and report.has_changes:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
