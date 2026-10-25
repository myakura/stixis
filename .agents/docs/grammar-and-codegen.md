# Grammar Compiler and Code Generation Guide

## Overview

Stixis separates build-time specification ingestion (`codegen/`) from its zero-dependency runtime kernel (`src/stixis/syntax.py`). Both structured Python values (`serialize_value`) and raw CSS token streams (`canonicalize`) are evaluated against the same compiled `ValueNode` combinator tree.

## Value Definition Syntax (`src/stixis/syntax.py`)

`compile_value_syntax(syntax_str)` compiles W3C CSS Value Definition Syntax strings (`css-values-4` §5) into an immutable `ValueNode` hierarchy:

* `KeywordNode(name)` and `DelimNode(delim)`: Literal keywords (case-insensitively matched, serialized in ASCII lowercase) and punctuation delimiters (`,`, `/`, `:`).
* `PrimitiveNode(kind, min_val, max_val, min_unit, max_unit)`: Terminal and microsyntax types (`<number>`, `<integer>`, `<length>`, `<angle>`, `<time>`, `<frequency>`, `<resolution>`, `<percentage>`, `<string>`, `<url>`, `<ident>`, `<custom-ident>`, `<dashed-ident>`, `<urange>`, `<opentype-tag>`, `<feature-tag-value>`, `<family-name>`, `<generic-family>`, `<font-src>`, `<font-src-list>`, `<font-format>`, `<font-tech>`, `<hex-color>`, `<declaration-value>`, `<any-value>`), including bracketed range constraints such as `<number [1,1000]>` or `<angle [-90deg,90deg]>`.
* `RefNode(name, is_property, min_val, max_val)`: Non-terminal references (`<color>`) and property references (`<'font-width'>`), resolved lazily via `lookup_value_node` with cycle protection (`_active_refs`).
* `FunctionNode(name, arg)`: Typed CSS functions (`generic(...)`, `palette-mix(...)`, `fit-content(...)`).
* Combinators:
  * `JuxtaposeNode(items)`: Ordered sequence `A B`.
  * `AllOfNode(items)`: Unordered conjunction `A && B`. Matches branches in any input order (allowing optional `MultiplierNode(min_count=0)` branches to be omitted) and serializes matched branches in the canonical grammar order `0 .. k-1`.
  * `AnyOrderNode(items)`: Unordered inclusive-or `A || B`. Requires at least one branch and serializes matched branches in canonical grammar order `0 .. k-1`.
  * `ExclusiveOrNode(items)`: Ordered choice `A | B`.
  * `MultiplierNode(child, min_count, max_count, comma_separated)`: Multipliers `?` (`0..1`), `*` (`0..∞`), `+` (`1..∞`), `{A,B}`, `#` (`comma_separated=True`), and `#{A,B}`.
  * `NonEmptyGroupNode(child)`: Required group `[ ... ]!`.

### PEP 750 `Template` Rendering (`render_css_template`)

When `serialize_value`, `StyleRule`, `AtRule`, `FontFace`, or `StyleSheet` receives a Python 3.14+ `string.templatelib.Template` (`t"..."`), `render_css_template` inspects the lexical context preceding each `Interpolation` slot:

1. **Inside a CSS comment (`/* ... */`)**: Replaces any `*/` in the interpolated value with `* /` so untrusted text cannot prematurely close the comment and inject rules.
2. **Inside a quoted string (`"..."` or `'...'`)**: Escapes characters using CSSOM string rules without adding outer quotes.
3. **Inside `url(...)`**: Escapes the URL string via `serialize_string` so spaces, quotes, and parentheses cannot break out of `url(...)`.
4. **Top-level value slot**: Formats `int`/`float` via `serialize_number`, `Dimension`/`Percentage`/`Url`/`LocalFont`/`Keyword` via their primitive serializers, and plain `str` via `serialize_string` (unless `!r` conversion is specified), then passes the assembled CSS string through `evaluate_canonicalize` for full `ValueNode` grammar validation.

## Declarative Spec Patches (`codegen/spec_patches.toml`)

W3C `@webref/css` (`ed/css/*.json`) has prose-defined aliases and delta specifications (`css-fonts-5.json`, `css-values-5.json`, `css-color-5.json`). Never hardcode spec overrides inside `codegen/gen_specs.py`; declare them in `codegen/spec_patches.toml`:

* `[extra_units]`: Units defined in prose or pending upstream extraction (such as container query length units `cqw`..`cqmax` and speech units `dB`, `st`).
* `[delta_merges]`: Explicit ordering of base and delta JSON files so delta descriptors (`@font-face/size-adjust`, two-value `ascent-override`, `<font-features-tech>` with `avar2`) extend or override base definitions cleanly.
* `[values]`: Non-terminal syntax aliases defined in prose (`<font-src-list> = "<font-src>#"`, `<palette-identifier> = "<dashed-ident>"`, `<counter-name> = "<custom-ident>"`).
* `[atrule_preludes]`: Prelude grammars validated by `AtRule` (such as `@font-palette-values = "<dashed-ident>"`, `@property = "<custom-property-name>#"`, `@layer = "<layer-name>? | <layer-name>#"`).
* `[atrule_descriptors."@font-face"]`: Prose-defined descriptor aliases (`font-stretch = "auto | <'font-width'>{1,2}"`).
* `[properties]`: Property syntax overrides where a draft omits prose keywords (`font-synthesis` with `oblique-only`, `font-stretch = "<'font-width'>"`).

After editing `codegen/spec_patches.toml`, run:

```bash
uv run python codegen/gen_specs.py
uv run python codegen/gen_specs.py --check
```

## WPT Test Generation (`codegen/gen_wpt_tests.py`)

`codegen/gen_wpt_tests.py` extracts test vectors from local WPT HTML files into `tests/_generated/`:

* `test_wpt_urange.py`: All 94 valid and invalid `<urange>` vectors from `css-syntax/urange-parsing.html`.
* `test_wpt_font_family.py`: `<family-name>` and `<generic-family>` quoting and normalization vectors from `cssom/font-family-serialization-001.html` and `css-fonts/support/font-family-keywords.js`.
* `test_wpt_font_face.py`: `@font-face` descriptor tests from `css-fonts/parsing/font-face-*.html`.
* `test_wpt_css_fonts.py`: All property parsing vectors from `css-fonts/parsing/*-{valid,invalid}.html`.
* `test_wpt_css_properties.py`: General CSS property vectors across `css-*/parsing/*-{valid,invalid}.html`.

Always verify that generated files are synchronized before committing:

```bash
uv run python codegen/gen_wpt_tests.py --check
uv run pytest
```

## Upstream Sync & Agent Triage (`codegen/check_upstream.py` & `codegen/upstream.json`)

`codegen/upstream.json` records the baseline commit SHAs and generated counts for `w3c/webref` (`ed/css`) and `web-platform-tests/wpt` (`css`).

When upstream repositories advance (or when `.github/workflows/upstream-watch.yml` opens or updates an `upstream-sync` GitHub Issue), run `codegen/check_upstream.py` to classify what changed:

1. **Auto-applicable updates**: New or modified properties, descriptors, units, or WPT vectors that compile and pass with the existing `src/stixis/syntax.py` kernel and `codegen/spec_patches.toml`.
2. **Action-required items**:
   * **Unresolved non-terminals (`<new-type>`)**: Add prose aliases to `[values]` in `codegen/spec_patches.toml`, or add a microsyntax handler under `src/stixis/microsyntax/`.
   * **Failing WPT vectors**: Inspect the target grammar with `uv run stixis inspect <target> --json`, reproduce with `uv run stixis check <target> <raw_css>`, and adjust `codegen/spec_patches.toml` or `src/stixis/syntax.py`.

After resolving any action items, regenerate all tables, tests, and baseline metadata:

```bash
uv run python codegen/gen_specs.py --webref-dir <path-to-webref-ed-css>
uv run python codegen/gen_wpt_tests.py --wpt-dir <path-to-wpt-css>
uv run python codegen/check_upstream.py --webref-dir <path-to-webref-ed-css> --wpt-dir <path-to-wpt-css> --update-baseline
```
