"""PEP 750 `Template` (`t"..."`) context-aware CSS rendering."""

from __future__ import annotations

from string.templatelib import Interpolation, Template, convert

from stixis._syntax.serialize import (
    escape_char,
    escape_code_point,
    serialize_number,
    serialize_string,
)
from stixis._syntax.tokenizer import _is_ident_code_point
from stixis.errors import StixisSyntaxError
from stixis.options import DEFAULT_OPTIONS, SerializationOptions

_CSS_WHITESPACE = (" ", "\t", "\n", "\r", "\f")


def _is_standalone_url_slot(items: list[str | Interpolation], start_idx: int) -> bool:
    """Return True if the slot before `start_idx` is followed by `)`, whitespace (e.g. `<url-modifier>`), or `/*`."""
    for item in items[start_idx:]:
        if isinstance(item, Interpolation):
            return False
        if not item:
            continue
        return item[0] == ")" or item[0] in _CSS_WHITESPACE or item.startswith("/*")
    return False


def _escape_unquoted_url(value: str) -> str:
    """Escape unsafe `<url-token>` characters for an interpolation inside unquoted `url(...)`."""
    out: list[str] = []
    for ch in value:
        cp = ord(ch)
        if cp == 0x0000:
            out.append("\ufffd")
        elif (0x0001 <= cp <= 0x001F) or cp == 0x007F or ch == "*":
            out.append(escape_code_point(cp))
        elif ch in (" ", '"', "'", "(", ")", "\\"):
            out.append(escape_char(ch))
        else:
            out.append(ch)
    return "".join(out)


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

    items = list(tpl)
    out: list[str] = []
    in_comment = start_in_comment
    in_quote: str | None = None
    in_unquoted_url = False
    url_has_content = False

    for idx, item in enumerate(items):
        if isinstance(item, str):
            prev_tail = next((chunk[-1] for chunk in reversed(out) if chunk), "")
            out.append(item)
            i = 0
            n = len(item)
            while i < n:
                ch = item[i]
                if in_comment:
                    if not start_in_comment and ch == "*" and i + 1 < n and item[i + 1] == "/":
                        in_comment = False
                        i += 2
                        continue
                elif in_quote is not None:
                    if ch == "\\" and i + 1 < n:
                        i += 2
                        continue
                    if ch == in_quote:
                        in_quote = None
                elif in_unquoted_url:
                    if ch == "\\" and i + 1 < n and item[i + 1] not in ("\n", "\r", "\f"):
                        url_has_content = True
                        i += 2
                        continue
                    if ch in _CSS_WHITESPACE:
                        i += 1
                        continue
                    if ch in ('"', "'") and not url_has_content:
                        in_quote = ch
                        in_unquoted_url = False
                    elif ch == ")":
                        in_unquoted_url = False
                        url_has_content = False
                    else:
                        url_has_content = True
                else:
                    if ch == "/" and i + 1 < n and item[i + 1] == "*":
                        in_comment = True
                        i += 2
                        continue
                    if ch == "\\" and i + 1 < n and item[i + 1] not in ("\n", "\r", "\f"):
                        i += 2
                        continue
                    if ch in ('"', "'"):
                        in_quote = ch
                    elif i >= 3 and item[i - 3 : i + 1].lower() == "url(":
                        before_url = item[i - 4] if i >= 4 else prev_tail
                        if not before_url or (
                            not _is_ident_code_point(before_url) and before_url != "\\"
                        ):
                            in_unquoted_url = True
                            url_has_content = False
                i += 1
        elif isinstance(item, Interpolation):
            val = convert(item.value, item.conversion)
            if in_comment:
                text = format(val, item.format_spec) if item.format_spec else str(val)
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
                text = format(val, item.format_spec) if item.format_spec else str(val)
                q_opts = (
                    options
                    if options.quote_char == in_quote
                    else SerializationOptions(quote_char=in_quote)  # type: ignore[arg-type]
                )
                # Strip the outer quotes added by serialize_string
                escaped = serialize_string(text, options=q_opts)[1:-1]
                out.append(escaped)
            elif in_unquoted_url:
                text = format(val, item.format_spec) if item.format_spec else str(val)
                if not url_has_content and _is_standalone_url_slot(items, idx + 1):
                    # Standalone url({val}) (optionally followed by modifiers/comments) renders quoted
                    out.append(serialize_string(text, options=options))
                    in_unquoted_url = False
                    url_has_content = False
                else:
                    out.append(_escape_unquoted_url(text))
                    url_has_content = True
            elif (
                not item.format_spec and not isinstance(val, bool) and isinstance(val, (int, float))
            ):
                out.append(serialize_number(val))
            else:
                text = format(val, item.format_spec) if item.format_spec else str(val)
                out.append(text)

    if in_comment and not start_in_comment:
        raise StixisSyntaxError("Unclosed CSS comment `/*` in Template")
    if in_quote is not None:
        raise StixisSyntaxError(f"Unclosed CSS string quote {in_quote!r} in Template")

    return "".join(out)
