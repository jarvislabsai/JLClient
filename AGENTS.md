## Code Style

- Python 3.11+, pythonic, simple, readable, maintainable
- **Don't overbloat.** Only write what's needed. No premature abstractions.
- Follow DRY. No duplicate code.
- Split files only when they are genuinely necessary.
- Use type hints on public API functions. Internal code: use them when they help clarity.
- Do not over-engineer. Keep it simple.
- Do not account for backward compatibility for major changes and refactors unless asked.

## Formatting

- Ruff is configured in `pyproject.toml`. Run `uv run ruff format . && uv run ruff check --fix .` after major code changes or before committing.

## Tooling

- Use `uv` for everything: `uv run`, `uv pip`, `uv venv`, `uv build`

## Security

- Never commit secrets, tokens, or .env files

## Git
- Do not commit changes unless you are asked to do so.
- Do not push changes to the main branch unless you are asked to do so.