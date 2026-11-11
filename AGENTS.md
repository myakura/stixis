# Stixis

Stixis (`stixis`) builds and validates everyday static CSS from Python 3.14+ data with zero runtime dependencies, generating grammar tables from W3C `@webref/css` and browser-support counts from `@mdn/browser-compat-data`.

## Commands

* Install dependencies: `uv sync`
* Run the test suite: `uv run pytest`
* Check lint and formatting: `uv run ruff check` and `uv run ruff format --check`
* Regenerate and verify spec tables: `uv run python codegen/gen_specs.py` and `uv run python codegen/gen_specs.py --check --webref-dir .upstream/webref/ed/css --bcd-dir .upstream/bcd/css`
* Regenerate and verify WPT tests: `uv run python codegen/gen_wpt_tests.py --wpt-dir .upstream/wpt/css` and `uv run python codegen/gen_wpt_tests.py --check --wpt-dir .upstream/wpt/css`
* Diff upstream specs and WPT: `uv run python codegen/check_upstream.py [--webref-dir <dir>] [--wpt-dir <dir>] [--bcd-dir <dir>] [--min-engines 0..3] [--format markdown|json] [--update-baseline]`
* Inspect syntax or validate a value: `uv run stixis inspect <target> [--json]` and `uv run stixis check <target> <value> [--at-rule <name>] [--min-engines 0..3]`

## Working guidelines

Apply `Scope and principles` in [README.md](README.md) when triaging issues, scoping features, and fixing bugs:

* Do not add ambiguous type inference, positional tuple aliases, or fragment/partial-token lexers.
* Do not expand PEP 750 `Template` features in CSS syntax positions (values, selectors, preludes), which are deprecated; removing CSS-position `Template` support and addressing its unresolved escaping edge cases will be handled as a separate change.
* Within the supported scope, keep syntactic validation separate from expression rewriting: do not add computed-value normalization, color conversion, shorthand collapsing, or new `calc()` simplification rules (changing existing `calc()` simplification requires explicit scope).
* Exclude out-of-scope WPT cases with a stated reason or mark them `xfail` when useful; do not treat full browser WPT coverage as a goal.
* Verify issue claims against live runtime output and W3C CSS specifications before editing code, and close or scope out claims that contradict CSS specs.
* Check downstream impact before changing behavior or public APIs, state breaking changes and migration paths at the top of the PR body, and keep [skills/stixis/SKILL.md](skills/stixis/SKILL.md) and [README.md](README.md) in sync without copying generated counts into docs.

## Project invariants

* Keep `src/stixis/` on the Python 3.14+ standard library with zero runtime dependencies, and guard top-level execution with `if __name__ == "__main__":` in runnable `README.md` snippets.
* Never edit `src/stixis/_generated/` or `tests/_generated/` by hand; declare grammar overrides, prose aliases, and `[support]` rules (`min_engines=2` default across Blink, Gecko, WebKit) in `codegen/spec_patches.toml` and rerun the generators.
* Validate frozen AST and value dataclasses (`src/stixis/stylesheet.py`, `src/stixis/values.py`) eagerly on construction, raising `StixisError` subclasses (`ValueError` on `SerializationOptions`).
* `compact_urange` bridges `<urange>` gaps only when no code point in `forbidden_sorted` lies inside the gap, preventing glyph shadowing across `@font-face` slices.
* Keep `src/stixis/cli.py` read-only and free of agent-detection environment variables, reporting errors on `stderr` without a traceback (`0` valid, `1` validation error, `2` usage error, `4` unknown target).
* Place hand-written tests in the matching `tests/test_<layer>.py` module.
* Never create or push git tags or GitHub releases: `.github/workflows/publish.yml` publishes tagged commits directly to PyPI.

## Domain guides

* Read [.agents/docs/grammar-and-codegen.md](.agents/docs/grammar-and-codegen.md) when modifying CSS grammar or WPT evaluation boundaries (`src/stixis/_grammar/`, `codegen/`) or triaging `upstream-sync` reports (`codegen/check_upstream.py`, `codegen/upstream.json`).
