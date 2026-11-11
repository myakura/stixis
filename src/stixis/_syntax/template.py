"""PEP 750 `Template` (`t"..."`) context-aware CSS rendering."""

from __future__ import annotations

from string.templatelib import Interpolation, Template, convert
from typing import Any

from stixis._syntax.serialize import serialize_number, serialize_string, serialize_url
from stixis._syntax.tokenizer import _is_ident_code_point
from stixis.errors import StixisSyntaxError
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

    out: list[str] = []
    in_comment = start_in_comment
    in_quote: str | None = None
    in_unquoted_url = False

    for item in tpl:
        if isinstance(item, str):
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
                else:
                    if ch == "/" and i + 1 < n and item[i + 1] == "*":
                        in_comment = True
                        i += 2
                        continue
                    if ch in ('"', "'"):
                        in_quote = ch
                        in_unquoted_url = False
                    elif ch == ")":
                        in_unquoted_url = False
                    elif (
                        i >= 3
                        and item[i - 3 : i + 1].lower() == "url("
                        and (i < 4 or not _is_ident_code_point(item[i - 4]))
                    ):
                        in_unquoted_url = True
                i += 1
        elif isinstance(item, Interpolation):
            val = convert(item.value, item.conversion)
            if in_comment:
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
                # Render as a quoted string inside url("...")
                out.append(serialize_string(text, options=options))
                in_unquoted_url = False
            elif (
                not item.format_spec and not isinstance(val, bool) and isinstance(val, (int, float))
            ):
                out.append(serialize_number(val))
            else:
                out.append(_format_template_value(val, item.format_spec, options=options))

    if in_comment and not start_in_comment:
        raise StixisSyntaxError("Unclosed CSS comment `/*` in Template")
    if in_quote is not None:
        raise StixisSyntaxError(f"Unclosed CSS string quote {in_quote!r} in Template")

    return "".join(out)
