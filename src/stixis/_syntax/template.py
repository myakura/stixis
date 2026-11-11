"""PEP 750 `Template` (`t"..."`) context-aware CSS rendering."""

from __future__ import annotations

from collections.abc import Sequence
from string.templatelib import Interpolation, Template, convert
from typing import Any

from stixis._syntax.serialize import (
    consume_url_modifier_tokens,
    escape_char,
    escape_code_point,
    serialize_number,
    serialize_string,
    serialize_url,
)
from stixis._syntax.tokenizer import (
    _consume_escape,
    _consume_ident_sequence,
    _consume_number,
    _is_ident_code_point,
    _starts_a_number,
    _starts_an_ident_sequence,
    _starts_with_valid_escape,
    validate_token_stream_safety,
)
from stixis.errors import StixisSyntaxError, StixisValidationError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions


def _is_default_values_instance(value: object, class_name: str) -> bool:
    for cls in type(value).__mro__:
        if cls.__name__ == class_name and getattr(cls, "__module__", "") == "stixis.values":
            return type(value).__str__ is cls.__str__
    return False


def _get_converted(
    idx: int,
    item: Interpolation,
    converted_cache: dict[int, Any],
) -> Any:
    if idx in converted_cache:
        return converted_cache[idx]
    val = convert(item.value, item.conversion)
    converted_cache[idx] = val
    return val


def _get_raw_str(
    idx: int,
    item: Interpolation,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
) -> str:
    if idx in str_cache:
        return str_cache[idx]
    val = _get_converted(idx, item, converted_cache)
    text = format(val, item.format_spec) if item.format_spec else str(val)
    str_cache[idx] = text
    return text


def _format_template_slot_value(
    idx: int,
    item: Interpolation,
    *,
    options: SerializationOptions,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
) -> str:
    val = _get_converted(idx, item, converted_cache)
    if item.format_spec:
        return _get_raw_str(idx, item, converted_cache, str_cache)
    if _is_default_values_instance(val, "Url"):
        return serialize_url(val, options=options)
    if _is_default_values_instance(val, "LocalFont"):
        return f"local({serialize_string(val.family, options=options)})"
    return _get_raw_str(idx, item, converted_cache, str_cache)


def _format_normal_slot(
    idx: int,
    item: Interpolation,
    *,
    options: SerializationOptions,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
) -> str:
    val = _get_converted(idx, item, converted_cache)
    if not item.format_spec and not isinstance(val, bool) and isinstance(val, (int, float)):
        prim = (
            val if type(val) in (int, float) else (int(val) if isinstance(val, int) else float(val))
        )
        return serialize_number(prim)
    return _format_template_slot_value(
        idx,
        item,
        options=options,
        converted_cache=converted_cache,
        str_cache=str_cache,
    )


def _preprocess_css_chunk(text: str) -> str:
    return (
        text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").replace("\x00", "\ufffd")
    )


def _is_token_prefix_char(ch: str) -> bool:
    return _is_ident_code_point(ch) or ch in ("#", "@", ".", "+", "\\")


def _slice_token_tail(scan_str: str, start: int, end: int) -> str:
    if end - start > 32:
        return "_" + scan_str[end - 32 : end]
    return scan_str[start:end]


def _ends_with_hex_escape_ws(norm: str) -> bool:
    n = len(norm)
    if n < 3 or norm[-1] not in (" ", "\t", "\n"):
        return False
    hex_start = n - 1
    while hex_start > 0 and norm[hex_start - 1] in "0123456789abcdefABCDEF":
        hex_start -= 1
    hex_len = (n - 1) - hex_start
    if not (1 <= hex_len <= 6):
        return False
    bs_start = hex_start
    while bs_start > 0 and norm[bs_start - 1] == "\\":
        bs_start -= 1
    return (hex_start - bs_start) % 2 == 1


def _update_token_tail(prev_tail: str, emitted: str) -> str:
    if not emitted:
        return prev_tail
    norm = _preprocess_css_chunk(emitted)
    combined = (prev_tail + norm) if len(norm) <= 40 else norm[-40:]
    n = len(combined)
    idx = n - 1 if _ends_with_hex_escape_ws(combined) else n
    limit = max(0, idx - 33)
    while idx > limit and _is_token_prefix_char(combined[idx - 1]):
        idx -= 1
    if idx == n:
        return ""
    return ("_" + combined[n - 32 :]) if n - idx > 32 else combined[idx:]


def _scan_unquoted_url_chunk(norm: str, pos: int) -> int | None:
    n = len(norm)
    while pos < n:
        ch = norm[pos]
        if ch == ")":
            return pos + 1
        if ch == "\\" and _starts_with_valid_escape(norm, pos):
            _, pos = _consume_escape(norm, pos + 1)
        else:
            pos += 1
    return None


def _escape_unquoted_url_value(
    text: str,
    *,
    prev_ends_with_star: bool = False,
    next_starts_with_star: bool = False,
) -> str:
    if not text:
        return ""
    out: list[str] = []
    last_idx = len(text) - 1
    for idx, ch in enumerate(text):
        cp = ord(ch)
        if cp == 0x0000:
            out.append("\ufffd")
        elif (
            (0x0001 <= cp <= 0x001F)
            or cp == 0x007F
            or ch == "*"
            or (idx == 0 and ch == "/" and prev_ends_with_star)
            or (idx == last_idx and ch == "/" and next_starts_with_star)
        ):
            out.append(escape_code_point(cp))
        elif ch in ('"', "'", "(", ")", "\\", " "):
            out.append(escape_char(ch))
        else:
            out.append(ch)
    return "".join(out)


def _advance_url_probe_text(
    text: str,
    paren_depth: int,
    in_comment: bool,
    in_quote: str | None,
    in_unquoted_url: bool = False,
    pending_url: bool = False,
    *,
    stop_at_zero: bool = True,
) -> tuple[int, int, bool, str | None, bool, bool]:
    i = 0
    n = len(text)
    while i < n:
        if pending_url:
            while i < n and text[i] in (" ", "\t", "\n"):
                i += 1
            if i >= n:
                return n, paren_depth, in_comment, in_quote, False, True
            pending_url = False
            if text[i] not in ('"', "'"):
                in_unquoted_url = True
                continue
        if in_unquoted_url:
            after_url = _scan_unquoted_url_chunk(text, i)
            if after_url is None:
                return n, paren_depth, in_comment, in_quote, True, False
            in_unquoted_url = False
            if stop_at_zero:
                paren_depth -= 1
                if paren_depth == 0:
                    return after_url, 0, in_comment, in_quote, False, False
            else:
                paren_depth = max(0, paren_depth - 1)
            i = after_url
            continue
        ch = text[i]
        if in_comment:
            end_c = text.find("*/", i)
            if end_c == -1:
                return n, paren_depth, True, in_quote, False, False
            in_comment = False
            i = end_c + 2
            continue
        if in_quote is not None:
            if ch == "\\":
                if i + 1 >= n:
                    return n, paren_depth, in_comment, in_quote, False, False
                if text[i + 1] == "\n":
                    i += 2
                else:
                    _, i = _consume_escape(text, i + 1)
                continue
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            in_comment = True
            i += 2
            continue
        if ch in ('"', "'"):
            in_quote = ch
            i += 1
            continue
        if ch == "#":
            if i + 1 < n and (
                _is_ident_code_point(text[i + 1]) or _starts_with_valid_escape(text, i + 1)
            ):
                _, i = _consume_ident_sequence(text, i + 1)
            else:
                i += 1
            continue
        if _starts_a_number(text, i):
            _, _, _, i = _consume_number(text, i)
            if _starts_an_ident_sequence(text, i):
                _, i = _consume_ident_sequence(text, i)
            elif i < n and text[i] == "%":
                i += 1
            continue
        if _starts_an_ident_sequence(text, i):
            ident, i = _consume_ident_sequence(text, i)
            if i < n and text[i] == "(":
                paren_depth += 1
                i += 1
                if ident.lower() == "url":
                    pending_url = True
            continue
        if ch == "@":
            if _starts_an_ident_sequence(text, i + 1):
                _, i = _consume_ident_sequence(text, i + 1)
            else:
                i += 1
            continue
        if ch == "\\" and _starts_with_valid_escape(text, i):
            _, i = _consume_escape(text, i + 1)
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            if stop_at_zero:
                paren_depth -= 1
                if paren_depth == 0:
                    return i + 1, 0, in_comment, in_quote, False, False
            else:
                paren_depth = max(0, paren_depth - 1)
        i += 1
    return n, paren_depth, in_comment, in_quote, in_unquoted_url, pending_url


def _find_unquoted_url_close(
    norm_parts: Sequence[str | Interpolation],
    start_idx: int,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
) -> tuple[int, int] | None:
    trailing_ws = False
    for m in range(start_idx, len(norm_parts)):
        part = norm_parts[m]
        if isinstance(part, Interpolation):
            if trailing_ws and _get_raw_str(m, part, converted_cache, str_cache) != "":
                return None
            continue
        i = 0
        n = len(part)
        while i < n:
            ch = part[i]
            if ch == ")":
                return m, i
            if ch in (" ", "\t", "\n"):
                trailing_ws = True
                i += 1
                continue
            if trailing_ws:
                return None
            if ch in ('"', "'", "(") or ord(ch) <= 0x1F or ord(ch) == 0x7F:
                return None
            if ch == "\\":
                if _starts_with_valid_escape(part, i):
                    _, i = _consume_escape(part, i + 1)
                    continue
                return None
            i += 1
    return None


def _has_unmatched_static_close(
    norm_parts: Sequence[str | Interpolation],
    start_idx: int,
    outer_depth: int,
) -> bool:
    depth = outer_depth + 1
    in_c = False
    in_q: str | None = None
    in_u = False
    pend_u = False
    for m in range(start_idx, len(norm_parts)):
        part = norm_parts[m]
        if isinstance(part, str):
            _, depth, in_c, in_q, in_u, pend_u = _advance_url_probe_text(
                part, depth, in_c, in_q, in_u, pend_u
            )
            if depth == 0:
                return True
    return False


def _validate_quoted_url_probe(probe: str) -> bool:
    try:
        tokens = validate_token_stream_safety(
            probe,
            forbid_top_level_semicolon=True,
            forbid_top_level_bang=True,
            forbid_top_level_braces=True,
            allow_empty=False,
            target="<url>",
        )
    except StixisValidationError:
        return False
    non_ws = [t for t in tokens if t.kind != "whitespace"]
    if (
        len(non_ws) < 3
        or non_ws[0].kind != "function"
        or non_ws[1].kind != "string"
        or non_ws[-1].kind != ")"
    ):
        return False
    pos = 2
    end = len(non_ws) - 1
    while pos < end:
        nxt = consume_url_modifier_tokens(non_ws, pos, end)
        if nxt is None:
            return False
        pos = nxt
    return True


def _scan_static_chunk(
    norm: str,
    part_idx: int,
    norm_parts: Sequence[str | Interpolation],
    *,
    start_in_comment: bool,
    in_comment: bool,
    in_quote: str | None,
    url_mode: str | None,
    tail: str,
    end_outer_depth: int,
    options: SerializationOptions,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
    decision_cache: dict[int, bool],
) -> tuple[bool, str | None, str | None, str]:
    if not norm:
        return in_comment, in_quote, url_mode, tail
    if not in_comment and in_quote is None and url_mode is None and tail:
        scan_str = tail + norm
        offset = len(tail)
        tail = ""
    else:
        scan_str = norm
        offset = 0
    num_parts = len(norm_parts)
    i = 0
    n = len(scan_str)
    while i < n:
        ch = scan_str[i]
        if in_comment:
            if start_in_comment:
                break
            end_c = scan_str.find("*/", i)
            if end_c == -1:
                break
            in_comment = False
            tail = ""
            i = end_c + 2
            continue
        if in_quote is not None:
            if ch == "\\":
                if i + 1 >= n:
                    break
                if scan_str[i + 1] == "\n":
                    i += 2
                else:
                    _, i = _consume_escape(scan_str, i + 1)
                continue
            if ch == in_quote:
                in_quote = None
                tail = ""
            i += 1
            continue
        if url_mode == "unquoted":
            after_url = _scan_unquoted_url_chunk(scan_str, i)
            if after_url is None:
                break
            url_mode = None
            tail = ""
            i = after_url
            continue

        if ch == "/" and i + 1 < n and scan_str[i + 1] == "*":
            in_comment = True
            tail = ""
            i += 2
            continue
        if ch in (" ", "\t", "\n"):
            while i < n and scan_str[i] in (" ", "\t", "\n"):
                i += 1
            tail = ""
            continue
        if scan_str.startswith("<!--", i):
            tail = ""
            i += 4
            continue
        if scan_str.startswith("-->", i):
            tail = ""
            i += 3
            continue
        if ch in ('"', "'"):
            in_quote = ch
            url_mode = None
            tail = ""
            i += 1
            continue
        if ch == "#":
            tok_start = i
            if i + 1 < n and (
                _is_ident_code_point(scan_str[i + 1]) or _starts_with_valid_escape(scan_str, i + 1)
            ):
                _, i = _consume_ident_sequence(scan_str, i + 1)
            else:
                i += 1
            tail = _slice_token_tail(scan_str, tok_start, i) if i == n else ""
            continue
        if _starts_a_number(scan_str, i):
            tok_start = i
            _, _, _, i = _consume_number(scan_str, i)
            if _starts_an_ident_sequence(scan_str, i):
                _, i = _consume_ident_sequence(scan_str, i)
                tail = _slice_token_tail(scan_str, tok_start, i) if i == n else ""
            elif i < n and scan_str[i] == "%":
                i += 1
                tail = ""
            else:
                tail = _slice_token_tail(scan_str, tok_start, i) if i == n else ""
            continue
        if _starts_an_ident_sequence(scan_str, i):
            tok_start = i
            ident, i = _consume_ident_sequence(scan_str, i)
            if i < n and scan_str[i] == "(":
                paren_pos = i
                i += 1
                tail = ""
                if paren_pos >= offset and ident.lower() == "url":
                    look = i
                    while look < n and scan_str[look] in (" ", "\t", "\n"):
                        look += 1
                    if look < n and scan_str[look] in ('"', "'"):
                        i = look
                    elif look < n:
                        url_mode = "unquoted"
                        i = look
                    elif part_idx + 1 < num_parts:
                        url_mode = (
                            "quoted_single"
                            if _is_quoted_single_url_slot(
                                norm_parts,
                                part_idx + 1,
                                outer_depth=end_outer_depth,
                                options=options,
                                converted_cache=converted_cache,
                                str_cache=str_cache,
                                decision_cache=decision_cache,
                            )
                            else "unquoted"
                        )
                        i = look
            else:
                tail = _slice_token_tail(scan_str, tok_start, i) if i == n else ""
            continue
        if ch == "@":
            tok_start = i
            if _starts_an_ident_sequence(scan_str, i + 1):
                _, i = _consume_ident_sequence(scan_str, i + 1)
            else:
                i += 1
            tail = _slice_token_tail(scan_str, tok_start, i) if i == n else ""
            continue

        tok_start = i
        i += 1
        tail = scan_str[tok_start:i] if (i == n and ch in (".", "+", "-", "\\")) else ""

    return in_comment, in_quote, url_mode, tail


def _render_interpolation_slot(
    part_idx: int,
    item: Interpolation,
    norm_parts: Sequence[str | Interpolation],
    *,
    prev_chunk: str,
    in_comment: bool,
    in_quote: str | None,
    url_mode: str | None,
    tail: str,
    options: SerializationOptions,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
) -> tuple[str, str | None, str]:
    if in_comment:
        text = _format_template_slot_value(
            part_idx,
            item,
            options=options,
            converted_cache=converted_cache,
            str_cache=str_cache,
        )
        cleaned = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\f", " ")
        while "*/" in cleaned:
            cleaned = cleaned.replace("*/", "* /")
        return cleaned, url_mode, tail
    if in_quote is not None:
        text = _get_raw_str(part_idx, item, converted_cache, str_cache)
        q_opts = (
            options if options.quote_char == in_quote else SerializationOptions(quote_char=in_quote)  # type: ignore[arg-type]
        )
        # Strip the outer quotes added by serialize_string
        return serialize_string(text, options=q_opts)[1:-1], url_mode, tail
    if url_mode == "quoted_single":
        text = _get_raw_str(part_idx, item, converted_cache, str_cache)
        return serialize_string(text, options=options), None, ""
    if url_mode == "unquoted":
        text = _get_raw_str(part_idx, item, converted_cache, str_cache)
        prev_star = prev_chunk.endswith("*")
        next_part = norm_parts[part_idx + 1] if part_idx + 1 < len(norm_parts) else None
        next_star = isinstance(next_part, str) and next_part.startswith("*")
        escaped = _escape_unquoted_url_value(
            text,
            prev_ends_with_star=prev_star,
            next_starts_with_star=next_star,
        )
        return escaped, url_mode, ""
    emitted = _format_normal_slot(
        part_idx,
        item,
        options=options,
        converted_cache=converted_cache,
        str_cache=str_cache,
    )
    return emitted, url_mode, _update_token_tail(tail, emitted)


def _is_quoted_single_url_slot(
    norm_parts: Sequence[str | Interpolation],
    slot_idx: int,
    *,
    outer_depth: int,
    options: SerializationOptions,
    converted_cache: dict[int, Any],
    str_cache: dict[int, str],
    decision_cache: dict[int, bool],
) -> bool:
    if slot_idx in decision_cache:
        return decision_cache[slot_idx]
    num_parts = len(norm_parts)
    if slot_idx >= num_parts:
        decision_cache[slot_idx] = True
        return True
    first_part = norm_parts[slot_idx]
    if isinstance(first_part, Interpolation):
        _get_raw_str(slot_idx, first_part, converted_cache, str_cache)

    paren_depth = 1
    probe_comment = False
    probe_quote: str | None = None
    probe_unquoted = False
    probe_pending = False
    tpl_comment = False
    tpl_quote: str | None = None
    tpl_url_mode: str | None = None
    tpl_tail = ""
    probe_chunks: list[str] = ['url("x"']
    closed = False

    for k in range(slot_idx + 1, num_parts):
        part = norm_parts[k]
        if isinstance(part, str):
            (
                end_idx,
                paren_depth,
                probe_comment,
                probe_quote,
                probe_unquoted,
                probe_pending,
            ) = _advance_url_probe_text(
                part,
                paren_depth,
                probe_comment,
                probe_quote,
                probe_unquoted,
                probe_pending,
            )
            chunk = part[:end_idx]
            probe_chunks.append(chunk)
            if paren_depth == 0:
                closed = True
                break
            tpl_comment, tpl_quote, tpl_url_mode, tpl_tail = _scan_static_chunk(
                chunk,
                k,
                norm_parts,
                start_in_comment=False,
                in_comment=tpl_comment,
                in_quote=tpl_quote,
                url_mode=tpl_url_mode,
                tail=tpl_tail,
                end_outer_depth=max(0, outer_depth + paren_depth - 1),
                options=options,
                converted_cache=converted_cache,
                str_cache=str_cache,
                decision_cache=decision_cache,
            )
        elif isinstance(part, Interpolation):
            try:
                emitted, tpl_url_mode, tpl_tail = _render_interpolation_slot(
                    k,
                    part,
                    norm_parts,
                    prev_chunk=probe_chunks[-1],
                    in_comment=tpl_comment,
                    in_quote=tpl_quote,
                    url_mode=tpl_url_mode,
                    tail=tpl_tail,
                    options=options,
                    converted_cache=converted_cache,
                    str_cache=str_cache,
                )
            except StixisValidationError:
                decision_cache[slot_idx] = False
                return False
            norm_emitted = _preprocess_css_chunk(emitted)
            (
                end_idx,
                paren_depth,
                probe_comment,
                probe_quote,
                probe_unquoted,
                probe_pending,
            ) = _advance_url_probe_text(
                norm_emitted,
                paren_depth,
                probe_comment,
                probe_quote,
                probe_unquoted,
                probe_pending,
            )
            if paren_depth == 0:
                if not probe_comment and probe_quote is None:
                    unquoted_close = _find_unquoted_url_close(
                        norm_parts, slot_idx + 1, converted_cache, str_cache
                    )
                    if unquoted_close is None:
                        probe_chunks.append(norm_emitted[:end_idx])
                        closed = True
                        break
                    close_m, close_i = unquoted_close
                    next_part = norm_parts[close_m]
                    if (
                        outer_depth > 0
                        and close_m == k + 1
                        and isinstance(next_part, str)
                        and next_part[:close_i].strip(" \t\n") == ""
                        and norm_emitted[end_idx:].strip(" \t\n") == ""
                        and not _has_unmatched_static_close(norm_parts, k + 1, outer_depth)
                    ):
                        probe_chunks.append(norm_emitted[:end_idx])
                        closed = True
                        break
                decision_cache[slot_idx] = False
                return False
            probe_chunks.append(norm_emitted)

    if not closed:
        if len(probe_chunks) == 1:
            decision_cache[slot_idx] = True
            return True
        if (
            probe_comment
            or probe_quote is not None
            or probe_unquoted
            or tpl_comment
            or tpl_quote is not None
        ):
            decision_cache[slot_idx] = False
            return False
        probe_chunks.append(")" * paren_depth)

    res = _validate_quoted_url_probe("".join(probe_chunks))
    decision_cache[slot_idx] = res
    return res


def render_css_template(
    tpl: Template | str,
    *,
    options: SerializationOptions = DEFAULT_OPTIONS,
    start_in_comment: bool = False,
) -> str:
    """Render a PEP 750 `Template` (`t"..."`) with context-aware escaping for CSS comments, strings, URLs, and numbers."""
    if isinstance(tpl, str):
        return tpl
    if not isinstance(tpl, Template):
        raise TypeError(f"Expected str or string.templatelib.Template, got {type(tpl).__name__}")

    parts = list(tpl)
    norm_parts: list[str | Interpolation] = [
        _preprocess_css_chunk(p) if isinstance(p, str) else p for p in parts
    ]
    converted_cache: dict[int, Any] = {}
    str_cache: dict[int, str] = {}
    decision_cache: dict[int, bool] = {}
    out: list[str] = []
    in_comment = start_in_comment
    in_quote: str | None = None
    url_mode: str | None = None
    tail = ""
    stream_depth = 0
    stream_comment = start_in_comment
    stream_quote: str | None = None
    stream_unquoted = False
    stream_pending = False

    for part_idx, item in enumerate(parts):
        if isinstance(item, str):
            out.append(item)
            norm = norm_parts[part_idx]
            assert isinstance(norm, str)
            (
                _,
                next_depth,
                next_comment,
                next_quote,
                next_unquoted,
                next_pending,
            ) = _advance_url_probe_text(
                norm,
                stream_depth,
                stream_comment,
                stream_quote,
                stream_unquoted,
                stream_pending,
                stop_at_zero=False,
            )
            in_comment, in_quote, url_mode, tail = _scan_static_chunk(
                norm,
                part_idx,
                norm_parts,
                start_in_comment=start_in_comment,
                in_comment=in_comment,
                in_quote=in_quote,
                url_mode=url_mode,
                tail=tail,
                end_outer_depth=max(0, next_depth - 1),
                options=options,
                converted_cache=converted_cache,
                str_cache=str_cache,
                decision_cache=decision_cache,
            )
            stream_depth = next_depth
            stream_comment = next_comment
            stream_quote = next_quote
            stream_unquoted = next_unquoted
            stream_pending = next_pending
        elif isinstance(item, Interpolation):
            emitted, url_mode, tail = _render_interpolation_slot(
                part_idx,
                item,
                norm_parts,
                prev_chunk=out[-1] if out else "",
                in_comment=in_comment,
                in_quote=in_quote,
                url_mode=url_mode,
                tail=tail,
                options=options,
                converted_cache=converted_cache,
                str_cache=str_cache,
            )
            out.append(emitted)
            (
                _,
                stream_depth,
                stream_comment,
                stream_quote,
                stream_unquoted,
                stream_pending,
            ) = _advance_url_probe_text(
                _preprocess_css_chunk(emitted),
                stream_depth,
                stream_comment,
                stream_quote,
                stream_unquoted,
                stream_pending,
                stop_at_zero=False,
            )

    if in_comment and not start_in_comment:
        raise StixisSyntaxError("Unclosed CSS comment `/*` in Template")
    if in_quote is not None:
        raise StixisSyntaxError(f"Unclosed CSS string quote {in_quote!r} in Template")

    return "".join(out)
