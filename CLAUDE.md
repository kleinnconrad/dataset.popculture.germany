# Repository Guidelines

This file contains foundational instructions and conventions for developing in this repository. AI agents and developers must strictly adhere to these rules. The architecture, the variables, the known data issues and the implementation status are described in [project_architecture.md](project_architecture.md).

## Table of Contents
- [1. Directory Structure](#1-directory-structure)
- [2. Dependencies](#2-dependencies)
- [3. Environment Execution](#3-environment-execution)
- [4. Code Quality & Formatting](#4-code-quality--formatting)
- [5. Language & Documentation Style](#5-language--documentation-style)
- [6. Linting & Validation](#6-linting--validation)
- [7. Security Best Practices](#7-security-best-practices)
- [8. Data Collection Rules](#8-data-collection-rules)
- [9. Long-Term Stability Rules](#9-long-term-stability-rules)
- [10. Error Handling](#10-error-handling)
- [11. Version Control & Committing](#11-version-control--committing)

## 1. Directory Structure
- The `src` directory is strictly reserved for the core logic of the application.
- Do not place auxiliary code in `src`. Place supporting files in meaningfully named directories (e.g., `scripts`, `config`).
- Maintain a separate `tests` directory for unit and integration tests to keep the core codebase clean.
- Datasets and pipeline state live in `data/` and are written only by the pipeline. Derived tables (`features/`) are not committed.

## 2. Dependencies
- Always maintain a `pyproject.toml` and a `uv.lock` file at the root of the project.
- Any time a new Python dependency is introduced, it must be added to the `dependencies` array in `pyproject.toml`.
- After modifying dependencies, always run `uv lock` to update the `uv.lock` file and ensure reproducible builds.

## 3. Environment Execution
- Run local scripts and tests with `uv run` (e.g., `uv run python src/main.py`, `uv run pytest`), so that they use the locked dependencies.

## 4. Code Quality & Formatting
- Avoid hardcoding paths without environment checks; use relative paths or standard library path resolution (e.g., `pathlib`).
- Use the standard Python `logging` module instead of `print()` statements for proper log leveling, observability, and debugging.
- Implement Python type hinting (PEP 484) across all function signatures and class definitions to improve readability and catch type errors early.
- Follow PEP 8 style guidelines for Python code.

## 5. Language & Documentation Style
- **Tone:** Maintain a sober, objective, and professional tone across all documentation (including `README.md`).
- **Vocabulary:** Use only as many adjectives as strictly required. Do not use superlatives (e.g., "fastest", "best", "most") or filler words.
- **Emojis:** Do not use emojis anywhere in the codebase, documentation, or user interfaces.
- **Language:** Everything in the project must be written in English (documentation, markdown files, code comments, variable names, etc.). German proper names (outlets, shows, institutions, Wikipedia titles) keep their original spelling.
- **Table of Contents:** Every markdown file containing more than one paragraph must include a Table of Contents.
- **Docstrings:** All modules, classes, and public functions must include clear, structured docstrings (e.g., Google-style) detailing their purpose, arguments, and return types.
- **License:** The repository has no license file and grants no license for its code or data while the dataset builds its history. Do not add a license file or a license statement. The field `license` of the records names the terms of the sources, not a license of this repository; derived data and configuration say "no license granted".

## 6. Linting & Validation
- Code must pass `ruff check` before being finalized.
- Always verify that configuration files (e.g., `.yml`, `.toml` or `.json` files) remain structurally compliant and correctly reflect any changes made to the codebase (such as script renames or moving files).
- The tests check that the README data model, example records and variable overview match `src/schema.py` and `config/charts.toml`; update the README together with the schema and the charts.

## 7. Security Best Practices
- Never hardcode secrets, passwords, or API keys in the repository. Always rely on environment variables or the `.env` file, which is listed in `.gitignore`.
- The repository is public. New secret variables are documented by name in the configuration table of `README.md`, never with values.
- Never send personal data such as an email address to a source, also not in a User-Agent header.

## 8. Data Collection Rules
- All HTTP requests go through `src/web.py` (`WebClient`), which applies the fetch rules.
- Never bypass bot protection. A source that returns HTTP 401 or 403, a challenge page or a CAPTCHA is set to `blocked` (news and press sources) or paused (charts). Do not add headless browsers.
- Respect `robots.txt` for every website request, including the chart feeds of Apple and Google. Only documented APIs that are offered for programmatic use (the Wikimedia REST API and the MediaWiki API) are called with `respect_robots=False`, under their terms and rate limits.
- Respect text-and-data-mining reservations: a machine-readable reservation (TDMRep) excludes a source; a reservation in natural language in robots.txt or the imprint sets `fetch_pages = false`.
- Admit a new source only under the rule of [project_architecture.md](project_architecture.md#63-admission-of-sources): terms that forbid the inclusion in databases, automated collection or the transfer into a machine-readable form exclude it (`terms`); a general reservation of rights or a ban on reprints does not. Record the check (date, feeds, robots.txt, TDM, imprint) in a comment of the entry, and record rejected candidates as `[[excluded]]` entries with status and reason.
- Do not store full texts of articles or press releases. Text fetched for annotation is discarded after use; only titles, URLs, metadata and derived variables are stored.
- Store people only as articles of the German Wikipedia, by Wikidata id and title. Names of persons without an article are not stored, only counted. Author names are not stored.
- Every record carries the license or terms of its source in the `license` field.
- Keep the Gemini cost low: the cost grows with `MAX_ANNOTATIONS_PER_RUN` and the number of runs per day. Do not raise them, add Gemini requests (e.g. to the source review) or turn thinking on without stating the expected cost per month.

## 9. Long-Term Stability Rules
The dataset is collected over many years. Changes must keep the time series comparable:
- **Ids are permanent.** Chart ids (`<provider>.<chart>`), entity ids, source ids, dataset names, field names and `record_id` formats are never reused for a different meaning. A chart, entity or source that is no longer collected keeps its entry with `removed` and `removed_reason`.
- **Snapshots are complete.** `chart_snapshots` stores every new state of a list; do not rewrite or drop past records.
- **Vocabularies are only extended.** Values of the vocabularies in `src/schema.py` (fields, opinion targets, family forms, lifestyle topics, platforms, product categories) are never renamed or removed. A new value is added, documented and logged; it changes the extraction hash of the annotations.
- **Schema changes are additive.** New fields get a description and are added without changing existing ones; a changed meaning of a field requires a new field and an increment of `SCHEMA_VERSION` in `src/schema.py`. After an intended change, regenerate `tests/fixtures/schema_snapshot.txt`.
- **Measurement changes are logged.** Any change of a source, a chart, a filter, a vocabulary, a prompt or a counting rule is recorded with its date in the measurement log of [project_architecture.md](project_architecture.md#12-measurement-log), so that structural breaks in the series can be identified.
- **Identifiers are verified.** A new or changed chart in `config/charts.toml` is fetched from the live source and its terms are checked; a new or changed entity in `config/entities.toml` is checked against the German Wikipedia (an existing article, no redirect, no disambiguation page) before it is committed. A new source in `config/sources.toml` is checked with the fetch rules (robots.txt, TDM reservation, bot protection, feed content, imprint).

## 10. Error Handling
- Use specific exception handling (`try...except` blocks) instead of catching generic `Exception`s where possible, and provide informative error messages.

## 11. Version Control & Committing
- Never run `git commit` or `git push`, and never commit or push through GitHub tools or APIs, without the user's explicit approval of that specific commit. Prepare the changes locally, show the diff and the proposed commit message, and wait for approval. Approval of one commit does not extend to later commits.
- Commit messages follow the Conventional Commits format: `type(optional-scope)!: description`.
