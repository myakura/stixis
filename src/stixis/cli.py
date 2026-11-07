"""Read-only agent-first CLI (`stixis inspect` and `stixis check`)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, NoReturn

from stixis._generated.specs import (
    ALWAYS_ALLOWED_PROPERTIES,
    ATRULE_DESCRIPTORS,
    ATRULE_PRELUDE_SYNTAXES,
    DEFAULT_MIN_ENGINES,
    PROPERTY_ENGINE_COUNTS,
    PROPERTY_SYNTAXES,
)
from stixis._grammar.targets import normalize_identifier_key, resolve_target_syntax
from stixis.api import canonicalize
from stixis.errors import StixisError
from stixis.options import SerializationOptions

EXIT_OK = 0
EXIT_VALIDATION_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_NOT_FOUND = 4


class _StixisArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that emits a single-line diagnostic on `stderr` and exits with code 2."""

    def error(self, message: str) -> NoReturn:
        sys.stderr.write(f"error: {message}\n")
        raise SystemExit(EXIT_USAGE_ERROR)


def _describe_python_inputs(target_kind: str, name: str, syntax: str) -> list[str]:
    """Return concise descriptions of accepted Python input types for `name`."""
    hints: list[str] = ["str", "Template (PEP 750 t'...')"]
    if name in ("unicode-range", "<urange>"):
        hints.append("Sequence[int | tuple[int, int] | str] (code points or (start, end) ranges)")
    elif name in ("font-weight", "<font-weight-absolute>"):
        hints.append("int | float | tuple[int | float, int | float] (1..1000 or range)")
    elif name in ("font-width", "font-stretch", "<font-width-css3>"):
        hints.append("Percentage | tuple[Percentage | str, Percentage | str] (range)")
    elif name == "font-style":
        hints.append(
            "tuple[str, str | Dimension] | tuple[str, Dimension, Dimension] ('oblique' range)"
        )
    elif name in ("ascent-override", "descent-override", "line-gap-override"):
        hints.append("Percentage | tuple[Percentage | str, Percentage | str] (1..2 items)")
    elif name in ("font-feature-settings", "<feature-tag-value>"):
        hints.append("Mapping[str, int | bool] | Sequence[tuple[str, int | bool]]")
    elif name == "font-variation-settings":
        hints.append("Mapping[str, int | float] | Sequence[tuple[str, int | float]]")
    elif name in ("src", "<font-src-list>", "<font-src>"):
        hints.append("FontSrc | Url | LocalFont | Sequence[FontSrc | Url | LocalFont | str]")
    elif name in ("font-family", "<family-name>"):
        hints.append(
            "str (always quoted as <family-name>) | GenericFamily | Keyword | Sequence[...]"
        )
    elif (
        "<number" in syntax
        or "<integer" in syntax
        or "<length" in syntax
        or "<percentage" in syntax
    ):
        hints.append("int | float | Dimension | Percentage")
    if "#" in syntax or "+" in syntax:
        hints.append("list[...] | tuple[...]")
    return hints


def _inspect_target(raw_target: str, *, at_rule: str | None = None) -> dict[str, Any] | None:
    target = raw_target.strip()
    if not target:
        return None

    if at_rule is None and target.startswith("@") and "/" not in target:
        norm_at = target.lower()
        if norm_at not in ATRULE_DESCRIPTORS and norm_at not in ATRULE_PRELUDE_SYNTAXES:
            return None
        descs = ATRULE_DESCRIPTORS.get(norm_at, {})
        prelude = ATRULE_PRELUDE_SYNTAXES.get(norm_at)
        return {
            "kind": "at-rule",
            "name": norm_at,
            "prelude_syntax": prelude,
            "descriptors": dict(sorted(descs.items())),
        }

    try:
        norm_name, norm_at, syn, _ = resolve_target_syntax(
            target,
            at_rule=at_rule,
            options=SerializationOptions(min_engines=0),
        )
    except StixisError:
        if at_rule is None and not target.startswith(("<", "@")):
            try:
                norm = normalize_identifier_key(target)
            except StixisError:
                return None
            matching_at = [
                (at_name, descs[norm])
                for at_name, descs in sorted(ATRULE_DESCRIPTORS.items())
                if norm in descs
            ]
            if matching_at:
                at_name, desc_syn = matching_at[0]
                return {
                    "kind": "descriptor",
                    "name": norm,
                    "at_rule": at_name,
                    "all_at_rules": [a for a, _ in matching_at],
                    "python_key": norm.replace("-", "_"),
                    "syntax": desc_syn,
                    "python_inputs": _describe_python_inputs("descriptor", norm, desc_syn),
                }
        return None

    if norm_at is not None:
        return {
            "kind": "descriptor",
            "name": norm_name,
            "at_rule": norm_at,
            "python_key": norm_name.replace("-", "_"),
            "syntax": syn,
            "python_inputs": _describe_python_inputs("descriptor", norm_name, syn),
        }

    if norm_name.startswith("<") and norm_name.endswith(">"):
        # If bare identifier matched both a descriptor-only name and a `<type>`, check if `target` was bare
        if not target.startswith("<") and norm_name[1:-1] not in PROPERTY_SYNTAXES:
            bare_norm = norm_name[1:-1]
            matching_at = [
                (at_name, descs[bare_norm])
                for at_name, descs in sorted(ATRULE_DESCRIPTORS.items())
                if bare_norm in descs
            ]
            if matching_at:
                at_name, desc_syn = matching_at[0]
                return {
                    "kind": "descriptor",
                    "name": bare_norm,
                    "at_rule": at_name,
                    "all_at_rules": [a for a, _ in matching_at],
                    "python_key": bare_norm.replace("-", "_"),
                    "syntax": desc_syn,
                    "python_inputs": _describe_python_inputs("descriptor", bare_norm, desc_syn),
                }
        return {
            "kind": "type",
            "name": norm_name,
            "syntax": syn,
            "python_inputs": _describe_python_inputs("type", norm_name, syn),
        }

    if norm_name in PROPERTY_SYNTAXES:
        eng = PROPERTY_ENGINE_COUNTS.get(norm_name, 0)
        also_in_at_rules = sorted(
            at_name for at_name, descs in ATRULE_DESCRIPTORS.items() if norm_name in descs
        )
        return {
            "kind": "property",
            "name": norm_name,
            "python_key": norm_name.replace("-", "_"),
            "syntax": syn,
            "engines": eng,
            "enabled_by_default": eng >= DEFAULT_MIN_ENGINES
            or norm_name in ALWAYS_ALLOWED_PROPERTIES,
            "also_descriptor_in": also_in_at_rules,
            "python_inputs": _describe_python_inputs("property", norm_name, syn),
        }

    return None


def _run_inspect(args: argparse.Namespace) -> int:
    info = _inspect_target(args.target, at_rule=args.at_rule)
    if info is None:
        ctx = f" (in {args.at_rule})" if args.at_rule else ""
        sys.stderr.write(f"error: unknown CSS target {args.target!r}{ctx}\n")
        return EXIT_NOT_FOUND

    if args.json:
        sys.stdout.write(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    kind = info["kind"]
    if kind == "at-rule":
        sys.stdout.write(f"{info['name']} (at-rule)\n")
        if info.get("syntax"):
            sys.stdout.write(f"  syntax: {info['syntax']}\n")
        prelude = info.get("prelude_syntax")
        if prelude:
            sys.stdout.write(f"  prelude: {prelude}\n")
        descs: dict[str, str] = info.get("descriptors", {})
        if descs:
            sys.stdout.write(f"  descriptors ({len(descs)}):\n")
            for d_name, d_syn in descs.items():
                sys.stdout.write(f"    {d_name}: {d_syn}\n")
        return EXIT_OK

    header = f"{info['name']} ({kind})"
    if info.get("at_rule"):
        header = f"{info['at_rule']}/{info['name']} ({kind})"
    sys.stdout.write(f"{header}\n")
    sys.stdout.write(f"  syntax: {info['syntax']}\n")
    if info.get("python_key"):
        sys.stdout.write(f"  python_key: {info['python_key']}\n")
    if "engines" in info:
        status_note = (
            "enabled"
            if info["enabled_by_default"]
            else f"disabled by default (requires min_engines<={info['engines']})"
        )
        sys.stdout.write(f"  engines: {info['engines']}/3 ({status_note})\n")
    if info.get("also_descriptor_in"):
        sys.stdout.write(f"  also_descriptor_in: {', '.join(info['also_descriptor_in'])}\n")
    if info.get("python_inputs"):
        sys.stdout.write(f"  python_inputs: {' | '.join(info['python_inputs'])}\n")
    return EXIT_OK


def _run_check(args: argparse.Namespace) -> int:
    target: str = args.target
    at_rule: str | None = args.at_rule

    # Resolve target existence first so unknown targets return EXIT_NOT_FOUND (4)
    stripped = target.strip()
    if not stripped:
        ctx = f" (in {at_rule})" if at_rule else ""
        sys.stderr.write(f"error: unknown CSS target {target!r}{ctx}\n")
        return EXIT_NOT_FOUND

    is_custom_prop = False
    if not stripped.startswith(("<", "@")) and at_rule is None:
        try:
            norm_prop = normalize_identifier_key(stripped)
            is_custom_prop = norm_prop.startswith("--") and len(norm_prop) >= 3
        except StixisError:
            is_custom_prop = False

    info = _inspect_target(target, at_rule=at_rule)
    if not is_custom_prop and info is None:
        ctx = f" (in {at_rule})" if at_rule else ""
        sys.stderr.write(f"error: unknown CSS target {target!r}{ctx}\n")
        return EXIT_NOT_FOUND

    effective_target = target
    effective_at_rule = at_rule
    if info is not None and info["kind"] == "descriptor":
        effective_target = info["name"]
        if effective_at_rule is None:
            effective_at_rule = info.get("at_rule")

    try:
        opts = SerializationOptions(min_engines=args.min_engines)
        canonical = canonicalize(
            effective_target, args.value, at_rule=effective_at_rule, options=opts
        )
    except StixisError as exc:
        msg = str(exc).splitlines()[0] if str(exc) else "invalid CSS value"
        sys.stderr.write(f"error: {msg}\n")
        return EXIT_VALIDATION_ERROR

    sys.stdout.write(f"{canonical}\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the read-only `stixis` CLI argument parser."""
    parser = _StixisArgumentParser(
        prog="stixis",
        description="Inspect W3C @webref/css syntax definitions and validate/canonicalize CSS values.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    inspect_p = subparsers.add_parser(
        "inspect",
        help="Show the Value Definition Syntax, descriptors, and Python input types for a property, @at-rule, or <type>.",
    )
    inspect_p.add_argument(
        "target",
        help="CSS property ('font-style'), at-rule ('@font-face'), descriptor ('@font-face/src'), or type ('<font-src>').",
    )
    inspect_p.add_argument(
        "--at-rule",
        dest="at_rule",
        default=None,
        help="Scope lookup to descriptors of a specific at-rule (e.g. '@font-face').",
    )
    inspect_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout.",
    )
    inspect_p.set_defaults(handler=_run_inspect)

    check_p = subparsers.add_parser(
        "check",
        help="Validate and canonicalize a CSS value against a property, descriptor, or <type>.",
    )
    check_p.add_argument(
        "target",
        help="CSS property ('font-style'), descriptor ('src'), or type ('<urange>').",
    )
    check_p.add_argument(
        "value",
        help="CSS value text to validate and canonicalize.",
    )
    check_p.add_argument(
        "--at-rule",
        dest="at_rule",
        default=None,
        help="Validate as a descriptor of the specified at-rule (e.g. '@font-face').",
    )
    check_p.add_argument(
        "--min-engines",
        dest="min_engines",
        type=int,
        choices=(0, 1, 2, 3),
        default=DEFAULT_MIN_ENGINES,
        help="Minimum browser engine count required for CSS properties (0..3, default: 2).",
    )
    check_p.set_defaults(handler=_run_check)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint returning exit codes `0` (valid), `1` (invalid CSS), `2` (bad CLI usage), or `4` (unknown target)."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE_ERROR
        return code
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE_ERROR
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
