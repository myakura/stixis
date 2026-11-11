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


def _format_template_value(
    val: Any,
    format_spec: str,
    *,
    options: SerializationOptions,
) -> str:
    if format_spec:
        return format(val, format_spec)
    if _is_default_values_instance(val, "Url"):
        return serialize_url(val, options=options)
    if _is_default_values_instance(val, "LocalFont"):
        return f"local({serialize_string(val.family, options=options)})"
    return str(val)


def _format_normal_interpolation(
    item: Interpolation,
    *,
    options: SerializationOptions,
) -> str:
    val = convert(item.value, item.conversion)
    if not item.format_spec and not isinstance(val, bool) and isinstance(val, (int, float)):
        return serialize_number(val)
    return _format_template_value(val, item.format_spec, options=options)


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
    *,
    stop_at_zero: bool,
) -> tuple[int, int, bool, str | None]:
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_comment:
            end_c = text.find("*/", i)
            if end_c == -1:
                return n, paren_depth, True, in_quote
            in_comment = False
            i = end_c + 2
            continue
        if in_quote is not None:
            if ch == "\\":
                if i + 1 >= n:
                    return n, paren_depth, in_comment, in_quote
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
        if ch == "\\" and _starts_with_valid_escape(text, i):
            _, i = _consume_escape(text, i + 1)
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth == 0:
                if stop_at_zero:
                    return i + 1, 0, in_comment, in_quote
                return -1, 0, in_comment, in_quote
        i += 1
    return n, paren_depth, in_comment, in_quote


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


def _is_quoted_single_url_slot(
    norm_parts: Sequence[str | Interpolation],
    slot_idx: int,
    *,
    options: SerializationOptions,
) -> bool:
    num_parts = len(norm_parts)
    if slot_idx >= num_parts:
        return True
    paren_depth = 1
    in_comment = False
    in_quote: str | None = None
    probe_chunks: list[str] = ['url("x"']
    closed = False
    for k in range(slot_idx + 1, num_parts):
        part = norm_parts[k]
        if isinstance(part, str):
            end_idx, paren_depth, in_comment, in_quote = _advance_url_probe_text(
                part, paren_depth, in_comment, in_quote, stop_at_zero=True
            )
            probe_chunks.append(part[:end_idx])
            if paren_depth == 0:
                closed = True
                break
        elif isinstance(part, Interpolation):
            if in_comment or in_quote is not None:
                continue
            try:
                rendered = _format_normal_interpolation(part, options=options)
            except StixisValidationError, StixisSyntaxError, ValueError, TypeError:
                return False
            norm_rendered = _preprocess_css_chunk(rendered)
            end_idx, paren_depth, interp_comment, interp_quote = _advance_url_probe_text(
                norm_rendered, paren_depth, False, None, stop_at_zero=False
            )
            if end_idx == -1 or interp_comment or interp_quote is not None:
                return False
            probe_chunks.append(norm_rendered)
    if not closed:
        if len(probe_chunks) == 1:
            return True
        if in_comment or in_quote is not None:
            return False
        probe_chunks.append(")" * paren_depth)
    return _validate_quoted_url_probe("".join(probe_chunks))


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
    num_parts = len(parts)
    out: list[str] = []
    in_comment = start_in_comment
    in_quote: str | None = None
    url_mode: str | None = None
    tail = ""

    for part_idx, item in enumerate(parts):
        if isinstance(item, str):
            out.append(item)
            norm = norm_parts[part_idx]
            assert isinstance(norm, str)
            if not norm:
                continue
            if not in_comment and in_quote is None and url_mode is None and tail:
                scan_str = tail + norm
                offset = len(tail)
                tail = ""
            else:
                scan_str = norm
                offset = 0
            i = 0
            n = len(scan_str)
            while i < n:
                ch = scan_str[i]
                if in_comment:
                    if start_in_comment:
                        i = n
                        break
                    end_c = scan_str.find("*/", i)
                    if end_c == -1:
                        i = n
                        break
                    in_comment = False
                    tail = ""
                    i = end_c + 2
                    continue
                if in_quote is not None:
                    if ch == "\\":
                        if i + 1 >= n:
                            i = n
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
                        i = n
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
                        _is_ident_code_point(scan_str[i + 1])
                        or _starts_with_valid_escape(scan_str, i + 1)
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
                                        norm_parts, part_idx + 1, options=options
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
        elif isinstance(item, Interpolation):
            if in_comment:
                val = convert(item.value, item.conversion)
                text = _format_template_value(val, item.format_spec, options=options)
                cleaned = (
                    text.replace("\r\n", " ")
                    .replace("\r", " ")
                    .replace("\n", " ")
                    .replace("\f", " ")
                )
                while "*/" in cleaned:
                    cleaned = cleaned.replace("*/", "* /")
                out.append(cleaned)
            elif in_quote is not None:
                val = convert(item.value, item.conversion)
                text = format(val, item.format_spec) if item.format_spec else str(val)
                q_opts = (
                    options
                    if options.quote_char == in_quote
                    else SerializationOptions(quote_char=in_quote)  # type: ignore[arg-type]
                )
                # Strip the outer quotes added by serialize_string
                escaped = serialize_string(text, options=q_opts)[1:-1]
                out.append(escaped)
            elif url_mode == "quoted_single":
                val = convert(item.value, item.conversion)
                text = format(val, item.format_spec) if item.format_spec else str(val)
                out.append(serialize_string(text, options=options))
                url_mode = None
                tail = ""
            elif url_mode == "unquoted":
                val = convert(item.value, item.conversion)
                text = format(val, item.format_spec) if item.format_spec else str(val)
                prev_star = bool(out and out[-1].endswith("*"))
                next_part = norm_parts[part_idx + 1] if part_idx + 1 < num_parts else None
                next_star = isinstance(next_part, str) and next_part.startswith("*")
                out.append(
                    _escape_unquoted_url_value(
                        text,
                        prev_ends_with_star=prev_star,
                        next_starts_with_star=next_star,
                    )
                )
                tail = ""
            else:
                emitted = _format_normal_interpolation(item, options=options)
                out.append(emitted)
                tail = _update_token_tail(tail, emitted)

    if in_comment and not start_in_comment:
        raise StixisSyntaxError("Unclosed CSS comment `/*` in Template")
    if in_quote is not None:
        raise StixisSyntaxError(f"Unclosed CSS string quote {in_quote!r} in Template")

    return "".join(out)
