# AGENTS.md

Stixis (`stixis`) is a CSS serializer and validator for Python 3.14+ with zero runtime dependencies, generating grammar tables and `TypedDict` types from W3C `@webref/css` and test suites from Web Platform Tests (WPT) and Hypothesis.


## Commands

* Install dependencies with `uv sync`.
* Run the test suite with `uv run pytest`.
* Check lint and formatting with `uv run ruff check` and `uv run ruff format --check`.
* Regenerate spec tables and types with `uv run python codegen/gen_specs.py`, and verify they match upstream checkouts with `uv run python codegen/gen_specs.py --check --webref-dir .upstream/webref/ed/css --bcd-dir .upstream/bcd/css`.
* Regenerate WPT test modules with `uv run python codegen/gen_wpt_tests.py --wpt-dir .upstream/wpt/css`, and verify they are current with `uv run python codegen/gen_wpt_tests.py --check --wpt-dir .upstream/wpt/css`.
* Inspect CSS syntax or `@font-face` descriptors with `uv run stixis inspect @font-face` or `uv run stixis inspect font-style --json`.
* Validate and canonicalize a CSS value with `uv run stixis check font-weight "100 900" --at-rule @font-face` or `uv run stixis check hanging-punctuation "first" --min-engines 1`.


## Core rules

* Keep `src/stixis/` on the Python 3.14+ standard library with zero runtime dependencies.
* Never edit `src/stixis/_generated/` or `tests/_generated/` by hand; declare grammar overrides, prose aliases, and `[support]` rules in `codegen/spec_patches.toml` and rerun the generators.
* Gate top-level CSS properties at `min_engines=2` across Blink, Gecko, and WebKit by default (`SerializationOptions(min_engines=2)`), plus explicit entries in `codegen/spec_patches.toml` `[support]`.
* Validate AST nodes (`src/stixis/stylesheet.py`) and structured wrappers (`src/stixis/values.py`) eagerly on construction, and escape PEP 750 `Template` (`t"..."`) interpolation slots by surrounding token context so untrusted values cannot break out of CSS comments, strings, or `url(...)`.
* `serialize_value` formats structured Python values (treating a plain `str` in `<family-name>` or `font-family` as a quoted family name rather than a `<generic-family>`), whereas `canonicalize` validates raw CSS value text against the compiled grammar.
* `compact_urange` bridges `<urange>` gaps only when no code point in `forbidden_sorted` lies inside the gap, preventing accidental glyph shadowing across `@font-face` slices.
* Public APIs raise `StixisError` or `ValueError` on invalid input; `src/stixis/cli.py` is read-only, reports errors on `stderr` without a traceback, and exits with `0` (valid), `1` (validation error), `2` (usage error), or `4` (unknown target).
* Place hand-written tests in the matching `tests/test_<layer>.py` module, check issue premises against live runtime output and W3C CSS specs or WPT before changing code (close or scope out claims that contradict CSS specs), keep [`skills/stixis/SKILL.md`](skills/stixis/SKILL.md) and `README.md` in sync with public API or CLI changes without copying generated counts into docs, and never create or push git tags or GitHub releases (`.github/workflows/publish.yml` publishes tagged commits to PyPI).


## Domain guides

* Read [.agents/docs/grammar-and-codegen.md](.agents/docs/grammar-and-codegen.md) when modifying CSS grammar or WPT evaluation boundaries (`src/stixis/_grammar/`, `codegen/`) or triaging `upstream-sync` reports (`codegen/check_upstream.py`, `codegen/upstream.json`).
