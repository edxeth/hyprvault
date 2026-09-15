# AGENTS.md

hyprvault — Python session manager for Hyprland. Code lives in `hyprvault/`, tests in `tests/`.

## Commands

| Command | What it does |
| --- | --- |
| `PYTHONPATH=. uvx --quiet --python 3.14 pytest -q` | Runs the test suite; system python has no pytest and requires-python is >=3.14 |

- When running any `gh` command here: always pass `-R edxeth/hyprvault`. The `upstream` remote (Tunahanyrd/hyprvault) points to a deleted repo and gh prefers it over `origin`, so bare `gh` calls fail.
