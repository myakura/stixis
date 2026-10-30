---
name: stixis
description: Serialize and validate CSS rules, @font-face descriptors, unicode-range subsets, and PEP 750 Template strings using the Stixis (`stixis`) Python 3.14+ library and CLI. Activate when building CSS stylesheets, webfont subset CSS, or validating CSS values in Python.
---

# Stixis (`stixis`) consumer guide

Stixis is a CSS serializer and validator for Python 3.14+ with zero runtime dependencies. All AST nodes (`FontFace`, `StyleRule`, `AtRule`, `StyleSheet`, `CommentNode`, `RawDeclaration`) validate inputs in `__post_init__` against W3C `@webref/css` Value Definition Syntax grammars and raise `StixisValidationError` or `StixisSyntaxError` on invalid input.

## Core API signatures

```python
from stixis import (
    AtRule,
    CommentNode,
    Deg,
    Dimension,
    Em,
    FontFace,
    FontSrc,
    FontFaceDescriptors,
    GenericFamily,
    Keyword,
    LocalFont,
    Pct,
    Px,
    RawDeclaration,
    Rem,
    Rule,
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
    serialize,
    serialize_declarations,
    serialize_value,
    using_options,
)
```

* `dumps(node_or_nodes, *, minify: bool = False, options: SerializationOptions | None = None) -> str` (alias: `serialize`): Render a `StyleSheet`, a single `Rule`, or an iterable of `Rule` nodes.
* `FontFace(descriptors=None, *, comment=None, options=None, **kw_descriptors) -> AtRule`: Typed `@font-face` constructor backed by `FontFaceDescriptors` (`TypedDict`). Accepts `snake_case` keyword arguments (`font_family`, `font_style`, `font_weight`, `font_width`, `font_display`, `src`, `unicode_range`, `size_adjust`, `ascent_override`, `descent_override`, `line_gap_override`, `font_feature_settings`, `font_variation_settings`).
* `StyleRule(selector, declarations=..., *, rules=(), comment=None, options=None)`
* `AtRule(name, prelude=None, *, descriptors=None, rules=(), comment=None, statement=False, options=None)`
* `CommentNode(text, *, leading_newline: bool = False, options: SerializationOptions | None = None)`: Standalone CSS comment node. Emits a leading blank line in non-minified output when `leading_newline=True` or when `text` starts with `\n`.
* `StyleSheet(rules, *, header_comment=None, options=None)`
* `using_options(options: SerializationOptions)`: Context manager that sets default `SerializationOptions` for AST nodes and `serialize_value` or `canonicalize` calls inside the `with` block.
* `serialize_value(target: str, value: Any, *, at_rule: str | None = None, options: SerializationOptions | None = None) -> str`: Validate and serialize a Python value or PEP 750 `Template`.
* `canonicalize(target: str, css_text: str, *, at_rule: str | None = None, options: SerializationOptions | None = None) -> str`: Tokenize, validate, and normalize raw CSS value text.

## Mapping Python values to CSS

* **`font-family` (`<family-name>` and `<generic-family>`).**
  * A plain `str` is always treated as a `<family-name>` and quoted: `"Noto Sans JP"` formats as `"\"Noto Sans JP\""`, and `"serif"` formats as `"\"serif\""`.
  * Generic families require `generic("sans-serif")`, `GenericFamily("serif")`, or `Keyword("system-ui")`.
  * Fallback list: `["Noto Sans JP", generic("sans-serif")]` formats as `"\"Noto Sans JP\", sans-serif"`.
* **`{1,2}` descriptor and property ranges.**
  * `font_weight=400` formats as `"400"`, and `font_weight=(100, 900)` formats as `"100 900"` (checks `1 <= w <= 1000`).
  * `font_width=Pct(100)` formats as `"100%"`, and `font_width=(Pct(75), Pct(125))` or `("75%", "125%")` formats as `"75% 125%"`.
  * `font_style="italic"` formats as `"italic"`, and `font_style=("oblique", Deg(-10), Deg(20))` formats as `"oblique -10deg 20deg"` (checks `-90deg <= a <= 90deg`).
  * `ascent_override=(Pct(90), Pct(100))` formats as `"90% 100%"`.
* **`@font-face` `src` (`<font-src-list>`).**
  * `FontSrc(url="sub-0.woff2", format="woff2", tech=("variations", "color-COLRv1"))` formats as `'url("sub-0.woff2") format(woff2) tech(variations, color-colrv1)'`. `format()` always precedes `tech()`, and `tech()` keywords are lowercased.
  * Local fallback list: `[LocalFont("Inter"), FontSrc(url="inter.woff2", format="woff2")]`.
  * PEP 750 `Template`: `t"url({woff2_url}) format(woff2) tech(variations)"`.
* **OpenType feature and variation settings.**
  * `font_feature_settings={"liga": True, "dlig": False, "swsh": 2}` formats as `'"liga", "dlig" 0, "swsh" 2'`.
  * `font_variation_settings={"wght": 700, "wdth": 87.5}` or `[("wght", 700), ("wdth", 87.5)]` formats as `'"wght" 700, "wdth" 87.5'`.
* **`unicode-range` (`<urange>#`) and range compaction.**
  * `unicode_range=[0x3000, (0x3040, 0x309F)]`, `set[int]`, or `Sequence[int]` formats as `"U+3000, U+3040-309F"`. Contiguous integers in a `set[int]`, `list[int]`, or `tuple[int, ...]` merge into ranges (`[0x3041, 0x3042, 0x3043]` formats as `"U+3041-3043"`). Pass `coalesce=True` to `format_urange` or `parse_urange` to sort and merge intervals across mixed sequences or raw CSS strings.
  * For multi-slice `@font-face` rules, call `compact_urange(sorted_target_cps, forbidden_sorted, max_gap=0x80)`. `forbidden_sorted` is the sorted list of code points present in later slices (`all_font_cps - current_slice_cps`), and `max_gap` is the maximum missing code point count (`cp - prev_end - 1 <= max_gap`). This bridges gaps up to `max_gap` without shadowing code points in later `@font-face` rules.

## PEP 750 `Template` (`t"..."`) usage

Use Python 3.14+ `t"..."` templates when combining static CSS structure with Python variables:

* **URLs.** `src=t"url({slice_filename}) format(woff2)"` quotes and escapes `slice_filename` inside `url(...)`.
* **Comments.** `comment=t"[{slice_id}] {description} ({size_kb:.1f} KB)"` replaces `*/` inside interpolated values with `* /` and normalizes newlines to spaces so interpolated strings cannot close `/* ... */` early.
* **Values.** `font_style=t"oblique {min_angle}deg {max_angle}deg"` formats numbers, rejects `bool`, `nan`, and `inf`, and validates the resulting tokens against the `ValueNode` grammar.

## Presets (`SerializationOptions`)

* `SerializationOptions()` (default): double quotes `"`, `<family-name>` always quoted, `font-width` descriptor name, unquoted `format(woff2)`, `min_engines=2`.
* `SerializationOptions.cleome(min_engines=...)`: single quotes `'`, `@font-face` outputs `font-stretch` instead of `font-width`, and quotes `format('woff2')`.
* `SerializationOptions.cssom(min_engines=...)`: W3C CSSOM shortest serialization. Unquotes valid `<custom-ident>+` family names such as `Noto Sans JP`, while keeping digit-starting or keyword-colliding names quoted.
* **Engine support threshold (`min_engines` and `extra_properties`).** `min_engines` (default `2`, range `0..3`) gates top-level CSS properties using shipping unprefixed engine counts (Blink, Gecko, WebKit, including `partial_implementation: true`) from `@mdn/browser-compat-data`, plus `ALWAYS_ALLOWED_PROPERTIES`. It does not gate `@at-rule` descriptors, `<type>` targets, or individual functions/keywords inside an enabled property's grammar. Pass `min_engines=1` to allow single-engine properties or `extra_properties=frozenset({"hanging-punctuation"})` to enable specific properties.

## Read-only CLI (`stixis`)

Run the `stixis` CLI to inspect grammars or validate CSS values:

* Inspect a property, `@at-rule`, or `<type>`:
  * `uv run stixis inspect @font-face`
  * `uv run stixis inspect font-style --json`
  * `uv run stixis inspect <font-src>`
* Validate and canonicalize a CSS value (exits `0` with canonical CSS on `stdout`, `1` on invalid CSS, `2` on bad CLI usage, `4` on unknown target):
  * `uv run stixis check font-weight "100 900" --at-rule @font-face`
  * `uv run stixis check unicode-range "u+3040-309f" --at-rule @font-face`

## Filing Issues from consumer projects

When filing a GitHub Issue on `myakura/stixis` from a consumer project:

1. **Put one concern in each Issue.** Group related edge cases in the same module (such as `urange.py` or `CommentNode` template rendering) into one Issue, and split unrelated changes into separate Issues.
2. **Use the three-section layout (`What Happens?`, `How to Reproduce?`, `What Should Happen?`).**
   * Name the Stixis function, class, or CLI command and its observed behavior.
   * Write a self-contained Python 3.14+ snippet using only `stixis` imports.
3. **Run every reproduction snippet before filing.**
   * Execute the snippet with `uv run python -c "..."` or `uv run stixis check ...` and include the actual output or traceback. Do not mix unexecuted guesses from reading source code with verified assertions.
4. **Add labels when creating the Issue.**
   * Pass `--label bug`, `--label enhancement`, or `--label documentation` to `gh issue create`, and use `--body-file <file>` when the Markdown body contains backticks.
