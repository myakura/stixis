"""`unicode-range` (`<urange>`) parser, formatter, and cascade shadowing compaction (`css-syntax-3` §8)."""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Sequence
from typing import Any

from stixis.errors import StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

MAX_CODEPOINT = 0x10FFFF

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HEX_RE = re.compile(r"^[0-9A-Fa-f]{1,6}$")
_WILDCARD_RE = re.compile(r"^([0-9A-Fa-f]{0,5})(\?{1,6})$")


def _strip_comments_for_urange(text: str) -> str:
    """Strip CSS comments while preserving token separation rules of CSS Syntax 3 §8."""
    # Check for unclosed comment
    if "/*" in _COMMENT_RE.sub("", text):
        raise StixisValidationError("unicode-range", "<urange>#", text)
    return _COMMENT_RE.sub("", text)


def parse_single_urange(raw_item: str) -> tuple[int, int]:
    """Parse a single `<urange>` item according to CSS Syntax Level 3 §8 and WPT `urange-parsing.html`."""
    cleaned = _strip_comments_for_urange(raw_item).strip()
    if not cleaned:
        raise StixisValidationError("unicode-range", "<urange>#", raw_item)

    # Whitespace is not permitted inside a single <urange> production
    if any(ch.isspace() for ch in cleaned):
        raise StixisValidationError("unicode-range", "<urange>#", raw_item)

    if len(cleaned) < 3 or cleaned[0] not in ("u", "U") or cleaned[1] != "+":
        raise StixisValidationError("unicode-range", "<urange>#", raw_item)

    body = cleaned[2:]
    if not body:
        raise StixisValidationError("unicode-range", "<urange>#", raw_item)

    if "-" in body:
        # Range syntax: U+start-end (no '?' allowed)
        parts = body.split("-")
        if len(parts) != 2:
            raise StixisValidationError("unicode-range", "<urange>#", raw_item)
        start_hex, end_hex = parts
        if not (_HEX_RE.match(start_hex) and _HEX_RE.match(end_hex)):
            raise StixisValidationError("unicode-range", "<urange>#", raw_item)
        start = int(start_hex, 16)
        end = int(end_hex, 16)
    elif "?" in body:
        m = _WILDCARD_RE.match(body)
        if not m:
            raise StixisValidationError("unicode-range", "<urange>#", raw_item)
        prefix, qmarks = m.group(1), m.group(2)
        total_len = len(prefix) + len(qmarks)
        if not (1 <= total_len <= 6):
            raise StixisValidationError("unicode-range", "<urange>#", raw_item)
        start = int(prefix + ("0" * len(qmarks)), 16)
        end = int(prefix + ("F" * len(qmarks)), 16)
    else:
        if not _HEX_RE.match(body):
            raise StixisValidationError("unicode-range", "<urange>#", raw_item)
        start = end = int(body, 16)

    if start < 0 or end < start or end > MAX_CODEPOINT:
        raise StixisValidationError("unicode-range", "<urange>#", raw_item)

    return (start, end)


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


def parse_urange(value: Any, *, coalesce: bool = False) -> list[tuple[int, int]]:
    """Parse a CSS `unicode-range` string or structured Python value into a list of `(start, end)` intervals."""
    if isinstance(value, str):
        if not value.strip():
            raise StixisValidationError("unicode-range", "<urange>#", value)
        # Split on top-level commas after removing comments
        no_comments = _strip_comments_for_urange(value)
        parts = no_comments.split(",")
        if not parts:
            raise StixisValidationError("unicode-range", "<urange>#", value)
        parsed = [parse_single_urange(part) for part in parts]
        if coalesce and parsed:
            sorted_iv = sorted(parsed)
            merged: list[tuple[int, int]] = [sorted_iv[0]]
            for s, e in sorted_iv[1:]:
                prev_s, prev_e = merged[-1]
                if s <= prev_e + 1:
                    merged[-1] = (prev_s, max(prev_e, e))
                else:
                    merged.append((s, e))
            return merged
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
            sorted_iv = sorted(out)
            merged: list[tuple[int, int]] = [sorted_iv[0]]
            for s, e in sorted_iv[1:]:
                prev_s, prev_e = merged[-1]
                if s <= prev_e + 1:
                    merged[-1] = (prev_s, max(prev_e, e))
                else:
                    merged.append((s, e))
            return merged
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
