"""CSS Syntax Level 3 tokenizer (`css-syntax-3` §4)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from stixis.errors import StixisSyntaxError, StixisValidationError


@dataclass(frozen=True, slots=True)
class CssToken:
    """A single token produced by the CSS Syntax Level 3 tokenizer."""

    kind: str  # "ident", "function", "at-keyword", "hash", "string", "url", "number", "percentage", "dimension", "whitespace", "comma", "colon", "semicolon", "delim", "(", ")", "[", "]", "{", "}"
    value: str
    num: int | float | None = None
    is_integer: bool = False
    unit: str = ""
    hash_type: str = "unrestricted"  # "id" or "unrestricted"
    ws_around: bool = field(default=True, compare=False)
    ws_before: bool = field(default=False, compare=False)
    start: int = field(default=0, compare=False)
    raw: str = field(default="", compare=False)


def _consume_escape(text: str, pos: int) -> tuple[str, int]:
    """Consume an escaped code point after `\\` at `pos` (`css-syntax-3` §4.3.7)."""
    n = len(text)
    if pos >= n:
        return "\ufffd", pos
    ch = text[pos]
    if ch in "0123456789abcdefABCDEF":
        hex_digits = [ch]
        pos += 1
        while pos < n and len(hex_digits) < 6 and text[pos] in "0123456789abcdefABCDEF":
            hex_digits.append(text[pos])
            pos += 1
        if pos < n and text[pos] in (" ", "\t", "\n", "\r", "\f"):
            if text[pos : pos + 2] == "\r\n":
                pos += 2
            else:
                pos += 1
        cp = int("".join(hex_digits), 16)
        if cp == 0 or (0xD800 <= cp <= 0xDFFF) or cp > 0x10FFFF:
            return "\ufffd", pos
        return chr(cp), pos
    return ch, pos + 1


def _is_ident_start_char(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x80 or ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_"


def _is_ident_code_point(ch: str) -> bool:
    return _is_ident_start_char(ch) or ("0" <= ch <= "9") or ch == "-"


def _starts_with_valid_escape(text: str, pos: int) -> bool:
    if pos >= len(text) or text[pos] != "\\":
        return False
    if pos + 1 >= len(text):
        return True
    return text[pos + 1] not in ("\n", "\r", "\f")


def _starts_an_ident_sequence(text: str, pos: int) -> bool:
    n = len(text)
    if pos >= n:
        return False
    ch = text[pos]
    if ch == "-":
        return bool(
            pos + 1 < n
            and (
                _is_ident_start_char(text[pos + 1])
                or text[pos + 1] == "-"
                or _starts_with_valid_escape(text, pos + 1)
            )
        )
    if _is_ident_start_char(ch):
        return True
    if ch == "\\":
        return _starts_with_valid_escape(text, pos)
    return False


def _consume_ident_sequence(text: str, pos: int) -> tuple[str, int]:
    n = len(text)
    chars: list[str] = []
    while pos < n:
        ch = text[pos]
        if _is_ident_code_point(ch):
            chars.append(ch)
            pos += 1
        elif _starts_with_valid_escape(text, pos):
            esc_ch, pos = _consume_escape(text, pos + 1)
            chars.append(esc_ch)
        else:
            break
    return "".join(chars), pos


def _starts_a_number(text: str, pos: int) -> bool:
    n = len(text)
    if pos >= n:
        return False
    ch = text[pos]
    if ch in ("+", "-"):
        if pos + 1 >= n:
            return False
        nxt = text[pos + 1]
        if "0" <= nxt <= "9":
            return True
        return bool(nxt == "." and pos + 2 < n and ("0" <= text[pos + 2] <= "9"))
    if ch == ".":
        return pos + 1 < n and ("0" <= text[pos + 1] <= "9")
    return "0" <= ch <= "9"


def _consume_number(text: str, pos: int) -> tuple[str, int | float, bool, int]:
    n = len(text)
    start = pos
    is_int = True
    if pos < n and text[pos] in ("+", "-"):
        pos += 1
    while pos < n and ("0" <= text[pos] <= "9"):
        pos += 1
    if pos + 1 < n and text[pos] == "." and ("0" <= text[pos + 1] <= "9"):
        is_int = False
        pos += 2
        while pos < n and ("0" <= text[pos] <= "9"):
            pos += 1
    if pos + 1 < n and text[pos] in ("e", "E"):
        if "0" <= text[pos + 1] <= "9":
            is_int = False
            pos += 2
            while pos < n and ("0" <= text[pos] <= "9"):
                pos += 1
        elif pos + 2 < n and text[pos + 1] in ("+", "-") and ("0" <= text[pos + 2] <= "9"):
            is_int = False
            pos += 3
            while pos < n and ("0" <= text[pos] <= "9"):
                pos += 1
    repr_str = text[start:pos]
    if is_int:
        try:
            int_val = int(repr_str)
        except ValueError:
            int_val = None
        if int_val is not None:
            try:
                float(int_val)
            except OverflowError:
                return repr_str, (-math.inf if int_val < 0 else math.inf), False, pos
            return repr_str, int_val, True, pos
    num_val = float(repr_str)
    return repr_str, num_val, is_int, pos


def _consume_unquoted_url(text: str, pos: int) -> tuple[str, int]:
    n = len(text)
    while pos < n and text[pos] in (" ", "\t", "\n", "\r", "\f"):
        pos += 1
    chars: list[str] = []
    while pos < n:
        ch = text[pos]
        if ch == ")":
            return "".join(chars), pos + 1
        if ch in (" ", "\t", "\n", "\r", "\f"):
            while pos < n and text[pos] in (" ", "\t", "\n", "\r", "\f"):
                pos += 1
            if pos < n and text[pos] == ")":
                return "".join(chars), pos + 1
            raise StixisSyntaxError("Bad URL token: whitespace inside unquoted url()")
        if ch in ('"', "'", "(") or ord(ch) <= 0x1F or ord(ch) == 0x7F:
            raise StixisSyntaxError(
                f"Bad URL token: invalid character {ch!r} inside unquoted url()"
            )
        if ch == "\\":
            if _starts_with_valid_escape(text, pos):
                esc_ch, pos = _consume_escape(text, pos + 1)
                chars.append(esc_ch)
            else:
                raise StixisSyntaxError("Bad URL token: invalid escape inside unquoted url()")
        else:
            chars.append(ch)
            pos += 1
    raise StixisSyntaxError("Unclosed url() token")


def _has_ws_after(text: str, pos: int) -> bool:
    n = len(text)
    while pos < n and text[pos] == "/" and pos + 1 < n and text[pos + 1] == "*":
        end_comment = text.find("*/", pos + 2)
        if end_comment == -1:
            return False
        pos = end_comment + 2
    return pos < n and text[pos] in (" ", "\t", "\n")


def tokenize_css(text: str, *, preserve_whitespace: bool = False) -> list[CssToken]:
    """Tokenize a CSS string according to W3C CSS Syntax Module Level 3 §4."""
    # Preprocess newlines and NULL characters per §3.3
    normalized = (
        text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").replace("\x00", "\ufffd")
    )
    tokens: list[CssToken] = []
    pos = 0
    n = len(normalized)
    had_ws_before = False

    while pos < n:
        ch = normalized[pos]

        # Comments
        if ch == "/" and pos + 1 < n and normalized[pos + 1] == "*":
            end_comment = normalized.find("*/", pos + 2)
            if end_comment == -1:
                raise StixisSyntaxError("Unclosed CSS comment `/*`")
            pos = end_comment + 2
            continue

        # Stray closing comment (only when the `/` does not start a `/*` comment)
        if (
            ch == "*"
            and pos + 1 < n
            and normalized[pos + 1] == "/"
            and not (pos + 2 < n and normalized[pos + 2] == "*")
        ):
            raise StixisSyntaxError("Unexpected comment close `*/`")

        # Whitespace
        if ch in (" ", "\t", "\n"):
            start = pos
            while pos < n and normalized[pos] in (" ", "\t", "\n"):
                pos += 1
            if preserve_whitespace:
                tokens.append(
                    CssToken(
                        "whitespace",
                        normalized[start:pos],
                        start=start,
                        raw=normalized[start:pos],
                    )
                )
            had_ws_before = True
            continue

        cur_ws_before = had_ws_before
        had_ws_before = False
        tok_start = pos

        # CDO `<!--` and CDC `-->`
        if normalized.startswith("<!--", pos):
            pos += 4
            tokens.append(
                CssToken("CDO", "<!--", ws_before=cur_ws_before, start=tok_start, raw="<!--")
            )
            continue
        if normalized.startswith("-->", pos):
            pos += 3
            tokens.append(
                CssToken("CDC", "-->", ws_before=cur_ws_before, start=tok_start, raw="-->")
            )
            continue

        # Strings
        if ch in ('"', "'"):
            quote = ch
            pos += 1
            chars: list[str] = []
            closed = False
            while pos < n:
                c = normalized[pos]
                if c == quote:
                    pos += 1
                    closed = True
                    break
                if c == "\n":
                    raise StixisSyntaxError("Unescaped newline inside CSS string literal")
                if c == "\\":
                    if pos + 1 >= n:
                        break
                    if normalized[pos + 1] == "\n":
                        pos += 2
                        continue
                    esc_ch, pos = _consume_escape(normalized, pos + 1)
                    chars.append(esc_ch)
                else:
                    chars.append(c)
                    pos += 1
            if not closed:
                raise StixisSyntaxError(f"Unclosed CSS string literal starting with {quote!r}")
            tokens.append(
                CssToken(
                    "string",
                    "".join(chars),
                    ws_before=cur_ws_before,
                    start=tok_start,
                    raw=normalized[tok_start:pos],
                )
            )
            continue

        # Hash `#`
        if ch == "#":
            if pos + 1 < n and (
                _is_ident_code_point(normalized[pos + 1])
                or _starts_with_valid_escape(normalized, pos + 1)
            ):
                hash_type = (
                    "id" if _starts_an_ident_sequence(normalized, pos + 1) else "unrestricted"
                )
                ident, pos = _consume_ident_sequence(normalized, pos + 1)
                tokens.append(
                    CssToken(
                        "hash",
                        ident,
                        hash_type=hash_type,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            else:
                pos += 1
                tokens.append(
                    CssToken("delim", "#", ws_before=cur_ws_before, start=tok_start, raw="#")
                )
            continue

        # Numeric tokens
        if _starts_a_number(normalized, pos):
            repr_str, num_val, is_int, pos = _consume_number(normalized, pos)
            if _starts_an_ident_sequence(normalized, pos):
                unit, pos = _consume_ident_sequence(normalized, pos)
                tokens.append(
                    CssToken(
                        "dimension",
                        repr_str,
                        num=num_val,
                        is_integer=is_int,
                        unit=unit,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            elif pos < n and normalized[pos] == "%":
                pos += 1
                tokens.append(
                    CssToken(
                        "percentage",
                        repr_str,
                        num=num_val,
                        is_integer=is_int,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            else:
                tokens.append(
                    CssToken(
                        "number",
                        repr_str,
                        num=num_val,
                        is_integer=is_int,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            continue

        # Ident-like tokens
        if _starts_an_ident_sequence(normalized, pos):
            ident, pos = _consume_ident_sequence(normalized, pos)
            if pos < n and normalized[pos] == "(":
                pos += 1
                if ident.lower() == "url":
                    look = pos
                    while look < n and normalized[look] in (" ", "\t", "\n"):
                        look += 1
                    if look < n and normalized[look] not in ('"', "'"):
                        url_val, pos = _consume_unquoted_url(normalized, look)
                        tokens.append(
                            CssToken(
                                "url",
                                url_val,
                                ws_before=cur_ws_before,
                                start=tok_start,
                                raw=normalized[tok_start:pos],
                            )
                        )
                        continue
                tokens.append(
                    CssToken(
                        "function",
                        ident,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            else:
                tokens.append(
                    CssToken(
                        "ident",
                        ident,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            continue

        # At-keyword
        if ch == "@":
            if _starts_an_ident_sequence(normalized, pos + 1):
                ident, pos = _consume_ident_sequence(normalized, pos + 1)
                tokens.append(
                    CssToken(
                        "at-keyword",
                        ident,
                        ws_before=cur_ws_before,
                        start=tok_start,
                        raw=normalized[tok_start:pos],
                    )
                )
            else:
                pos += 1
                tokens.append(
                    CssToken("delim", "@", ws_before=cur_ws_before, start=tok_start, raw="@")
                )
            continue

        # Single-character punctuation tokens
        if ch in ("(", ")", "[", "]", "{", "}"):
            pos += 1
            tokens.append(CssToken(ch, ch, ws_before=cur_ws_before, start=tok_start, raw=ch))
            continue
        if ch == ",":
            pos += 1
            tokens.append(CssToken("comma", ",", ws_before=cur_ws_before, start=tok_start, raw=","))
            continue
        if ch == ":":
            pos += 1
            tokens.append(CssToken("colon", ":", ws_before=cur_ws_before, start=tok_start, raw=":"))
            continue
        if ch == ";":
            pos += 1
            tokens.append(
                CssToken("semicolon", ";", ws_before=cur_ws_before, start=tok_start, raw=";")
            )
            continue

        tokens.append(
            CssToken(
                "delim",
                ch,
                ws_around=(cur_ws_before and _has_ws_after(normalized, pos + 1)),
                ws_before=cur_ws_before,
                start=tok_start,
                raw=ch,
            )
        )
        pos += 1

    return tokens


def validate_token_stream_safety(
    text: str,
    *,
    forbid_top_level_semicolon: bool = True,
    forbid_top_level_bang: bool = False,
    forbid_top_level_braces: bool = True,
    allow_empty: bool = False,
    target: str = "<declaration-value>",
) -> list[CssToken]:
    """Validate bracket/quote balance and top-level token safety (`<declaration-value>` / selector / prelude)."""
    try:
        tokens = tokenize_css(text, preserve_whitespace=True)
    except StixisSyntaxError as exc:
        raise StixisValidationError(target, target, text, str(exc)) from exc

    non_ws = [t for t in tokens if t.kind != "whitespace"]
    if not non_ws and not allow_empty:
        raise StixisValidationError(target, target, text, f"Empty token stream for {target}")

    stack: list[str] = []
    matching = {"(": ")", "[": "]", "{": "}"}
    closing = {")", "]", "}"}

    for tok in tokens:
        if tok.kind == "function":
            stack.append(")")
        elif tok.kind in matching:
            if not stack and tok.kind == "{" and forbid_top_level_braces:
                raise StixisValidationError(target, target, text, "Unexpected top-level `{` token")
            stack.append(matching[tok.kind])
        elif tok.kind in closing:
            if not stack or stack[-1] != tok.kind:
                raise StixisValidationError(
                    target, target, text, f"Unmatched closing `{tok.kind}` token"
                )
            stack.pop()
        elif not stack:
            if forbid_top_level_semicolon and tok.kind == "semicolon":
                raise StixisValidationError(target, target, text, "Unexpected top-level `;` token")
            if forbid_top_level_bang and tok.kind == "delim" and tok.value == "!":
                raise StixisValidationError(target, target, text, "Unexpected top-level `!` token")

    if stack:
        raise StixisValidationError(
            target, target, text, f"Unclosed bracket expecting `{stack[-1]}`"
        )

    return tokens
