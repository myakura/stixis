"""PEP 750 `Template` (`t"..."`) comment-only CSS rendering."""

from __future__ import annotations

from string.templatelib import Interpolation, Template, convert
from typing import Any

from stixis._syntax.serialize import serialize_string, serialize_url
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
) -> str:
    """Render a PEP 750 `Template` (`t"..."`) for CSS comments, sanitizing newlines and `*/`."""
    if isinstance(tpl, str):
        return tpl
    if not isinstance(tpl, Template):
        raise TypeError(f"Expected str or string.templatelib.Template, got {type(tpl).__name__}")

    out: list[str] = []
    for item in tpl:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Interpolation):
            val = convert(item.value, item.conversion)
            text = _format_template_value(val, item.format_spec, options=options)
            cleaned = (
                text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\f", " ")
            )
            while "*/" in cleaned:
                cleaned = cleaned.replace("*/", "* /")
            out.append(cleaned)

    return "".join(out)
