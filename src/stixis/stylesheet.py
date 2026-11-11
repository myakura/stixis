"""Immutable, eagerly-validated CSS rule and stylesheet AST nodes (`StyleRule`, `AtRule`, `FontFace`, `StyleSheet`)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from string.templatelib import Interpolation, Template, convert
from typing import Any, Unpack

from stixis._generated.specs import (
    ATRULE_DESCRIPTORS,
    ATRULE_PRELUDE_SYNTAXES,
    PROPERTY_ENGINE_COUNTS,
    PROPERTY_SYNTAXES,
)
from stixis._generated.types import FontFaceDescriptors
from stixis._grammar.matcher import evaluate_canonicalize, evaluate_serialize_value
from stixis._grammar.targets import (
    is_property_enabled,
    lookup_value_node,
    normalize_identifier_key,
)
from stixis._grammar.vds import compile_value_syntax
from stixis._syntax.serialize import (
    _has_option_aware_str,
    serialize_dashed_ident,
    serialize_ident,
    serialize_string,
    serialize_url,
)
from stixis._syntax.template import render_css_template
from stixis._syntax.tokenizer import tokenize_css, validate_token_stream_safety
from stixis.api import serialize_value
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.options import (
    DEFAULT_OPTIONS,
    SerializationOptions,
    active_or_explicit_options,
    current_options,
)

__all__ = [
    "AtRule",
    "CommentNode",
    "FontFace",
    "RawDeclaration",
    "Rule",
    "StyleRule",
    "StyleSheet",
    "dumps",
    "serialize",
    "serialize_declarations",
]


def _render_raw_declaration_value(
    prop: str,
    raw_val: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    if not prop.startswith("--") and isinstance(raw_val, str):
        normalized = (
            raw_val.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\f", "\n")
            .replace("\x00", "\ufffd")
        )
        tokens = validate_token_stream_safety(
            normalized,
            forbid_top_level_semicolon=True,
            forbid_top_level_bang=True,
            target="<declaration-value>",
        )
        out: list[str] = []
        cursor = 0
        for tok in tokens:
            if tok.kind == "string":
                out.append(normalized[cursor : tok.start])
                out.append(serialize_string(tok.value, options=options))
                cursor = tok.start + len(tok.raw)
            elif tok.kind == "url":
                out.append(normalized[cursor : tok.start])
                out.append(serialize_url(tok.value, options=options))
                cursor = tok.start + len(tok.raw)
        if cursor == 0:
            return raw_val.strip()
        out.append(normalized[cursor:])
        return "".join(out).strip()
    return serialize_value("--raw", raw_val, options=options)


@dataclass(frozen=True, slots=True)
class RawDeclaration:
    """Explicit opt-in declaration for custom or vendor-prefixed properties validated with `<declaration-value>`."""

    property: str
    value: Any
    _raw_value: Any = field(init=False, repr=False, compare=False)
    _effective_options: SerializationOptions = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        opts = current_options()
        norm = normalize_identifier_key(self.property)
        frozen_raw = _freeze_value(self.value)
        rendered = _render_raw_declaration_value(norm, frozen_raw, options=opts)
        object.__setattr__(self, "property", norm)
        object.__setattr__(self, "value", rendered)
        object.__setattr__(self, "_raw_value", frozen_raw)
        object.__setattr__(self, "_effective_options", opts)


def _has_leading_newline(comment: str | Template) -> bool:
    if isinstance(comment, str):
        for ch in comment:
            if ch in ("\n", "\r"):
                return True
            if not ch.isspace():
                return False
        return False
    if isinstance(comment, Template) and comment.strings:
        for ch in comment.strings[0]:
            if ch in ("\n", "\r"):
                return True
            if not ch.isspace():
                return False
    return False


@dataclass(frozen=True, slots=True)
class CommentNode:
    """A standalone CSS comment (`/* ... */`) validated against comment-close injection."""

    text: str | Template
    leading_newline: bool = False
    options: SerializationOptions | None = None
    _effective_options: SerializationOptions = field(init=False, repr=False)
    _has_leading_nl: bool = field(init=False, repr=False)
    _rendered: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = current_options(self.options)
        has_nl = bool(self.leading_newline) or _has_leading_newline(self.text)
        rendered = _validate_and_render_comment(self.text, options=opts)
        if isinstance(self.text, Template):
            object.__setattr__(self, "text", _freeze_value(self.text))
        object.__setattr__(self, "_effective_options", opts)
        object.__setattr__(self, "_has_leading_nl", has_nl)
        object.__setattr__(self, "_rendered", rendered)

    def format(
        self,
        *,
        minify: bool = False,
        indent: int = 0,
        options: SerializationOptions | None = None,
    ) -> str:
        if minify:
            return ""
        pad = "  " * indent
        body = (
            _validate_and_render_comment(self.text, options=options)
            if options is not None and options != self._effective_options
            else self._rendered
        )
        prefix = "\n" if self._has_leading_nl else ""
        return f"{prefix}{pad}{body}"

    def __str__(self) -> str:
        return self._rendered


def _validate_and_render_comment(
    comment: str | Template,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    if isinstance(comment, Template):
        rendered = render_css_template(comment, options=options, start_in_comment=True).strip()
        if rendered.startswith("/*") and rendered.endswith("*/") and len(rendered) >= 4:
            # Ensure no premature `*/` exists inside the body
            body = rendered[2:-2]
            while "*/" in body:
                body = body.replace("*/", "* /")
            if body.endswith("*"):
                body = f"{body} "
            return f"/*{body}*/"
        cleaned = rendered
        while "*/" in cleaned:
            cleaned = cleaned.replace("*/", "* /")
        return f"/* {cleaned} */"

    if isinstance(comment, str):
        stripped = comment.strip()
        if stripped.startswith("/*") and stripped.endswith("*/") and len(stripped) >= 4:
            body = stripped[2:-2]
            if "*/" in body:
                raise StixisValidationError(
                    "comment",
                    "/* ... */",
                    comment,
                    "CSS comment contains premature closing `*/`",
                )
            if body.endswith("*"):
                return f"/*{body} */"
            return stripped
        if "*/" in comment:
            raise StixisValidationError(
                "comment",
                "/* ... */",
                comment,
                "Unescaped `*/` in plain string comment (use a PEP 750 `t'...'` Template for auto-escaping)",
            )
        return f"/* {comment} */"

    raise StixisValidationError("comment", "str | Template", comment)


class _FrozenMapping(Mapping[Any, Any]):
    """Immutable, hashable `Mapping` wrapper around a tuple of `(key, value)` pairs."""

    __slots__ = ("_dict", "_pairs")

    def __init__(self, pairs: tuple[tuple[Any, Any], ...]) -> None:
        self._pairs = pairs
        self._dict = dict(pairs)

    def __getitem__(self, key: Any) -> Any:
        return self._dict[key]

    def __iter__(self) -> Any:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __hash__(self) -> int:
        return hash(self._pairs)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _FrozenMapping):
            return self._pairs == other._pairs
        if isinstance(other, Mapping):
            return self._dict == dict(other)
        return NotImplemented

    def __repr__(self) -> str:
        return repr(self._dict)


class _FrozenList(Sequence[Any]):
    """Immutable, hashable `Sequence` (not a `tuple`) preserving list vs tuple disambiguation."""

    __slots__ = ("_items",)

    def __init__(self, items: tuple[Any, ...]) -> None:
        self._items = items

    def __getitem__(self, index: Any) -> Any:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(("_FrozenList", self._items))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _FrozenList):
            return self._items == other._items
        return NotImplemented

    def __repr__(self) -> str:
        return repr(list(self._items))


def _freeze_interpolation(item: Interpolation) -> Interpolation:
    val = item.value
    if not isinstance(val, bool) and isinstance(val, (int, float, str)):
        return item
    if item.conversion is None and not item.format_spec and _has_option_aware_str(val):
        return item
    converted = convert(val, item.conversion)
    text = format(converted, item.format_spec) if item.format_spec else str(converted)
    return Interpolation(text, item.expression, None, "")


def _freeze_value(val: Any) -> Any:
    """Recursively convert mutable containers inside AST inputs into immutable, hashable equivalents."""
    if isinstance(val, Template):
        frozen_parts: list[str | Interpolation] = []
        for item in val:
            if isinstance(item, Interpolation):
                frozen_parts.append(_freeze_interpolation(item))
            else:
                frozen_parts.append(item)
        return Template(*frozen_parts)
    if isinstance(val, _FrozenMapping):
        return val
    if isinstance(val, Mapping):
        return _FrozenMapping(tuple((k, _freeze_value(v)) for k, v in val.items()))
    if isinstance(val, (set, frozenset)):
        return frozenset(_freeze_value(x) for x in val)
    if isinstance(val, tuple):
        return tuple(_freeze_value(x) for x in val)
    if isinstance(val, _FrozenList):
        return val
    if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
        return _FrozenList(tuple(_freeze_value(x) for x in val))
    return val


def _freeze_declarations(
    declarations: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] | None,
) -> tuple[tuple[str, Any] | RawDeclaration, ...]:
    if not declarations:
        return ()
    items = list(declarations.items()) if isinstance(declarations, Mapping) else list(declarations)
    out: list[tuple[str, Any] | RawDeclaration] = []
    for item in items:
        if isinstance(item, RawDeclaration):
            out.append(item)
        else:
            k, v = item
            out.append((k, _freeze_value(v)))
    return tuple(out)


def _validate_and_normalize_declarations(
    declarations: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] | None,
    *,
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
    raw_override_options: SerializationOptions | None = None,
) -> tuple[tuple[str, str], ...]:
    if not declarations:
        return ()

    items: list[tuple[str, Any] | RawDeclaration]
    if isinstance(declarations, Mapping):
        items = list(declarations.items())
    elif isinstance(declarations, Sequence):
        items = list(declarations)
    else:
        raise StixisValidationError("declarations", "Mapping[str, Any]", declarations)

    normalized_pairs: list[tuple[str, str]] = []
    norm_at = (
        (
            at_rule.strip().lower()
            if at_rule.strip().startswith("@")
            else f"@{at_rule.strip().lower()}"
        )
        if at_rule is not None
        else None
    )

    for item in items:
        if isinstance(item, RawDeclaration):
            val_s = (
                str(item.value)
                if raw_override_options is None or raw_override_options == item._effective_options
                else _render_raw_declaration_value(
                    item.property, item._raw_value, options=raw_override_options
                )
            )
            normalized_pairs.append((item.property, val_s))
            continue
        if not isinstance(item, tuple) or len(item) != 2:
            raise StixisValidationError("declarations", "(property, value)", item)
        raw_key, val = item
        if val is None:
            continue
        prop = normalize_identifier_key(raw_key)

        if prop.startswith("--") and len(prop) >= 2:
            if norm_at is not None and norm_at == "@font-face":
                raise StixisValidationError(
                    f"{norm_at}/{prop}",
                    "descriptor of @font-face",
                    prop,
                    "Custom properties are not valid inside @font-face",
                )
            val_str = evaluate_serialize_value(
                prop,
                "<declaration-value>",
                compile_value_syntax("<declaration-value>"),
                val,
                specs_lookup=lookup_value_node,
                options=options,
            )
            normalized_pairs.append((prop, val_str))
            continue

        if norm_at is not None:
            descs = ATRULE_DESCRIPTORS.get(norm_at, {})
            if prop not in descs:
                if options.allow_unknown_properties or (
                    options.allow_vendor_prefixes and prop.startswith("-")
                ):
                    val_str = evaluate_serialize_value(
                        prop,
                        "<declaration-value>",
                        compile_value_syntax("<declaration-value>"),
                        val,
                        specs_lookup=lookup_value_node,
                        options=options,
                    )
                    normalized_pairs.append((prop, val_str))
                    continue
                raise StixisValidationError(
                    f"{norm_at}/{prop}",
                    f"descriptor of {norm_at}",
                    val,
                    f"Unknown descriptor {prop!r} for {norm_at}",
                )
            val_str = serialize_value(prop, val, at_rule=norm_at, options=options)
            if norm_at == "@font-face" and prop in ("font-width", "font-stretch"):
                if options.font_face_stretch_alias == "font-stretch":
                    normalized_pairs.append(("font-stretch", val_str))
                elif options.font_face_stretch_alias == "both":
                    normalized_pairs.append(("font-stretch", val_str))
                    normalized_pairs.append(("font-width", val_str))
                else:
                    normalized_pairs.append((prop, val_str))
            else:
                normalized_pairs.append((prop, val_str))
            continue

        # Standard style rule property
        if prop in PROPERTY_SYNTAXES and is_property_enabled(prop, options):
            val_str = serialize_value(prop, val, options=options)
            normalized_pairs.append((prop, val_str))
            continue

        if options.allow_unknown_properties or (
            options.allow_vendor_prefixes and prop.startswith("-") and not prop.startswith("--")
        ):
            val_str = evaluate_serialize_value(
                prop,
                "<declaration-value>",
                compile_value_syntax("<declaration-value>"),
                val,
                specs_lookup=lookup_value_node,
                options=options,
            )
            normalized_pairs.append((prop, val_str))
            continue

        if prop in PROPERTY_SYNTAXES:
            eng = PROPERTY_ENGINE_COUNTS.get(prop, 0)
            raise StixisValidationError(
                prop,
                f"CSS property with >= {options.min_engines} browser engines (got {eng})",
                val,
                f"Experimental CSS property {prop!r} ({eng} engine(s)) is disabled by min_engines={options.min_engines}; pass SerializationOptions(min_engines={eng}) or extra_properties=frozenset({{{prop!r}}})",
            )

        raise StixisValidationError(
            prop,
            "known CSS property",
            val,
            f"Unknown CSS property: {prop!r}",
        )

    return tuple(normalized_pairs)


def serialize_declarations(
    declarations: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration],
    *,
    at_rule: str | None = None,
    minify: bool = False,
    options: SerializationOptions | None = None,
) -> str:
    """Validate and serialize a mapping of CSS declarations (`prop: value;`)."""
    opts = current_options(options)
    raw_override = active_or_explicit_options(options)
    pairs = _validate_and_normalize_declarations(
        declarations, at_rule=at_rule, options=opts, raw_override_options=raw_override
    )
    if minify:
        return ";".join(f"{k}:{v}" for k, v in pairs)
    return " ".join(f"{k}: {v};" for k, v in pairs)


def _validate_selector(
    selector: str | Sequence[str] | Template,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    if isinstance(selector, Template):
        sel_str = render_css_template(selector, options=options).strip()
    elif isinstance(selector, str):
        sel_str = selector.strip()
    elif isinstance(selector, Sequence):
        parts = [s.strip() for s in selector if isinstance(s, str) and s.strip()]
        if not parts or len(parts) != len(selector):
            raise StixisValidationError("selector", "<selector-list>", selector)
        sel_str = ", ".join(parts)
    else:
        raise StixisValidationError("selector", "<selector-list>", selector)

    if not sel_str:
        raise StixisValidationError(
            "selector", "<selector-list>", selector, "Selector cannot be empty"
        )

    validate_token_stream_safety(
        sel_str,
        forbid_top_level_semicolon=True,
        forbid_top_level_bang=True,
        forbid_top_level_braces=True,
        target="selector",
    )
    return sel_str


def _validate_at_rule_prelude(
    at_name: str,
    prelude: Any,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    prelude_syntax = ATRULE_PRELUDE_SYNTAXES.get(at_name)
    if prelude is None or prelude == "":
        if (
            prelude_syntax
            and "?" not in prelude_syntax
            and at_name
            in (
                "@font-palette-values",
                "@property",
                "@keyframes",
                "@counter-style",
                "@color-profile",
                "@position-try",
            )
        ):
            raise StixisValidationError(
                at_name,
                prelude_syntax,
                prelude,
                f"{at_name} requires a prelude matching {prelude_syntax}",
            )
        return ""

    if prelude_syntax == "":
        raise StixisValidationError(
            at_name,
            "no prelude",
            prelude,
            f"{at_name} does not accept a prelude",
        )

    if prelude_syntax:
        node = compile_value_syntax(prelude_syntax)
        if isinstance(prelude, Template):
            rendered = render_css_template(prelude, options=options)
            return evaluate_canonicalize(
                f"{at_name} prelude",
                prelude_syntax,
                node,
                rendered,
                specs_lookup=lookup_value_node,
                options=options,
            )
        return evaluate_serialize_value(
            f"{at_name} prelude",
            prelude_syntax,
            node,
            prelude,
            specs_lookup=lookup_value_node,
            options=options,
        )

    # Conditional or general prelude (`@media`, `@supports`, `@container`, `@scope`, `@import`, etc.)
    raw_prelude = (
        render_css_template(prelude, options=options).strip()
        if isinstance(prelude, Template)
        else str(prelude).strip()
    )
    if not raw_prelude:
        return ""
    validate_token_stream_safety(
        raw_prelude,
        forbid_top_level_semicolon=True,
        forbid_top_level_bang=False,
        forbid_top_level_braces=True,
        target=f"{at_name} prelude",
    )
    return raw_prelude


def _formatting_profile(options: SerializationOptions) -> tuple[Any, ...]:
    return (
        options.quote_char,
        options.always_quote_family_name,
        options.normalize_zero_oblique,
        options.font_face_stretch_alias,
        options.quote_font_format,
        options.uppercase_urange_prefix,
    )


def _needs_rebuild_for_options(
    built_opts: SerializationOptions,
    target_opts: SerializationOptions,
) -> bool:
    if built_opts == target_opts:
        return False
    if _formatting_profile(built_opts) != _formatting_profile(target_opts):
        return True
    # When `allow_unknown_properties` or `allow_vendor_prefixes` was active, disabled known properties
    # were accepted as `<declaration-value>` rather than validated against their specific property grammar.
    if (built_opts.allow_unknown_properties or built_opts.allow_vendor_prefixes) and (
        target_opts.min_engines != built_opts.min_engines
        or target_opts.extra_properties != built_opts.extra_properties
    ):
        return True
    # Tightening validation gates requires re-validating declarations/descriptors
    if target_opts.min_engines > built_opts.min_engines:
        return True
    if not (built_opts.extra_properties <= target_opts.extra_properties):
        return True
    if built_opts.allow_vendor_prefixes and not target_opts.allow_vendor_prefixes:
        return True
    return bool(built_opts.allow_unknown_properties and not target_opts.allow_unknown_properties)


def _has_mismatched_raw_decl_options(
    decls: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] | None,
    target_opts: SerializationOptions,
) -> bool:
    if not decls or isinstance(decls, Mapping):
        return False
    return any(
        isinstance(d, RawDeclaration)
        and _formatting_profile(d._effective_options) != _formatting_profile(target_opts)
        for d in decls
    )


@dataclass(frozen=True, slots=True)
class StyleRule:
    """An immutable CSS qualified style rule (`selector { declarations; nested_rules }`), eagerly validated on construction."""

    selector: str | Sequence[str] | Template
    declarations: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] = ()
    rules: Sequence[Rule] = ()
    comment: str | Template | None = None
    options: SerializationOptions | None = None
    _effective_options: SerializationOptions = field(init=False, repr=False)
    _selector_str: str = field(init=False, repr=False)
    _decl_pairs: tuple[tuple[str, str], ...] = field(init=False, repr=False)
    _rules_tuple: tuple[Rule, ...] = field(init=False, repr=False)
    _comment_str: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = current_options(self.options)
        raw_override = active_or_explicit_options(self.options)
        sel_s = _validate_selector(self.selector, options=opts)
        decls = _validate_and_normalize_declarations(
            self.declarations, at_rule=None, options=opts, raw_override_options=raw_override
        )
        rules_t = _validate_child_rules(self.rules, options=opts)
        comm_s = (
            _validate_and_render_comment(self.comment, options=opts)
            if self.comment is not None
            else None
        )
        frozen_sel = (
            _freeze_value(self.selector)
            if isinstance(self.selector, Template)
            else (
                self.selector
                if isinstance(self.selector, str)
                else tuple(s.strip() for s in self.selector)
            )
        )
        frozen_decls = _freeze_declarations(self.declarations)
        frozen_comm = (
            _freeze_value(self.comment) if isinstance(self.comment, Template) else self.comment
        )
        object.__setattr__(self, "selector", frozen_sel)
        object.__setattr__(self, "declarations", frozen_decls)
        object.__setattr__(self, "rules", rules_t)
        object.__setattr__(self, "comment", frozen_comm)
        object.__setattr__(self, "_effective_options", opts)
        object.__setattr__(self, "_selector_str", sel_s)
        object.__setattr__(self, "_decl_pairs", decls)
        object.__setattr__(self, "_rules_tuple", rules_t)
        object.__setattr__(self, "_comment_str", comm_s)

    def format(
        self,
        *,
        minify: bool = False,
        indent: int = 0,
        options: SerializationOptions | None = None,
    ) -> str:
        eff_opts = options if options is not None else self.options
        if options is not None and (
            _needs_rebuild_for_options(self._effective_options, options)
            or (
                self.options != options
                and _has_mismatched_raw_decl_options(self.declarations, options)
            )
        ):
            rebuilt = StyleRule(
                self.selector,
                self.declarations,
                rules=self.rules,
                comment=self.comment,
                options=options,
            )
            return rebuilt.format(minify=minify, indent=indent, options=options)

        if minify:
            decl_part = ";".join(f"{k}:{v}" for k, v in self._decl_pairs)
            child_part = "".join(
                _format_rule_node(c, minify=True, indent=0, options=eff_opts)
                for c in self._rules_tuple
            )
            sep = ";" if (decl_part and child_part) else ""
            return f"{self._selector_str}{{{decl_part}{sep}{child_part}}}"

        pad = "  " * indent
        inner_pad = "  " * (indent + 1)
        lines: list[str] = []
        if self._comment_str:
            lines.append(f"{pad}{self._comment_str}")

        body_items: list[str] = []
        for k, v in self._decl_pairs:
            body_items.append(f"{inner_pad}{k}: {v};")

        for child in self._rules_tuple:
            body_items.append(
                _format_rule_node(child, minify=False, indent=indent + 1, options=eff_opts)
            )

        if not body_items:
            lines.append(f"{pad}{self._selector_str} {{}}")
        else:
            lines.append(f"{pad}{self._selector_str} {{")
            lines.extend(body_items)
            lines.append(f"{pad}}}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format(minify=False)


@dataclass(frozen=True, slots=True)
class AtRule:
    """An immutable CSS `@` rule (`@font-face`, `@media`, `@font-palette-values`, `@property`, `@layer`, etc.), eagerly validated on construction."""

    name: str
    prelude: Any = None
    descriptors: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] | None = None
    rules: Sequence[Rule] = ()
    comment: str | Template | None = None
    statement: bool = False
    options: SerializationOptions | None = None
    _effective_options: SerializationOptions = field(init=False, repr=False)
    _at_name: str = field(init=False, repr=False)
    _prelude_str: str = field(init=False, repr=False)
    _decl_pairs: tuple[tuple[str, str], ...] = field(init=False, repr=False)
    _rules_tuple: tuple[Rule, ...] = field(init=False, repr=False)
    _comment_str: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = current_options(self.options)
        raw_name = self.name.strip() if isinstance(self.name, str) else ""
        if not raw_name or "/*" in raw_name or "*/" in raw_name:
            raise StixisValidationError("at-rule", "@<ident>", self.name)
        at_cand = raw_name if raw_name.startswith("@") else f"@{raw_name}"
        try:
            at_toks = tokenize_css(at_cand, preserve_whitespace=True)
        except StixisSyntaxError as exc:
            raise StixisValidationError("at-rule", "@<ident>", self.name) from exc
        if len(at_toks) != 1 or at_toks[0].kind != "at-keyword" or not at_toks[0].value:
            raise StixisValidationError("at-rule", "@<ident>", self.name)
        at_ident = at_toks[0].value.lower()
        at_name = (
            f"@{serialize_dashed_ident(at_ident)}"
            if at_ident.startswith("--")
            else f"@{serialize_ident(at_ident)}"
        )

        prelude_s = _validate_at_rule_prelude(at_name, self.prelude, options=opts)
        decls = _validate_and_normalize_declarations(
            self.descriptors,
            at_rule=at_name if ATRULE_DESCRIPTORS.get(at_name) else None,
            options=opts,
            raw_override_options=active_or_explicit_options(self.options),
        )
        rules_t = _validate_child_rules(self.rules, options=opts)
        comm_s = (
            _validate_and_render_comment(self.comment, options=opts)
            if self.comment is not None
            else None
        )
        frozen_decls = None if self.descriptors is None else _freeze_declarations(self.descriptors)
        frozen_comm = (
            _freeze_value(self.comment) if isinstance(self.comment, Template) else self.comment
        )

        object.__setattr__(self, "prelude", _freeze_value(self.prelude))
        object.__setattr__(self, "descriptors", frozen_decls)
        object.__setattr__(self, "rules", rules_t)
        object.__setattr__(self, "comment", frozen_comm)
        object.__setattr__(self, "_effective_options", opts)
        object.__setattr__(self, "_at_name", at_name)
        object.__setattr__(self, "_prelude_str", prelude_s)
        object.__setattr__(self, "_decl_pairs", decls)
        object.__setattr__(self, "_rules_tuple", rules_t)
        object.__setattr__(self, "_comment_str", comm_s)

    def format(
        self,
        *,
        minify: bool = False,
        indent: int = 0,
        options: SerializationOptions | None = None,
    ) -> str:
        eff_opts = options if options is not None else self.options
        if options is not None and (
            _needs_rebuild_for_options(self._effective_options, options)
            or (
                self.options != options
                and _has_mismatched_raw_decl_options(self.descriptors, options)
            )
        ):
            rebuilt = AtRule(
                self.name,
                self.prelude,
                descriptors=self.descriptors,
                rules=self.rules,
                comment=self.comment,
                statement=self.statement,
                options=options,
            )
            return rebuilt.format(minify=minify, indent=indent, options=options)

        pad = "" if minify else ("  " * indent)
        inner_pad = "" if minify else ("  " * (indent + 1))
        head = f"{self._at_name} {self._prelude_str}" if self._prelude_str else self._at_name

        is_stmt = self.statement or (
            not self._decl_pairs
            and not self._rules_tuple
            and self.descriptors is None
            and self._at_name in ("@import", "@charset", "@namespace", "@layer", "@custom-media")
            and bool(self._prelude_str)
        )
        if is_stmt:
            if self._comment_str and not minify:
                return f"{pad}{self._comment_str}\n{pad}{head};"
            return f"{pad}{head};"

        lines: list[str] = []
        if self._comment_str and not minify:
            lines.append(f"{pad}{self._comment_str}")

        if minify:
            decl_part = ";".join(f"{k}:{v}" for k, v in self._decl_pairs)
            child_part = "".join(
                _format_rule_node(c, minify=True, indent=0, options=eff_opts)
                for c in self._rules_tuple
            )
            sep = ";" if (decl_part and child_part) else ""
            return f"{head}{{{decl_part}{sep}{child_part}}}"

        body_items: list[str] = []
        for k, v in self._decl_pairs:
            body_items.append(f"{inner_pad}{k}: {v};")
        for child in self._rules_tuple:
            body_items.append(
                _format_rule_node(child, minify=minify, indent=indent + 1, options=eff_opts)
            )

        if not body_items:
            lines.append(f"{pad}{head} {{}}")
        else:
            lines.append(f"{pad}{head} {{")
            lines.extend(body_items)
            lines.append(f"{pad}}}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format(minify=False)


type Rule = StyleRule | AtRule | CommentNode


def _validate_child_rules(
    rules: Sequence[Rule] | Iterable[Rule],
    *,
    options: SerializationOptions | None = None,
) -> tuple[Rule, ...]:
    out: list[Rule] = []
    for r in rules:
        if isinstance(r, (StyleRule, AtRule, CommentNode)):
            out.append(r)
        elif isinstance(r, (str, Template)):
            out.append(CommentNode(r, options=options))
        else:
            raise StixisValidationError("rules", "StyleRule | AtRule | CommentNode", r)
    return tuple(out)


def FontFace(
    descriptors: Mapping[str, Any] | None = None,
    *,
    comment: str | Template | None = None,
    options: SerializationOptions | None = None,
    **kw_descriptors: Unpack[FontFaceDescriptors],
) -> AtRule:
    """Typed shorthand constructor for an eagerly-validated `@font-face` `AtRule`."""
    merged: dict[str, Any] = {}
    if descriptors is not None:
        merged.update(descriptors)
    merged.update(kw_descriptors)
    return AtRule(
        "@font-face",
        descriptors=merged,
        comment=comment,
        options=options,
    )


@dataclass(frozen=True, slots=True)
class StyleSheet:
    """An immutable CSS stylesheet composed of validated `Rule` nodes and an optional header comment."""

    rules: Sequence[Rule]
    header_comment: str | Template | None = None
    options: SerializationOptions | None = None
    _effective_options: SerializationOptions = field(init=False, repr=False)
    _rules_tuple: tuple[Rule, ...] = field(init=False, repr=False)
    _header_str: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = current_options(self.options)
        rules_t = _validate_child_rules(self.rules, options=opts)
        hdr_s = (
            _validate_and_render_comment(self.header_comment, options=opts)
            if self.header_comment is not None
            else None
        )
        frozen_hdr = (
            _freeze_value(self.header_comment)
            if isinstance(self.header_comment, Template)
            else self.header_comment
        )
        object.__setattr__(self, "rules", rules_t)
        object.__setattr__(self, "header_comment", frozen_hdr)
        object.__setattr__(self, "_effective_options", opts)
        object.__setattr__(self, "_rules_tuple", rules_t)
        object.__setattr__(self, "_header_str", hdr_s)

    def format(
        self,
        *,
        minify: bool = False,
        options: SerializationOptions | None = None,
    ) -> str:
        eff_opts = options if options is not None else self.options
        blocks: list[str] = []
        if self._header_str and not minify:
            if (
                options is not None
                and options != self._effective_options
                and self.header_comment is not None
            ):
                blocks.append(_validate_and_render_comment(self.header_comment, options=options))
            else:
                blocks.append(self._header_str)

        for r in self._rules_tuple:
            blocks.append(_format_rule_node(r, minify=minify, indent=0, options=eff_opts))

        if minify:
            return "".join(blocks)
        if not blocks:
            return ""
        return "\n".join(blocks) + "\n"

    def __str__(self) -> str:
        return self.format(minify=False)


def _format_rule_node(
    node: Rule,
    *,
    minify: bool,
    indent: int,
    options: SerializationOptions | None,
) -> str:
    if isinstance(node, CommentNode):
        return node.format(minify=minify, indent=indent, options=options)
    return node.format(minify=minify, indent=indent, options=options)


def dumps(
    node_or_nodes: Rule | StyleSheet | Iterable[Rule],
    *,
    minify: bool = False,
    options: SerializationOptions | None = None,
) -> str:
    """Serialize a `StyleSheet`, single `Rule`, or iterable of `Rule`s into a CSS string."""
    if isinstance(node_or_nodes, StyleSheet):
        return node_or_nodes.format(minify=minify, options=options)
    if isinstance(node_or_nodes, (StyleRule, AtRule, CommentNode)):
        return _format_rule_node(node_or_nodes, minify=minify, indent=0, options=options)
    if isinstance(node_or_nodes, Iterable) and not isinstance(node_or_nodes, (str, bytes, Mapping)):
        sheet = StyleSheet(tuple(node_or_nodes), options=options)
        return sheet.format(minify=minify, options=options)
    raise StixisValidationError("dumps", "Rule | StyleSheet | Iterable[Rule]", node_or_nodes)


serialize = dumps
