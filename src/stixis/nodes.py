"""Immutable, eagerly-validated CSS rule and stylesheet AST nodes (`StyleRule`, `AtRule`, `FontFace`, `StyleSheet`)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from string.templatelib import Template
from typing import Any, Unpack

from stixis._generated.specs import (
    ATRULE_DESCRIPTORS,
    ATRULE_PRELUDE_SYNTAXES,
    PROPERTY_SYNTAXES,
    VALUE_SYNTAXES,
)
from stixis._generated.types import FontFaceDescriptors
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.primitives import serialize_dashed_ident, serialize_ident
from stixis.syntax import (
    ValueNode,
    compile_value_syntax,
    evaluate_canonicalize,
    evaluate_serialize_value,
    render_css_template,
    tokenize_css,
    validate_token_stream_safety,
)


@lru_cache(maxsize=4096)
def lookup_value_node(name: str, is_property: bool = False) -> ValueNode | None:
    """Resolve a `<type>` or `<'property'>` name from the generated `@webref/css` tables."""
    if is_property:
        syn = PROPERTY_SYNTAXES.get(name)
    else:
        syn = VALUE_SYNTAXES.get(name) or VALUE_SYNTAXES.get(f"<{name.strip('<>')}>")
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


def resolve_target_syntax(
    target: str,
    *,
    at_rule: str | None = None,
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
        syn = VALUE_SYNTAXES.get(raw)
        if syn is not None:
            return raw, None, syn, compile_value_syntax(syn)
        node = compile_value_syntax(raw)
        return raw, None, raw, node

    if raw.startswith("@") and "/" in raw:
        at_part, desc_part = raw.split("/", 1)
        return resolve_target_syntax(desc_part, at_rule=at_part)

    norm_prop = normalize_identifier_key(raw)
    if norm_prop.startswith("--") and len(norm_prop) >= 3:
        return norm_prop, None, "<declaration-value>", compile_value_syntax("<declaration-value>")

    syn = PROPERTY_SYNTAXES.get(norm_prop)
    if syn is not None:
        return norm_prop, None, syn, compile_value_syntax(syn)

    if f"<{norm_prop}>" in VALUE_SYNTAXES:
        tname = f"<{norm_prop}>"
        tsyn = VALUE_SYNTAXES[tname]
        return tname, None, tsyn, compile_value_syntax(tsyn)

    raise StixisValidationError(
        norm_prop,
        "known CSS property or <type>",
        target,
        f"Unknown CSS target: {target!r}",
    )


def serialize_value(
    target: str,
    value: Any,
    *,
    at_rule: str | None = None,
    options: SerializationOptions | None = None,
) -> str:
    """Validate and serialize a structured Python `value` (or PEP 750 `Template`) for `target`."""
    opts = DEFAULT_OPTIONS if options is None else options
    norm_target, norm_at, syntax, node = resolve_target_syntax(target, at_rule=at_rule)
    return evaluate_serialize_value(
        norm_target,
        syntax,
        node,
        value,
        specs_lookup=lookup_value_node,
        at_rule=norm_at,
        options=opts,
    )


def canonicalize(
    target: str,
    css_text: str,
    *,
    at_rule: str | None = None,
    options: SerializationOptions | None = None,
) -> str:
    """Tokenize and validate raw `css_text` against `target`'s `ValueNode` grammar, returning canonical CSSOM text."""
    opts = DEFAULT_OPTIONS if options is None else options
    norm_target, norm_at, syntax, node = resolve_target_syntax(target, at_rule=at_rule)
    return evaluate_canonicalize(
        norm_target,
        syntax,
        node,
        css_text,
        specs_lookup=lookup_value_node,
        at_rule=norm_at,
        options=opts,
    )


@dataclass(frozen=True, slots=True)
class RawDeclaration:
    """Explicit opt-in declaration for custom or vendor-prefixed properties validated with `<declaration-value>`."""

    property: str
    value: Any

    def __post_init__(self) -> None:
        norm = normalize_identifier_key(self.property)
        if not norm or any(ch.isspace() or ch in (":", ";", "{", "}") for ch in norm):
            raise StixisValidationError(self.property, "<ident>", self.property)
        rendered = serialize_value(
            "--raw",
            self.value,
            options=DEFAULT_OPTIONS,
        )
        object.__setattr__(self, "property", norm)
        object.__setattr__(self, "value", rendered)


@dataclass(frozen=True, slots=True)
class CommentNode:
    """A standalone CSS comment (`/* ... */`) validated against comment-close injection."""

    text: str | Template
    _rendered: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        rendered = _validate_and_render_comment(self.text, options=DEFAULT_OPTIONS)
        object.__setattr__(self, "_rendered", rendered)

    def __str__(self) -> str:
        return self._rendered


def _validate_and_render_comment(
    comment: str | Template,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    if isinstance(comment, Template):
        rendered = render_css_template(comment, options=options).strip()
        if rendered.startswith("/*") and rendered.endswith("*/"):
            # Ensure no premature `*/` exists inside the body
            body = rendered[2:-2]
            if "*/" in body:
                body = body.replace("*/", "* /")
                return f"/*{body}*/"
            return rendered
        cleaned = rendered.replace("*/", "* /")
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


def _validate_and_normalize_declarations(
    declarations: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] | None,
    *,
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
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
        (at_rule.strip().lower() if at_rule.strip().startswith("@") else f"@{at_rule.strip().lower()}")
        if at_rule is not None
        else None
    )

    for item in items:
        if isinstance(item, RawDeclaration):
            normalized_pairs.append((item.property, str(item.value)))
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
        if prop in PROPERTY_SYNTAXES:
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
    opts = DEFAULT_OPTIONS if options is None else options
    pairs = _validate_and_normalize_declarations(declarations, at_rule=at_rule, options=opts)
    if minify:
        return ";".join(f"{k}:{v}" for k, v in pairs)
    return " ".join(f"{k}: {v};" for k, v in pairs)


def _validate_selector(selector: str | Sequence[str] | Template) -> str:
    if isinstance(selector, Template):
        sel_str = render_css_template(selector).strip()
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
        raise StixisValidationError("selector", "<selector-list>", selector, "Selector cannot be empty")

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
        if prelude_syntax and "?" not in prelude_syntax and at_name in (
            "@font-palette-values",
            "@property",
            "@keyframes",
            "@counter-style",
            "@color-profile",
            "@position-try",
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


@dataclass(frozen=True, slots=True)
class StyleRule:
    """An immutable CSS qualified style rule (`selector { declarations; nested_rules }`), eagerly validated on construction."""

    selector: str | Sequence[str] | Template
    declarations: Mapping[str, Any] | Sequence[tuple[str, Any] | RawDeclaration] = field(
        default_factory=dict
    )
    rules: Sequence[Rule] = ()
    comment: str | Template | None = None
    options: SerializationOptions | None = None
    _selector_str: str = field(init=False, repr=False)
    _decl_pairs: tuple[tuple[str, str], ...] = field(init=False, repr=False)
    _rules_tuple: tuple[Rule, ...] = field(init=False, repr=False)
    _comment_str: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = DEFAULT_OPTIONS if self.options is None else self.options
        sel_s = _validate_selector(self.selector)
        decls = _validate_and_normalize_declarations(
            self.declarations, at_rule=None, options=opts
        )
        rules_t = _validate_child_rules(self.rules)
        comm_s = (
            _validate_and_render_comment(self.comment, options=opts)
            if self.comment is not None
            else None
        )
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
        if options is not None and options != (self.options or DEFAULT_OPTIONS):
            rebuilt = StyleRule(
                self.selector,
                self.declarations,
                rules=self.rules,
                comment=self.comment,
                options=options,
            )
            return rebuilt.format(minify=minify, indent=indent, options=options)

        pad = "" if minify else ("  " * indent)
        inner_pad = "" if minify else ("  " * (indent + 1))
        lines: list[str] = []
        if self._comment_str and not minify:
            lines.append(f"{pad}{self._comment_str}")

        body_items: list[str] = []
        for k, v in self._decl_pairs:
            if minify:
                body_items.append(f"{k}:{v}")
            else:
                body_items.append(f"{inner_pad}{k}: {v};")

        for child in self._rules_tuple:
            body_items.append(
                _format_rule_node(child, minify=minify, indent=indent + 1, options=eff_opts)
            )

        if minify:
            decl_part = ";".join(f"{k}:{v}" for k, v in self._decl_pairs)
            child_part = "".join(
                _format_rule_node(c, minify=True, indent=0, options=eff_opts)
                for c in self._rules_tuple
            )
            sep = ";" if (decl_part and child_part) else ""
            return f"{self._selector_str}{{{decl_part}{sep}{child_part}}}"

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
    _at_name: str = field(init=False, repr=False)
    _prelude_str: str = field(init=False, repr=False)
    _decl_pairs: tuple[tuple[str, str], ...] = field(init=False, repr=False)
    _rules_tuple: tuple[Rule, ...] = field(init=False, repr=False)
    _comment_str: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = DEFAULT_OPTIONS if self.options is None else self.options
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
        )
        rules_t = _validate_child_rules(self.rules)
        comm_s = (
            _validate_and_render_comment(self.comment, options=opts)
            if self.comment is not None
            else None
        )

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
        if options is not None and options != (self.options or DEFAULT_OPTIONS):
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


def _validate_child_rules(rules: Sequence[Rule] | Iterable[Rule]) -> tuple[Rule, ...]:
    out: list[Rule] = []
    for r in rules:
        if isinstance(r, (StyleRule, AtRule, CommentNode)):
            out.append(r)
        elif isinstance(r, (str, Template)):
            out.append(CommentNode(r))
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
    _rules_tuple: tuple[Rule, ...] = field(init=False, repr=False)
    _header_str: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        opts = DEFAULT_OPTIONS if self.options is None else self.options
        rules_t = _validate_child_rules(self.rules)
        hdr_s = (
            _validate_and_render_comment(self.header_comment, options=opts)
            if self.header_comment is not None
            else None
        )
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
            if options is not None and self.header_comment is not None:
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
        if minify:
            return ""
        pad = "  " * indent
        if options is not None:
            return f"{pad}{_validate_and_render_comment(node.text, options=options)}"
        return f"{pad}{node}"
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
