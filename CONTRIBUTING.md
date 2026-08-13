# Contributing

Contributions should keep HATS generic, bounded and easy to operate.

1. Check [`docs/tooling-lifecycle.md`](docs/tooling-lifecycle.md) before introducing a new reusable tool or helper.
2. Keep private deployment configuration and credentials outside this repository.
3. Add focused tests for behavior changes and run `uv run --frozen --extra dev pytest`.
4. Update relevant documentation and examples when a public contract changes.
5. Keep commits small enough to review and do not mix unrelated semantic changes.

Bug reports should include the smallest reproducible behavior, expected result, observed result and relevant HATS version or commit without including secrets or private deployment data.
