"""PEP 750 `Template` (`t"..."`) context-aware CSS rendering."""

from __future__ import annotations

from string.templatelib import Template, convert
from typing import Any

from stixis._syntax.serialize import (
    _format_option_aware_value,
    escape_char,
    escape_code_point,
    serialize_number,
    serialize_string,
)
from stixis._syntax.tokenizer import (
    _consume_escape,
    _consume_ident_sequence,
    _consume_number,
    _is_ident_code_point,
    _starts_a_number,
    _starts_an_ident_sequence,
    _starts_with_valid_escape,
)
from stixis.errors import StixisSyntaxError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_MAX_RAW_TAIL = 32


def _format_template_value(
    val: Any,
    format_spec: str,
    *,
    options: SerializationOptions,
) -> str:
    if format_spec:
        return format(val, format_spec)
    formatted = _format_option_aware_value(val, options)
    return formatted if formatted is not None else str(val)


def _preprocess_css_chunk(text: str) -> str:
    return (
        text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").replace("\x00", "\ufffd")
    )


def _trailing_escape_suffix(text: str) -> str:
    """Return an unfinished trailing `\\` or `\\<1..6 hex>` suffix at the end of `text`, or `""`."""
    n = len(text)
    if n == 0:
        return ""
    bs_end = n
    while bs_end > 0 and text[bs_end - 1] == "\\":
        bs_end -= 1
    if (n - bs_end) % 2 == 1:
        return "\\"
    hex_start = n
    while hex_start > 0 and text[hex_start - 1] in _HEX_DIGITS:
        hex_start -= 1
    hex_len = n - hex_start
    if 1 <= hex_len <= 6:
        bs_start = hex_start
        while bs_start > 0 and text[bs_start - 1] == "\\":
            bs_start -= 1
        if (hex_start - bs_start) % 2 == 1:
            return text[hex_start - 1 :]
    return ""


def _resolve_pending_escape_prefix(pending_escape: str, non_empty_escaped: str) -> str:
    """Return a prefix that terminates `pending_escape` cleanly before `non_empty_escaped`."""
    if pending_escape == "\\":
        return "\\"
    if pending_escape and (
        (len(pending_escape) < 7 and non_empty_escaped[0] in _HEX_DIGITS)
        or non_empty_escaped[0] in (" ", "\t", "\n")
    ):
        return " "
    return ""


def _escape_unquoted_url_value(text: str) -> str:
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        cp = ord(ch)
        if cp == 0x0000:
            out.append("\ufffd")
        elif (0x0001 <= cp <= 0x001F) or cp == 0x007F or ch == "*":
            out.append(escape_code_point(cp))
        elif ch in ('"', "'", "(", ")", "\\", " "):
            out.append(escape_char(ch))
        else:
            out.append(ch)
    return "".join(out)


def _scan_string_body(text: str, quote: str) -> tuple[bool, int]:
    """Scan inside an open CSS string up to `quote` or end of `text`."""
    j = 0
    m = len(text)
    while j < m:
        c = text[j]
        if c == quote:
            return True, j + 1
        if c == "\\":
            if j + 1 >= m:
                break
            if text[j + 1] == "\n":
                j += 2
            else:
                _, j = _consume_escape(text, j + 1)
        else:
            j += 1
    return False, j


def _scan_unquoted_url_body(text: str) -> tuple[bool, int]:
    """Scan inside an unquoted `url(...)` up to an unescaped `)` or end of `text`."""
    j = 0
    m = len(text)
    while j < m:
        c = text[j]
        if c == ")":
            return True, j + 1
        if c == "\\" and _starts_with_valid_escape(text, j):
            if j + 1 >= m:
                break
            _, j = _consume_escape(text, j + 1)
        else:
            j += 1
    return False, j


def _is_single_quoted_url_after_slot(next_static: str, *, is_last_slot: bool) -> bool:
    """Decide from `next_static` (`tpl.strings[k + 1]`) whether `url({slot}...)` quotes `slot`."""
    if not next_static:
        return is_last_slot
    if next_static[0] in (" ", "\t", "\n", ")"):
        return True
    pos = 0
    n = len(next_static)
    while pos + 1 < n and next_static[pos] == "/" and next_static[pos + 1] == "*":
        end_c = next_static.find("*/", pos + 2)
        if end_c == -1:
            return False
        pos = end_c + 2
        if pos < n and next_static[pos] in (" ", "\t", "\n", ")"):
            return True
    return False


def _tail_at_end(scan_str: str, tok_start: int, compressed_prefix: str) -> str:
    if len(scan_str) - tok_start <= _MAX_RAW_TAIL:
        return scan_str[tok_start:]
    return compressed_prefix + _trailing_escape_suffix(scan_str)


def _step_other_top_level(
    scan_str: str,
    i: int,
    *,
    tok_is_static: bool,
) -> tuple[int, str, bool]:
    """Consume one non-comment, non-string, non-ident top-level token starting at `i`."""
    n = len(scan_str)
    ch = scan_str[i]
    if ch in (" ", "\t", "\n"):
        while i < n and scan_str[i] in (" ", "\t", "\n"):
            i += 1
        return i, "", False
    if scan_str.startswith("<!--", i):
        return i + 4, "", False
    if scan_str.startswith("-->", i):
        return i + 3, "", False
    if ch == "#":
        tok_start = i
        if i + 1 < n and (
            _is_ident_code_point(scan_str[i + 1]) or _starts_with_valid_escape(scan_str, i + 1)
        ):
            _, i = _consume_ident_sequence(scan_str, i + 1)
            return i, (_tail_at_end(scan_str, tok_start, "#a") if i == n else ""), False
        i += 1
        return i, ("#" if i == n else ""), False
    if _starts_a_number(scan_str, i):
        tok_start = i
        repr_s, _, is_int, i = _consume_number(scan_str, i)
        if _starts_an_ident_sequence(scan_str, i):
            _, i = _consume_ident_sequence(scan_str, i)
            return i, (_tail_at_end(scan_str, tok_start, "1a") if i == n else ""), False
        if i < n and scan_str[i] == "%":
            return i + 1, "", False
        if i == n:
            num_prefix = (
                "1e0" if ("e" in repr_s or "E" in repr_s) else ("1.0" if not is_int else "1")
            )
            return i, _tail_at_end(scan_str, tok_start, num_prefix), False
        return i, "", False
    if ch == "@":
        tok_start = i
        if _starts_an_ident_sequence(scan_str, i + 1):
            _, i = _consume_ident_sequence(scan_str, i + 1)
            return i, (_tail_at_end(scan_str, tok_start, "@a") if i == n else ""), False
        i += 1
        return i, ("@" if i == n else ""), False
    i += 1
    if i == n:
        for cdo_prefix in ("<!-", "<!", "<"):
            if (
                scan_str.endswith(cdo_prefix)
                and _trailing_escape_suffix(scan_str[: n - len(cdo_prefix)]) != "\\"
            ):
                return i, cdo_prefix, False
        if ch in (".", "+", "-", "/", "\\"):
            return i, ch, (ch == "\\" and tok_is_static)
    return i, "", False


def _advance_dynamic_tail(prev_tail: str, emitted: str) -> str:
    """Update top-level token prefix `tail` across a rendered raw interpolation."""
    norm = _preprocess_css_chunk(emitted)
    if not norm:
        return prev_tail
    scan_str = prev_tail + norm
    i = 0
    n = len(scan_str)
    tail = ""
    while i < n:
        ch = scan_str[i]
        if ch == "/" and i + 1 < n and scan_str[i + 1] == "*":
            end_c = scan_str.find("*/", i + 2)
            if end_c == -1:
                return ""
            tail = ""
            i = end_c + 2
            continue
        if ch in ('"', "'"):
            closed, consumed = _scan_string_body(scan_str[i + 1 :], ch)
            if not closed:
                return ""
            tail = ""
            i += 1 + consumed
            continue
        if not scan_str.startswith("-->", i) and _starts_an_ident_sequence(scan_str, i):
            tok_start = i
            ident, i = _consume_ident_sequence(scan_str, i)
            if i < n and scan_str[i] == "(":
                i += 1
                tail = ""
                if ident.lower() == "url":
                    look = i
                    while look < n and scan_str[look] in (" ", "\t", "\n"):
                        look += 1
                    if look < n and scan_str[look] not in ('"', "'"):
                        closed_u, consumed_u = _scan_unquoted_url_body(scan_str[look:])
                        if not closed_u:
                            return ""
                        i = look + consumed_u
            else:
                tail = _tail_at_end(scan_str, tok_start, "x") if i == n else ""
            continue
        i, tail, _ = _step_other_top_level(scan_str, i, tok_is_static=False)
    return tail


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

    raw_strings = tpl.strings
    interpolations = tpl.interpolations
    norm_strings = [_preprocess_css_chunk(s) for s in raw_strings]
    num_interps = len(interpolations)

    out: list[str] = []
    in_comment = start_in_comment
    comment_star_pending = False
    in_quote: str | None = None
    url_mode: str | None = None
    pending_escape = ""
    tail = ""
    tail_from_static = False

    for idx, raw_s in enumerate(raw_strings):
        if raw_s:
            out.append(raw_s)
        norm = norm_strings[idx]
        if norm:
            if not in_comment and in_quote is None and url_mode is None and tail:
                scan_str = tail + norm
                offset = len(tail)
                carry_static = tail_from_static
                tail = ""
                tail_from_static = False
            else:
                scan_str = norm
                offset = 0
                carry_static = False

            i = 0
            n = len(scan_str)
            while i < n:
                if in_comment:
                    if start_in_comment:
                        break
                    if comment_star_pending and scan_str[i] == "/":
                        in_comment = False
                        comment_star_pending = False
                        i += 1
                        continue
                    end_c = scan_str.find("*/", i)
                    if end_c == -1:
                        comment_star_pending = scan_str.endswith("*")
                        break
                    in_comment = False
                    comment_star_pending = False
                    i = end_c + 2
                    continue

                if in_quote is not None:
                    pref_len = len(pending_escape)
                    sub = pending_escape + scan_str[i:]
                    pending_escape = ""
                    closed_quote, consumed = _scan_string_body(sub, in_quote)
                    if closed_quote:
                        in_quote = None
                        i += consumed - pref_len
                        continue
                    pending_escape = _trailing_escape_suffix(sub)
                    break

                if url_mode == "unquoted":
                    pref_len = len(pending_escape)
                    sub = pending_escape + scan_str[i:]
                    pending_escape = ""
                    closed_url, consumed = _scan_unquoted_url_body(sub)
                    if closed_url:
                        url_mode = None
                        i += consumed - pref_len
                        continue
                    pending_escape = _trailing_escape_suffix(sub)
                    break

                ch = scan_str[i]
                if ch == "/" and i + 1 < n and scan_str[i + 1] == "*":
                    in_comment = True
                    comment_star_pending = False
                    tail = ""
                    tail_from_static = False
                    i += 2
                    continue
                if ch in ('"', "'"):
                    in_quote = ch
                    url_mode = None
                    pending_escape = ""
                    tail = ""
                    tail_from_static = False
                    i += 1
                    continue
                if not scan_str.startswith("-->", i) and _starts_an_ident_sequence(scan_str, i):
                    tok_start = i
                    is_static_tok = tok_start >= offset or carry_static
                    ident, i = _consume_ident_sequence(scan_str, i)
                    if i < n and scan_str[i] == "(":
                        paren_pos = i
                        i += 1
                        tail = ""
                        tail_from_static = False
                        if paren_pos >= offset and is_static_tok and ident.lower() == "url":
                            look = i
                            while look < n and scan_str[look] in (" ", "\t", "\n"):
                                look += 1
                            if look < n and scan_str[look] in ('"', "'"):
                                i = look
                            elif look < n:
                                url_mode = "unquoted"
                                pending_escape = ""
                                i = look
                            elif idx < num_interps:
                                url_mode = (
                                    "quoted_single"
                                    if _is_single_quoted_url_after_slot(
                                        norm_strings[idx + 1],
                                        is_last_slot=(idx + 1 == num_interps),
                                    )
                                    else "unquoted"
                                )
                                pending_escape = ""
                                i = look
                    elif i == n:
                        tail = _tail_at_end(scan_str, tok_start, "x")
                        tail_from_static = is_static_tok
                    else:
                        tail = ""
                        tail_from_static = False
                    continue

                i, tail, tail_from_static = _step_other_top_level(
                    scan_str,
                    i,
                    tok_is_static=(i >= offset or carry_static),
                )

        if idx >= num_interps:
            break

        item = interpolations[idx]
        val = convert(item.value, item.conversion)
        if in_comment:
            text = _format_template_value(val, item.format_spec, options=options)
            cleaned = (
                text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\f", " ")
            )
            while "*/" in cleaned:
                cleaned = cleaned.replace("*/", "* /")
            if cleaned:
                if comment_star_pending and cleaned.startswith("/"):
                    cleaned = " " + cleaned
                comment_star_pending = False
                out.append(cleaned)
        elif in_quote is not None:
            text = format(val, item.format_spec) if item.format_spec else str(val)
            if text:
                q_opts = (
                    options
                    if options.quote_char == in_quote
                    else SerializationOptions(quote_char=in_quote)  # type: ignore[arg-type]
                )
                # Strip the outer quotes added by serialize_string
                escaped = serialize_string(text, options=q_opts)[1:-1]
                out.append(_resolve_pending_escape_prefix(pending_escape, escaped) + escaped)
                pending_escape = ""
        elif url_mode == "quoted_single":
            text = format(val, item.format_spec) if item.format_spec else str(val)
            out.append(serialize_string(text, options=options))
            url_mode = None
            tail = ""
            tail_from_static = False
        elif url_mode == "unquoted":
            text = format(val, item.format_spec) if item.format_spec else str(val)
            if text:
                escaped = _escape_unquoted_url_value(text)
                out.append(_resolve_pending_escape_prefix(pending_escape, escaped) + escaped)
                pending_escape = ""
        else:
            if not item.format_spec and not isinstance(val, bool) and isinstance(val, (int, float)):
                emitted = serialize_number(val)
            else:
                emitted = _format_template_value(val, item.format_spec, options=options)
            if emitted:
                out.append(emitted)
                tail = _advance_dynamic_tail(tail, emitted)
                tail_from_static = False

    if in_comment and not start_in_comment:
        raise StixisSyntaxError("Unclosed CSS comment `/*` in Template")
    if in_quote is not None:
        raise StixisSyntaxError(f"Unclosed CSS string quote {in_quote!r} in Template")

    return "".join(out)
