## Code Style

- Python 3.11+, pythonic, simple, readable, maintainable
- **Don't overbloat.** Only write what's needed. No premature abstractions.
- Follow DRY. No duplicate code.
- Split files only when they are genuinely necessary.
- No comments explaining obvious code. Comments only for non-obvious "why"
- No docstrings on internal/private functions unless the logic is complex
- Use type hints on public API functions. Internal code: use them when they help clarity.
- Do not over-engineer. Keep it simple.
- Do not account for backward compatibility for major changes and refactors unless asked.

## Tooling

- Use `uv` for everything: `uv run`, `uv pip`, `uv venv`, `uv build`

## Security

- Never commit secrets, tokens, or .env files