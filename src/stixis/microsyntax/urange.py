"""`unicode-range` (`<urange>`) parser, formatter, and cascade shadowing compaction (`css-syntax-3` §8)."""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Sequence
from typing import Any

from stixis._syntax.tokenizer import CssToken, tokenize_css
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

MAX_CODEPOINT = 0x10FFFF

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{1,6}$")
_WILDCARD_RE = re.compile(r"^([0-9A-Fa-f]{0,5})(\?{1,6})$")


def _parse_urange_body(
    body: str,
    raw_item: Any,
    *,
    target: str = "unicode-range",
    syntax: str = "<urange>#",
) -> tuple[int, int]:
    """Validate the post-`U+` body of a single `<urange>` item (`css-syntax-3` §8.1)."""
    if not body or any(ch.isspace() for ch in body):
        raise StixisValidationError(target, syntax, raw_item)

    if "-" in body:
        # Range syntax: U+start-end (no '?' allowed)
        parts = body.split("-")
        if len(parts) != 2:
            raise StixisValidationError(target, syntax, raw_item)
        start_hex, end_hex = parts
        if not (_HEX_RE.match(start_hex) and _HEX_RE.match(end_hex)):
            raise StixisValidationError(target, syntax, raw_item)
        start = int(start_hex, 16)
        end = int(end_hex, 16)
    elif "?" in body:
        m = _WILDCARD_RE.match(body)
        if not m:
            raise StixisValidationError(target, syntax, raw_item)
        prefix, qmarks = m.group(1), m.group(2)
        total_len = len(prefix) + len(qmarks)
        if not (1 <= total_len <= 6):
            raise StixisValidationError(target, syntax, raw_item)
        start = int(prefix + ("0" * len(qmarks)), 16)
        end = int(prefix + ("F" * len(qmarks)), 16)
    else:
        if not _HEX_RE.match(body):
            raise StixisValidationError(target, syntax, raw_item)
        start = end = int(body, 16)

    if start < 0 or end < start or end > MAX_CODEPOINT:
        raise StixisValidationError(target, syntax, raw_item)

    return (start, end)


def consume_urange_tokens(
    tokens: Sequence[CssToken],
    pos: int,
) -> tuple[tuple[int, int], int] | None:
    """Consume a single `<urange>` token sequence at `pos` (`css-syntax-3` §8.1)."""
    n = len(tokens)
    if pos + 1 >= n:
        return None
    lead = tokens[pos]
    if lead.kind != "ident" or lead.value.lower() != "u":
        return None

    t1 = tokens[pos + 1]
    if t1.ws_before:
        return None

    cur = pos + 2
    body_parts: list[str] = []
    allow_qmarks = False

    if t1.kind == "delim" and t1.value == "+":
        if cur < n and not tokens[cur].ws_before and tokens[cur].kind == "ident":
            body_parts.append(tokens[cur].value)
            cur += 1
        allow_qmarks = True
    elif t1.kind == "dimension" and t1.value.startswith("+"):
        body_parts.append(t1.value[1:] + t1.unit)
        allow_qmarks = True
    elif t1.kind == "number" and t1.value.startswith("+"):
        body_parts.append(t1.value[1:])
        if cur < n and not tokens[cur].ws_before:
            t2 = tokens[cur]
            if t2.kind == "number" and t2.value.startswith("-"):
                body_parts.append(t2.value)
                cur += 1
            elif t2.kind == "dimension" and t2.value.startswith("-"):
                body_parts.append(t2.value + t2.unit)
                cur += 1
            else:
                allow_qmarks = True
        else:
            allow_qmarks = True
    else:
        return None

    if allow_qmarks:
        while (
            cur < n
            and not tokens[cur].ws_before
            and tokens[cur].kind == "delim"
            and tokens[cur].value == "?"
        ):
            body_parts.append("?")
            cur += 1

    body = "".join(body_parts)
    if not body:
        return None
    try:
        interval = _parse_urange_body(body, body, target="<urange>", syntax="<urange>")
    except StixisValidationError:
        return None
    return interval, cur


def parse_single_urange(
    raw_item: str,
    *,
    target: str = "<urange>",
    syntax: str = "<urange>",
) -> tuple[int, int]:
    """Parse a single `<urange>` item according to CSS Syntax Level 3 §8 and WPT `urange-parsing.html`."""
    if not isinstance(raw_item, str) or not raw_item.strip():
        raise StixisValidationError(target, syntax, raw_item)
    try:
        tokens = tokenize_css(raw_item)
    except StixisSyntaxError as exc:
        raise StixisValidationError(target, syntax, raw_item, str(exc)) from exc
    if not tokens:
        raise StixisValidationError(target, syntax, raw_item)
    consumed = consume_urange_tokens(tokens, 0)
    if consumed is None or consumed[1] != len(tokens):
        raise StixisValidationError(target, syntax, raw_item)
    return consumed[0]


def format_single_urange(
    start: int,
    end: int,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Format a validated `(start, end)` inclusive code point interval into canonical `U+XXXX` or `U+XXXX-YYYY`."""
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end < start
        or end > MAX_CODEPOINT
    ):
        raise StixisValidationError("unicode-range", "<urange>#", (start, end))

    prefix = "U+" if options.uppercase_urange_prefix else "u+"
    if start == end:
        return f"{prefix}{start:X}"
    return f"{prefix}{start:X}-{end:X}"


def serialize_single_urange(
    value: Any,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    target: str = "<urange>",
) -> str:
    """Validate and format a single `<urange>` CSS string, code point `int`, or `(start, end)` 2-tuple."""
    if isinstance(value, str):
        start, end = parse_single_urange(value, target=target, syntax=target)
        return format_single_urange(start, end, options=options)
    if isinstance(value, bool):
        raise StixisValidationError(target, target, value)
    if isinstance(value, int):
        if value < 0 or value > MAX_CODEPOINT:
            raise StixisValidationError(target, target, value)
        return format_single_urange(value, value, options=options)
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and not isinstance(value[0], bool)
        and not isinstance(value[1], bool)
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    ):
        start, end = value
        if start < 0 or end < start or end > MAX_CODEPOINT:
            raise StixisValidationError(target, target, value)
        return format_single_urange(start, end, options=options)
    raise StixisValidationError(target, target, value)


def _coalesce_codepoint_set(cps: list[int]) -> list[tuple[int, int]]:
    unique_sorted = sorted(set(cps))
    ranges: list[tuple[int, int]] = []
    r_start = r_end = unique_sorted[0]
    for cp in unique_sorted[1:]:
        if cp == r_end + 1:
            r_end = cp
        else:
            ranges.append((r_start, r_end))
            r_start = r_end = cp
    ranges.append((r_start, r_end))
    return ranges


def _merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    sorted_iv = sorted(intervals)
    merged: list[tuple[int, int]] = [sorted_iv[0]]
    for s, e in sorted_iv[1:]:
        prev_s, prev_e = merged[-1]
        if s <= prev_e + 1:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def parse_urange(value: Any, *, coalesce: bool = False) -> list[tuple[int, int]]:
    """Parse a CSS `unicode-range` string or structured Python value into a list of `(start, end)` intervals."""
    if isinstance(value, str):
        if not value.strip():
            raise StixisValidationError("unicode-range", "<urange>#", value)
        try:
            tokens = tokenize_css(value)
        except StixisSyntaxError as exc:
            raise StixisValidationError("unicode-range", "<urange>#", value, str(exc)) from exc
        if not tokens:
            raise StixisValidationError("unicode-range", "<urange>#", value)
        segments: list[list[CssToken]] = [[]]
        for tok in tokens:
            if tok.kind == "comma":
                segments.append([])
            else:
                segments[-1].append(tok)
        parsed: list[tuple[int, int]] = []
        for seg in segments:
            if not seg:
                raise StixisValidationError("unicode-range", "<urange>#", value)
            consumed = consume_urange_tokens(seg, 0)
            if consumed is None or consumed[1] != len(seg):
                raise StixisValidationError("unicode-range", "<urange>#", value)
            parsed.append(consumed[0])
        if coalesce and parsed:
            return _merge_intervals(parsed)
        return parsed

    if isinstance(value, bool):
        raise StixisValidationError("unicode-range", "<urange>#", value)

    if isinstance(value, int):
        if value < 0 or value > MAX_CODEPOINT:
            raise StixisValidationError("unicode-range", "<urange>#", value)
        return [(value, value)]

    if (
        isinstance(value, tuple)
        and len(value) == 2
        and not isinstance(value[0], bool)
        and not isinstance(value[1], bool)
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    ):
        start, end = value
        if start < 0 or end < start or end > MAX_CODEPOINT:
            raise StixisValidationError("unicode-range", "<urange>#", value)
        return [(start, end)]

    if isinstance(value, (set, frozenset)):
        if not value:
            raise StixisValidationError("unicode-range", "<urange>#", value)
        cps: list[int] = []
        for item in value:
            if (
                isinstance(item, bool)
                or not isinstance(item, int)
                or item < 0
                or item > MAX_CODEPOINT
            ):
                raise StixisValidationError("unicode-range", "<urange>#", value)
            cps.append(item)
        return _coalesce_codepoint_set(cps)

    if isinstance(value, Sequence):
        if not value:
            raise StixisValidationError("unicode-range", "<urange>#", value)
        out: list[tuple[int, int]] = []
        int_cps: list[int] = []
        all_plain_ints = True
        for elem in value:
            if isinstance(elem, str):
                all_plain_ints = False
                out.extend(parse_urange(elem, coalesce=coalesce))
            elif (
                isinstance(elem, tuple)
                and len(elem) == 2
                and not isinstance(elem[0], bool)
                and not isinstance(elem[1], bool)
                and isinstance(elem[0], int)
                and isinstance(elem[1], int)
            ):
                all_plain_ints = False
                s, e = elem
                if s < 0 or e < s or e > MAX_CODEPOINT:
                    raise StixisValidationError("unicode-range", "<urange>#", value)
                out.append((s, e))
            elif not isinstance(elem, bool) and isinstance(elem, int):
                if elem < 0 or elem > MAX_CODEPOINT:
                    raise StixisValidationError("unicode-range", "<urange>#", value)
                int_cps.append(elem)
                out.append((elem, elem))
            else:
                raise StixisValidationError("unicode-range", "<urange>#", value)
        if coalesce:
            if all_plain_ints:
                return _coalesce_codepoint_set(int_cps)
            # Merge overlapping/adjacent intervals when coalesce=True on mixed sequences
            return _merge_intervals(out)
        if all_plain_ints:
            return _coalesce_codepoint_set(int_cps)
        return out

    raise StixisValidationError("unicode-range", "<urange>#", value)


def parse_urange_codepoints(value: Any) -> set[int]:
    """Expand a `unicode-range` value into the set of all covered Unicode code points."""
    intervals = parse_urange(value)
    result: set[int] = set()
    for start, end in intervals:
        result.update(range(start, end + 1))
    return result


def format_urange(
    value: Any,
    *,
    coalesce: bool = False,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Validate and format a `unicode-range` value into a comma-separated CSS string."""
    intervals = parse_urange(value, coalesce=coalesce)
    return ", ".join(format_single_urange(s, e, options=options) for s, e in intervals)


def compact_urange(
    sorted_target_cps: Iterable[int],
    forbidden_sorted: Sequence[int] = (),
    *,
    max_gap: int = 0x80,
    options: SerializationOptions = DEFAULT_OPTIONS,
) -> str:
    """Compact target code points into minimal `unicode-range` intervals using CSS Cascade Shadowing.

    `max_gap` specifies the maximum number of missing code points between two target code points
    (`cp - prev_end - 1 <= max_gap`) that may be bridged into a single interval when no code point
    in `forbidden_sorted` lies inside `(prev_end, cp)`.
    """
    if isinstance(max_gap, bool) or not isinstance(max_gap, int) or max_gap < 0:
        raise StixisValidationError("unicode-range", "<urange>#", max_gap)
    if isinstance(sorted_target_cps, (str, bytes)) or not isinstance(sorted_target_cps, Iterable):
        raise StixisValidationError("unicode-range", "<urange>#", sorted_target_cps)
    if isinstance(forbidden_sorted, (str, bytes)) or not isinstance(forbidden_sorted, Iterable):
        raise StixisValidationError("unicode-range", "<urange>#", forbidden_sorted)

    raw_cps: set[int] = set()
    for cp in sorted_target_cps:
        if isinstance(cp, bool) or not isinstance(cp, int) or cp < 0 or cp > MAX_CODEPOINT:
            raise StixisValidationError("unicode-range", "<urange>#", cp)
        raw_cps.add(cp)
    if not raw_cps:
        raise StixisValidationError("unicode-range", "<urange>#", sorted_target_cps)
    cps = sorted(raw_cps)

    forbidden_set: list[int] = []
    for fcp in forbidden_sorted:
        if isinstance(fcp, bool) or not isinstance(fcp, int) or fcp < 0 or fcp > MAX_CODEPOINT:
            raise StixisValidationError("unicode-range", "<urange>#", fcp)
        forbidden_set.append(fcp)
    forbidden = sorted(forbidden_set)

    intervals: list[tuple[int, int]] = []
    cur_start = cur_end = cps[0]

    for cp in cps[1:]:
        gap = cp - cur_end - 1
        if gap == 0:
            cur_end = cp
            continue
        if gap <= max_gap:
            # Check via binary search whether any forbidden code point lies strictly in (cur_end, cp)
            idx = bisect.bisect_right(forbidden, cur_end)
            if idx >= len(forbidden) or forbidden[idx] >= cp:
                cur_end = cp
                continue
        intervals.append((cur_start, cur_end))
        cur_start = cur_end = cp

    intervals.append((cur_start, cur_end))
    return ", ".join(format_single_urange(s, e, options=options) for s, e in intervals)
