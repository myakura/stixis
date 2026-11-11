#!/usr/bin/env python3
"""Generate `src/stixis/_generated/{units,specs,types}.py` from `@webref/css` and `codegen/spec_patches.toml`."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATCHES_PATH = REPO_ROOT / "codegen" / "spec_patches.toml"
UPSTREAM_WEBREF_CSS_DIR = REPO_ROOT / ".upstream" / "webref" / "ed" / "css"
UPSTREAM_BCD_CSS_DIR = REPO_ROOT / ".upstream" / "bcd" / "css"
sys.path.insert(0, str(REPO_ROOT / "src"))

from stixis._grammar.vds import (
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


def _spec_sort_key(path: Path, priority_order: list[str]) -> tuple[int, str, int, str]:
    fname = path.name
    if fname in priority_order:
        prio = priority_order.index(fname)
    else:
        prio = len(priority_order)
    stem = path.stem
    m = re.match(r"^(.*?)(?:-(\d+))?$", stem)
    assert m is not None
    base = m.group(1)
    level = int(m.group(2)) if m.group(2) else 0
    return (prio, base, level, fname)


def _extract_subvalue_keywords(subvalues: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for sv in subvalues:
        if sv.get("type") == "value":
            val = sv.get("value") or sv.get("name") or ""
            if val and " " not in val and val not in out:
                out.append(val)
    return out


def _collect_nested_types(
    items: list[dict[str, Any]],
    values_map: dict[str, str],
    value_owners: dict[str, str] | None = None,
    spec_name: str = "",
    superseded_by: dict[str, set[str]] | None = None,
) -> None:
    def _can_set(key: str) -> bool:
        if value_owners is None or superseded_by is None:
            return True
        cur = value_owners.get(key)
        return cur is None or cur not in superseded_by.get(spec_name, ())

    for sv in items:
        vname = sv.get("name", "")
        vtype = sv.get("type", "")
        if vtype in ("type", "function") and vname:
            if sv.get("value"):
                if _can_set(vname):
                    values_map[vname] = sv["value"]
                    if value_owners is not None:
                        value_owners[vname] = spec_name
                if not vname.startswith("<"):
                    ang = f"<{vname}>"
                    if _can_set(ang):
                        values_map[ang] = sv["value"]
                        if value_owners is not None:
                            value_owners[ang] = spec_name
            elif vname.startswith("<") and vname not in values_map and sv.get("values"):
                kws = _extract_subvalue_keywords(sv["values"])
                if kws:
                    values_map[vname] = " | ".join(kws)
                    if value_owners is not None:
                        value_owners[vname] = spec_name
        if "values" in sv:
            _collect_nested_types(
                sv["values"],
                values_map,
                value_owners=value_owners,
                spec_name=spec_name,
                superseded_by=superseded_by,
            )


def _is_shipping_unprefixed_statement(stmt: Any) -> bool:
    """Return True if a BCD support statement describes a shipping, unprefixed, flagless release.

    `partial_implementation: true` is intentionally counted as shipping because BCD uses it for
    layout or rendering edge cases (such as `overflow-clip-margin` in Blink or `overscroll-behavior`
    in WebKit) even when the unprefixed CSS property is shipped and parsed in CSSOM.
    """
    if not isinstance(stmt, dict):
        return False
    ver = stmt.get("version_added")
    if not ver or ver == "preview":
        return False
    if stmt.get("version_removed"):
        return False
    if stmt.get("flags"):
        return False
    return not bool(stmt.get("prefix") or stmt.get("alternative_name"))


def load_bcd_engine_counts(
    bcd_css_dir: Path | None,
    properties: dict[str, str],
) -> dict[str, int]:
    """Compute how many core browser engines (Blink, Gecko, WebKit; 0..3) ship each CSS property."""
    counts: dict[str, int] = {p: 0 for p in properties}
    if bcd_css_dir is not None and (bcd_css_dir / "properties").is_dir():
        for jf in sorted((bcd_css_dir / "properties").glob("*.json")):
            pname = jf.stem
            data = json.loads(jf.read_text(encoding="utf-8"))
            compat = data.get("css", {}).get("properties", {}).get(pname, {}).get("__compat", {})
            sup = compat.get("support", {})
            eng = 0
            for browser in ("chrome", "firefox", "safari"):
                b_sup = sup.get(browser)
                stmts = b_sup if isinstance(b_sup, list) else ([b_sup] if b_sup else [])
                if any(_is_shipping_unprefixed_statement(s) for s in stmts):
                    eng += 1
            if pname in counts:
                counts[pname] = eng

    # Propagate target engine counts to non-prefixed `legacyAliasOf` properties (`word-wrap`, `grid-gap`, `font-stretch`)
    for pname, syn in properties.items():
        if not pname.startswith("-") and syn.startswith("<'") and syn.endswith("'>"):
            target = syn[2:-2]
            if target in counts and counts[target] > counts.get(pname, 0):
                counts[pname] = counts[target]

    return counts


def _merge_new_values(existing: str, new_values: str) -> str:
    """Append top-level `|` branches from `new_values` not already present in `existing`."""
    existing_parts = [p.strip() for p in re.split(r"(?<!\|)\|(?!\|)", existing) if p.strip()]
    for nv in [p.strip() for p in re.split(r"(?<!\|)\|(?!\|)", new_values) if p.strip()]:
        if nv not in existing_parts:
            existing_parts.append(nv)
    return " | ".join(existing_parts)


def load_and_merge_specs(
    webref_dir: Path,
    patches: dict[str, Any],
) -> tuple[
    dict[str, set[str]],
    dict[str, str],
    dict[str, str],
    dict[str, dict[str, Any]],
    dict[str, set[str]],
]:
    if not webref_dir.is_dir():
        raise FileNotFoundError(f"@webref/css directory not found: {webref_dir}")

    excluded_specs: set[str] = set(patches.get("support", {}).get("exclude_specs", []))
    json_files = [p for p in sorted(webref_dir.glob("*.json")) if p.name not in excluded_specs]
    if not json_files:
        raise FileNotFoundError(f"No JSON files found in {webref_dir}")

    delta_cfg = patches.get("delta_merges", {})
    priority_order: list[str] = delta_cfg.get("order", [])
    supersedes_cfg: dict[str, list[str]] = delta_cfg.get("supersedes", {})
    superseded_by: dict[str, set[str]] = {}
    for auth_spec, old_specs in supersedes_cfg.items():
        for old_spec in old_specs:
            superseded_by.setdefault(old_spec, set()).add(auth_spec)

    def _can_overwrite(current_owner: str | None, new_owner: str) -> bool:
        return current_owner is None or current_owner not in superseded_by.get(new_owner, ())

    json_files.sort(key=lambda p: _spec_sort_key(p, priority_order))

    units_by_type: dict[str, set[str]] = {
        "<length>": set(),
        "<angle>": set(),
        "<time>": set(),
        "<frequency>": set(),
        "<resolution>": set(),
        "<flex>": set(),
        "<decibel>": set(),
        "<semitones>": set(),
    }

    for unit_type, extra_list in patches.get("extra_units", {}).items():
        units_by_type.setdefault(unit_type, set()).update(extra_list)

    properties: dict[str, str] = {}
    property_owners: dict[str, str] = {}
    property_new_values: dict[str, list[tuple[str, str]]] = {}
    values: dict[str, str] = {}
    value_owners: dict[str, str] = {}
    value_new_values: dict[str, list[tuple[str, str]]] = {}
    atrules: dict[str, dict[str, Any]] = {}
    atrule_syntax_owners: dict[str, str] = {}
    descriptor_owners: dict[tuple[str, str], str] = {}
    relative_color_channels: dict[str, set[str]] = {}
    channel_owners: dict[str, str] = {}

    for jf in json_files:
        spec_name = jf.name
        data = json.loads(jf.read_text(encoding="utf-8"))

        # 1. Values & units
        for v in data.get("values", []):
            vname = v["name"]
            if vname in units_by_type:
                for sv in v.get("values", []):
                    uval = sv.get("value", "")
                    if uval and " " not in uval and not uval.startswith("<"):
                        units_by_type[vname].add(uval)

            if (
                v.get("type") == "function"
                and vname.endswith("()")
                and "from <color>" in (v.get("value") or "")
            ):
                fn_name = vname[:-2].lower()
                ch_kws = [
                    kw.lower()
                    for kw in _extract_subvalue_keywords(v.get("values", []))
                    if kw.isascii() and kw.isalpha()
                ]
                if ch_kws and _can_overwrite(channel_owners.get(fn_name), spec_name):
                    relative_color_channels[fn_name] = set(ch_kws)
                    channel_owners[fn_name] = spec_name

            if v.get("value"):
                if _can_overwrite(value_owners.get(vname), spec_name):
                    values[vname] = v["value"]
                    value_owners[vname] = spec_name
                if not vname.startswith("<"):
                    ang_name = f"<{vname}>"
                    if _can_overwrite(value_owners.get(ang_name), spec_name):
                        values[ang_name] = v["value"]
                        value_owners[ang_name] = spec_name
            elif v.get("newValues"):
                value_new_values.setdefault(vname, []).append((spec_name, v["newValues"]))
            elif vname.startswith("<") and vname not in values and v.get("values"):
                kws = _extract_subvalue_keywords(v["values"])
                if kws:
                    values[vname] = " | ".join(kws)
                    value_owners[vname] = spec_name

            _collect_nested_types(
                v.get("values", []),
                values,
                value_owners=value_owners,
                spec_name=spec_name,
                superseded_by=superseded_by,
            )

        # 2. Properties
        for p in data.get("properties", []):
            pname = p["name"]
            if p.get("value"):
                if _can_overwrite(property_owners.get(pname), spec_name):
                    properties[pname] = p["value"]
                    property_owners[pname] = spec_name
            elif p.get("newValues"):
                property_new_values.setdefault(pname, []).append((spec_name, p["newValues"]))
            elif p.get("legacyAliasOf") and _can_overwrite(property_owners.get(pname), spec_name):
                alias_target = p["legacyAliasOf"]
                properties[pname] = f"<'{alias_target}'>"
                property_owners[pname] = spec_name

            _collect_nested_types(
                p.get("values", []),
                values,
                value_owners=value_owners,
                spec_name=spec_name,
                superseded_by=superseded_by,
            )

        # 3. At-rules & descriptors
        for a in data.get("atrules", []):
            aname = a["name"]
            entry = atrules.setdefault(
                aname,
                {
                    "syntax": None,
                    "prelude": None,
                    "descriptors": {},
                },
            )
            if a.get("value") and _can_overwrite(atrule_syntax_owners.get(aname), spec_name):
                entry["syntax"] = a["value"]
                atrule_syntax_owners[aname] = spec_name
            for d in a.get("descriptors", []):
                _collect_nested_types(
                    d.get("values", []),
                    values,
                    value_owners=value_owners,
                    spec_name=spec_name,
                    superseded_by=superseded_by,
                )
                if d.get("type") in ("at-rule", "discrete", "range"):
                    continue
                dname = d["name"]
                dkey = (aname, dname)
                if d.get("value"):
                    if _can_overwrite(descriptor_owners.get(dkey), spec_name):
                        entry["descriptors"][dname] = d["value"]
                        descriptor_owners[dkey] = spec_name
                elif (
                    d.get("newValues")
                    and dname in entry["descriptors"]
                    and _can_overwrite(descriptor_owners.get(dkey), spec_name)
                ):
                    entry["descriptors"][dname] += " | " + d["newValues"]
            _collect_nested_types(
                a.get("values", []),
                values,
                value_owners=value_owners,
                spec_name=spec_name,
                superseded_by=superseded_by,
            )

    for vname, nv_list in value_new_values.items():
        for nv_owner, nv in nv_list:
            if not _can_overwrite(value_owners.get(vname), nv_owner):
                continue
            values[vname] = _merge_new_values(values.get(vname, ""), nv)
            if not vname.startswith("<"):
                values[f"<{vname}>"] = values[vname]

    for pname, nv_list in property_new_values.items():
        for nv_owner, nv in nv_list:
            if not _can_overwrite(property_owners.get(pname), nv_owner):
                continue
            properties[pname] = _merge_new_values(properties.get(pname, ""), nv)

    # Apply declarative patches from `spec_patches.toml`
    for vname, vsyn in patches.get("values", {}).items():
        values[vname] = vsyn
        if not vname.startswith("<"):
            values[f"<{vname}>"] = vsyn

    for pname, psyn in patches.get("properties", {}).items():
        properties[pname] = psyn

    for aname, prelude_syn in patches.get("atrule_preludes", {}).items():
        entry = atrules.setdefault(
            aname,
            {"syntax": None, "prelude": None, "descriptors": {}},
        )
        entry["prelude"] = prelude_syn

    for aname, desc_map in patches.get("atrule_descriptors", {}).items():
        entry = atrules.setdefault(
            aname,
            {"syntax": None, "prelude": None, "descriptors": {}},
        )
        for dname, dsyn in desc_map.items():
            entry["descriptors"][dname] = dsyn

    for fn_name, ch_list in patches.get("relative_color_channels", {}).items():
        clean_fn = fn_name[:-2].lower() if fn_name.endswith("()") else fn_name.lower()
        relative_color_channels[clean_fn] = {str(ch).lower() for ch in ch_list}

    return units_by_type, properties, values, atrules, relative_color_channels


def is_node_resolvable(
    node: ValueNode,
    properties: dict[str, str],
    values: dict[str, str],
    visited: set[tuple[str, bool]] | None = None,
) -> bool:
    """Return True if all `<type>` and `<'prop'>` references in `node` can be resolved to built-in primitives."""
    if visited is None:
        visited = set()

    if isinstance(node, (KeywordNode, DelimNode, PrimitiveNode)):
        return True
    if isinstance(node, FunctionNode):
        return node.arg is None or is_node_resolvable(node.arg, properties, values, visited)
    if isinstance(node, (MultiplierNode, NonEmptyGroupNode)):
        return is_node_resolvable(node.child, properties, values, visited)
    if isinstance(node, (JuxtaposeNode, AllOfNode, AnyOrderNode, ExclusiveOrNode)):
        return all(is_node_resolvable(c, properties, values, visited) for c in node.items)
    if isinstance(node, RefNode):
        key = (node.name, node.is_property)
        if key in visited:
            return True
        visited.add(key)
        if node.is_property:
            syn = properties.get(node.name)
            if not syn:
                return False
            return is_node_resolvable(compile_value_syntax(syn), properties, values, visited)
        if node.name in _BUILTIN_PRIMITIVES:
            return True
        syn = values.get(node.name) or values.get(node.name.strip("<>"))
        if not syn:
            return False
        return is_node_resolvable(compile_value_syntax(syn), properties, values, visited)
    return False


def extract_top_level_keywords(
    node: ValueNode,
    properties: dict[str, str],
    values: dict[str, str],
    visited: set[tuple[str, bool]] | None = None,
) -> list[str]:
    """Extract top-level literal keywords for generating `Literal[...]` type annotations."""
    if visited is None:
        visited = set()
    out: list[str] = []

    if isinstance(node, KeywordNode):
        if node.name and re.match(r"^[a-zA-Z0-9_-]+$", node.name):
            out.append(node.name.lower())
    elif isinstance(node, (ExclusiveOrNode, AnyOrderNode, AllOfNode)):
        for b in node.items:
            for kw in extract_top_level_keywords(b, properties, values, visited):
                if kw not in out:
                    out.append(kw)
    elif isinstance(node, MultiplierNode):
        for kw in extract_top_level_keywords(node.child, properties, values, visited):
            if kw not in out:
                out.append(kw)
    elif isinstance(node, RefNode):
        key = (node.name, node.is_property)
        if key not in visited:
            visited.add(key)
            syn = (
                properties.get(node.name)
                if node.is_property
                else (values.get(node.name) or values.get(node.name.strip("<>")))
            )
            if syn:
                for kw in extract_top_level_keywords(
                    compile_value_syntax(syn), properties, values, visited
                ):
                    if kw not in out:
                        out.append(kw)
    return out


def infer_python_type_expr(
    syntax: str,
    properties: dict[str, str],
    values: dict[str, str],
) -> str:
    """Derive a descriptive Python type annotation string from a Value Definition Syntax."""
    node = compile_value_syntax(syntax)
    kws = extract_top_level_keywords(node, properties, values)
    types: list[str] = []
    if kws:
        lit_items = ", ".join(repr(k) for k in kws[:24])
        types.append(f"Literal[{lit_items}]")
    types.extend(["str", "Template", "Keyword"])

    if any(
        tok in syntax
        for tok in ("<number", "<integer", "<length", "<angle", "<percentage", "weight")
    ):
        types.extend(["int", "float", "Dimension", "Percentage"])
    if any(
        tok in syntax for tok in ("{1,2}", "#", "||", "&&", "override", "src", "family", "settings")
    ):
        types.append("Sequence[Any]")
    if "urange" in syntax or "unicode-range" in syntax:
        types.extend(["set[int]", "frozenset[int]", "tuple[int, int]"])
    if "settings" in syntax or "src" in syntax:
        types.append("Mapping[str, Any]")

    # Deduplicate while preserving order
    unique_types: list[str] = []
    for t in types:
        if t not in unique_types:
            unique_types.append(t)
    return " | ".join(unique_types)


def format_units_py(units_by_type: dict[str, set[str]]) -> str:
    lines = [
        '"""Auto-generated CSS unit tables extracted from `@webref/css`."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    const_names = {
        "<length>": "LENGTH_UNITS",
        "<angle>": "ANGLE_UNITS",
        "<time>": "TIME_UNITS",
        "<frequency>": "FREQUENCY_UNITS",
        "<resolution>": "RESOLUTION_UNITS",
        "<flex>": "FLEX_UNITS",
        "<decibel>": "DECIBEL_UNITS",
        "<semitones>": "SEMITONES_UNITS",
    }
    for type_key, const_name in const_names.items():
        sorted_units = sorted(units_by_type.get(type_key, set()))
        items_str = ", ".join(repr(u) for u in sorted_units)
        lines.append(f"{const_name}: frozenset[str] = frozenset({{{items_str}}})")

    lines.extend(
        [
            "",
            "UNITS_BY_TYPE: dict[str, frozenset[str]] = {",
        ]
    )
    for type_key, const_name in const_names.items():
        lines.append(f"    {type_key!r}: {const_name},")
    lines.extend(
        [
            "}",
            "",
            "LOWER_TO_CANONICAL_UNIT: dict[str, dict[str, str]] = {",
            "    type_name: {u.lower(): u for u in units}",
            "    for type_name, units in UNITS_BY_TYPE.items()",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def format_specs_py(
    properties: dict[str, str],
    values: dict[str, str],
    atrules: dict[str, dict[str, Any]],
    engine_counts: dict[str, int] | None = None,
    support_cfg: dict[str, Any] | None = None,
    relative_color_channels: dict[str, set[str]] | None = None,
) -> str:
    if engine_counts is None:
        from stixis._generated import specs as existing_specs

        engine_counts = dict(getattr(existing_specs, "PROPERTY_ENGINE_COUNTS", {}))
    if support_cfg is None:
        support_cfg = {}
    if relative_color_channels is None:
        from stixis._generated import specs as existing_specs

        relative_color_channels = {
            k: set(v)
            for k, v in getattr(existing_specs, "RELATIVE_COLOR_CHANNEL_KEYWORDS", {}).items()
        }

    default_min_engines = int(support_cfg.get("default_min_engines", 2))
    allow_props = sorted(set(support_cfg.get("allow_properties", [])))

    lines = [
        '"""Auto-generated W3C `@webref/css` property, at-rule, descriptor, and value syntax tables."""',
        "",
        "from __future__ import annotations",
        "",
        f"DEFAULT_MIN_ENGINES: int = {default_min_engines}",
        f"ALWAYS_ALLOWED_PROPERTIES: frozenset[str] = frozenset({{{', '.join(repr(p) for p in allow_props)}}})",
        "",
        "PROPERTY_ENGINE_COUNTS: dict[str, int] = {",
    ]
    for k in sorted(properties):
        lines.append(f"    {k!r}: {engine_counts.get(k, 0)},")
    lines.extend(
        [
            "}",
            "",
            "RELATIVE_COLOR_CHANNEL_KEYWORDS: dict[str, frozenset[str]] = {",
        ]
    )
    for k in sorted(relative_color_channels):
        sorted_chs = sorted(relative_color_channels[k])
        chs_str = ", ".join(repr(ch) for ch in sorted_chs)
        lines.append(f"    {k!r}: frozenset({{{chs_str}}}),")
    lines.extend(
        [
            "}",
            "",
            "VALUE_SYNTAXES: dict[str, str] = {",
        ]
    )
    for k in sorted(values):
        lines.append(f"    {k!r}: {values[k]!r},")
    lines.extend(
        [
            "}",
            "",
            "PROPERTY_SYNTAXES: dict[str, str] = {",
        ]
    )
    for k in sorted(properties):
        lines.append(f"    {k!r}: {properties[k]!r},")
    lines.extend(
        [
            "}",
            "",
            "ATRULE_PRELUDE_SYNTAXES: dict[str, str | None] = {",
        ]
    )
    for k in sorted(atrules):
        lines.append(f"    {k!r}: {atrules[k]['prelude']!r},")
    lines.extend(
        [
            "}",
            "",
            "ATRULE_DESCRIPTORS: dict[str, dict[str, str]] = {",
        ]
    )
    for k in sorted(atrules):
        descs = atrules[k]["descriptors"]
        lines.append(f"    {k!r}: {{")
        for dk in sorted(descs):
            lines.append(f"        {dk!r}: {descs[dk]!r},")
        lines.append("    },")
    lines.extend(["}", ""])
    return "\n".join(lines)


def format_types_py(
    properties: dict[str, str],
    values: dict[str, str],
    atrules: dict[str, dict[str, Any]],
) -> str:
    lines = [
        '"""Auto-generated `TypedDict` and `Literal` type definitions for `@font-face` and CSS properties."""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Mapping, Sequence",
        "from string.templatelib import Template",
        "from typing import Any, Literal, TypedDict",
        "",
        "from stixis.values import Dimension, Keyword, Percentage",
        "",
    ]

    # Generate FontDisplayLiteral, FontStyleLiteral, FontWeightLiteral, FontWidthLiteral, FontTechLiteral, FontFormatLiteral
    font_face_descs = atrules.get("@font-face", {}).get("descriptors", {})

    for alias_name, target_syn in [
        (
            "FontDisplayKeyword",
            font_face_descs.get("font-display", "auto | block | swap | fallback | optional"),
        ),
        ("FontWidthKeyword", properties.get("font-width", "normal")),
        ("FontFormatKeyword", values.get("<font-format>", "woff2")),
        ("FontTechKeyword", values.get("<font-tech>", "variations")),
    ]:
        kws = extract_top_level_keywords(compile_value_syntax(target_syn), properties, values)
        if kws:
            lit = ", ".join(repr(k) for k in kws)
            lines.append(f"type {alias_name} = Literal[{lit}]")

    lines.extend(
        [
            "",
            "_FontFaceDescriptorsHyphen = TypedDict(",
            '    "_FontFaceDescriptorsHyphen",',
            "    {",
        ]
    )

    # Support both hyphenated (`font-family`) and snake_case (`font_family`) keys in FontFaceDescriptors
    all_ff_keys: list[tuple[str, str]] = []
    for dname in sorted(font_face_descs):
        dsyn = font_face_descs[dname]
        py_t = infer_python_type_expr(dsyn, properties, values)
        all_ff_keys.append((dname, f"{py_t} | None"))
        snake = dname.replace("-", "_")
        if snake != dname:
            all_ff_keys.append((snake, f"{py_t} | None"))

    for key_name, type_expr in all_ff_keys:
        lines.append(f"        {key_name!r}: {type_expr},")

    lines.extend(
        [
            "    },",
            "    total=False,",
            ")",
            "",
            "",
            "class FontFaceDescriptors(_FontFaceDescriptorsHyphen, total=False):",
            '    """TypedDict of `@font-face` descriptors (accepts both `snake_case` and `kebab-case` keys)."""',
            "",
        ]
    )
    for dname in sorted(font_face_descs):
        snake = dname.replace("-", "_")
        if snake.isidentifier():
            dsyn = font_face_descs[dname]
            py_t = infer_python_type_expr(dsyn, properties, values)
            lines.append(f"    {snake}: {py_t} | None")

    lines.append("")
    return "\n".join(lines)


def _write_or_check(path: Path, content: str, check: bool) -> bool:
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return True
    if check:
        print(f"Out of date generated file: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def resolve_default_webref_dir(patches: dict[str, Any], *, explicit_patches: bool) -> Path:
    if not explicit_patches and UPSTREAM_WEBREF_CSS_DIR.is_dir():
        return UPSTREAM_WEBREF_CSS_DIR
    raw_webref = Path(patches["meta"]["webref_css_dir"]).expanduser()
    return raw_webref if raw_webref.is_absolute() else (Path.home() / raw_webref)


def resolve_default_bcd_dir(patches: dict[str, Any], *, explicit_patches: bool) -> Path | None:
    if not explicit_patches and (UPSTREAM_BCD_CSS_DIR / "properties").is_dir():
        return UPSTREAM_BCD_CSS_DIR
    if "bcd_css_dir" in patches.get("meta", {}):
        raw_bcd = Path(patches["meta"]["bcd_css_dir"]).expanduser()
        candidate_bcd = raw_bcd if raw_bcd.is_absolute() else (Path.home() / raw_bcd)
        if (candidate_bcd / "properties").is_dir():
            return candidate_bcd
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_specs.py",
        description="Generate Stixis CSS specification tables and TypedDicts from @webref/css.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that generated files in src/stixis/_generated/ are up to date without writing.",
    )
    parser.add_argument(
        "--patches",
        default=None,
        help="Path to spec_patches.toml.",
    )
    parser.add_argument(
        "--webref-dir",
        type=Path,
        default=None,
        help="Path to @webref/css ed/css/ directory (overrides spec_patches.toml).",
    )
    parser.add_argument(
        "--bcd-dir",
        type=Path,
        default=None,
        help="Path to @mdn/browser-compat-data css/ directory (overrides spec_patches.toml).",
    )
    args = parser.parse_args(argv)

    explicit_patches = args.patches is not None
    patches_path = Path(args.patches) if explicit_patches else DEFAULT_PATCHES_PATH
    if not patches_path.is_file():
        print(f"{patches_path}:1: missing spec_patches.toml", file=sys.stderr)
        return 4

    patches = tomllib.loads(patches_path.read_text(encoding="utf-8"))
    if args.webref_dir is not None:
        webref_dir = args.webref_dir.expanduser()
    else:
        webref_dir = resolve_default_webref_dir(patches, explicit_patches=explicit_patches)
    if not webref_dir.is_dir():
        print(f"{patches_path}:1: missing webref_css_dir {webref_dir}", file=sys.stderr)
        return 4

    bcd_dir: Path | None = None
    if args.bcd_dir is not None:
        cand = args.bcd_dir.expanduser()
        if not (cand / "properties").is_dir():
            print(f"{patches_path}:1: missing bcd_css_dir {cand}", file=sys.stderr)
            return 4
        bcd_dir = cand
    else:
        bcd_dir = resolve_default_bcd_dir(patches, explicit_patches=explicit_patches)

    if bcd_dir is None and args.check:
        missing_desc: Path | str = "bcd_css_dir"
        if "bcd_css_dir" in patches.get("meta", {}):
            raw_bcd = Path(patches["meta"]["bcd_css_dir"]).expanduser()
            missing_desc = raw_bcd if raw_bcd.is_absolute() else (Path.home() / raw_bcd)
        print(
            f"{patches_path}:1: missing bcd_css_dir {missing_desc} (required with --check)",
            file=sys.stderr,
        )
        return 4

    (
        units_by_type,
        properties,
        values,
        atrules,
        relative_color_channels,
    ) = load_and_merge_specs(webref_dir, patches)
    if bcd_dir is not None:
        engine_counts = load_bcd_engine_counts(bcd_dir, properties)
    else:
        print(
            "Warning: BCD directory not found; reusing existing PROPERTY_ENGINE_COUNTS from src/stixis/_generated/specs.py",
            file=sys.stderr,
        )
        from stixis._generated import specs as existing_specs

        existing_counts = dict(getattr(existing_specs, "PROPERTY_ENGINE_COUNTS", {}))
        engine_counts = {p: existing_counts.get(p, 0) for p in properties}

    # Measure resolvability coverage across all properties and descriptors
    resolvable_props = sum(
        1
        for p, syn in properties.items()
        if is_node_resolvable(compile_value_syntax(syn), properties, values)
    )
    total_props = len(properties)

    all_descs = [
        (aname, dname, dsyn)
        for aname, ainfo in atrules.items()
        for dname, dsyn in ainfo["descriptors"].items()
    ]
    resolvable_descs = sum(
        1
        for _, _, dsyn in all_descs
        if is_node_resolvable(compile_value_syntax(dsyn), properties, values)
    )
    total_descs = len(all_descs)

    prop_pct = (resolvable_props / total_props * 100.0) if total_props else 0.0
    desc_pct = (resolvable_descs / total_descs * 100.0) if total_descs else 0.0
    if prop_pct < 95.0 or desc_pct < 95.0:
        print(
            f"Coverage below 95% threshold: properties={prop_pct:.1f}%, descriptors={desc_pct:.1f}%",
            file=sys.stderr,
        )
        return 1

    gen_dir = REPO_ROOT / "src" / "stixis" / "_generated"
    ok = True
    ok &= _write_or_check(gen_dir / "units.py", format_units_py(units_by_type), args.check)
    ok &= _write_or_check(
        gen_dir / "specs.py",
        format_specs_py(
            properties,
            values,
            atrules,
            engine_counts=engine_counts,
            support_cfg=patches.get("support", {}),
            relative_color_channels=relative_color_channels,
        ),
        args.check,
    )
    ok &= _write_or_check(
        gen_dir / "types.py", format_types_py(properties, values, atrules), args.check
    )

    if not ok:
        return 1

    status_verb = "Verified" if args.check else "Generated"
    print(
        f"{status_verb} specs: {resolvable_props}/{total_props} properties ({prop_pct:.1f}%), "
        f"{resolvable_descs}/{total_descs} descriptors ({desc_pct:.1f}%), "
        f"{len(values)} value definitions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
