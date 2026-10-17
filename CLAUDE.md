# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt   # runtime + pytest
pytest                                                     # full suite, offline, < 1 s
pytest tests/test_validator.py::test_boundary_values_are_inside_the_range   # one test
python -m datasheet_extractor.pipeline datasheets/         # extract every PDF in a folder (needs GOOGLE_API_KEY in .env)
python -m datasheet_extractor.dashboard                    # Dash app on http://127.0.0.1:8050
python tests/fixtures/make_fixture.py                      # regenerate the two fixture PDFs
python docs/make_screenshot.py http://127.0.0.1:8050       # README screenshot; needs a running dashboard, Selenium and Chrome
```

There is no linter or formatter configured. CI (`.github/workflows/ci.yml`) only installs requirements and runs `pytest` on Python 3.12.

## Architecture

One direction of flow, one job per module:

`loader.DocumentLoader` (PDF to page strings) → `selection.select_pages` (keyword-scored pages under a character cap) → `extractor.Extractor` (prompt, call, parse) → `validator.Validator` (clean values plus `Problem` list) → `store.Store` (SQLite). `pipeline.Pipeline` wires them; `dashboard.py` reads only from `Store`.

Things that span files and are easy to miss:

- **`schemas/sensor.json` is the single source of truth.** `schema.load_schema` turns it into `FieldSpec` dataclasses used by both the extractor (prompt text, which keys survive) and the validator (units, ranges, enums, patterns). Adding a field is a JSON edit. A number field's `unit` must have an entry in `validator.UNIT_CONVERSIONS` or the `Validator` constructor raises.
- **The model sits behind `extractor.LLMClient`**, a one-method ABC (`complete(prompt) -> str`). `GeminiClient` is the only code that imports the provider SDK, and it does so inside `__init__`. Tests never import it except to check the missing-key error.
- **Data contract between stages:** the extractor returns every schema key; number fields are `{"value", "unit"}` dicts or `None`, strings and enums are `str` or `None`. The extractor never judges values; the validator never calls the model.
- **Validator severities are fixed:** missing required field, unknown unit and non-numeric or non-finite number are `error` and the value becomes `None`; out of range, unknown enum value, pattern mismatch and missing unit are `warning` and the value is kept.
- **Store has two tables.** `records` holds fixed columns plus `data` and `problems` as JSON text, queried with SQLite `json_extract`. `files` remembers every PDF by SHA-256 with an outcome. Upsert identity is the file hash first, then manufacturer plus part number, so a re-extracted file updates its own row even if the model words the part number differently.
- **Pipeline outcomes per file:** `unchanged` (hash seen and not failed), `skipped` (`NoTextError`, scanned PDF), `failed` (any extraction, validation or storage exception, retried automatically next run), `stored`, `deferred` (an earlier file hit HTTP 429, so the rest of the batch is left untouched). A single bad file must never abort the batch; keep new stages inside that error handling.
- **Dashboard filtering is the pure function `filter_rows`**, tested without a browser. The page layout is a sentence of inline controls; styling lives in `datasheet_extractor/assets/style.css`, which Dash serves automatically.

## Testing

The suite must stay offline and deterministic. Tests hand `Extractor` or `build_pipeline(client=...)` a `tests/fakes.FakeLLMClient`, which returns scripted replies in order, records prompts, and raises if called more times than it has replies. Shared fixtures (`sensor_schema`, `good_reply`, `fixtures_dir`) are in `tests/conftest.py`; `tests/fixtures/llm_good.json` is the canned reply matching `sample_sensor.pdf`. Store and pipeline tests use `tmp_path` for the database.

## Gotchas

- **Free Gemini quota is about 20 requests per day per model.** Avoid `--force` on the pipeline, which re-extracts every file. `GEMINI_MODEL` in `.env` switches to another model's quota; the default lives in `extractor.DEFAULT_GEMINI_MODEL` and Google retires model names, so a 404 there means update the default.
- **Dash stamps asset URLs with the modification time read at startup.** After editing `style.css`, restart the dashboard server or the browser keeps the old file.
- **Dash 4 renders `dcc.Dropdown` as a `<button class="dash-dropdown">`** and wraps `dcc.Input` in `.dash-input-container` with stepper buttons. Style those elements, not the old `.Select-*` classes. Inside a flex row the input container's auto width collapses to zero, so it needs an explicit width.
- **The Bash tool on this machine collapses `\\` to `\`.** Write files containing backslashes, such as regex patterns in `schemas/sensor.json`, with the Write or Edit tool rather than a shell heredoc.
- **`.gitattributes` marks `*.pdf` as binary.** The fixture PDFs are hand-built with exact byte offsets; line-ending conversion would corrupt them.
- `.env`, `datasheets.db` and `datasheets/*.pdf` are gitignored and hold the owner's key, local records and real datasheets.

## Repository conventions

- Commit after each self-contained change, with a descriptive body. Commits are authored solely by the owner: no `Co-Authored-By` or "Generated with" lines in commit messages or PR descriptions.
- Pushing is done by the owner; do not push or force-push.
- The README is written for a non-engineer and its Limitations section reflects real test runs; update it when behaviour it describes changes, including the test count in its Development section.
