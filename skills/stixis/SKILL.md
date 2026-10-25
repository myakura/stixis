---
name: stixis
description: Serialize and validate CSS rules, @font-face descriptors, unicode-range subsets, and PEP 750 Template strings using the Stixis (`stixis`) Python 3.14+ library and CLI. Activate when building CSS stylesheets, webfont subset CSS, or validating CSS values in Python.
---

# Stixis (`stixis`) Consumer Guide

Stixis is a spec-driven, zero-dependency CSS serializer and validator for Python 3.14+. All AST nodes (`FontFace`, `StyleRule`, `AtRule`, `StyleSheet`, `CommentNode`, `RawDeclaration`) validate eagerly on construction (`__post_init__`) against W3C `@webref/css` Value Definition Syntax grammars and raise `StixisValidationError` or `StixisSyntaxError` immediately on invalid input.

## Core API Signatures

```python
from stixis import (
    AtRule,
    CommentNode,
    Deg,
    Dimension,
    Em,
    FontFace,
    FontSrc,
    GenericFamily,
    Keyword,
    LocalFont,
    Pct,
    Px,
    RawDeclaration,
    Rem,
    SerializationOptions,
    StixisError,
    StixisSyntaxError,
    StixisValidationError,
    StyleRule,
    StyleSheet,
    Url,
    canonicalize,
    compact_urange,
    dumps,
    format_urange,
    generic,
    parse_urange,
    parse_urange_codepoints,
    serialize_declarations,
    serialize_value,
)
```

* `dumps(node_or_nodes, *, minify: bool = False, options: SerializationOptions | None = None) -> str` (alias: `serialize`): Render a `StyleSheet`, single `Rule`, or iterable of `Rule`s.
* `FontFace(descriptors=None, *, comment=None, options=None, **kw_descriptors) -> AtRule`: Typed `@font-face` constructor backed by `FontFaceDescriptors` (`TypedDict`). Accepts `snake_case` keyword arguments (`font_family`, `font_style`, `font_weight`, `font_width`, `font_display`, `src`, `unicode_range`, `size_adjust`, `ascent_override`, `descent_override`, `line_gap_override`, `font_feature_settings`, `font_variation_settings`).
* `StyleRule(selector, declarations=..., *, rules=(), comment=None, options=None)`
* `AtRule(name, prelude=None, *, descriptors=None, rules=(), comment=None, statement=False, options=None)`
* `StyleSheet(rules, *, header_comment=None, options=None)`
* `serialize_value(target: str, value: Any, *, at_rule: str | None = None, options: SerializationOptions | None = None) -> str`: Validate and serialize a Python value or PEP 750 `Template`.
* `canonicalize(target: str, css_text: str, *, at_rule: str | None = None, options: SerializationOptions | None = None) -> str`: Tokenize, validate, and normalize raw CSS value text.

## Python Native Types to CSS Mapping

* **`font-family` (`<family-name>` vs. `<generic-family>`)**:
  * Plain `str` is **always** treated as a `<family-name>` and quoted: `"Noto Sans JP"` -> `"\"Noto Sans JP\""`, `"serif"` -> `"\"serif\""`.
  * Generic families must use `generic("sans-serif")`, `GenericFamily("serif")`, or `Keyword("system-ui")`.
  * Fallback list: `["Noto Sans JP", generic("sans-serif")]` -> `"\"Noto Sans JP\", sans-serif"`.
* **`{1,2}` Descriptor & Property Ranges**:
  * `font_weight=400` -> `"400"`; `font_weight=(100, 900)` -> `"100 900"` (validates `1 <= w <= 1000`).
  * `font_width=Pct(100)` -> `"100%"`; `font_width=(Pct(75), Pct(125))` or `("75%", "125%")` -> `"75% 125%"`.
  * `font_style="italic"` -> `"italic"`; `font_style=("oblique", Deg(-10), Deg(20))` -> `"oblique -10deg 20deg"` (validates `-90deg <= a <= 90deg`).
  * `ascent_override=(Pct(90), Pct(100))` -> `"90% 100%"`.
* **`@font-face` `src` (`<font-src-list>`)**:
  * Structured: `FontSrc(url="sub-0.woff2", format="woff2", tech=("variations", "color-COLRv1"))` -> `'url("sub-0.woff2") format(woff2) tech(variations, color-colrv1)'` (`format()` is always emitted before `tech()`, and `tech()` keywords are lowercased).
  * List with local fallback: `[LocalFont("Inter"), FontSrc(url="inter.woff2", format="woff2")]`.
  * PEP 750 `Template`: `t"url({woff2_url}) format(woff2) tech(variations)"`.
* **OpenType Feature & Variation Settings**:
  * `font_feature_settings={"liga": True, "dlig": False, "swsh": 2}` -> `'"liga", "dlig" 0, "swsh" 2'`.
  * `font_variation_settings={"wght": 700, "wdth": 87.5}` or `[("wght", 700), ("wdth", 87.5)]` -> `'"wght" 700, "wdth" 87.5'`.
* **`unicode-range` (`<urange>#`) & Cascade Shadowing Compaction**:
  * `unicode_range=[0x3000, (0x3040, 0x309F)]` or `set[int]` -> `"U+3000, U+3040-309F"`.
  * When generating multi-slice `@font-face` rules, use `compact_urange(sorted_target_cps, forbidden_sorted, max_gap=0x80)` where `forbidden_sorted` is the sorted list of code points present in **later** slices (`all_font_cps - current_slice_cps`). This bridges small gaps up to `max_gap` without shadowing code points owned by subsequent `@font-face` rules.

## PEP 750 `Template` (`t"..."`) Usage

Use Python 3.14+ `t"..."` templates when mixing static CSS structure with dynamic Python variables:

* **URLs**: `src=t"url({slice_filename}) format(woff2)"` automatically quotes and escapes `slice_filename` inside `url(...)`.
* **Comments**: `comment=t"[{slice_id}] {description} ({size_kb:.1f} KB)"` automatically sanitizes any `*/` inside interpolated values to `* /` so untrusted strings cannot break out of `/* ... */`.
* **Values**: `font_style=t"oblique {min_angle}deg {max_angle}deg"` formats numbers (rejecting `bool`, `nan`, `inf`) and validates the resulting tokens against the `ValueNode` grammar.

## Presets (`SerializationOptions`)

* `SerializationOptions()` (default): Double quotes `"`, `<family-name>` always quoted, `font-width` descriptor name, unquoted `format(woff2)`.
* `SerializationOptions.cleome()`: Single quotes `'`, `@font-face` outputs `font-stretch` instead of `font-width`, and quotes `format('woff2')`.
* `SerializationOptions.cssom()`: W3C CSSOM shortest serialization (unquotes valid `<custom-ident>+` family names such as `Noto Sans JP` while keeping digit-starting or keyword-colliding names quoted).

## Read-Only Introspection CLI (`stixis`)

Instead of reading specification files, run the `stixis` CLI to inspect grammars or validate CSS values:

* Inspect a property, `@at-rule`, or `<type>`:
  * `uv run stixis inspect @font-face`
  * `uv run stixis inspect font-style --json`
  * `uv run stixis inspect <font-src>`
* Validate and canonicalize a CSS value (exits `0` with canonical CSS on `stdout`, `1` on invalid CSS, `2` on bad CLI usage, `4` on unknown target):
  * `uv run stixis check font-weight "100 900" --at-rule @font-face`
  * `uv run stixis check unicode-range "u+3040-309f" --at-rule @font-face`
