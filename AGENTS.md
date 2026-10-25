# Stixis

Stixis (`stixis`) is a zero-dependency, spec-driven CSS serializer and validator for Python 3.14+ that generates its grammar tables and `TypedDict` types from W3C `@webref/css` and its conformance test suites from Web Platform Tests (WPT) and Hypothesis.

## Commands

* Install dependencies: `uv sync`
* Run test suite: `uv run pytest`
* Lint and format check: `uv run ruff check`
* Regenerate spec tables and types: `uv run python codegen/gen_specs.py`
* Verify generated spec tables are current: `uv run python codegen/gen_specs.py --check`
* Regenerate WPT test suite: `uv run python codegen/gen_wpt_tests.py --wpt-dir <path-to-wpt-css>`
* Verify generated WPT tests are current: `uv run python codegen/gen_wpt_tests.py --check`
* Diff upstream `@webref/css` & WPT and emit an agent Issue report: `uv run python codegen/check_upstream.py [--webref-dir <dir>] [--wpt-dir <dir>] [--format markdown|json] [--update-baseline]`
* Inspect CSS syntax or `@font-face` descriptors: `uv run stixis inspect <target> [--json]`
* Validate and canonicalize a CSS value: `uv run stixis check <target> <value> [--at-rule <name>]`

## Architecture & Invariants

* **Zero runtime dependencies**: Everything under `src/stixis/` must use only the Python 3.14+ standard library (`string.templatelib`, `dataclasses`, `bisect`, `math`, `re`, `argparse`, `json`).
* **Never hand-edit generated files**: `src/stixis/_generated/` (`units.py`, `specs.py`, `types.py`) and `tests/_generated/` (`test_wpt_*.py`) are produced by `codegen/gen_specs.py` and `codegen/gen_wpt_tests.py`. Put grammar fixes and prose aliases in `codegen/spec_patches.toml` and rerun the generators.
* **Eager validation on AST nodes**: `StyleRule`, `AtRule`, `FontFace`, `StyleSheet`, `CommentNode`, and `RawDeclaration` (`src/stixis/nodes.py`) are frozen dataclasses (`dataclass(frozen=True, slots=True)`) that validate all selectors, preludes, descriptors, declarations, and comments inside `__post_init__`.
* **Value vs. raw CSS boundary**:
  * `serialize_value(target, value)` serializes structured Python values (`int`, `float`, `tuple`, `list`, `dict`, `set`, `Dimension`, `Pct`, `Deg`, `Url`, `LocalFont`, `FontSrc`, `Keyword`, `GenericFamily`, and PEP 750 `Template`). A plain Python `str` passed to `<family-name>` or `font-family` is always treated as a font family name (quoted by default); generic families require `generic("serif")` or `Keyword("serif")`.
  * `canonicalize(target, css_text)` tokenizes and validates raw CSS value text against the compiled `ValueNode` grammar and emits canonical CSSOM text.
* **Entrypoint guard**: Any runnable Python snippet in `README.md` or any script using `multiprocessing` must wrap top-level execution in `if __name__ == "__main__":`.
* **CLI contract**: `src/stixis/cli.py` is strictly read-only, never branches on agent-detection environment variables, never mixes `nargs="*"`/`"+"` optional flags with positional arguments, and exits with `0` (valid), `1` (validation error), `2` (usage error), or `4` (unknown target).

## Domain Guides

* Read [.agents/docs/grammar-and-codegen.md](.agents/docs/grammar-and-codegen.md) when modifying the Value Definition Syntax parser/evaluator (`src/stixis/syntax.py`), adding spec overrides in `codegen/spec_patches.toml`, or updating `codegen/gen_specs.py` and `codegen/gen_wpt_tests.py`.
* Read [.agents/docs/microsyntaxes-and-cssom.md](.agents/docs/microsyntaxes-and-cssom.md) when modifying CSSOM primitive serialization (`src/stixis/primitives.py`), `SerializationOptions` presets (`src/stixis/options.py`), or the five microsyntax modules (`src/stixis/microsyntax/`).
