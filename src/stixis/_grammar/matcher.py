"""Token-stream matcher (`_TokenMatcher`) and structured Python-value evaluator against `ValueNode`."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from string.templatelib import Template
from typing import Any

from stixis._generated.units import LOWER_TO_CANONICAL_UNIT, UNITS_BY_TYPE
from stixis._grammar.calc import (
    _consume_balanced_group,
    _contains_global_substitution,
    _is_math_function_token,
    _parse_calc_sum,
)
from stixis._grammar.color import (
    extract_relative_color_rest_node,
    narrow_predefined_color_param_channels,
)
from stixis._grammar.vds import (
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
    _collect_grammar_calc_functions,
    _collect_grammar_keywords,
    _is_node_nullable,
    _split_juxtapose_omissible_commas,
)
from stixis._syntax.serialize import (
    CSS_WIDE_KEYWORDS,
    RESERVED_CUSTOM_IDENT_KEYWORDS,
    _serialize_dimension_unit,
    format_token_sequence,
    serialize_custom_ident,
    serialize_dashed_ident,
    serialize_dimension,
    serialize_ident,
    serialize_integer,
    serialize_number,
    serialize_percentage,
    serialize_string,
    serialize_url,
)
from stixis._syntax.template import render_css_template
from stixis._syntax.tokenizer import (
    CssToken,
    tokenize_css,
    validate_token_stream_safety,
)
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.microsyntax.family_name import (
    GENERIC_FAMILY_KEYWORDS,
    GENERIC_SCRIPT_KEYWORDS,
    serialize_family_name,
    serialize_font_family_list,
    serialize_generic_family,
)
from stixis.microsyntax.font_src import (
    FONT_TECH_KEYWORDS,
    FontSrc,
    serialize_font_format,
    serialize_font_src_list,
    serialize_font_tech,
    serialize_single_font_src,
)
from stixis.microsyntax.opentype import (
    serialize_font_feature_settings,
    serialize_font_language_override,
    serialize_font_variation_settings,
    serialize_opentype_tag,
)
from stixis.microsyntax.ranges import (
    angle_to_deg,
    serialize_font_style,
    serialize_font_weight,
    serialize_font_width,
    serialize_metric_override,
)
from stixis.microsyntax.urange import (
    consume_urange_tokens,
    format_single_urange,
    format_urange,
    serialize_single_urange,
)
from stixis.options import DEFAULT_OPTIONS, SerializationOptions
from stixis.values import (
    Dimension,
    GenericFamily,
    Keyword,
    LocalFont,
    Percentage,
    Url,
)


def _is_property_target(target: str, at_rule: str | None) -> bool:
    """Return True if `target` is a CSS property (standard or custom `--*`), not a `<type>`, descriptor, or prelude."""
    if at_rule is not None:
        return False
    return not target.startswith(("<", "@"))


class _TokenMatcher:
    """Backtracking matcher and CSSOM canonicalizer for `CssToken` streams against `ValueNode`."""

    def __init__(
        self,
        tokens: Sequence[CssToken],
        specs_lookup: Callable[[str, bool], ValueNode | None],
        options: SerializationOptions,
        forbidden_custom_idents: frozenset[str] = frozenset(),
        raw_text: str = "",
        allow_css_wide_in_multiword_family: bool = False,
        calc_context_keywords: frozenset[str] = frozenset(),
        calc_context_functions: frozenset[str] = frozenset(),
    ) -> None:
        self.tokens = [t for t in tokens if t.kind != "whitespace"]
        self.specs_lookup = specs_lookup
        self.options = options
        self.forbidden_custom_idents = forbidden_custom_idents
        self.raw_text = raw_text
        self.allow_css_wide_in_multiword_family = allow_css_wide_in_multiword_family
        self.calc_context_keywords = calc_context_keywords
        self.calc_context_functions = calc_context_functions
        self._active_refs: set[tuple[str, bool, int]] = set()

    def match_root(self, node: ValueNode, *, allow_css_wide: bool = True) -> str | None:
        if allow_css_wide and len(self.tokens) == 1 and self.tokens[0].kind == "ident":
            kw_low = self.tokens[0].value.lower()
            if kw_low in CSS_WIDE_KEYWORDS:
                return kw_low

        if _contains_global_substitution(self.tokens):
            validate_token_stream_safety(
                self.raw_text or format_token_sequence(self.tokens, options=self.options),
                forbid_top_level_bang=True,
                forbid_top_level_semicolon=True,
            )
            return format_token_sequence(self.tokens, options=self.options)

        for pos, rendered in self.match_node(node, 0):
            if pos == len(self.tokens) and rendered != "":
                return rendered
        return None

    def match_node(
        self,
        node: ValueNode,
        pos: int,
        min_override: float | None = None,
        max_override: float | None = None,
    ) -> list[tuple[int, str]]:
        n_toks = len(self.tokens)

        if isinstance(node, KeywordNode):
            if not node.name:
                return [(pos, "")]
            if (
                pos < n_toks
                and self.tokens[pos].kind == "ident"
                and self.tokens[pos].value.lower() == node.name.lower()
            ):
                return [(pos + 1, node.name.lower())]
            return []

        if isinstance(node, DelimNode):
            if pos >= n_toks:
                return []
            tok = self.tokens[pos]
            if node.delim == "," and tok.kind == "comma":
                return [(pos + 1, ",")]
            if node.delim == ":" and tok.kind == "colon":
                return [(pos + 1, ":")]
            if node.delim == ";" and tok.kind == "semicolon":
                return [(pos + 1, ";")]
            if node.delim in ("(", ")", "[", "]", "{", "}") and tok.kind == node.delim:
                return [(pos + 1, node.delim)]
            if tok.kind == "delim" and tok.value == node.delim:
                return [(pos + 1, node.delim)]
            return []

        if isinstance(node, PrimitiveNode):
            return self._match_primitive(node, pos, min_override, max_override)

        if isinstance(node, RefNode):
            return self._match_ref(node, pos, min_override, max_override)

        if isinstance(node, FunctionNode):
            return self._match_function(node, pos)

        if isinstance(node, NonEmptyGroupNode):
            results = self.match_node(node.child, pos, min_override, max_override)
            return [(p, s) for p, s in results if p > pos and s != ""]

        if isinstance(node, ExclusiveOrNode):
            out: list[tuple[int, str]] = []
            seen: set[tuple[int, str]] = set()
            for branch in node.items:
                for res in self.match_node(branch, pos, min_override, max_override):
                    if res not in seen:
                        seen.add(res)
                        out.append(res)
            return out

        if isinstance(node, JuxtaposeNode):
            return self._match_juxtapose(node.items, pos, min_override, max_override)

        if isinstance(node, AllOfNode):
            return self._match_unordered(
                node.items,
                pos,
                require_all=True,
                min_override=min_override,
                max_override=max_override,
            )

        if isinstance(node, AnyOrderNode):
            return self._match_unordered(
                node.items,
                pos,
                require_all=False,
                min_override=min_override,
                max_override=max_override,
            )

        if isinstance(node, MultiplierNode):
            return self._match_multiplier(node, pos, min_override, max_override)

        return []

    def _match_ref(
        self,
        node: RefNode,
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        eff_min = node.min_val if node.min_val is not None else min_override
        eff_max = node.max_val if node.max_val is not None else max_override

        # Special microsyntax fast-paths for font properties when referenced via `<'prop'>`
        if node.is_property and node.name == "font-style":
            res = self._match_font_style_tokens(pos, is_descriptor=False)
            if res:
                return res

        if not node.is_property and node.name == "<calc-sum>":
            calc_res = _parse_calc_sum(
                self.tokens,
                pos,
                extra_keywords=self.calc_context_keywords,
                extra_functions=self.calc_context_functions,
                specs_lookup=self.specs_lookup,
                options=self.options,
            )
            if calc_res is not None:
                rendered_sum, next_pos = calc_res
                return [(next_pos, rendered_sum)]
            return []

        guard_key = (node.name, node.is_property, pos)
        if guard_key in self._active_refs:
            return []
        target_node = self.specs_lookup(node.name, node.is_property)
        if target_node is None:
            return []
        self._active_refs.add(guard_key)
        saved_calc_keywords = self.calc_context_keywords
        narrowed_channels = (
            narrow_predefined_color_param_channels(node.name, saved_calc_keywords)
            if not node.is_property
            else None
        )
        if narrowed_channels is not None:
            self.calc_context_keywords = narrowed_channels
            try:
                matched_params = self.match_node(target_node, pos, eff_min, eff_max)
                if not matched_params:
                    self.calc_context_keywords = saved_calc_keywords
                return matched_params
            except Exception:
                self.calc_context_keywords = saved_calc_keywords
                raise
            finally:
                self._active_refs.discard(guard_key)
        try:
            return self.match_node(target_node, pos, eff_min, eff_max)
        finally:
            self._active_refs.discard(guard_key)

    def _match_function(self, node: FunctionNode, pos: int) -> list[tuple[int, str]]:
        if pos >= len(self.tokens) or self.tokens[pos].kind != "function":
            return []
        tok = self.tokens[pos]
        if node.name != "<function-token>" and tok.value.lower() != node.name.lower():
            return []
        grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
        if grouped is None:
            return []
        inner_tokens, next_pos = grouped
        fn_name = serialize_ident(tok.value.lower())

        if node.arg is None:
            if not inner_tokens:
                return [(next_pos, f"{fn_name}()")]
            return []

        rel_color = extract_relative_color_rest_node(fn_name, inner_tokens, node)
        if rel_color is not None:
            from_node, rest_node, channel_kws = rel_color
            origin_matcher = _TokenMatcher(
                inner_tokens,
                self.specs_lookup,
                self.options,
                self.forbidden_custom_idents,
                calc_context_keywords=self.calc_context_keywords,
                calc_context_functions=self.calc_context_functions,
            )
            channel_matcher = _TokenMatcher(
                inner_tokens,
                self.specs_lookup,
                self.options,
                self.forbidden_custom_idents,
                calc_context_keywords=self.calc_context_keywords | channel_kws,
                calc_context_functions=self.calc_context_functions,
            )
            results: list[tuple[int, str]] = []
            seen: set[tuple[int, str]] = set()
            for after_origin, origin_str in origin_matcher.match_node(from_node, 0):
                for end_p, rest_str in channel_matcher.match_node(rest_node, after_origin):
                    if end_p == len(channel_matcher.tokens):
                        joined = _join_serialized_parts([origin_str, rest_str])
                        pair = (next_pos, f"{fn_name}({joined})")
                        if pair not in seen:
                            seen.add(pair)
                            results.append(pair)
            return results

        sub_matcher = _TokenMatcher(
            inner_tokens,
            self.specs_lookup,
            self.options,
            self.forbidden_custom_idents,
            calc_context_keywords=self.calc_context_keywords,
            calc_context_functions=self.calc_context_functions,
        )
        results = []
        for end_p, rendered in sub_matcher.match_node(node.arg, 0):
            if end_p == len(sub_matcher.tokens):
                results.append((next_pos, f"{fn_name}({rendered})"))
        return results

    def _match_juxtapose(
        self,
        items: Sequence[ValueNode],
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        clauses = _split_juxtapose_omissible_commas(tuple(items))
        if clauses is not None:
            c_states: list[tuple[int, list[str]]] = [(pos, [])]
            for clause in clauses:
                c_next: list[tuple[int, list[str]]] = []
                c_seen: set[tuple[int, tuple[str, ...]]] = set()
                clause_nullable = _is_node_nullable(clause, self.specs_lookup)
                for cur_pos, rendered_clauses in c_states:
                    if not rendered_clauses:
                        for nxt_pos, rendered in self.match_node(
                            clause, cur_pos, min_override, max_override
                        ):
                            if rendered == "" and nxt_pos == cur_pos:
                                key = (cur_pos, ())
                                if key not in c_seen:
                                    c_seen.add(key)
                                    c_next.append((cur_pos, []))
                            elif nxt_pos > cur_pos and rendered != "":
                                key = (nxt_pos, (rendered,))
                                if key not in c_seen:
                                    c_seen.add(key)
                                    c_next.append((nxt_pos, [rendered]))
                    else:
                        if clause_nullable:
                            key = (cur_pos, tuple(rendered_clauses))
                            if key not in c_seen:
                                c_seen.add(key)
                                c_next.append((cur_pos, rendered_clauses))
                        if cur_pos < len(self.tokens) and self.tokens[cur_pos].kind == "comma":
                            for nxt_pos, rendered in self.match_node(
                                clause, cur_pos + 1, min_override, max_override
                            ):
                                if nxt_pos > cur_pos + 1 and rendered != "":
                                    new_clauses = [*rendered_clauses, rendered]
                                    key = (nxt_pos, tuple(new_clauses))
                                    if key not in c_seen:
                                        c_seen.add(key)
                                        c_next.append((nxt_pos, new_clauses))
                c_states = c_next
                if not c_states:
                    return []
            return [(end_pos, ", ".join(rc)) for end_pos, rc in c_states]

        states: list[tuple[int, list[str]]] = [(pos, [])]
        for child in items:
            next_states: list[tuple[int, list[str]]] = []
            seen: set[tuple[int, tuple[str, ...]]] = set()
            for cur_pos, parts in states:
                for nxt_pos, rendered in self.match_node(
                    child, cur_pos, min_override, max_override
                ):
                    new_parts = parts + ([rendered] if rendered != "" else [])
                    key = (nxt_pos, tuple(new_parts))
                    if key not in seen:
                        seen.add(key)
                        next_states.append((nxt_pos, new_parts))
            states = next_states
            if not states:
                return []

        out: list[tuple[int, str]] = []
        for end_pos, parts in states:
            joined = _join_serialized_parts(parts)
            out.append((end_pos, joined))
        return out

    def _match_unordered(
        self,
        items: Sequence[ValueNode],
        pos: int,
        *,
        require_all: bool,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        k = len(items)
        required_indices = {
            idx
            for idx, b in enumerate(items)
            if not (isinstance(b, MultiplierNode) and b.min_count == 0)
        }
        # States: (cur_pos, matched_dict {branch_idx: rendered_str})
        results: list[tuple[int, dict[int, str]]] = []
        seen_states: set[tuple[int, tuple[tuple[int, str], ...]]] = set()

        def dfs(cur_pos: int, matched: dict[int, str]) -> None:
            state_key = (cur_pos, tuple(sorted(matched.items())))
            if state_key in seen_states:
                return
            seen_states.add(state_key)

            if (
                require_all and required_indices.issubset(matched.keys()) and len(matched) >= 1
            ) or (not require_all and len(matched) >= 1):
                results.append((cur_pos, dict(matched)))
                if len(matched) == k:
                    return

            for idx, branch in enumerate(items):
                if idx in matched:
                    continue
                for nxt_pos, rendered in self.match_node(
                    branch, cur_pos, min_override, max_override
                ):
                    if nxt_pos > cur_pos and rendered != "":
                        matched[idx] = rendered
                        dfs(nxt_pos, matched)
                        del matched[idx]

        dfs(pos, {})

        # Sort results to prefer longer token matches first
        out: list[tuple[int, str]] = []
        seen_out: set[tuple[int, str]] = set()
        for end_pos, matched in sorted(results, key=lambda x: -x[0]):
            # Assemble in canonical grammar definition order (0 .. k-1)!
            canon_parts = [matched[i] for i in range(k) if i in matched and matched[i] != ""]
            joined = _join_serialized_parts(canon_parts)
            pair = (end_pos, joined)
            if pair not in seen_out:
                seen_out.add(pair)
                out.append(pair)
        return out

    def _match_multiplier(
        self,
        node: MultiplierNode,
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        # Fast path for `?` (0 or 1)
        if not node.comma_separated and node.min_count == 0 and node.max_count == 1:
            child_matches = self.match_node(node.child, pos, min_override, max_override)
            out = [(p, s) for p, s in child_matches if p > pos]
            out.append((pos, ""))
            return out

        results: list[tuple[int, list[str]]] = []
        if node.min_count == 0:
            results.append((pos, []))

        # BFS/DFS over occurrences
        queue: list[tuple[int, list[str]]] = [(pos, [])]
        seen: set[tuple[int, tuple[str, ...]]] = set()
        seen_comma: set[tuple[int, int]] = set()

        while queue:
            cur_pos, acc = queue.pop()
            count = len(acc)
            if node.max_count is not None and count >= node.max_count:
                continue

            next_start = cur_pos
            if count > 0 and node.comma_separated:
                if next_start >= len(self.tokens) or self.tokens[next_start].kind != "comma":
                    continue
                next_start += 1

            child_matches = self.match_node(node.child, next_start, min_override, max_override)
            seen_nxt_pos: set[int] = set()
            for nxt_pos, rendered in child_matches:
                if nxt_pos == next_start:
                    continue
                if node.comma_separated and count > 0:
                    if nxt_pos in seen_nxt_pos:
                        continue
                    seen_nxt_pos.add(nxt_pos)
                    ckey = (nxt_pos, count + 1)
                    if ckey in seen_comma:
                        continue
                    seen_comma.add(ckey)
                else:
                    key = (nxt_pos, tuple(acc + [rendered]))
                    if key in seen:
                        continue
                    seen.add(key)
                new_acc = acc + [rendered]
                if len(new_acc) >= node.min_count:
                    results.append((nxt_pos, new_acc))
                queue.append((nxt_pos, new_acc))

        sep = ", " if node.comma_separated else " "
        out_pairs: list[tuple[int, str]] = []
        seen_pairs: set[tuple[int, str]] = set()
        for end_pos, items in sorted(results, key=lambda x: -x[0]):
            joined = sep.join(s for s in items if s != "")
            pair = (end_pos, joined)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                out_pairs.append(pair)
        return out_pairs

    def _match_font_style_tokens(self, pos: int, *, is_descriptor: bool) -> list[tuple[int, str]]:
        if pos >= len(self.tokens) or self.tokens[pos].kind != "ident":
            return []
        kw = self.tokens[pos].value.lower()
        if kw in ("normal", "italic", "left", "right"):
            return [(pos + 1, kw)]
        if is_descriptor and kw == "auto":
            return [(pos + 1, "auto")]
        if kw == "oblique":
            results: list[tuple[int, str]] = [(pos + 1, "oblique")]
            angle_prim = PrimitiveNode(
                "<angle>", min_val=-90.0, max_val=90.0, min_unit="deg", max_unit="deg"
            )
            for p1, a1 in self._match_primitive(angle_prim, pos + 1, -90.0, 90.0):
                if not is_descriptor and self.options.normalize_zero_oblique and a1 == "0deg":
                    results.insert(0, (p1, "normal"))
                else:
                    results.insert(0, (p1, f"oblique {a1}"))
                if is_descriptor:
                    for p2, a2 in self._match_primitive(angle_prim, p1, -90.0, 90.0):
                        results.insert(0, (p2, f"oblique {a1} {a2}"))
            return results
        return []

    def _match_primitive(
        self,
        node: PrimitiveNode,
        pos: int,
        min_override: float | None,
        max_override: float | None,
    ) -> list[tuple[int, str]]:
        kind = node.kind
        eff_min = node.min_val if node.min_val is not None else min_override
        eff_max = node.max_val if node.max_val is not None else max_override

        if kind in ("<declaration-value>", "<any-value>"):
            if pos >= len(self.tokens):
                return []
            rest = self.tokens[pos:]
            return [(len(self.tokens), format_token_sequence(rest, options=self.options))]

        if kind in ("<unicode-range-token>", "<urange>"):
            consumed = consume_urange_tokens(self.tokens, pos)
            if consumed is not None:
                (start, end), next_pos = consumed
                return [(next_pos, format_single_urange(start, end, options=self.options))]
            return []

        if pos >= len(self.tokens):
            return []

        tok = self.tokens[pos]

        # Numeric & dimension math function support (`calc(...)`, `min(...)`, `max(...)`, `clamp(...)`, etc.)
        if kind in (
            "<number>",
            "<integer>",
            "<percentage>",
            "<dimension>",
            "<length>",
            "<angle>",
            "<time>",
            "<frequency>",
            "<resolution>",
            "<flex>",
            "<length-percentage>",
            "<angle-percentage>",
            "<time-percentage>",
            "<frequency-percentage>",
        ):
            math_res = _is_math_function_token(
                self.tokens,
                pos,
                extra_keywords=self.calc_context_keywords,
                extra_functions=self.calc_context_functions,
                specs_lookup=self.specs_lookup,
                options=self.options,
            )
            if math_res is not None:
                rendered_math, next_pos = math_res
                return [(next_pos, rendered_math)]
            if (
                kind in ("<length>", "<length-percentage>")
                and tok.kind == "function"
                and tok.value.lower() in self.calc_context_functions
                and self.specs_lookup is not None
            ):
                fn_low = tok.value.lower()
                fn_node = self.specs_lookup(f"<{fn_low}()>", False)
                if fn_node is not None:
                    return self.match_node(fn_node, pos)

        if kind == "<zero>":
            if tok.kind == "number" and tok.num == 0:
                return [(pos + 1, "0")]
            return []

        if kind == "<integer>":
            if (
                tok.kind == "number"
                and tok.is_integer
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                return [(pos + 1, str(int(tok.num)))]
            return []

        if kind == "<number>":
            if (
                tok.kind == "number"
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, serialize_number(val))]
            if tok.kind == "ident" and tok.value.lower() in self.calc_context_keywords:
                return [(pos + 1, tok.value.lower())]
            return []

        if kind == "<percentage>":
            if (
                tok.kind == "percentage"
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, f"{serialize_number(val)}%")]
            return []

        if kind in (
            "<length>",
            "<angle>",
            "<time>",
            "<frequency>",
            "<resolution>",
            "<flex>",
            "<decibel>",
            "<semitones>",
        ):
            allowed_units = UNITS_BY_TYPE.get(kind, frozenset())
            lower_map = LOWER_TO_CANONICAL_UNIT.get(kind, {})
            if kind == "<length>" and tok.kind == "number" and tok.num == 0:
                if (eff_min is None or 0 >= eff_min) and (eff_max is None or 0 <= eff_max):
                    return [(pos + 1, "0px")]
                return []
            if tok.kind == "dimension" and tok.num is not None and math.isfinite(tok.num):
                canon_u = lower_map.get(tok.unit.lower())
                if canon_u is None or canon_u not in allowed_units:
                    return []
                check_val = tok.num
                if kind == "<angle>" and (node.min_unit or node.max_unit):
                    check_val = angle_to_deg(tok.num, canon_u)
                if eff_min is not None and check_val < eff_min - 1e-9:
                    return []
                if eff_max is not None and check_val > eff_max + 1e-9:
                    return []
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, f"{serialize_number(val)}{canon_u}")]
            return []

        if kind == "<dimension>":
            if (
                tok.kind == "dimension"
                and tok.num is not None
                and math.isfinite(tok.num)
                and (eff_min is None or tok.num >= eff_min)
                and (eff_max is None or tok.num <= eff_max)
            ):
                val = int(tok.num) if tok.is_integer else tok.num
                return [(pos + 1, f"{serialize_number(val)}{_serialize_dimension_unit(tok.unit)}")]
            return []

        if kind in (
            "<length-percentage>",
            "<angle-percentage>",
            "<time-percentage>",
            "<frequency-percentage>",
        ):
            base_kind = f"<{kind[1:].split('-')[0]}>"
            dim_matches = self._match_primitive(
                PrimitiveNode(base_kind, eff_min, eff_max, node.min_unit, node.max_unit),
                pos,
                eff_min,
                eff_max,
            )
            pct_matches = self._match_primitive(
                PrimitiveNode("<percentage>", eff_min, eff_max),
                pos,
                eff_min,
                eff_max,
            )
            return dim_matches + pct_matches

        if kind in ("<string>", "<string-token>"):
            if tok.kind == "string":
                return [(pos + 1, serialize_string(tok.value, options=self.options))]
            return []

        if kind == "<opentype-tag>":
            if tok.kind == "string":
                try:
                    return [(pos + 1, serialize_opentype_tag(tok.value, options=self.options))]
                except StixisValidationError:
                    return []
            return []

        if kind == "<feature-tag-value>":
            if tok.kind == "string":
                try:
                    tag_s = serialize_opentype_tag(tok.value, options=self.options)
                except StixisValidationError:
                    return []
                results: list[tuple[int, str]] = [(pos + 1, tag_s)]
                if pos + 1 < len(self.tokens):
                    nxt = self.tokens[pos + 1]
                    if nxt.kind == "ident":
                        low = nxt.value.lower()
                        if low == "on":
                            results.insert(0, (pos + 2, tag_s))
                        elif low == "off":
                            results.insert(0, (pos + 2, f"{tag_s} 0"))
                    elif (
                        nxt.kind == "number"
                        and nxt.is_integer
                        and nxt.num is not None
                        and nxt.num >= 0
                    ):
                        iv = int(nxt.num)
                        if iv == 1:
                            results.insert(0, (pos + 2, tag_s))
                        else:
                            results.insert(0, (pos + 2, f"{tag_s} {iv}"))
                return results
            return []

        if kind in ("<url>", "<url-token>"):
            if tok.kind == "url":
                return [(pos + 1, serialize_url(tok.value, options=self.options))]
            if tok.kind == "function" and tok.value.lower() in ("url", "src"):
                grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
                if grouped is not None:
                    inner_toks, next_pos = grouped
                    if len(inner_toks) == 1 and inner_toks[0].kind == "string":
                        fn_low = tok.value.lower()
                        str_s = serialize_string(inner_toks[0].value, options=self.options)
                        return [(next_pos, f"{fn_low}({str_s})")]
            return []

        if kind in ("<ident>", "<ident-token>"):
            if tok.kind == "ident":
                return [(pos + 1, serialize_ident(tok.value))]
            return []

        if kind == "<dashed-ident>":
            if tok.kind == "ident" and tok.value.startswith("--") and len(tok.value) >= 2:
                return [(pos + 1, serialize_dashed_ident(tok.value))]
            return []

        if kind == "<custom-ident>":
            if tok.kind == "ident":
                low = tok.value.lower()
                if low in RESERVED_CUSTOM_IDENT_KEYWORDS or low in self.forbidden_custom_idents:
                    return []
                return [(pos + 1, serialize_ident(tok.value))]
            return []

        if kind in ("<generic-family>", "<generic-font-family>"):
            if tok.kind == "ident":
                low = tok.value.lower()
                if low in GENERIC_FAMILY_KEYWORDS or (
                    low.startswith("-webkit-") and low[8:] in GENERIC_FAMILY_KEYWORDS
                ):
                    return [(pos + 1, low)]
            elif tok.kind == "function" and tok.value.lower() == "generic":
                grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
                if grouped is not None:
                    inner_toks, next_pos = grouped
                    if len(inner_toks) == 1 and inner_toks[0].kind == "ident":
                        arg_low = inner_toks[0].value.lower()
                        if arg_low in GENERIC_SCRIPT_KEYWORDS:
                            return [(next_pos, f"generic({arg_low})")]
            return []

        if kind in ("<family-name>", "<font-family-name>"):
            if tok.kind == "string":
                return [
                    (
                        pos + 1,
                        serialize_family_name(
                            tok.value, options=self.options, is_quoted_token=True
                        ),
                    )
                ]
            if tok.kind == "ident":
                # Consume one or more consecutive <custom-ident> tokens
                idents: list[str] = []
                cur = pos
                while cur < len(self.tokens) and self.tokens[cur].kind == "ident":
                    cand = self.tokens[cur].value
                    idents.append(cand)
                    cur += 1
                if not idents:
                    return []
                # Single ident cannot be a CSS-wide/reserved keyword or a generic-family keyword
                if len(idents) == 1:
                    low = idents[0].lower()
                    if low in RESERVED_CUSTOM_IDENT_KEYWORDS or low in GENERIC_FAMILY_KEYWORDS:
                        return []
                else:
                    # In a multi-word unquoted `<family-name>` (`<custom-ident>+`),
                    # CSS-wide/reserved keywords are never valid `<custom-ident>`s (except inside `local(default A)`),
                    # and `<generic-family>` keywords are not valid inside `font-family`.
                    if any(w.lower() in GENERIC_FAMILY_KEYWORDS for w in idents):
                        return []
                    if not self.allow_css_wide_in_multiword_family and any(
                        w.lower() in RESERVED_CUSTOM_IDENT_KEYWORDS for w in idents
                    ):
                        return []
                joined_name = " ".join(idents)
                if self.options.always_quote_family_name:
                    return [(cur, serialize_string(joined_name, options=self.options))]
                return [(cur, " ".join(serialize_ident(w) for w in idents))]
            return []

        if kind == "<font-format>":
            if tok.kind == "string":
                try:
                    s = serialize_font_format(tok.value, is_quoted_token=True, options=self.options)
                    # Strip outer `format(` and `)`
                    return [(pos + 1, s[7:-1])]
                except StixisValidationError:
                    return []
            if tok.kind == "ident":
                try:
                    s = serialize_font_format(
                        Keyword(tok.value), is_quoted_token=False, options=self.options
                    )
                    return [(pos + 1, s[7:-1])]
                except StixisValidationError:
                    return []
            return []

        if kind == "<font-tech>":
            if tok.kind == "ident" and tok.value.lower() in FONT_TECH_KEYWORDS:
                return [(pos + 1, tok.value.lower())]
            return []

        if kind in ("<font-src>", "<font-src-list>"):
            return self._match_font_src(pos, is_list=(kind == "<font-src-list>"))

        if kind == "<hex-color>":
            if (
                tok.kind == "hash"
                and len(tok.value) in (3, 4, 6, 8)
                and all(c in "0123456789abcdefABCDEF" for c in tok.value)
            ):
                return [(pos + 1, f"#{tok.value.lower()}")]
            return []

        return []

    def _match_font_src(self, pos: int, *, is_list: bool) -> list[tuple[int, str]]:
        if not is_list:
            return self._match_single_font_src_tokens(pos)
        # Comma-separated list of `<font-src>`
        mult = MultiplierNode(PrimitiveNode("<font-src>"), 1, None, comma_separated=True)
        return self._match_multiplier(mult, pos, None, None)

    def _match_single_font_src_tokens(self, pos: int) -> list[tuple[int, str]]:
        if pos >= len(self.tokens):
            return []
        tok = self.tokens[pos]
        if tok.kind == "function" and tok.value.lower() == "local":
            grouped = _consume_balanced_group(self.tokens, pos + 1, ")")
            if grouped is None:
                return []
            inner_toks, next_pos = grouped
            if not inner_toks:
                return []
            sub = _TokenMatcher(
                inner_toks,
                self.specs_lookup,
                self.options,
                allow_css_wide_in_multiword_family=True,
            )
            fam_matches = sub._match_primitive(PrimitiveNode("<family-name>"), 0, None, None)
            out: list[tuple[int, str]] = []
            for end_p, fam_str in fam_matches:
                if end_p == len(sub.tokens):
                    out.append((next_pos, f"local({fam_str})"))
            return out

        url_matches = self._match_primitive(PrimitiveNode("<url>"), pos, None, None)
        if not url_matches:
            return []

        results: list[tuple[int, str]] = []
        for after_url, url_str in url_matches:
            cur = after_url
            parts = [url_str]

            # Optional `format(<font-format>)`
            if (
                cur < len(self.tokens)
                and self.tokens[cur].kind == "function"
                and self.tokens[cur].value.lower() == "format"
            ):
                grouped = _consume_balanced_group(self.tokens, cur + 1, ")")
                if grouped is None:
                    continue
                fmt_toks, after_fmt = grouped
                if len(fmt_toks) != 1:
                    continue
                sub = _TokenMatcher(fmt_toks, self.specs_lookup, self.options)
                fmt_m = sub._match_primitive(PrimitiveNode("<font-format>"), 0, None, None)
                if not fmt_m or fmt_m[0][0] != 1:
                    continue
                parts.append(f"format({fmt_m[0][1]})")
                cur = after_fmt

            # Optional `tech(<font-tech>#)`
            if (
                cur < len(self.tokens)
                and self.tokens[cur].kind == "function"
                and self.tokens[cur].value.lower() == "tech"
            ):
                grouped = _consume_balanced_group(self.tokens, cur + 1, ")")
                if grouped is None:
                    continue
                tech_toks, after_tech = grouped
                if not tech_toks:
                    continue
                sub = _TokenMatcher(tech_toks, self.specs_lookup, self.options)
                tech_mult = MultiplierNode(
                    PrimitiveNode("<font-tech>"), 1, None, comma_separated=True
                )
                tech_m = sub._match_multiplier(tech_mult, 0, None, None)
                valid_tech = [s for end_p, s in tech_m if end_p == len(sub.tokens)]
                if not valid_tech:
                    continue
                parts.append(f"tech({valid_tech[0]})")
                cur = after_tech

            results.append((cur, " ".join(parts)))
        return results


def _join_serialized_parts(parts: Sequence[str]) -> str:
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if not out:
            out.append(p)
            continue
        prev = out[-1]
        if p in (",", ":", ";", ")", "]") or prev in ("(", "["):
            out.append(p)
        elif p.startswith((",", ":", ";", ")")) and out[-1]:
            out[-1] = out[-1] + p
        else:
            out.append(" " + p)
    return "".join(out)


def _node_matches_single_value(
    node: ValueNode,
    value: Any,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None = None,
    max_override: float | None = None,
    active_refs: set[tuple[str, bool]] | None = None,
) -> str | None:
    """Try to serialize a structured Python `value` directly against `node`, returning `None` if it does not match."""
    if active_refs is None:
        active_refs = set()

    if isinstance(node, KeywordNode):
        if isinstance(value, Keyword):
            raw = value.value
        elif isinstance(value, str):
            raw = value
        else:
            return None
        if raw.strip().lower() == node.name.lower():
            return node.name.lower()
        return None

    if isinstance(node, DelimNode):
        if isinstance(value, str) and value.strip() == node.delim:
            return node.delim
        return None

    if isinstance(node, PrimitiveNode):
        return _serialize_primitive_python_value(
            node,
            value,
            specs_lookup,
            options,
            forbidden_custom_idents,
            min_override,
            max_override,
        )

    if isinstance(node, FunctionNode):
        if (
            isinstance(value, GenericFamily)
            and node.name.lower() == "generic"
            and node.arg is not None
        ):
            rendered_gen = serialize_generic_family(value)
            if rendered_gen.startswith("generic(") and rendered_gen.endswith(")"):
                inner = rendered_gen[8:-1]
                if (
                    _node_matches_single_value(
                        node.arg,
                        Keyword(inner),
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    is not None
                ):
                    return rendered_gen
        return None

    if isinstance(node, RefNode):
        eff_min = node.min_val if node.min_val is not None else min_override
        eff_max = node.max_val if node.max_val is not None else max_override
        key = (node.name, node.is_property)
        if key in active_refs:
            return None
        target_node = specs_lookup(node.name, node.is_property)
        if target_node is None:
            return None
        active_refs.add(key)
        try:
            return _node_matches_single_value(
                target_node,
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                eff_min,
                eff_max,
                active_refs,
            )
        finally:
            active_refs.discard(key)

    if isinstance(node, NonEmptyGroupNode):
        res = _node_matches_single_value(
            node.child,
            value,
            specs_lookup,
            options,
            forbidden_custom_idents,
            min_override,
            max_override,
            active_refs,
        )
        return res if res else None

    if isinstance(node, ExclusiveOrNode):
        # If `value` is a plain `str` (and not `Keyword`/`GenericFamily`) and one of the branches
        # is `<string>` or `<family-name>` / `<font-family-name>`, check whether it matches a keyword first
        # ONLY when neither `<family-name>` nor `<font-family-name>` is a branch!
        has_family_branch = any(
            isinstance(b, PrimitiveNode)
            and b.kind in ("<family-name>", "<font-family-name>")
            or (isinstance(b, RefNode) and b.name in ("<family-name>", "<font-family-name>"))
            for b in node.items
        )
        if isinstance(value, str) and has_family_branch:
            for branch in node.items:
                if (
                    isinstance(branch, PrimitiveNode)
                    and branch.kind in ("<family-name>", "<font-family-name>")
                ) or (
                    isinstance(branch, RefNode)
                    and branch.name in ("<family-name>", "<font-family-name>")
                ):
                    res = _node_matches_single_value(
                        branch,
                        value,
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    if res is not None:
                        return res

        for branch in node.items:
            res = _node_matches_single_value(
                branch,
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                min_override,
                max_override,
                active_refs,
            )
            if res is not None:
                return res
        return None

    if isinstance(node, MultiplierNode):
        # A single item is valid if min_count <= 1
        if node.min_count <= 1:
            single = _node_matches_single_value(
                node.child,
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                min_override,
                max_override,
                active_refs,
            )
            if single is not None:
                return single
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            count = len(value)
            if count < node.min_count:
                return None
            if node.max_count is not None and count > node.max_count:
                return None
            rendered_items: list[str] = []
            for elem in value:
                r = _node_matches_single_value(
                    node.child,
                    elem,
                    specs_lookup,
                    options,
                    forbidden_custom_idents,
                    min_override,
                    max_override,
                    active_refs,
                )
                if r is None:
                    return None
                rendered_items.append(r)
            sep = ", " if node.comma_separated else " "
            return sep.join(rendered_items)
        return None

    if isinstance(node, JuxtaposeNode):
        clauses = _split_juxtapose_omissible_commas(node.items)
        if clauses is not None:
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                matched_clauses = _match_clauses_subsequence(
                    clauses,
                    value,
                    specs_lookup=specs_lookup,
                    options=options,
                    forbidden_custom_idents=forbidden_custom_idents,
                    min_override=min_override,
                    max_override=max_override,
                    active_refs=active_refs,
                )
                if matched_clauses is not None:
                    return matched_clauses
                return _match_sequence_via_tokens(
                    node, value, specs_lookup, options, forbidden_custom_idents
                )
            nullable = [_is_node_nullable(c, specs_lookup) for c in clauses]
            for idx, clause in enumerate(clauses):
                if all(nullable[j] for j in range(len(clauses)) if j != idx):
                    r = _node_matches_single_value(
                        clause,
                        value,
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    if r:
                        return r
            return None

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            # Try matching sequence items via positional matching, nullable single-child, or token stream
            if len(value) == len(node.items):
                parts: list[str] = []
                ok = True
                for sub_node, sub_val in zip(node.items, value, strict=True):
                    r = _node_matches_single_value(
                        sub_node,
                        sub_val,
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    if r is None:
                        ok = False
                        break
                    parts.append(r)
                if ok:
                    return _join_serialized_parts(parts)
            nullable_seq_items = [_is_node_nullable(c, specs_lookup) for c in node.items]
            for idx, item in enumerate(node.items):
                if all(nullable_seq_items[j] for j in range(len(node.items)) if j != idx):
                    r = _node_matches_single_value(
                        item,
                        value,
                        specs_lookup,
                        options,
                        forbidden_custom_idents,
                        min_override,
                        max_override,
                        active_refs,
                    )
                    if r:
                        return r
            return _match_sequence_via_tokens(
                node, value, specs_lookup, options, forbidden_custom_idents
            )
        nullable_items = [_is_node_nullable(c, specs_lookup) for c in node.items]
        for idx, item in enumerate(node.items):
            if all(nullable_items[j] for j in range(len(node.items)) if j != idx):
                r = _node_matches_single_value(
                    item,
                    value,
                    specs_lookup,
                    options,
                    forbidden_custom_idents,
                    min_override,
                    max_override,
                    active_refs,
                )
                if r:
                    return r
        return None

    if isinstance(node, (AllOfNode, AnyOrderNode)):
        require_all = isinstance(node, AllOfNode)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            res = _match_unordered_python_items(
                node.items,
                list(value),
                require_all=require_all,
                specs_lookup=specs_lookup,
                options=options,
                forbidden_custom_idents=forbidden_custom_idents,
                min_override=min_override,
                max_override=max_override,
            )
            if res is not None:
                return res
            return _match_sequence_via_tokens(
                node, value, specs_lookup, options, forbidden_custom_idents
            )
        if not require_all:
            # Single item matching one branch of `A || B`
            for branch in node.items:
                r = _node_matches_single_value(
                    branch,
                    value,
                    specs_lookup,
                    options,
                    forbidden_custom_idents,
                    min_override,
                    max_override,
                    active_refs,
                )
                if r is not None:
                    return r
        return None

    return None


def _match_unordered_python_items(
    branches: Sequence[ValueNode],
    values: Sequence[Any],
    *,
    require_all: bool,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None,
    max_override: float | None,
) -> str | None:
    if not values:
        return None
    k = len(branches)
    if len(values) > k:
        return None
    required_indices = {
        idx for idx, b in enumerate(branches) if not _is_node_nullable(b, specs_lookup)
    }
    if require_all and len(values) < len(required_indices):
        return None

    # Find assignment of `values` to distinct branch indices `0..k-1`
    matched: dict[int, str] = {}

    def backtrack(v_idx: int) -> bool:
        if v_idx == len(values):
            return not (require_all and not required_indices.issubset(matched.keys()))
        val = values[v_idx]
        for b_idx, branch in enumerate(branches):
            if b_idx in matched:
                continue
            r = _node_matches_single_value(
                branch,
                val,
                specs_lookup,
                options,
                forbidden_custom_idents,
                min_override,
                max_override,
            )
            if r is not None:
                matched[b_idx] = r
                if backtrack(v_idx + 1):
                    return True
                del matched[b_idx]
        return False

    if not backtrack(0):
        return None
    return _join_serialized_parts([matched[i] for i in range(k) if i in matched])


def _match_clauses_subsequence(
    clauses: tuple[ValueNode, ...],
    values: Sequence[Any],
    *,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None,
    max_override: float | None,
    active_refs: set[tuple[str, bool]],
) -> str | None:
    if not values or len(values) > len(clauses):
        return None
    nullable = [_is_node_nullable(c, specs_lookup) for c in clauses]

    def dfs(c_idx: int, v_idx: int) -> list[str] | None:
        if v_idx == len(values):
            return [] if all(nullable[j] for j in range(c_idx, len(clauses))) else None
        if c_idx == len(clauses):
            return None
        r = _node_matches_single_value(
            clauses[c_idx],
            values[v_idx],
            specs_lookup,
            options,
            forbidden_custom_idents,
            min_override,
            max_override,
            active_refs,
        )
        if r:
            rest = dfs(c_idx + 1, v_idx + 1)
            if rest is not None:
                return [r, *rest]
        if nullable[c_idx]:
            return dfs(c_idx + 1, v_idx)
        return None

    matched = dfs(0, 0)
    return ", ".join(matched) if matched else None


def _value_item_to_css_fragment(item: Any, options: SerializationOptions) -> str | None:
    if isinstance(item, Template):
        return render_css_template(item, options=options)
    if isinstance(item, Url):
        return serialize_url(item, options=options)
    if isinstance(item, LocalFont):
        return serialize_single_font_src(item, options=options)
    if isinstance(item, (Keyword, GenericFamily, Dimension, Percentage)):
        return str(item)
    if not isinstance(item, bool) and isinstance(item, (int, float)):
        return serialize_number(item)
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and not isinstance(item[0], bool)
        and isinstance(item[0], (int, float))
        and isinstance(item[1], str)
    ):
        return f"{serialize_number(item[0])}{item[1]}"
    if isinstance(item, str):
        return item
    return None


def _match_sequence_via_tokens(
    node: ValueNode,
    seq: Sequence[Any],
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
) -> str | None:
    fragments: list[str] = []
    for elem in seq:
        frag = _value_item_to_css_fragment(elem, options)
        if frag is None:
            return None
        fragments.append(frag)
    joined = " ".join(fragments)
    try:
        tokens = tokenize_css(joined)
    except StixisSyntaxError:
        return None
    matcher = _TokenMatcher(
        tokens,
        specs_lookup,
        options,
        forbidden_custom_idents,
        raw_text=joined,
        calc_context_functions=_collect_grammar_calc_functions(node, specs_lookup),
    )
    return matcher.match_root(node, allow_css_wide=False)


def _serialize_primitive_python_value(
    node: PrimitiveNode,
    value: Any,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    options: SerializationOptions,
    forbidden_custom_idents: frozenset[str],
    min_override: float | None,
    max_override: float | None,
) -> str | None:
    kind = node.kind
    eff_min = node.min_val if node.min_val is not None else min_override
    eff_max = node.max_val if node.max_val is not None else max_override

    try:
        if kind == "<zero>":
            if not isinstance(value, bool) and isinstance(value, (int, float)) and value == 0:
                return "0"
            return None

        if kind == "<integer>":
            return serialize_integer(value, min_val=eff_min, max_val=eff_max)

        if kind == "<number>":
            return serialize_number(value, min_val=eff_min, max_val=eff_max)

        if kind == "<percentage>":
            return serialize_percentage(value, min_val=eff_min, max_val=eff_max)

        if kind in (
            "<length>",
            "<angle>",
            "<time>",
            "<frequency>",
            "<resolution>",
            "<flex>",
            "<decibel>",
            "<semitones>",
        ):
            allowed = UNITS_BY_TYPE.get(kind, frozenset())
            lower_map = LOWER_TO_CANONICAL_UNIT.get(kind, {})
            if kind == "<angle>" and (node.min_unit or node.max_unit):
                if isinstance(value, Dimension):
                    deg = angle_to_deg(float(value.value), value.unit)
                elif (
                    isinstance(value, tuple)
                    and len(value) == 2
                    and not isinstance(value[0], bool)
                    and isinstance(value[0], (int, float))
                    and isinstance(value[1], str)
                ):
                    deg = angle_to_deg(float(value[0]), value[1])
                else:
                    return None
                if eff_min is not None and deg < eff_min - 1e-9:
                    return None
                if eff_max is not None and deg > eff_max + 1e-9:
                    return None
                return serialize_dimension(
                    value,
                    allowed_units=allowed,
                    lower_to_canonical=lower_map,
                    allow_unitless_zero=False,
                )
            return serialize_dimension(
                value,
                allowed_units=allowed,
                lower_to_canonical=lower_map,
                allow_unitless_zero=(kind == "<length>"),
                min_val=eff_min,
                max_val=eff_max,
            )

        if kind in (
            "<length-percentage>",
            "<angle-percentage>",
            "<time-percentage>",
            "<frequency-percentage>",
        ):
            base_kind = f"<{kind[1:].split('-')[0]}>"
            if isinstance(value, Percentage) or (
                isinstance(value, tuple) and len(value) == 2 and value[1] == "%"
            ):
                return serialize_percentage(value, min_val=eff_min, max_val=eff_max)
            return _serialize_primitive_python_value(
                PrimitiveNode(base_kind, eff_min, eff_max, node.min_unit, node.max_unit),
                value,
                specs_lookup,
                options,
                forbidden_custom_idents,
                eff_min,
                eff_max,
            )

        if kind in ("<string>", "<string-token>"):
            if isinstance(value, str):
                return serialize_string(value, options=options)
            return None

        if kind == "<opentype-tag>":
            if isinstance(value, str):
                return serialize_opentype_tag(value, options=options)
            return None

        if kind in ("<url>", "<url-token>"):
            if isinstance(value, Url):
                return serialize_url(value, options=options)
            return None

        if kind in ("<ident>", "<ident-token>"):
            if isinstance(value, Keyword):
                return serialize_ident(value.value)
            if isinstance(value, str):
                return serialize_ident(value)
            return None

        if kind == "<custom-ident>":
            raw = value.value if isinstance(value, Keyword) else value
            if isinstance(raw, str):
                return serialize_custom_ident(raw, forbidden_keywords=forbidden_custom_idents)
            return None

        if kind == "<dashed-ident>":
            raw = value.value if isinstance(value, Keyword) else value
            if isinstance(raw, str):
                return serialize_dashed_ident(raw)
            return None

        if kind in ("<family-name>", "<font-family-name>"):
            if isinstance(value, str):
                return serialize_family_name(value, options=options, is_quoted_token=True)
            return None

        if kind in ("<generic-family>", "<generic-font-family>"):
            if isinstance(value, (GenericFamily, Keyword)):
                return serialize_generic_family(value)
            return None

        if kind in ("<unicode-range-token>", "<urange>"):
            return serialize_single_urange(value, options=options, target=kind)

        if kind == "<font-src>":
            return serialize_single_font_src(value, options=options)

        if kind == "<font-src-list>":
            return serialize_font_src_list(value, options=options)

        if kind == "<font-format>":
            s = serialize_font_format(value, options=options)
            return s[7:-1]

        if kind == "<font-tech>":
            s = serialize_font_tech(value)
            return s[5:-1]

        if kind in ("<declaration-value>", "<any-value>"):
            if isinstance(value, Template):
                rendered = render_css_template(value, options=options)
                validate_token_stream_safety(
                    rendered,
                    forbid_top_level_semicolon=(kind == "<declaration-value>"),
                    forbid_top_level_bang=(kind == "<declaration-value>"),
                    target=kind,
                )
                return rendered.strip()
            if isinstance(value, str):
                validate_token_stream_safety(
                    value,
                    forbid_top_level_semicolon=(kind == "<declaration-value>"),
                    forbid_top_level_bang=(kind == "<declaration-value>"),
                    target=kind,
                )
                return value.strip()
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                return serialize_number(value)
            if isinstance(
                value, (Dimension, Percentage, Keyword, GenericFamily, Url, LocalFont, FontSrc)
            ):
                if isinstance(value, Url) and type(value).__str__ is Url.__str__:
                    rendered = serialize_url(value, options=options)
                elif isinstance(value, LocalFont) and type(value).__str__ is LocalFont.__str__:
                    rendered = f"local({serialize_string(value.family, options=options)})"
                elif isinstance(value, FontSrc):
                    rendered = serialize_single_font_src(value, options=options, target=kind)
                else:
                    rendered = str(value)
                validate_token_stream_safety(
                    rendered,
                    forbid_top_level_semicolon=(kind == "<declaration-value>"),
                    forbid_top_level_bang=(kind == "<declaration-value>"),
                    target=kind,
                )
                return rendered
            return None

        if kind == "<dimension>":
            if isinstance(value, Dimension):
                return f"{serialize_number(value.value, min_val=eff_min, max_val=eff_max)}{_serialize_dimension_unit(value.unit)}"
            if (
                isinstance(value, tuple)
                and len(value) == 2
                and not isinstance(value[0], bool)
                and isinstance(value[0], (int, float))
                and isinstance(value[1], str)
            ):
                dim = Dimension(value[0], value[1])
                return f"{serialize_number(dim.value, min_val=eff_min, max_val=eff_max)}{_serialize_dimension_unit(dim.unit)}"
            return None

        if kind == "<hex-color>":
            raw = value.value if isinstance(value, Keyword) else value
            if isinstance(raw, str):
                s = raw.strip()
                if (
                    s.startswith("#")
                    and len(s[1:]) in (3, 4, 6, 8)
                    and all(c in "0123456789abcdefABCDEF" for c in s[1:])
                ):
                    return f"#{s[1:].lower()}"
            return None

    except StixisValidationError:
        return None

    return None


def evaluate_canonicalize(
    target: str,
    syntax: str,
    node: ValueNode,
    css_text: str,
    *,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Validate raw `css_text` against `node` and return its canonical CSSOM string."""
    if not isinstance(css_text, str):
        raise StixisValidationError(target, syntax, css_text)
    is_custom_prop = target.startswith("--") and len(target) >= 3
    if not css_text.strip() and not is_custom_prop:
        raise StixisValidationError(target, syntax, css_text)

    # Dedicated microsyntax fast-paths for exact WPT conformance
    if target == "unicode-range":
        return format_urange(css_text, options=options)
    if target in ("<urange>", "<unicode-range-token>"):
        return serialize_single_urange(css_text, options=options, target=target)

    tokens = validate_token_stream_safety(
        css_text,
        forbid_top_level_semicolon=True,
        forbid_top_level_bang=True,
        forbid_top_level_braces=True,
        allow_empty=is_custom_prop,
        target=target,
    )
    non_ws = [t for t in tokens if t.kind != "whitespace"]
    if not non_ws and is_custom_prop:
        return ""

    if target == "font-style":
        matcher = _TokenMatcher(non_ws, specs_lookup, options, raw_text=css_text)
        if (
            len(non_ws) == 1
            and non_ws[0].kind == "ident"
            and non_ws[0].value.lower() in CSS_WIDE_KEYWORDS
            and at_rule is None
        ):
            return non_ws[0].value.lower()
        try:
            if _contains_global_substitution(non_ws) and at_rule is None:
                return format_token_sequence(non_ws, options=options)
            matches = matcher._match_font_style_tokens(0, is_descriptor=(at_rule == "@font-face"))
        except RecursionError as exc:
            raise StixisValidationError(
                target, syntax, css_text, "Maximum nesting depth exceeded"
            ) from exc
        for end_p, rendered in matches:
            if end_p == len(non_ws):
                return rendered
        raise StixisValidationError(target, syntax, css_text)

    if target == "font-language-override":
        if len(non_ws) == 1 and non_ws[0].kind == "ident":
            kw_low = non_ws[0].value.lower()
            if kw_low in CSS_WIDE_KEYWORDS and at_rule is None:
                return kw_low
            return serialize_font_language_override(
                kw_low, options=options, target=target, is_keyword_token=True
            )
        if len(non_ws) == 1 and non_ws[0].kind == "string":
            return serialize_font_language_override(
                non_ws[0].value, options=options, target=target, is_keyword_token=False
            )
        raise StixisValidationError(target, syntax, css_text)

    forbidden_idents = _collect_grammar_keywords(node, specs_lookup)
    calc_funcs = _collect_grammar_calc_functions(node, specs_lookup)
    matcher = _TokenMatcher(
        non_ws,
        specs_lookup,
        options,
        forbidden_custom_idents=forbidden_idents,
        raw_text=css_text,
        calc_context_functions=calc_funcs,
    )
    if (
        at_rule is not None
        and len(non_ws) == 1
        and non_ws[0].kind == "ident"
        and non_ws[0].value.lower() in RESERVED_CUSTOM_IDENT_KEYWORDS
    ):
        # CSS-wide and reserved keywords (`initial`, `inherit`, `unset`, `revert`, `default`) are NOT valid on @font-face descriptors
        raise StixisValidationError(target, syntax, css_text)

    try:
        res = matcher.match_root(node, allow_css_wide=_is_property_target(target, at_rule))
    except RecursionError as exc:
        raise StixisValidationError(
            target, syntax, css_text, "Maximum nesting depth exceeded"
        ) from exc
    if res is None:
        raise StixisValidationError(target, syntax, css_text)
    if target == "font-size-adjust" and res.startswith("ex-height "):
        res = res[len("ex-height ") :]
    return res


def evaluate_serialize_value(
    target: str,
    syntax: str,
    node: ValueNode,
    value: Any,
    *,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    at_rule: str | None = None,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Validate and serialize a structured Python `value` (or PEP 750 `Template`) against `node`."""
    if isinstance(value, Template):
        rendered_tpl = render_css_template(value, options=options)
        return evaluate_canonicalize(
            target,
            syntax,
            node,
            rendered_tpl,
            specs_lookup=specs_lookup,
            at_rule=at_rule,
            options=options,
        )

    is_font_face = at_rule == "@font-face"

    # Dedicated microsyntax handlers for structured Python inputs
    if target == "unicode-range":
        return format_urange(value, options=options)
    if target in ("<urange>", "<unicode-range-token>"):
        return serialize_single_urange(value, options=options, target=target)

    if target in ("font-family", "<family-name>", "<font-family-name>"):
        if target in ("<family-name>", "<font-family-name>") or is_font_face:
            if isinstance(value, str):
                return serialize_family_name(
                    value, options=options, target=target, is_quoted_token=True
                )
            raise StixisValidationError(target, syntax, value)
        return serialize_font_family_list(value, options=options, target=target)

    if target in ("<generic-family>", "<generic-font-family>"):
        return serialize_generic_family(value, target=target)

    if target == "font-feature-settings" or target == "<feature-tag-value>":
        if isinstance(value, str) and (
            "," in value or " " in value or value.startswith(('"', "'"))
        ):
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_font_feature_settings(value, options=options, target=target)

    if target == "font-variation-settings":
        if isinstance(value, str) and value.strip().lower() != "normal":
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_font_variation_settings(value, options=options, target=target)

    if target == "<opentype-tag>":
        return serialize_opentype_tag(value, options=options, target=target)

    if target == "font-language-override":
        if isinstance(value, str) and value.strip().startswith(('"', "'")):
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        if isinstance(value, str) and value.strip().lower() == "normal":
            return "normal"
        return serialize_font_language_override(value, options=options, target=target)

    if (target == "src" and is_font_face) or target == "<font-src-list>":
        if isinstance(value, str) and (
            value.strip().lower().startswith(("url(", "local(")) or "," in value
        ):
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_font_src_list(value, options=options, target=target)

    if target == "<font-src>":
        if isinstance(value, str) and value.strip().lower().startswith(("url(", "local(")):
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_single_font_src(value, options=options, target=target)

    if target == "font-weight":
        if isinstance(value, str) and (
            " " in value.strip() or value.strip().lower().startswith("calc(")
        ):
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_font_weight(value, is_descriptor=is_font_face, target=target)

    if target in ("font-width", "font-stretch"):
        if isinstance(value, str) and (
            " " in value.strip() or value.strip().lower().startswith("calc(")
        ):
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_font_width(value, is_descriptor=is_font_face, target=target)

    if target == "font-style":
        if isinstance(value, str) and "calc(" in value.lower():
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_font_style(
            value, is_descriptor=is_font_face, options=options, target=target
        )

    if is_font_face and target in ("ascent-override", "descent-override", "line-gap-override"):
        if isinstance(value, str) and " " in value.strip():
            return evaluate_canonicalize(
                target,
                syntax,
                node,
                value,
                specs_lookup=specs_lookup,
                at_rule=at_rule,
                options=options,
            )
        return serialize_metric_override(value, allow_from_font=False, min_val=0.0, target=target)

    if is_font_face and target in ("superscript-size-override", "subscript-size-override"):
        return serialize_metric_override(value, allow_from_font=True, min_val=0.0, target=target)

    if is_font_face and target in ("superscript-position-override", "subscript-position-override"):
        return serialize_metric_override(value, allow_from_font=True, min_val=None, target=target)

    # General ValueNode evaluation
    if _is_property_target(target, at_rule) and isinstance(value, (str, Keyword)):
        raw_kw = value.value if isinstance(value, Keyword) else value
        if raw_kw.strip().lower() in CSS_WIDE_KEYWORDS:
            return raw_kw.strip().lower()

    forbidden_idents = _collect_grammar_keywords(node, specs_lookup)
    try:
        direct = _node_matches_single_value(node, value, specs_lookup, options, forbidden_idents)
    except RecursionError as exc:
        raise StixisValidationError(
            target, syntax, value, "Maximum nesting depth exceeded"
        ) from exc
    if direct is not None:
        return direct

    # Fallback: if `value` is a CSS string expression (e.g. `"10px 20px"` or `"calc(100% - 20px)"`),
    # canonicalize it through the token matcher!
    if isinstance(value, str):
        return evaluate_canonicalize(
            target,
            syntax,
            node,
            value,
            specs_lookup=specs_lookup,
            at_rule=at_rule,
            options=options,
        )

    raise StixisValidationError(target, syntax, value)
