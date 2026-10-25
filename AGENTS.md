# Stixis

Stixis (`stixis`) is a CSS serializer and validator for Python 3.14+ with zero runtime dependencies. It generates its grammar tables and `TypedDict` types from W3C `@webref/css` and its test suites from Web Platform Tests (WPT) and Hypothesis.

## Commands

* Install dependencies: `uv sync`
* Run test suite: `uv run pytest`
* Lint and format check: `uv run ruff check`
* Regenerate spec tables and types: `uv run python codegen/gen_specs.py`
* Verify generated spec tables are current: `uv run python codegen/gen_specs.py --check`
* Regenerate WPT test suite: `uv run python codegen/gen_wpt_tests.py --wpt-dir <path-to-wpt-css>`
* Verify generated WPT tests are current: `uv run python codegen/gen_wpt_tests.py --check`
* Diff upstream `@webref/css` and WPT and emit an agent Issue report: `uv run python codegen/check_upstream.py [--webref-dir <dir>] [--wpt-dir <dir>] [--min-engines 0..3] [--format markdown|json] [--update-baseline]`
* Inspect CSS syntax or `@font-face` descriptors: `uv run stixis inspect <target> [--json]`
* Validate and canonicalize a CSS value: `uv run stixis check <target> <value> [--at-rule <name>] [--min-engines 0..3]`

## Architecture and invariants

* **Zero runtime dependencies.** Everything under `src/stixis/` must use only the Python 3.14+ standard library (`string.templatelib`, `dataclasses`, `bisect`, `math`, `re`, `argparse`, `json`).
* **Never edit generated files by hand.** `codegen/gen_specs.py` and `codegen/gen_wpt_tests.py` generate `src/stixis/_generated/` (`units.py`, `specs.py`, `types.py`) and `tests/_generated/` (`test_wpt_*.py`). Put grammar fixes, prose aliases, and `[support]` thresholds in `codegen/spec_patches.toml` and rerun the generators.
* **Multi-engine support threshold.** By default (`SerializationOptions(min_engines=2)`), Stixis enables properties implemented in at least 2 core browser engines (Blink, Gecko, WebKit) plus `ALWAYS_ALLOWED_PROPERTIES` in `codegen/spec_patches.toml`. To enable 1-engine features, pass `SerializationOptions(min_engines=1)`, `SerializationOptions.cleome(min_engines=1)`, `using_options(SerializationOptions.cleome(min_engines=1))`, or `extra_properties=frozenset({"prop-name"})`, or add the property to `[support] allow_properties` in `codegen/spec_patches.toml`.
* **Validation on AST construction.** `StyleRule`, `AtRule`, `FontFace`, `StyleSheet`, `CommentNode`, and `RawDeclaration` (`src/stixis/nodes.py`) are frozen dataclasses (`dataclass(frozen=True, slots=True)`) that validate selectors, preludes, descriptors, declarations, and comments in `__post_init__`.
* **Value and raw CSS boundary.**
  * `serialize_value(target, value)` serializes structured Python values (`int`, `float`, `tuple`, `list`, `dict`, `set`, `Dimension`, `Pct`, `Deg`, `Url`, `LocalFont`, `FontSrc`, `Keyword`, `GenericFamily`, and PEP 750 `Template`). A plain Python `str` passed to `<family-name>` or `font-family` is always treated as a font family name and quoted by default. Generic families require `generic("serif")` or `Keyword("serif")`.
  * `canonicalize(target, css_text)` tokenizes and validates raw CSS value text against the compiled `ValueNode` grammar and returns canonical CSSOM text.
* **Entrypoint guard.** Wrap top-level execution in `if __name__ == "__main__":` in any runnable Python snippet in `README.md` or any script that uses `multiprocessing`.
* **CLI contract.** `src/stixis/cli.py` is read-only, never branches on agent-detection environment variables, never mixes `nargs="*"` or `"+"` optional flags with positional arguments, and exits with `0` (valid), `1` (validation error), `2` (usage error), or `4` (unknown target).

## Issue triage and release workflow

* **Verify premises first.** Before implementing an Issue, check every factual claim and reproduction snippet against live runtime output (`uv run pytest`, `uv run stixis check`) and the W3C CSS specification or WPT suite. If a claim contradicts the CSS specification, close the Issue or scope out that item, and cite the spec section or tokenizer behavior.
* **Label and link Issues and PRs.**
  * Add appropriate labels (`bug`, `enhancement`, `documentation`, `upstream-sync`) to every Issue and PR.
  * Use a dedicated branch per Issue, include `Closes #<n>` in the PR body, and run `codex review`, `uv run pytest`, and `uv run ruff check` before merging.
* **Keep the consumer skill and releases in sync.**
  * When you add or change public APIs, options, or microsyntax behavior, update [`skills/stixis/SKILL.md`](skills/stixis/SKILL.md) and `README.md`.
  * To cut a release, bump the version in `pyproject.toml` and `src/stixis/__init__.py`, run `uv sync`, commit `chore(release): bump version to X.Y.Z`, push tag `vX.Y.Z`, and run `gh release create vX.Y.Z --title "vX.Y.Z" --generate-notes`.

## Domain guides

* Read [.agents/docs/grammar-and-codegen.md](.agents/docs/grammar-and-codegen.md) when modifying the Value Definition Syntax parser or evaluator (`src/stixis/syntax.py`), adding spec overrides in `codegen/spec_patches.toml`, or updating `codegen/gen_specs.py` and `codegen/gen_wpt_tests.py`.
* Read [.agents/docs/microsyntaxes-and-cssom.md](.agents/docs/microsyntaxes-and-cssom.md) when modifying CSSOM serializers (`src/stixis/primitives.py`), `SerializationOptions` presets (`src/stixis/options.py`), or the five microsyntax modules (`src/stixis/microsyntax/`).
