# CLAUDE.md

Agent instructions for Agent Review Loop.

## What This Is

Agent Review Loop is a reusable local coordination tool for iterative AI
document review. The planned v1 has a broker process, SQLite state, a
Unix-domain JSONL socket, generated role prompts, and a CLI named `arl`.

Do not implement broker or CLI behavior until `docs/v1-plan.md` has been
reviewed and accepted. Use Beads (`bd`) for all task tracking.

## Commands

```bash
# Setup
make install                          # install deps + pre-commit hooks

# Development loop
uv run --frozen pytest                # run tests
uv run --frozen pytest --cov          # run tests with coverage
uv run --frozen ruff check .          # lint
uv run --frozen ruff check . --fix    # auto-fix lint issues
uv run --frozen ruff format .         # format
uv run --frozen pyright               # type-check
make ci                               # run all checks (lint + typecheck + test)
```

## Project Structure

```
src/agent_review_loop/  # Source code (src layout)
  __init__.py           # Package root — version lives here
  py.typed              # PEP 561 marker for type stub consumers
tests/                  # Tests mirror src structure
  conftest.py           # Shared fixtures
  test_*.py             # Test modules
scripts/                # One-off analysis/migration scripts (not packaged)
docs/                   # Maintained design and plan documents
```

## Code Conventions

### Style
- **Line length:** 100 chars (ruff format handles wrapping)
- **Imports:** sorted by ruff (stdlib, third-party, local), use `TYPE_CHECKING` blocks for type-only imports
- **Type hints:** required on all function signatures
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_CASE` for constants
- **Strings:** f-strings preferred, `pathlib.Path` over `os.path`

### Testing
- **Framework:** pytest
- **Naming:** `test_should_<expected_behavior>` style
- **Coverage:** 80% minimum enforced, branch coverage enabled
- **Fixtures:** shared fixtures in `conftest.py`, module-specific fixtures in test files
- New features require tests. Bug fixes require regression tests.

### Error Handling
- Use specific exception types, not bare `except:`
- `except Exception` is acceptable only at resilience boundaries (top-level handlers, cleanup)
- Use `logger.exception()` inside `except` blocks (not `logger.error()`) to capture tracebacks
- Use `datetime.UTC` for all timezone-aware datetime construction

### Linting Rules in This Project

The ruff config in `pyproject.toml` enforces these categories. Key ones to know:

| Rule | What it catches |
|------|----------------|
| `BLE` | Bare `except Exception` — use specific types or add per-file ignore with comment |
| `DTZ` | Naive `datetime()` — always pass `tzinfo=UTC` |
| `TCH` | Imports used only in type annotations — move behind `if TYPE_CHECKING:` |
| `D417` | Docstring exists but is missing params — add them or remove the docstring |
| `FURB` | Non-idiomatic patterns — `x if x else y` should be `x or y`, etc. |
| `TRY400` | `logger.error` in except — use `logger.exception` for auto-traceback |
| `PLR0912` | >12 branches — split function or add per-file ignore with rationale |

The pylint pre-commit hook additionally checks:
| Rule | What it catches |
|------|----------------|
| `W0621` | Redefining a name from an outer scope — rename the local variable |

### Adding Per-File Ignores

When a rule fires but the code is intentional, add a per-file ignore in `pyproject.toml` with a comment explaining why:

```toml
[tool.ruff.lint.per-file-ignores]
"src/agent_review_loop/handler.py" = ["BLE001"]  # resilience boundary, must catch all
```

For single-line suppressions, use `# noqa: RULE` with a rationale:
```python
from agent_review_loop.models import MyModel  # noqa: TC001 — Pydantic needs this at runtime
```

## Git Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- Feature branches: `feature/descriptive-name`
- Run `make ci` before merging
