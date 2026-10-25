# Grammar compiler and code generation guide

## Overview

Stixis separates build-time specification ingestion (`codegen/`) from its runtime evaluator (`src/stixis/syntax.py`). `serialize_value` and `canonicalize` both evaluate inputs against the same compiled `ValueNode` combinator tree.

## Value Definition Syntax (`src/stixis/syntax.py`)

`compile_value_syntax(syntax_str)` compiles W3C CSS Value Definition Syntax strings (`css-values-4` section 5) into an immutable `ValueNode` tree:

* `KeywordNode(name)` and `DelimNode(delim)`: literal keywords (matched case-insensitively, serialized in ASCII lowercase) and punctuation delimiters (`,`, `/`, `:`).
* `PrimitiveNode(kind, min_val, max_val, min_unit, max_unit)`: terminal and microsyntax types (`<number>`, `<integer>`, `<length>`, `<angle>`, `<time>`, `<frequency>`, `<resolution>`, `<percentage>`, `<string>`, `<url>`, `<ident>`, `<custom-ident>`, `<dashed-ident>`, `<urange>`, `<opentype-tag>`, `<feature-tag-value>`, `<family-name>`, `<generic-family>`, `<font-src>`, `<font-src-list>`, `<font-format>`, `<font-tech>`, `<hex-color>`, `<declaration-value>`, `<any-value>`), including bracketed range constraints such as `<number [1,1000]>` or `<angle [-90deg,90deg]>`.
* `RefNode(name, is_property, min_val, max_val)`: non-terminal references (`<color>`) and property references (`<'font-width'>`), resolved lazily by `lookup_value_node` with cycle tracking (`_active_refs`).
* `FunctionNode(name, arg)`: typed CSS functions (`generic(...)`, `palette-mix(...)`, `fit-content(...)`).
* Combinators:
  * `JuxtaposeNode(items)`: ordered sequence `A B`.
  * `AllOfNode(items)`: unordered conjunction `A && B`. Matches branches in any input order, allows optional `MultiplierNode(min_count=0)` branches to be omitted, and serializes matched branches in grammar order `0 .. k-1`.
  * `AnyOrderNode(items)`: unordered inclusive-or `A || B`. Requires at least one branch and serializes matched branches in grammar order `0 .. k-1`.
  * `ExclusiveOrNode(items)`: ordered choice `A | B`.
  * `MultiplierNode(child, min_count, max_count, comma_separated)`: multipliers `?` (`0..1`), `*` (`0..inf`), `+` (`1..inf`), `{A,B}`, `#` (`comma_separated=True`), and `#{A,B}`.
  * `NonEmptyGroupNode(child)`: required group `[ ... ]!`.

### PEP 750 `Template` rendering (`render_css_template`)

When `serialize_value`, `StyleRule`, `AtRule`, `FontFace`, `CommentNode`, or `StyleSheet` receives a Python 3.14+ `string.templatelib.Template` (`t"..."`), `render_css_template` checks the tokens before each `Interpolation` slot:

1. **Inside a CSS comment (`/* ... */`).** Replaces `*/` in the interpolated value with `* /` and normalizes newlines to spaces so interpolated text cannot close the comment early.
2. **Inside a quoted string (`"..."` or `'...'`).** Escapes characters using CSSOM string rules without adding outer quotes.
3. **Inside `url(...)`.** Formats the URL string with `serialize_string` so spaces, quotes, and parentheses cannot break out of `url(...)`.
4. **Top-level value slot.** Formats `int` and `float` with `serialize_number`, `Dimension`, `Percentage`, `Url`, `LocalFont`, and `Keyword` with their serializers, and plain `str` with `serialize_string` (unless `!r` conversion is set), then passes the assembled CSS string to `evaluate_canonicalize` for `ValueNode` grammar validation.

## Declarative spec patches (`codegen/spec_patches.toml`)

W3C `@webref/css` (`ed/css/*.json`) includes prose-defined aliases and delta specifications (`css-fonts-5.json`, `css-values-5.json`, `css-color-5.json`). Declare spec overrides in `codegen/spec_patches.toml` rather than hardcoding them in `codegen/gen_specs.py`:

* `[support]`: controls browser implementation filtering (`default_min_engines = 2`, `allow_properties = [...]`, `exclude_specs = [...]`) using engine counts (`0..3` across Blink, Gecko, WebKit) from `@mdn/browser-compat-data` (`PROPERTY_ENGINE_COUNTS` in `src/stixis/_generated/specs.py`). At runtime, callers can lower the threshold with `SerializationOptions(min_engines=1)` or `SerializationOptions.cleome(min_engines=1)`, or enable specific single-engine properties with `SerializationOptions(extra_properties=frozenset({"..."}))`.
* `[extra_units]`: units defined in prose or pending upstream extraction, such as container query length units `cqw`..`cqmax` and speech units `dB` and `st`.
* `[delta_merges]`: explicit ordering of base and delta JSON files so delta descriptors (`@font-face/size-adjust`, two-value `ascent-override`, `<font-features-tech>` with `avar2`) override base definitions in order.
* `[values]`: non-terminal syntax aliases defined in prose (`<font-src-list> = "<font-src>#"`, `<palette-identifier> = "<dashed-ident>"`, `<counter-name> = "<custom-ident>"`).
* `[atrule_preludes]`: prelude grammars validated by `AtRule` (`@font-palette-values = "<dashed-ident>"`, `@property = "<custom-property-name>#"`, `@layer = "<layer-name>? | <layer-name>#"`).
* `[atrule_descriptors."@font-face"]`: prose-defined descriptor aliases (`font-stretch = "auto | <'font-width'>{1,2}"`).
* `[properties]`: property syntax overrides where a draft omits prose keywords (`font-synthesis` with `oblique-only`, `font-stretch = "<'font-width'>"`).

After editing `codegen/spec_patches.toml`, run:

```bash
uv run python codegen/gen_specs.py
uv run python codegen/gen_specs.py --check
```

## WPT test generation (`codegen/gen_wpt_tests.py`)

`codegen/gen_wpt_tests.py` extracts test cases from local WPT HTML files into `tests/_generated/`:

* `test_wpt_urange.py`: all 94 valid and invalid `<urange>` cases from `css-syntax/urange-parsing.html`.
* `test_wpt_font_family.py`: `<family-name>` and `<generic-family>` quoting and normalization cases from `cssom/font-family-serialization-001.html` and `css-fonts/support/font-family-keywords.js`.
* `test_wpt_font_face.py`: `@font-face` descriptor tests from `css-fonts/parsing/font-face-*.html`.
* `test_wpt_css_fonts.py`: property parsing cases from `css-fonts/parsing/*-{valid,invalid}.html`.
* `test_wpt_css_properties.py`: general CSS property cases from `css-*/parsing/*-{valid,invalid}.html`.

Before committing, verify that generated files are current:

```bash
uv run python codegen/gen_wpt_tests.py --check
uv run pytest
```

## Upstream sync and triage (`codegen/check_upstream.py` and `codegen/upstream.json`)

`codegen/upstream.json` records the baseline commit SHAs and generated counts for `w3c/webref` (`ed/css`) and `web-platform-tests/wpt` (`css`).

When upstream repositories advance, or when `.github/workflows/upstream-watch.yml` opens or updates an `upstream-sync` GitHub Issue, run `codegen/check_upstream.py` to classify the changes:

1. **Auto-applicable updates.** New or modified properties, descriptors, units, or WPT cases that compile and pass with `src/stixis/syntax.py` and `codegen/spec_patches.toml`.
2. **Action-required items.**
   * **Unresolved non-terminals (`<new-type>`).** Add prose aliases to `[values]` in `codegen/spec_patches.toml`, or add a microsyntax handler under `src/stixis/microsyntax/`.
   * **Failing WPT cases.** Inspect the target grammar with `uv run stixis inspect <target> --json`, reproduce with `uv run stixis check <target> <raw_css>`, and update `codegen/spec_patches.toml` or `src/stixis/syntax.py`.

After resolving action items, regenerate all tables, tests, and baseline metadata:

```bash
uv run python codegen/gen_specs.py --webref-dir <path-to-webref-ed-css>
uv run python codegen/gen_wpt_tests.py --wpt-dir <path-to-wpt-css>
uv run python codegen/check_upstream.py --webref-dir <path-to-webref-ed-css> --wpt-dir <path-to-wpt-css> --update-baseline
```
