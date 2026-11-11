# Stixis

Stixis (`stixis`) builds and validates everyday static CSS from Python 3.14+ data with zero runtime dependencies. Pass computed numbers, units, sequences, URLs, or font metadata from build scripts or asset pipelines to author stylesheets (`StyleSheet`, `StyleRule`, `AtRule`, `FontFace`), custom properties, and `@font-face` / `unicode-range` subsets, or validate external CSS value strings against grammars generated from W3C `@webref/css` and browser-support counts from `@mdn/browser-compat-data`.

## Scope and principles

* **Non-goals.**
  * General-purpose CSS template engine that infers lexical context across arbitrary string fragments.
  * Browser computed-value, layout, or optimization engine (unit conversion, color-space conversion, shorthand collapsing, or per-item error-recovery dropping). *(Current status: `canonicalize` implements context-free CSS Values 4 §10.10 math simplification today; Stixis does not aim to expand algebraic expression rewriting or constant folding further.)*
  * General-purpose CSS parser, AST rewriter, or preprocessor for existing CSS files.
* **Design principles.**
  1. **Structured values for data needing escaping; direct strings where unambiguous.** Use structured types (`Url`, `FontSrc`, `LocalFont`, and `<string>` or `<family-name>` strings) for data that requires quoting or escaping. Targets that accept `<string>` or `<family-name>` treat a plain `str` as literal string data rather than a CSS expression (for example, `font_family="var(--font)"` quotes a family name); elsewhere, completed CSS values are generally accepted without a dedicated wrapper and validated for their target, normalizing spacing and quotes without preserving verbatim source text.
  2. **Comment-only `Template` scope.** PEP 750 `Template` (`t"..."`) belongs only in comment bodies (`CommentNode`, `comment=`, `header_comment=`), with CSS syntax positions rejecting `Template` by type rather than lexing partial tokens. *(Current status: CSS value, selector, and prelude positions still accept `Template` at runtime and are deprecated pending removal.)*
  3. **One recommended form, without ambiguous inference or high-maintenance aliases.** Keep clear typed shorthands (`Px`, `Rem`, `Pct`) alongside explicit wrappers (`Dimension`, `Percentage`), while avoiding positional tuple aliases whose meaning is not self-evident or implicit conversions that guess caller intent.
  4. **Separate grammar validation from expression rewriting.** Within the supported scope, validate syntactic correctness separately from rewriting or simplifying expressions. Out-of-scope Web Platform Tests (WPT) cases may be excluded with a stated reason or marked `xfail` when useful; Stixis does not aim to track every browser WPT assertion.
  5. **Bounded compatibility with explicit migration.** Do not grow context inference or parser state machines unboundedly to preserve incidental behavior. When changing behavior or public APIs, check downstream impact and document the migration path.

## What it does

* **Serializes Python values and validates CSS text (`serialize_value`, `canonicalize`, and the CLI).** Resolves standard CSS properties, custom properties (`--*`), `@at-rule` descriptors (`@font-face/src` or `at_rule="@font-face"`), and `<type>` syntax targets against `@webref/css` grammars; unknown `<...>` targets raise `StixisValidationError` (exit `4` on the CLI).
* **Gates experimental CSS properties by engine support (`SerializationOptions(min_engines=...)` and `--min-engines`).** Enables CSS properties shipped unprefixed in at least 2 core browser engines (Blink, Gecko, WebKit) by default (`min_engines=2`), plus an explicit allowlist in `codegen/spec_patches.toml`. Pass `min_engines=1`, `extra_properties=frozenset({"prop-name"})`, or `--min-engines 1` (`0..3`) to opt into single-engine properties.
* **Validates AST nodes and primitives on construction.** `FontFace`, `StyleRule`, `AtRule`, `StyleSheet`, `CommentNode`, `RawDeclaration`, `FontSrc`, `Url`, `Dimension`, `Percentage`, `Keyword`, `LocalFont`, and `GenericFamily` are frozen, hashable dataclasses that validate inputs in `__post_init__` and raise `StixisValidationError` or `StixisSyntaxError` on invalid input.
* **Sanitizes PEP 750 `Template` (`t"..."`) comments.** `CommentNode`, `comment=`, and `header_comment=` replace `*/` and normalize newlines inside interpolated values so untrusted strings cannot close `/* ... */` early. Passing `Template` in CSS value, selector, or prelude positions is deprecated; use `Url`, `FontSrc`, `LocalFont`, or structured primitives instead.
* **Handles five CSS microsyntaxes.** `<urange>` (`parse_urange`, `format_urange`, `compact_urange`), `<opentype-tag>` (`font-feature-settings`, `font-variation-settings`, `font-language-override`), `<font-src-list>` (`FontSrc`, `Url`, `LocalFont`), `<family-name>` and `<generic-family>` (quoting plain `str` by default and requiring `generic("serif")` or `Keyword("serif")` for generic families), and `{1,2}` descriptor and property ranges.
* **Includes a read-only CLI.** `stixis inspect` and `stixis check` exit with `0` for valid input, `1` for a validation error, `2` for a usage error, and `4` for an unknown target.

Only the top-level `stixis` package is public; submodules such as `stixis._syntax`, `stixis._grammar`, `stixis.values`, `stixis.stylesheet`, and `stixis.api` are internal and not part of the public API.

## Quickstart

```python
from stixis import (
    AtRule,
    Deg,
    FontFace,
    FontSrc,
    Pct,
    Px,
    Rem,
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
            StyleRule(":root", {"--space-md": Rem(1), "--brand": "#2563eb"}),
            FontFace(
                comment=t"Slice [{slice_id}] ({size_kb:.1f} KB)",
                font_family="Noto Sans JP",
                font_style=("oblique", Deg(-10), Deg(20)),
                font_weight=(100, 900),
                font_width=(Pct(75), Pct(125)),
                font_display="swap",
                src=FontSrc(url=woff2_url, format="woff2", tech="variations"),
                unicode_range=urange,
            ),
            StyleRule(
                "body",
                {
                    "max_width": Rem(48),
                    "width": "100%",
                    "margin": Px(0),
                    "padding": "var(--space-md)",
                    "color": "#111827",
                    "font_family": ["Noto Sans JP", generic("sans-serif")],
                    "font_feature_settings": {"liga": True, "dlig": False},
                },
            ),
            AtRule(
                "media",
                f"(min-width: {Rem(48)})",
                rules=[StyleRule("body", {"padding": Rem(2)})],
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

`codegen/gen_wpt_tests.py` generates the WPT parsing and serialization test modules in `tests/_generated/`.

```bash
uv sync
uv run python codegen/gen_specs.py --check --webref-dir .upstream/webref/ed/css --bcd-dir .upstream/bcd/css
uv run python codegen/gen_wpt_tests.py --check --wpt-dir .upstream/wpt/css
uv run pytest
uv run ruff check
```
