# Stixis

Stixis (`stixis`) is a CSS serializer and validator for Python 3.14+ with zero runtime dependencies. It generates its grammar tables and `TypedDict` definitions from W3C `@webref/css` (`ed/css/*.json`), derives property browser-support counts from `@mdn/browser-compat-data` (`css/properties/*.json`), and builds its test suite from Web Platform Tests (WPT) and Hypothesis.

## What it does

* **Serializes and canonicalizes CSS values against W3C Value Definition Syntax (`serialize_value`, `canonicalize`, and the CLI).** `serialize_value` formats structured Python values and PEP 750 `Template` strings, and `canonicalize` validates and normalizes raw CSS token streams against `@webref/css` grammars (`|`, `||`, `&&`, juxtaposition, `?`, `*`, `+`, `#`, `{A,B}`, `[...]!`, `<type [min,max]>`, `<'prop'>`, compiled into an internal `ValueNode` tree by the internal `compile_value_syntax`). `<!--` and `-->` are tokenized as `<CDO-token>` and `<CDC-token>`, so typed CSS properties reject them while custom property `<any-value>` allows them. `canonicalize` validates relative color channel keywords (the internal `RELATIVE_COLOR_CHANNEL_KEYWORDS` table generated from `@webref/css`, such as `r`, `g`, `b`, `alpha` in `rgb()` and `j`, `a`, `b`, `alpha` in `jzazbz()`) both as bare channel operands (`rgb(from rebeccapurple r g b)`) and inside math expressions (`calc(r * 0.5)`) when `from <color>` is present while rejecting them in absolute color functions, and simplifies math expressions using the context-free subset of CSS Values 4 §10.10 (folding pure numbers and same-unit terms, sorting sum terms as `number`, `percentage`, alphabetical dimensions, then other terms, placing numeric/dimensional factors first in products, unwrapping nested `calc()`, and keeping outer `calc()` on single values; unit conversion across different units, `min()`/`max()`/`clamp()` evaluation to `calc()`, and dimensional unit-type checking are not performed). `serialize_value`, `canonicalize`, and the CLI (`stixis inspect`, `stixis check`) share target resolution (via the internal `resolve_target_syntax`): standard CSS properties (internal `PROPERTY_SYNTAXES`), custom properties (`--*`), `@at-rule` descriptors (`@font-face/src` or `at_rule="@font-face"`), `@webref/css` value types (internal `VALUE_SYNTAXES`), and evaluator-supported built-in primitive types (internal `SUPPORTED_BUILTIN_TYPES`, such as `<custom-ident>`, `<integer>`, `<number>`, `<string>`, `<urange>`, and `<hex-color>`, angled or bare); unknown `<...>` targets are rejected with `StixisValidationError` (exit `4` on the CLI). Standard and custom properties accept CSS-wide keywords (internal `CSS_WIDE_KEYWORDS`: `initial`, `inherit`, `unset`, `revert`, `revert-layer`, `revert-rule`), whereas `<type>` targets and `@at-rule` descriptors reject them (`css-values-4` §3.1.1); `default` is accepted only where a property's grammar includes it (such as `cursor`), while reserved custom-ident keywords (internal `RESERVED_CUSTOM_IDENT_KEYWORDS`: `CSS_WIDE_KEYWORDS | {"default"}`) remain excluded from `<custom-ident>` and unquoted `<family-name>`.
* **Gates experimental CSS properties by engine support (`SerializationOptions(min_engines=...)` and `--min-engines`).** By default (`SerializationOptions(min_engines=2)`), Stixis enables CSS properties shipped unprefixed and without flags in at least 2 core browser engines (Blink/Chrome, Gecko/Firefox, WebKit/Safari) according to `@mdn/browser-compat-data` (internal `PROPERTY_ENGINE_COUNTS`), plus the internal `ALWAYS_ALLOWED_PROPERTIES` in `codegen/spec_patches.toml`. BCD entries marked `partial_implementation: true` (such as `overflow-clip-margin` in Chrome or `overscroll-behavior` in Safari, where the unprefixed CSS property is shipped and parsed in CSSOM with layout notes) count toward the internal `PROPERTY_ENGINE_COUNTS` table. `min_engines` checks top-level CSS properties only; it does not gate `@at-rule` descriptors, `<type>` targets, or individual CSS functions/keywords inside a property's `@webref/css` grammar (such as `calc-size()`). Pass `SerializationOptions(min_engines=1)`, `extra_properties=frozenset({"prop-name"})`, or `--min-engines 1` (`0..3`) on the CLI to opt into single-engine properties. `SerializationOptions` validates all fields in `__post_init__` and raises `ValueError` on invalid options.
* **Validates AST nodes and primitives on construction.** `FontFace`, `StyleRule`, `AtRule`, `StyleSheet`, `CommentNode`, `RawDeclaration`, `FontSrc`, and `Url` (including `Url.modifiers` validated against `<url-modifier>`) are frozen, hashable dataclasses (`dataclass(frozen=True, slots=True)`) that check selectors, preludes, descriptors, declarations, comments, and URL modifiers in `__post_init__`, copy mutable containers (`declarations`, `descriptors`, `rules`, `selector` sequences, `FontSrc.tech`) into immutable tuples so later caller mutations cannot alter a validated node, and raise `StixisValidationError` or `StixisSyntaxError` on invalid input.
* **Escapes PEP 750 `Template` (`t"..."`) slots by context (`serialize_value` and AST nodes).** Passing a `Template` to `serialize_value` or an AST node field (`FontFace`, `StyleRule`, `AtRule`, `CommentNode`) inspects the CSS tokens before each `Interpolation` slot (via the internal `render_css_template`), escapes comments (`/* ... */`), strings, `url(...)`, and numbers accordingly, and validates the rendered tokens against the target grammar.
* **Handles five CSS microsyntaxes.**
  1. `<urange>`: `parse_urange`, `format_urange`, and `compact_urange` for cascade-shadowing range compaction.
  2. `<opentype-tag>`: `font-feature-settings`, `font-variation-settings`, and `font-language-override`.
  3. `<font-src-list>`: `FontSrc`, `Url`, and `LocalFont`, enforcing `format()` before `tech()`.
  4. `<family-name>` and `<generic-family>`: quotes Python `str` family names by default, requires `generic("serif")` or `Keyword("serif")` for generic families, and matches WPT `font-family-serialization-001.html`.
  5. `{1,2}` descriptor and property ranges: `font-weight`, `font-width` and `font-stretch`, `font-style: oblique`, and metric overrides.
* **Includes a read-only CLI.** `stixis inspect` and `stixis check` exit with `0` for valid input, `1` for a validation error, `2` for a usage error, and `4` for an unknown target.

Only the top-level `stixis` package is public; submodules such as `stixis._syntax`, `stixis._grammar`, `stixis.values`, `stixis.stylesheet`, and `stixis.api` are internal and not part of the public API.

## Quickstart

```python
from stixis import (
    Deg,
    FontFace,
    FontSrc,
    Pct,
    Px,
    SerializationOptions,
    StyleRule,
    StyleSheet,
    compact_urange,
    dumps,
    generic,
)

if __name__ == "__main__":
    woff2_url = "https://example.com/fonts/noto-sans-jp-slice0.woff2"
    slice_id = "kana"
    size_kb = 18.4

    urange = compact_urange(
        sorted_target_cps=[0x3000, 0x3041, 0x3042, 0x3044],
        forbidden_sorted=[0x3043],
        max_gap=0x80,
    )

    sheet = StyleSheet(
        header_comment="Generated by Stixis",
        rules=[
            FontFace(
                comment=t"Slice [{slice_id}] ({size_kb:.1f} KB)",
                font_family="Noto Sans JP",
                font_style=("oblique", Deg(-10), Deg(20)),
                font_weight=(100, 900),
                font_width=(Pct(75), Pct(125)),
                font_display="swap",
                src=t"url({woff2_url}) format(woff2) tech(variations)",
                unicode_range=urange,
            ),
            StyleRule(
                "body",
                {
                    "font_family": ["Noto Sans JP", generic("sans-serif")],
                    "font_feature_settings": {"liga": True, "dlig": False},
                    "margin": Px(0),
                },
            ),
        ],
    )

    print(dumps(sheet))
    print(dumps(sheet, options=SerializationOptions.cleome()))
```

## CLI usage

```bash
# Inspect an at-rule, property, or <type>
uv run stixis inspect @font-face
uv run stixis inspect font-style --json
uv run stixis inspect "<font-src>"

# Validate and canonicalize CSS values (--min-engines accepts 0..3)
uv run stixis check font-weight "100 900" --at-rule @font-face
uv run stixis check unicode-range "u+3040-309f" --at-rule @font-face
uv run stixis check hanging-punctuation "first" --min-engines 1
```

## Development and verification

`codegen/gen_wpt_tests.py` extracts passing and `xfail` assertions across 5 WPT test modules in `tests/_generated/` (plus assertions skipped for `min_engines < 2` or unknown property names, recorded in `SKIPPED_PROPERTY_COUNTS`); run `uv run python codegen/gen_wpt_tests.py --check` to see the current counts. Known `xfail` vectors (such as CSS Values 4 `calc(...)` algebraic simplification/dimensional type-checking and CSSOM shorthand collapsing) are tracked with explicit reason codes and fail on unexpected `XPASS`.

```bash
uv sync
uv run python codegen/gen_specs.py --check
uv run python codegen/gen_wpt_tests.py --check
uv run pytest
uv run ruff check
```
