# CSSOM Serialization and Microsyntaxes Guide

## CSSOM Primitive Serialization (`src/stixis/primitives.py`)

All primitive serializers implement W3C CSSOM §2.1 (`Common Serializing Idioms`) and §6.7.2 (`Serializing CSS Values`):

* **Identifiers (`serialize_ident`, `serialize_custom_ident`, `serialize_dashed_ident`)**:
  * Replaces `U+0000` with `U+FFFD`.
  * Escapes `U+0001`..`U+001F` and `U+007F` as hex code points (`\1f ` with trailing space).
  * Escapes leading ASCII digits (`0`..`9`) or leading hyphen followed by a digit (`-[0-9]`) as hex code points (`\31 `), and lone `-` as `\-`.
  * `serialize_custom_ident` rejects CSS-wide keywords (`initial`, `inherit`, `unset`, `default`, `revert`, `revert-layer`, `revert-rule`) and any grammar-specific forbidden keywords.
  * `serialize_dashed_ident` requires a leading `--` (`len >= 2`, including `--`).
* **Strings and URLs (`serialize_string`, `serialize_url`)**:
  * Wraps in `options.quote_char` (`"` by default and in `.cssom()`, `'` in `.cleome()`).
  * Escapes `U+0001`..`U+001F` and `U+007F` as hex code points, and escapes `options.quote_char` and `\` with a backslash.
  * `serialize_url` formats `url(...)` with a quoted inner string.
* **Numbers, Integers, Percentages, and Dimensions (`serialize_number`, `serialize_integer`, `serialize_percentage`, `serialize_dimension`)**:
  * Rejects `bool` (`isinstance(v, bool)`) and non-finite floats (`nan`, `inf`, `-inf`).
  * Formats numbers as shortest decimal strings without scientific notation, rounded to at most 6 fractional digits with trailing zeros stripped (`400.0` -> `"400"`, `-0.0` -> `"0"`).
  * Enforces bracketed bounds (`[min, max]`) and validates units against `src/stixis/_generated/units.py`, normalizing unit names to their canonical lowercase form (except `Hz`/`kHz` or `Q` where specified, and `dB`/`st`).

## Five Microsyntaxes (`src/stixis/microsyntax/`)

### 1. `<urange>` and Cascade Shadowing Compaction (`urange.py`)

* `parse_urange(text)` implements CSS Syntax Level 3 §8 and passes all 94 cases in WPT `urange-parsing.html`. Because CSS tokenizers absorb `U+...` into `ident`, `number` (including scientific notation like `u+1e3`), or `dimension` tokens, `parse_urange` strips comments first while verifying that no comment separated the leading `u`/`U` from `+` (rejecting `u/**/0` while accepting `u/**/+/**/0`).
* `compact_urange(sorted_target_cps, forbidden_sorted, max_gap=0x80)` merges adjacent ranges when `gap <= max_gap` **only** if `bisect_right` confirms that no code point in `forbidden_sorted` lies inside `(prev_end, next_start)`. This minimizes `unicode-range` string length without shadowing glyphs in subsequent `@font-face` slices.

### 2. `<opentype-tag>` and Feature/Variation Settings (`opentype.py`)

* `validate_opentype_tag` enforces exactly 4 printable ASCII bytes (`0x20`..`0x7E`), escaping `"` and `\`.
* `serialize_font_feature_settings` accepts `Mapping[str, int | bool | str]` or `Sequence[tuple[str, int | bool | str]]`. Per CSSOM shortest serialization, value `1` / `True` / `"on"` is omitted (`"liga"`), `0` / `False` / `"off"` serializes as `0` (`"dlig" 0`), and integers `>= 2` serialize as `"swsh" 2`.
* `serialize_font_variation_settings` accepts `Mapping[str, int | float]` or `Sequence[tuple[str, int | float]]` and formats each pair as `"tag" <number>`.
* `serialize_font_language_override` validates `normal` or a 1..4 printable ASCII string and strips trailing spaces per WPT `font-language-override-valid.html`.

### 3. `<font-src-list>` (`font_src.py`)

* Enforces WPT `font-face-src-tech.html`, `font-face-src-format.html`, and `font-face-src-local.html`:
  * `format()` must precede `tech()`; reversed `tech(...) format(...)` is rejected.
  * `tech()` accepts only comma-separated `<font-tech>` keywords (serialized in ASCII lowercase) and rejects quoted strings.
  * `format()` accepts `<font-format>` keywords (`woff2`, `woff`, `opentype`, `truetype`, `collection`, `embedded-opentype`, `svg`) or strings; `options.quote_font_format` controls whether keywords are emitted bare (`format(woff2)`) or quoted (`format('woff2')` in `.cleome()`).
  * `local()` rejects single-word unquoted CSS-wide keywords (`local(inherit)`), accepts multi-word unquoted names containing CSS-wide keywords (`local(default A)`), and accepts quoted CSS-wide keywords (`local("inherit")`).

### 4. `<family-name>` and `<generic-family>` (`family_name.py`)

* In `serialize_value`, a Python `str` is always treated as `<family-name>` (never `<generic-family>`). Use `generic("serif")`, `GenericFamily("serif")`, or `Keyword("serif")` for generic families.
* With `DEFAULT_OPTIONS` (`always_quote_family_name=True`), `<family-name>` is always quoted.
* With `SerializationOptions.cssom()` (`always_quote_family_name=False`), valid `<custom-ident>+` names serialize unquoted (`Times New Roman`), while names starting with a digit (`"34J"`), containing multiple spaces (`"A  B"`), or colliding case-insensitively with `<generic-family>` or CSS-wide keywords (`"Serif"`, `"inherit"`) remain quoted per WPT `font-family-serialization-001.html`.

### 5. `{1,2}` Descriptor and Property Ranges (`ranges.py`)

* Supports single values and 2-tuples `(lo, hi)` for `font-weight` (`[1, 1000]`), `font-width` / `font-stretch` (`[0%, ∞]`), `font-style` (`oblique <angle [-90deg,90deg]>{1,2}`), and metric overrides (`ascent-override`, `descent-override`, `line-gap-override`).
* In property context (`is_descriptor=False`), `oblique 0deg` normalizes to `normal` when `options.normalize_zero_oblique=True`.
