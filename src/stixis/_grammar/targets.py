"""Shared CSS target resolution (`resolve_target_syntax`, `lookup_value_node`, `is_property_enabled`)."""

from __future__ import annotations

from functools import lru_cache

from stixis._generated.specs import (
    ALWAYS_ALLOWED_PROPERTIES,
    ATRULE_DESCRIPTORS,
    PROPERTY_ENGINE_COUNTS,
    PROPERTY_SYNTAXES,
    VALUE_SYNTAXES,
)
from stixis._grammar.vds import SUPPORTED_BUILTIN_TYPES, ValueNode, compile_value_syntax
from stixis._syntax.serialize import serialize_dashed_ident, serialize_ident
from stixis._syntax.tokenizer import tokenize_css
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

_LOWER_VALUE_SYNTAX_KEYS: dict[str, tuple[str, str]] = {
    k.lower(): (k, v) for k, v in VALUE_SYNTAXES.items() if k.startswith("<") and k.endswith(">")
}


@lru_cache(maxsize=4096)
def lookup_value_node(name: str, is_property: bool = False) -> ValueNode | None:
    """Resolve a `<type>` or `<'property'>` name from the generated `@webref/css` tables."""
    if is_property:
        syn = PROPERTY_SYNTAXES.get(name)
    else:
        syn = VALUE_SYNTAXES.get(name) or VALUE_SYNTAXES.get(f"<{name.strip('<>')}>")
        if syn is None:
            angled = f"<{name.strip('<>')}>".lower()
            entry = _LOWER_VALUE_SYNTAX_KEYS.get(angled)
            if entry is not None:
                syn = entry[1]
    if syn is None:
        return None
    return compile_value_syntax(syn)


def normalize_identifier_key(key: str) -> str:
    """Normalize a Python `snake_case` property or descriptor key to `kebab-case`, preserving `--*` custom properties."""
    if not isinstance(key, str) or not key.strip():
        raise StixisValidationError("property", "<ident>", key, "Empty property or descriptor name")
    stripped = key.strip()
    if "/*" in stripped or "*/" in stripped:
        raise StixisValidationError(
            "property",
            "<ident>",
            key,
            f"Invalid CSS property or descriptor identifier: {key!r}",
        )
    try:
        toks = tokenize_css(stripped, preserve_whitespace=True)
    except StixisSyntaxError as exc:
        raise StixisValidationError(
            "property",
            "<ident>",
            key,
            f"Invalid CSS property or descriptor identifier: {key!r}",
        ) from exc
    if len(toks) != 1 or toks[0].kind != "ident":
        raise StixisValidationError(
            "property",
            "<ident>",
            key,
            f"Invalid CSS property or descriptor identifier: {key!r}",
        )
    ident_val = toks[0].value
    if ident_val.startswith("--"):
        if len(ident_val) < 3:
            raise StixisValidationError(
                "property",
                "<custom-property-name>",
                key,
                "CSS custom property name must contain characters after '--'",
            )
        return serialize_dashed_ident(ident_val)
    return serialize_ident(ident_val.replace("_", "-").lower())


def is_property_enabled(prop: str, options: SerializationOptions = DEFAULT_OPTIONS) -> bool:
    """Return True if `prop` meets `options.min_engines` or is explicitly enabled."""
    if prop not in PROPERTY_SYNTAXES:
        return False
    if options.min_engines <= 0:
        return True
    if prop in ALWAYS_ALLOWED_PROPERTIES or prop in options.extra_properties:
        return True
    return PROPERTY_ENGINE_COUNTS.get(prop, 0) >= options.min_engines


def resolve_type_syntax(type_name: str) -> tuple[str, str] | None:
    """Return `(canonical_type_name, syntax_str)` for a known `<type>` (`VALUE_SYNTAXES` or `SUPPORTED_BUILTIN_TYPES`), or `None`."""
    syn = VALUE_SYNTAXES.get(type_name)
    if syn is not None:
        return type_name, syn
    low = type_name.lower()
    entry = _LOWER_VALUE_SYNTAX_KEYS.get(low)
    if entry is not None:
        return entry
    if low in SUPPORTED_BUILTIN_TYPES:
        return low, low
    return None


def resolve_target_syntax(
    target: str,
    *,
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> tuple[str, str | None, str, ValueNode]:
    """Resolve `target` (property, descriptor, or `<type>`) to `(normalized_name, normalized_at_rule, syntax_str, ValueNode)`."""
    if not isinstance(target, str):
        raise StixisValidationError(str(target), "<ident>", target)
    raw = target.strip()
    if not raw:
        raise StixisValidationError(target, "<ident>", target)

    if at_rule is not None:
        norm_at = at_rule.strip().lower()
        if not norm_at.startswith("@"):
            norm_at = f"@{norm_at}"
        descs = ATRULE_DESCRIPTORS.get(norm_at)
        if descs is None:
            raise StixisValidationError(
                norm_at, "known @at-rule", target, f"Unknown at-rule: {norm_at}"
            )
        norm_desc = normalize_identifier_key(raw)
        syn = descs.get(norm_desc)
        if syn is None:
            raise StixisValidationError(
                f"{norm_at}/{norm_desc}",
                f"descriptor of {norm_at}",
                target,
                f"Unknown descriptor {norm_desc!r} for {norm_at}",
            )
        return norm_desc, norm_at, syn, compile_value_syntax(syn)

    if raw.startswith("<") and raw.endswith(">"):
        resolved_type = resolve_type_syntax(raw)
        if resolved_type is not None:
            canon_type, syn = resolved_type
            return canon_type, None, syn, compile_value_syntax(syn)
        raise StixisValidationError(
            raw,
            "known CSS property or <type>",
            target,
            f"Unknown CSS target: {target!r}",
        )

    if raw.startswith("@") and "/" in raw:
        at_part, desc_part = raw.split("/", 1)
        return resolve_target_syntax(desc_part, at_rule=at_part, options=options)

    norm_prop = normalize_identifier_key(raw)
    if norm_prop.startswith("--") and len(norm_prop) >= 3:
        return norm_prop, None, "<declaration-value>", compile_value_syntax("<declaration-value>")

    syn = PROPERTY_SYNTAXES.get(norm_prop)
    if syn is not None:
        if not is_property_enabled(norm_prop, options):
            eng = PROPERTY_ENGINE_COUNTS.get(norm_prop, 0)
            raise StixisValidationError(
                norm_prop,
                f"CSS property with >= {options.min_engines} browser engines (got {eng})",
                target,
                f"Experimental CSS property {norm_prop!r} ({eng} engine(s)) is disabled by min_engines={options.min_engines}; pass SerializationOptions(min_engines={eng}) or extra_properties=frozenset({{{norm_prop!r}}})",
            )
        return norm_prop, None, syn, compile_value_syntax(syn)

    resolved_type = resolve_type_syntax(f"<{norm_prop}>")
    if resolved_type is not None:
        canon_type, tsyn = resolved_type
        return canon_type, None, tsyn, compile_value_syntax(tsyn)

    raise StixisValidationError(
        norm_prop,
        "known CSS property or <type>",
        target,
        f"Unknown CSS target: {target!r}",
    )
