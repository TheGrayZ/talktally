# Repository Guidelines

## Project Structure & Module Organization
- `src/talktally/` — core Python package; keep each agent or service in its own module with clear entry points.
- `tests/` — mirrors `src/` structure; add a matching test module for every new feature.
- `scripts/` — helper CLI utilities or data preparation steps; name scripts with verb-first patterns like `prepare_*.py`.
- `recordings/` — sample audio/transcript artifacts for manual validation; never commit private or sensitive data.

## Build, Test, and Development Commands
- Create a virtual environment and install editable deps: `python3 -m venv .venv && source .venv/bin/activate && pip install -e .[dev]`.
- Run the full automated test suite: `pytest`.
- Format and lint before committing: `ruff check --fix src tests` and `ruff format src tests`.
- Regenerate packaging artifacts when publishing: `python -m build`.

## Coding Style & Naming Conventions
- Target Python 3.11, use 4-space indentation, and prefer type hints everywhere.
- Modules and packages use snake_case; classes use PascalCase; constants stay UPPER_SNAKE.
- Keep functions short; factor shared logic into `src/talktally/common/`.
- Document agent behaviors with docstrings summarizing input, output, and side effects.

## Testing Guidelines
- Write pytest tests under `tests/` with filenames `test_<feature>.py`.
- Use fixtures for audio transcripts or agent configurations; place shared fixtures in `tests/conftest.py`.
- Aim for high coverage on conversational flows and failure modes; add regression tests whenever fixing a bug.
- Run `pytest --maxfail=1 --durations=10` before opening a pull request to surface slow tests early.

## Commit & Pull Request Guidelines
- Follow imperative, present-tense commit messages (e.g., `Add diarization agent`).
- Group logically related changes; avoid drive-by refactors unless documented in the commit body.
- Pull requests must include a short summary, linked issues, validation notes (`pytest`, `ruff` results), and updated docs when behavior changes.
- Request review from another agent owner for cross-cutting changes, and attach screenshots or logs for UI- or audio-related updates.
