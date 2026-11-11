"""W3C CSS Value Definition Syntax (`css-values-4` §5) AST (`ValueNode`) and compiler."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class ValueNode:
    """Base class for compiled W3C Value Definition Syntax combinator nodes."""


@dataclass(frozen=True, slots=True)
class KeywordNode(ValueNode):
    """Matches a case-insensitive CSS keyword and serializes it in ASCII lowercase."""

    name: str


@dataclass(frozen=True, slots=True)
class DelimNode(ValueNode):
    """Matches a literal delimiter or punctuation token (such as `/`, `,`, `:`, `(`, `)`)."""

    delim: str
    omissible: bool = False


@dataclass(frozen=True, slots=True)
class PrimitiveNode(ValueNode):
    """Matches a primitive or microsyntax type (`<number>`, `<length>`, `<string>`, `<urange>`, etc.)."""

    kind: str  # e.g., "<number>", "<integer>", "<length>", "<percentage>", "<angle>", "<string>", "<url>", "<custom-ident>", "<dashed-ident>", "<ident>", "<urange>", "<opentype-tag>", "<family-name>", "<generic-family>", "<font-src>", "<font-src-list>", "<font-format>", "<font-tech>", "<hex-color>", "<any-value>", "<declaration-value>"
    min_val: float | None = None
    max_val: float | None = None
    min_unit: str = ""
    max_unit: str = ""


@dataclass(frozen=True, slots=True)
class RefNode(ValueNode):
    """References a named value definition (`<color>`, `<font-weight-absolute>`, etc.) or property (`<'font-width'>`)."""

    name: str
    is_property: bool = False
    min_val: float | None = None
    max_val: float | None = None


@dataclass(frozen=True, slots=True)
class FunctionNode(ValueNode):
    """Matches a CSS function `name(arg_node)`."""

    name: str
    arg: ValueNode | None


@dataclass(frozen=True, slots=True)
class JuxtaposeNode(ValueNode):
    """Ordered juxtaposition `A B C`."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class AllOfNode(ValueNode):
    """Unordered conjunction `A && B` (all required, serialized in canonical grammar order)."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class AnyOrderNode(ValueNode):
    """Unordered inclusive-or `A || B` (at least one required, serialized in canonical grammar order)."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class ExclusiveOrNode(ValueNode):
    """Exclusive choice `A | B`."""

    items: tuple[ValueNode, ...]


@dataclass(frozen=True, slots=True)
class MultiplierNode(ValueNode):
    """Multiplied node (`?`, `*`, `+`, `{min,max}`, `#`, `#{min,max}`)."""

    child: ValueNode
    min_count: int
    max_count: int | None
    comma_separated: bool = False


@dataclass(frozen=True, slots=True)
class NonEmptyGroupNode(ValueNode):
    """Required non-empty group `[ ... ]!`."""

    child: ValueNode


_BUILTIN_PRIMITIVES: frozenset[str] = frozenset(
    {
        "<ident>",
        "<custom-ident>",
        "<dashed-ident>",
        "<string>",
        "<url>",
        "<url-token>",
        "<number>",
        "<integer>",
        "<percentage>",
        "<dimension>",
        "<zero>",
        "<length>",
        "<angle>",
        "<time>",
        "<frequency>",
        "<resolution>",
        "<flex>",
        "<decibel>",
        "<semitones>",
        "<length-percentage>",
        "<angle-percentage>",
        "<time-percentage>",
        "<frequency-percentage>",
        "<unicode-range-token>",
        "<urange>",
        "<opentype-tag>",
        "<feature-tag-value>",
        "<family-name>",
        "<font-family-name>",
        "<generic-family>",
        "<generic-font-family>",
        "<font-src>",
        "<font-src-list>",
        "<font-format>",
        "<font-tech>",
        "<hex-color>",
        "<declaration-value>",
        "<any-value>",
        "<ident-token>",
        "<function-token>",
        "<at-keyword-token>",
        "<hash-token>",
        "<string-token>",
        "<delim-token>",
        "<number-token>",
        "<percentage-token>",
        "<dimension-token>",
        "<whitespace-token>",
        "<colon-token>",
        "<semicolon-token>",
        "<comma-token>",
        "<[-token>",
        "<]-token>",
        "<(-token>",
        "<)-token>",
        "<{-token>",
        "<}-token>",
        "<an+b>",
        "<a-n-plus-b>",
        "<signless-integer>",
        "<signed-integer>",
        "<n-dimension>",
        "<ndash-dimension>",
        "<ndashdigit-dimension>",
        "<ndashdigit-ident>",
        "<dashndashdigit-ident>",
        "<declaration-list>",
        "<rule-list>",
        "<block-contents>",
        "<qualified-rule-list>",
        "<at-rule-list>",
        "<declaration-rule-list>",
    }
)

SUPPORTED_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "<ident>",
        "<ident-token>",
        "<custom-ident>",
        "<dashed-ident>",
        "<string>",
        "<string-token>",
        "<url>",
        "<url-token>",
        "<number>",
        "<integer>",
        "<signless-integer>",
        "<signed-integer>",
        "<percentage>",
        "<dimension>",
        "<zero>",
        "<length>",
        "<angle>",
        "<time>",
        "<frequency>",
        "<resolution>",
        "<flex>",
        "<decibel>",
        "<semitones>",
        "<length-percentage>",
        "<angle-percentage>",
        "<time-percentage>",
        "<frequency-percentage>",
        "<unicode-range-token>",
        "<urange>",
        "<opentype-tag>",
        "<feature-tag-value>",
        "<family-name>",
        "<font-family-name>",
        "<generic-family>",
        "<generic-font-family>",
        "<font-src>",
        "<font-src-list>",
        "<font-format>",
        "<font-tech>",
        "<hex-color>",
        "<declaration-value>",
        "<any-value>",
    }
)

_BOUND_VAL_RE = re.compile(r"^([+-]?(?:∞|infinity|\d+(?:\.\d+)?))([a-zA-Z%]*)$", re.IGNORECASE)


def _parse_bound(raw: str) -> tuple[float | None, str]:
    s = raw.strip()
    if s in ("-∞", "-infinity", "-inf"):
        return None, ""
    if s in ("∞", "+∞", "infinity", "+infinity", "inf", "+inf"):
        return None, ""
    m = _BOUND_VAL_RE.match(s)
    if not m:
        return None, ""
    return float(m.group(1)), m.group(2)


def _tokenize_vds(syntax: str) -> list[tuple[str, str]]:
    """Tokenize a W3C Value Definition Syntax string."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    n = len(syntax)

    while pos < n:
        ch = syntax[pos]
        if ch.isspace():
            pos += 1
            continue

        # Multi-character combinators
        if syntax[pos : pos + 2] == "||":
            tokens.append(("||", "||"))
            pos += 2
            continue
        if syntax[pos : pos + 2] == "&&":
            tokens.append(("&&", "&&"))
            pos += 2
            continue
        if ch == "|":
            tokens.append(("|", "|"))
            pos += 1
            continue

        # Quoted literal token: '+' or '<' or ','
        if ch == "'":
            end = syntax.find("'", pos + 1)
            if end != -1:
                lit = syntax[pos + 1 : end]
                tokens.append(("QUOTED_LITERAL", lit))
                pos = end + 1
                continue

        # Non-terminal `<...>` (tracking nested `<...>` and single-quoted `'<prop>'` spans)
        if ch == "<":
            scan = pos + 1
            angle_depth = 1
            in_quote = False
            end = -1
            while scan < n:
                sc = syntax[scan]
                if sc == "'":
                    in_quote = not in_quote
                elif not in_quote:
                    if sc == "<":
                        angle_depth += 1
                    elif sc == ">":
                        angle_depth -= 1
                        if angle_depth == 0:
                            end = scan
                            break
                scan += 1
            if end != -1:
                inner = syntax[pos : end + 1]
                if inner == "<function-token>":
                    tokens.append(("FUNC_START", "<function-token>"))
                else:
                    tokens.append(("NONTERM", inner))
                pos = end + 1
                continue

        # Grouping brackets `[` and `]`
        if ch == "[":
            tokens.append(("[", "["))
            pos += 1
            continue
        if ch == "]":
            tokens.append(("]", "]"))
            pos += 1
            continue

        # Braces `{A,B}` range multiplier vs literal `{` / `}`
        if ch == "{":
            end = syntax.find("}", pos + 1)
            if end != -1 and re.match(
                r"^\{\s*\d+\s*(?:,\s*(?:\d+|∞|inf|infinity)?\s*)?\}$",
                syntax[pos : end + 1],
                re.IGNORECASE,
            ):
                tokens.append(("RANGE_MULT", syntax[pos : end + 1]))
                pos = end + 1
                continue
            tokens.append(("LITERAL", "{"))
            pos += 1
            continue
        if ch == "}":
            tokens.append(("LITERAL", "}"))
            pos += 1
            continue

        # Multipliers and `!`
        if ch in ("?", "*", "+", "#", "!"):
            tokens.append(("MULT_CHAR", ch))
            pos += 1
            continue

        # Punctuation literals inside grammar (`,`, `/`, `:`, `;`, `(`, `)`)
        if ch in (",", "/", ":", ";", "(", ")", "="):
            tokens.append(("LITERAL", ch))
            pos += 1
            continue

        # Keyword or function start `fn(`
        start = pos
        while (
            pos < n
            and not syntax[pos].isspace()
            and syntax[pos]
            not in (
                "|",
                "&",
                "[",
                "]",
                "{",
                "}",
                "?",
                "*",
                "+",
                "#",
                "!",
                ",",
                "/",
                ":",
                ";",
                "(",
                ")",
                "<",
                ">",
                "'",
            )
        ):
            # Allow `+` or `-` inside keywords like `<an+b>` or `-webkit-` or `n-`
            pos += 1
        if pos == start:
            tokens.append(("LITERAL", syntax[pos]))
            pos += 1
            continue
        word = syntax[start:pos]
        if pos < n and syntax[pos] == "(":
            pos += 1
            tokens.append(("FUNC_START", word))
        else:
            tokens.append(("KEYWORD", word))

    return tokens


class _VdsParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_expr(self) -> ValueNode:
        return self.parse_exclusive_or()

    def parse_exclusive_or(self) -> ValueNode:
        items = [self.parse_any_order()]
        while (tok := self.peek()) is not None and tok[0] == "|":
            self.consume()
            items.append(self.parse_any_order())
        if len(items) == 1:
            return items[0]
        return ExclusiveOrNode(tuple(items))

    def parse_any_order(self) -> ValueNode:
        items = [self.parse_all_of()]
        while (tok := self.peek()) is not None and tok[0] == "||":
            self.consume()
            items.append(self.parse_all_of())
        if len(items) == 1:
            return items[0]
        return AnyOrderNode(tuple(items))

    def parse_all_of(self) -> ValueNode:
        items = [self.parse_juxtapose()]
        while (tok := self.peek()) is not None and tok[0] == "&&":
            self.consume()
            items.append(self.parse_juxtapose())
        if len(items) == 1:
            return items[0]
        return AllOfNode(tuple(items))

    def parse_juxtapose(self) -> ValueNode:
        items: list[ValueNode] = []
        while (tok := self.peek()) is not None:
            if tok[0] in ("|", "||", "&&", "]"):
                break
            if tok[0] == "LITERAL" and tok[1] == ")":
                break
            items.append(self.parse_multiplied())
        if not items:
            return KeywordNode("")
        if len(items) == 1:
            return items[0]
        return JuxtaposeNode(tuple(items))

    def parse_multiplied(self) -> ValueNode:
        node = self.parse_primary()
        while (tok := self.peek()) is not None and tok[0] in ("MULT_CHAR", "RANGE_MULT"):
            kind, val = self.consume()
            if kind == "MULT_CHAR":
                if val == "!":
                    node = NonEmptyGroupNode(node)
                elif val == "?":
                    node = MultiplierNode(node, 0, 1, comma_separated=False)
                elif val == "*":
                    node = MultiplierNode(node, 0, None, comma_separated=False)
                elif val == "+":
                    node = MultiplierNode(node, 1, None, comma_separated=False)
                elif val == "#":
                    # Check if followed by {A,B} or ?
                    nxt = self.peek()
                    if nxt is not None and nxt[0] == "RANGE_MULT":
                        _, r_str = self.consume()
                        lo, hi = _parse_range_mult(r_str)
                        node = MultiplierNode(node, lo, hi, comma_separated=True)
                    elif nxt is not None and nxt == ("MULT_CHAR", "?"):
                        self.consume()
                        node = MultiplierNode(node, 0, None, comma_separated=True)
                    else:
                        node = MultiplierNode(node, 1, None, comma_separated=True)
            elif kind == "RANGE_MULT":
                lo, hi = _parse_range_mult(val)
                node = MultiplierNode(node, lo, hi, comma_separated=False)
        return node

    def parse_primary(self) -> ValueNode:
        tok = self.peek()
        if tok is None:
            return KeywordNode("")
        kind, val = self.consume()

        if kind == "[":
            inner = self.parse_expr()
            if self.peek() == ("]", "]"):
                self.consume()
            return inner

        if kind == "FUNC_START":
            if self.peek() == ("LITERAL", ")"):
                self.consume()
                return FunctionNode(val, None)
            inner = self.parse_expr()
            if self.peek() == ("LITERAL", ")"):
                self.consume()
            return FunctionNode(val, inner)

        if kind == "LITERAL" and val == "(":
            if self.peek() == ("LITERAL", ")"):
                self.consume()
                return JuxtaposeNode((DelimNode("("), DelimNode(")")))
            inner = self.parse_expr()
            if self.peek() == ("LITERAL", ")"):
                self.consume()
            return JuxtaposeNode((DelimNode("("), inner, DelimNode(")")))

        if kind == "NONTERM":
            return _parse_nonterminal_token(val)

        if kind == "QUOTED_LITERAL":
            return DelimNode(val, omissible=False)

        if kind == "LITERAL":
            return DelimNode(val, omissible=(val == ","))

        if kind == "KEYWORD":
            return KeywordNode(val)

        return KeywordNode(val)


def _parse_range_mult(r_str: str) -> tuple[int, int | None]:
    inner = r_str[1:-1].strip()
    if "," not in inner:
        n = int(inner)
        return n, n
    left, right = inner.split(",", 1)
    lo = int(left.strip())
    right_s = right.strip()
    hi = None if right_s in ("", "∞", "inf", "infinity") else int(right_s)
    return lo, hi


def _find_top_level_open_bracket(inner: str) -> int:
    """Find `[` outside single quotes and nested `<...>` inside a `<...>` non-terminal body."""
    angle_depth = 0
    in_quote = False
    for idx, ch in enumerate(inner):
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "<":
                angle_depth += 1
            elif ch == ">":
                angle_depth = max(0, angle_depth - 1)
            elif ch == "[" and angle_depth == 0:
                return idx
    return -1


def _parse_nonterminal_token(raw: str) -> ValueNode:
    inner = raw[1:-1].strip()
    whole_type = f"<{inner}>"
    if whole_type in _BUILTIN_PRIMITIVES:
        if whole_type == "<signless-integer>":
            return PrimitiveNode("<integer>", min_val=0, max_val=None)
        if whole_type == "<signed-integer>":
            return PrimitiveNode("<integer>", min_val=None, max_val=None)
        return PrimitiveNode(whole_type)

    min_val: float | None = None
    max_val: float | None = None
    min_unit = ""
    max_unit = ""

    b_idx = _find_top_level_open_bracket(inner)
    if b_idx != -1 and inner.endswith("]"):
        bracket = inner[b_idx + 1 : -1].strip()
        base = inner[:b_idx].strip()
        if "<" not in bracket and "," in bracket:
            lo_s, hi_s = bracket.split(",", 1)
            min_val, min_unit = _parse_bound(lo_s)
            max_val, max_unit = _parse_bound(hi_s)
            inner = base
        else:
            return RefNode(f"<{base}>", is_property=False)

    if inner.startswith("'") and inner.endswith("'"):
        prop_name = inner[1:-1].strip()
        return RefNode(prop_name, is_property=True, min_val=min_val, max_val=max_val)

    type_name = f"<{inner}>"
    if type_name in _BUILTIN_PRIMITIVES:
        if type_name == "<signless-integer>":
            return PrimitiveNode("<integer>", min_val=0, max_val=max_val)
        if type_name == "<signed-integer>":
            return PrimitiveNode("<integer>", min_val=min_val, max_val=max_val)
        return PrimitiveNode(
            type_name,
            min_val=min_val,
            max_val=max_val,
            min_unit=min_unit,
            max_unit=max_unit,
        )

    return RefNode(type_name, is_property=False, min_val=min_val, max_val=max_val)


@lru_cache(maxsize=4096)
def compile_value_syntax(syntax: str) -> ValueNode:
    """Compile a W3C Value Definition Syntax string into an immutable `ValueNode` tree."""
    tokens = _tokenize_vds(syntax)
    parser = _VdsParser(tokens)
    return parser.parse_expr()


def _is_omissible_comma(node: ValueNode) -> bool:
    """Return True if `node` is an unquoted VDS `,` subject to CSS Values 4 §2.2.1 implicit comma elision."""
    return isinstance(node, DelimNode) and node.delim == "," and node.omissible


def _split_juxtapose_omissible_commas(
    items: tuple[ValueNode, ...] | list[ValueNode],
) -> tuple[ValueNode, ...] | None:
    """Partition `items` at CSS Values 4 §2.2.1 omissible `,` separators between non-empty clauses, or return `None`."""
    if len(items) < 3:
        return None
    if _is_omissible_comma(items[0]) or _is_omissible_comma(items[-1]):
        return None
    if not any(_is_omissible_comma(it) for it in items[1:-1]):
        return None
    clauses: list[ValueNode] = []
    current: list[ValueNode] = []
    for it in items:
        if _is_omissible_comma(it):
            if not current:
                return None
            clauses.append(current[0] if len(current) == 1 else JuxtaposeNode(tuple(current)))
            current = []
        else:
            current.append(it)
    if not current or not clauses:
        return None
    clauses.append(current[0] if len(current) == 1 else JuxtaposeNode(tuple(current)))
    return tuple(clauses)


def _is_node_nullable(
    node: ValueNode,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    visited: set[tuple[str, bool]] | None = None,
) -> bool:
    """Return True if `node` can match zero tokens (`""`) in `_TokenMatcher`."""
    if visited is None:
        visited = set()
    if isinstance(node, KeywordNode):
        return node.name == ""
    if isinstance(
        node, (DelimNode, PrimitiveNode, FunctionNode, NonEmptyGroupNode, AllOfNode, AnyOrderNode)
    ):
        return False
    if isinstance(node, MultiplierNode):
        return node.min_count == 0 or _is_node_nullable(node.child, specs_lookup, visited)
    if isinstance(node, ExclusiveOrNode):
        return any(_is_node_nullable(b, specs_lookup, visited) for b in node.items)
    if isinstance(node, JuxtaposeNode):
        clauses = _split_juxtapose_omissible_commas(node.items)
        seq = clauses if clauses is not None else node.items
        return all(_is_node_nullable(c, specs_lookup, visited) for c in seq)
    if isinstance(node, RefNode):
        if (node.is_property and node.name == "font-style") or (
            not node.is_property and node.name == "<calc-sum>"
        ):
            return False
        key = (node.name, node.is_property)
        if key in visited:
            return False
        visited.add(key)
        resolved = specs_lookup(node.name, node.is_property)
        return resolved is not None and _is_node_nullable(resolved, specs_lookup, visited)
    return False


def _collect_grammar_keywords(
    node: ValueNode,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    visited: set[tuple[str, bool]] | None = None,
) -> frozenset[str]:
    """Collect all explicit `KeywordNode` names in a grammar tree to exclude them from `<custom-ident>` when needed."""
    if visited is None:
        visited = set()
    if isinstance(node, KeywordNode):
        return frozenset({node.name.lower()}) if node.name else frozenset()
    if isinstance(node, (JuxtaposeNode, AllOfNode, AnyOrderNode, ExclusiveOrNode)):
        out: set[str] = set()
        for child in node.items:
            out.update(_collect_grammar_keywords(child, specs_lookup, visited))
        return frozenset(out)
    if isinstance(node, (MultiplierNode, NonEmptyGroupNode)):
        return _collect_grammar_keywords(node.child, specs_lookup, visited)
    if isinstance(node, RefNode):
        key = (node.name, node.is_property)
        if key in visited:
            return frozenset()
        visited.add(key)
        resolved = specs_lookup(node.name, node.is_property)
        if resolved is not None:
            return _collect_grammar_keywords(resolved, specs_lookup, visited)
    return frozenset()


def _collect_grammar_calc_functions(
    node: ValueNode,
    specs_lookup: Callable[[str, bool], ValueNode | None],
    visited: set[tuple[str, bool]] | None = None,
) -> frozenset[str]:
    """Collect `anchor` and `anchor-size` when referenced in `node`'s grammar tree so math expressions allow them."""
    if visited is None:
        visited = set()
    if isinstance(node, FunctionNode):
        fn_low = node.name.lower()
        base = frozenset({fn_low}) if fn_low in ("anchor", "anchor-size") else frozenset()
        if node.arg is not None:
            return base | _collect_grammar_calc_functions(node.arg, specs_lookup, visited)
        return base
    if isinstance(node, (JuxtaposeNode, AllOfNode, AnyOrderNode, ExclusiveOrNode)):
        out: set[str] = set()
        for child in node.items:
            out.update(_collect_grammar_calc_functions(child, specs_lookup, visited))
        return frozenset(out)
    if isinstance(node, (MultiplierNode, NonEmptyGroupNode)):
        return _collect_grammar_calc_functions(node.child, specs_lookup, visited)
    if isinstance(node, RefNode):
        out_ref: set[str] = set()
        if not node.is_property and node.name in ("<anchor()>", "anchor()"):
            out_ref.add("anchor")
        elif not node.is_property and node.name in ("<anchor-size()>", "anchor-size()"):
            out_ref.add("anchor-size")
        key = (node.name, node.is_property)
        if key in visited:
            return frozenset(out_ref)
        visited.add(key)
        resolved = specs_lookup(node.name, node.is_property)
        if resolved is not None:
            out_ref.update(_collect_grammar_calc_functions(resolved, specs_lookup, visited))
        return frozenset(out_ref)
    return frozenset()
