# Refactor plan: make `midi2mkcd` a library with a thin CLI

## Goal

Restructure this repo so that `midi2mkcd` is an importable library with a small,
stable public API, and the CLI is one thin consumer of it. The second consumer
will be a browser page that installs the wheel into Pyodide with `micropip` and
calls the same API. After this refactor the web page should need **zero copied
Python**: only HTML/JS glue that passes bytes and strings in and gets a string
back.

Target usage from Pyodide (this is the design constraint, not something to
build in this task):

```js
await micropip.install("midi2mkcd");
const m = pyodide.pyimport("midi2mkcd");
const result = m.convert(pyodide.toPy(midiBytes));        // bundled default params
const ts = result.to_typescript("code_extra");
```

Breaking changes to CLI flags, module paths and function names are all
acceptable. There are no external users to keep compatible.

## Non-goals

- Do not change the conversion algorithm. Everything under
  `midi_to_song/timeline/` and `arcade/music.py` keeps its behaviour.
- Do not build the web page.
- Do not publish to PyPI or add a release workflow (see "Later" at the end).

## Current problems (why this is needed)

1. **Output formatting lives in the CLI.** `cli.py::generate_single_conversion`
   and `generate_melodic_instrument_sample` build the TypeScript output with
   f-strings. Any other front end would have to duplicate them.
2. **The public entry point takes a `mido.MidiFile`.** Callers must import mido
   and have a file path. A browser has bytes.
3. **The library configures logging.** `utils/logger.py::create_logger` is
   called at import time in every module (about 24 call sites). It attaches a
   stdout handler and a stderr handler and sets `propagate = False`. Results:
   warnings are printed twice, log lines go to stdout and get mixed into the
   generated code when output is piped, and a host application cannot redirect
   or capture logs.
4. **Options are tangled.**
   - `TestingOptionsForMIDIToSong.generate_code` is an output format, not a
     conversion option.
   - `generate_melodic_instrument_sample` mutates the options object it was
     given.
   - `--generate-extra-code`, `--test-generate-code` and
     `--test-sample-melodic-instruments` are mutually exclusive but that is
     enforced by hand with `raise ValueError` in `main()`.
5. **CLI concerns leak into the library.** `utils/strings.py::parse_range`
   raises `argparse.ArgumentTypeError`. The `pyperclip` optional import is
   duplicated in `cli.py` and `main.py`. The simulator-crash warning is
   hardcoded in `main()` and printed for every song.
6. **Errors are untyped.** Validation raises bare `ValueError`, and
   `timeline/validation.py` has a `raise Warning("Empty track in the
   timeline!")`. A front end cannot tell "bad input" from "bug".
7. **The default instrument mapping is not in the wheel.** `examples/` is
   outside `src/`, so an installed package has no instrument parameters.
8. **There were no tests**, so there was nothing to prove a refactor kept the
   output identical. Step 0 fixed this.

## Step 0: capture golden output BEFORE changing anything — DONE

Committed as `a0d0aa3` ("Add pytest with a fixture MIDI and golden output
baseline") on the untouched code. What exists now:

- `pytest` is in the `dev` dependency group. `[tool.pytest.ini_options]` in
  `pyproject.toml` sets `testpaths = ["tests"]`, `--strict-markers`,
  `filterwarnings = ["error"]`, and the `unit` / `integration` markers.
- The test layout mirrors the MakeCode-Arcade-to-App project:
  - `tests/conftest.py` marks every test with its tier by directory
    (`tests/unit/` or `tests/integration/`) and fails collection for any test
    file outside those two directories. Tests don't mark themselves.
  - `tests/helpers.py` (plain module, imported as `from helpers import ...`)
    holds the paths `FIXTURES`, `FIXTURE_MIDI`, `GOLDEN` and
    `INSTRUMENT_PARAMS` (now `src/midi2mkcd/resources/instrument_params.yaml`;
    it was `examples/instrument_params.yaml` when the goldens were captured).
  - `tests/fixtures/make_fixture.py` regenerates `tests/fixtures/fixture.mid`
    (`uv run python tests/fixtures/make_fixture.py`). It has 4 tracks at 480
    ticks/beat: a conductor track (120 → 150 BPM tempo change at beat 4, and
    lyrics `"Hel"`, `"lo "`, `"café "`, `"world"`; mido writes lyrics as
    latin-1, so non-ASCII lyrics must be latin-1), piano (channel 0, program
    0, a melody then 3- and 4-note chords, one quieter), strings (channel 1,
    program 48, overlapping held notes), and drums (channel 9: kick, snare,
    closed hat).
  - `tests/fixtures/golden/` holds the baseline, captured from the **old** CLI
    with `-i tests/fixtures/fixture.mid -p examples/instrument_params.yaml -o ...`:

    | Golden file        | Old invocation (extra flags)                         |
    | ------------------ | ---------------------------------------------------- |
    | `hex.ts`           | none                                                 |
    | `play.ts`          | `--test-generate-code`                               |
    | `visualizer.ts`    | `--generate-extra-code`                              |
    | `replace.ts`       | `--test-replace-all-melodics-with 5 --test-generate-code` |
    | `sample_0-2.ts`    | `--test-sample-melodic-instruments 0-2`              |

  - `tests/integration/test_golden_cli.py` runs the old `midi2mkcd.main.main()`
    in-process (patched `sys.argv`, `-o` into `tmp_path`) for each golden and
    compares text. 5 tests, all passing.
- Line endings: on Windows the old CLI's `-o` writes CRLF, but the golden
  files are stored **LF only** (no trailing newline on `hex.ts`). The repo has
  `core.autocrlf=true`, so `.gitattributes` pins
  `tests/fixtures/golden/*.ts text eol=lf` and marks `*.mid` as binary.
  Compare against goldens with `read_text(encoding="utf-8")` (universal
  newlines) or exact strings from the API; never re-save them through
  `write_text` on Windows without `newline="\n"`.
- Checks: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check
  src tests`, `uv run mypy src tests` all pass. (`ruff format --check .` also
  flags this plan file's Python code blocks; ignore that; pre-commit only
  passes Python files to ruff.)

These files are the regression baseline for everything below. Notes for the
next steps:

- Steps 1–3 below must keep `test_golden_cli.py` passing unchanged.
- ~~When the YAML moves into `resources/` (step 4), update
  `helpers.INSTRUMENT_PARAMS` to point at the new location (do not keep a copy
  in `examples/`).~~ Done in `306633b`.
- ~~When the old CLI is deleted (step 6), delete `test_golden_cli.py`; the API
  golden test replaces it.~~ Done in step 6 (`test_cli.py` covers the CLI).

## Logging cleanup — DONE

Committed as `19634e1` ("Use plain module loggers; only the CLI configures
logging"). This is item 2 in "Suggested commit order". What exists now:

- Every library module does `logger = logging.getLogger(__name__)` and nothing
  else. `utils/logger.py` (`create_logger`, `set_all_stdout_logger_levels`) is
  deleted; nothing references it.
- `src/midi2mkcd/__init__.py` is no longer empty: it adds a `NullHandler` to the
  `midi2mkcd` logger. Step 5 adds the public re-exports below it.
- `cli.py::configure_logging(level)` is the only logging setup. It attaches one
  `StreamHandler(sys.stderr)` to the `midi2mkcd` logger (format
  `"%(asctime)s - %(name)s - %(levelname)s - %(message)s"`) and sets that
  logger's level. It keeps the handler in a module global (`_cli_log_handler`)
  and removes it on the next call, so calling `main()` repeatedly in-process
  (as the golden CLI tests do) doesn't pile up handlers. The step 6 CLI rewrite
  should keep this idempotent behaviour (or configure via `-v` the same way).
- The old `main.py` calls `configure_logging(args.debug)`. `--debug` stores
  `logging.DEBUG`/`logging.INFO`; the old code did `bool(args.debug)`, which was
  always true, so debug logging was on for every run. That bug is fixed; a
  normal run now logs at INFO and above.
- Checked by hand on the fixture: stdout has no log lines, stderr has no
  duplicate lines, and `--debug` adds DEBUG lines.
- `CLAUDE.md`'s Conventions section now describes the new logging setup.
- Still pending from the Logging section below: renaming `--debug` to
  `-v/--verbose` (step 6). The unconditional simulator-crash warning in
  `main()` is still there; it goes away in step 5.
- All checks still pass: `uv run pytest` (5 golden tests, unchanged),
  `uv run ruff check .`, `uv run ruff format --check src tests`,
  `uv run mypy src tests`.

## Errors — DONE

Committed as `6abb26a` ("Add typed exception hierarchy for input errors").
This is item 3 in "Suggested commit order". What exists now:

- `src/midi2mkcd/errors.py` defines `Midi2MkcdError` and its subclasses
  `InvalidMidiError`, `InstrumentParamsError` and `SongConstraintError`
  exactly as in the `errors.py` section. It imports nothing. They are not yet
  re-exported from the package root (step 5 does that).
- `timeline/validation.py`: too many tracks, tick > 65535, note bytes >
  65535, chord with > 255 notes, and the old `raise Warning("Empty track in
  the timeline!")` now raise `SongConstraintError`. The 6 "(please report)"
  checks now raise `RuntimeError` (also fixed a missing `)` in "Found non drum
  chord in drum track! (please report)"). Other messages are unchanged.
- `midi_to_song/__init__.py`: its "(please report)" octave-offset check raises
  `RuntimeError`. **New:** `check_instruments_are_mapped(notes, mapping)` runs
  right after the replace-melodics/drums testing options are applied (before
  the pitch shift). It raises one `InstrumentParamsError` listing every
  missing instrument/drum note, e.g. `No instrument parameters for MIDI
  instruments 0 and MIDI drum notes 36`. Before, the first one surfaced as a
  bare `KeyError` in `timeline_fix_gate_lens` or the final mapping.
- `midi_to_song/instruments.py` had no `ValueError`s; bad YAML failed with raw
  `KeyError`/`TypeError`. Now `load_instrument_params` raises
  `InstrumentParamsError` for: YAML parse errors (`yaml.YAMLError`, chained),
  a non-mapping top level, `melodic_instruments`/`drum_instruments` missing or
  not a list, and any bad entry (message from `_entry_error`, e.g. `Invalid
  melodic instrument entry (instrument 3): missing field 'amp_envelope'`).
  `waveform_from_str` raises `InstrumentParamsError("Unknown waveform: ...")`;
  the name table is now module-level `_WAVEFORMS_BY_NAME`. Force-load still
  skips bad entries (the old `testing_opts.force_load` path), but the
  top-level structure checks apply even with force-load.
  `examples/blank_instrument_params.yaml` still needs force-load (its entries
  are incomplete); it now fails with `InstrumentParamsError` instead of
  `KeyError`.
- `main.py` (old CLI, the only place MIDI is loaded today) does
  `input_path.read_bytes()` first, so missing files stay plain `OSError`, then
  `MidiFile(file=io.BytesIO(...))` inside `try/except Exception` →
  `InvalidMidiError(f"Could not parse MIDI file {path}: {e!r}") from e`. mido
  raises many types for malformed data (garbage bytes give `EOFError()`).
  Step 5's `convert()` should reuse this pattern for bytes/file/path input.
- Deliberately **not** changed: the `ValueError`s in `arcade/music.py`
  (decode-side, keep faithful to pxt), `main.py`'s mutual-exclusion
  `ValueError`s and pyperclip `RuntimeError` (gone in step 6), and
  `parse_range`'s `argparse.ArgumentTypeError` (step 6).
- No tests were added for the errors yet; they belong in `test_api.py`
  (step 5): garbage bytes → `InvalidMidiError`, malformed YAML →
  `InstrumentParamsError`. Consider also a unit test for an unmapped
  instrument → `InstrumentParamsError`. These were checked by hand with a
  throwaway script.
- All checks pass: `uv run pytest` (5 golden tests, unchanged),
  `uv run ruff check .`, `uv run ruff format --check src tests`,
  `uv run mypy src tests`.

## Default YAML moved into the package — DONE

Committed as `306633b` ("Bundle the default instrument params in the
package"). This is item 4 in "Suggested commit order". **Deviation from the
original plan:** the folder is `src/midi2mkcd/resources/` (not `data/`) to
match the MakeCode-Arcade-to-App project, and it has no `__init__.py` (not
needed for `files(...).joinpath`). The rest of this file has been updated to
say `resources/`.

- `git mv examples/instrument_params.yaml
  src/midi2mkcd/resources/instrument_params.yaml` (no copy left in
  `examples/`).
- `src/midi2mkcd/utils/resources.py::default_instrument_params_text()` returns
  `files("midi2mkcd").joinpath("resources", "instrument_params.yaml")
  .read_text(encoding="utf-8")`. No `as_file()`: only text is needed. Not yet
  re-exported from the package root (step 5 does that).
- `tests/helpers.py`: new `PACKAGE_ROOT`; `INSTRUMENT_PARAMS` points at the
  new location.
- README command examples (`example/...`, which was a typo) and links,
  plus CLAUDE.md, use the new path.
- hatchling includes the YAML with no config change: the built wheel
  (`py3-none-any`) contains `midi2mkcd/resources/instrument_params.yaml`.
- All checks pass (5 golden tests unchanged, ruff, ruff format, mypy).
- The CLI still requires `-p`; making it optional (default bundled) is step 6.

## Library API, tuning tools and rendering — DONE

Committed as `04060f5` ("Add library API, tuning tools and rendering
module"). This is item 5 in "Suggested commit order". What exists now:

- `src/midi2mkcd/api.py`: `convert(midi, params=None)`, `ConversionResult`
  (with `comments`, no `options`), `OutputFormat`, `MidiInput`, `ParamsInput`,
  `SIMULATOR_NOTE_WARNING_THRESHOLD`, and re-exports of
  `load_instrument_params` / `default_instrument_params_text`. Private
  helpers shared with `tuning`: `_load_midi` (bytes / bytearray / memoryview /
  binary stream / path → `MidiFile`, `InvalidMidiError` on parse failure,
  `OSError` for a bad path), `_resolve_params`, and `_convert(midi_file,
  mapping, *, replace_melodics, replace_drums)`.
- `src/midi2mkcd/render.py`: `song_hex`, `to_typescript`, `sampler` (shared
  by both samplers) and `usage_report` (the `tune --report` table, ready for
  step 6). The header rule: every format except plain `"hex"` gets the
  "Generated by" line; plain hex gets it only when the result has comments.
- `src/midi2mkcd/tuning.py`: `convert_replaced`, `sample_melodics`,
  `sample_drums`, `InstrumentUsage`, `report`, `check_params`, as designed in
  "Tuning tools". The samplers parse the MIDI and params once, so a stream
  input works. Comment order when both are replaced is melodics, then drums
  (the old CLI's prepending put drums first; no golden covered it).
- `load_instrument_params(yaml_text, *, allow_incomplete=False)`; the new
  internal `load_instrument_params_with_problems` also returns the skipped
  entries' messages (used by `check_params`, logged at DEBUG).
  `TestingOptionsForLoadInstrumentParams` and `TestingOptionsForMIDIToSong`
  are deleted; `convert_midi_to_song` takes `replace_melodics` /
  `replace_drums` keyword arguments.
- Package root re-exports the public names with `__all__` and does not
  import `tuning` (`from midi2mkcd import tuning` still works).
- Old CLI: `cli.py` now only holds `configure_logging`, the old argparse
  setup and `range_arg`; all TypeScript building is gone from it. `main.py`
  calls `convert` / `tuning.convert_replaced` / `tuning.sample_melodics` and
  `to_typescript`. The unconditional simulator warning is gone. The old
  flags and their behaviour are unchanged otherwise.
- Golden change: only `replace.ts`, which lost the trailing space after
  "MIDI instrument 5" (documented fix 2). Fix 1 (`is not None`) is covered by
  `test_tuning.py`.
- **Pulled forward from step 6:** `parse_range` now raises `ValueError`
  (`utils/strings.py` imported `argparse`, which `processor.py` imports, so
  `import midi2mkcd` pulled in argparse). `cli.range_arg` wraps it as
  `ArgumentTypeError`. Also added `format_ranges` (the inverse, used by
  `check_params`).
- **Deviation:** `check_params` does **not** flag amp-envelope sustain above
  amplitude. The bundled params do that on purpose ~60 times (sustain seems to
  be a level relative to the amplitude), so it was a false positive. Range
  limits come from the encoder: 16-bit fields 0–65535, LFO frequency 0–255,
  amplitudes/volumes 0–1024, drum step duration ≥ 1, ≤ 255 steps.
- Tests (55, all passing): `test_golden.py` (API vs every golden),
  `test_api.py`, `test_tuning.py`, `tests/unit/test_render.py`,
  `tests/unit/test_strings.py`, `tests/unit/test_import_hygiene.py` (pulled
  forward from step 7), and the old `test_golden_cli.py`, still passing.
- Checks: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check
  src tests`, `uv run mypy src tests` all pass. Manually checked: old CLI
  stdout has no log lines; a bad range shows the `parse_range` message.
- Not done here: `__version__` (step 7 with the version bump), a test that the
  15k-note warning fires (no fixture is that big).

## New CLI — DONE

Committed as "Rewrite the CLI as subcommands on top of the library" (the
commit after `f05a34e`; see `git log`) and pushed to `cli-refactor`. This is
item 6 in "Suggested commit order". What exists now:

- `src/midi2mkcd/cli.py` rewritten from scratch (~330 lines, over the 250
  target because every help string is spelled out; the logic is small).
  Commands, options, help text, exit statuses and errors are as in the "CLI"
  section below, which has been updated to match the real output.
  - `main(argv=None) -> int`; `build_parser()`; one `_run_*` per leaf;
    `_read_params(path, *, allow_incomplete)`; `_emit(text, output, copy) ->
    int`. `range_arg` and `configure_logging` (format unchanged, idempotent)
    are kept.
  - **Deviation:** shared options are added by small helper functions
    (`_add_params`, `_add_output`, `_add_verbose`, `_add_tune_common`,
    `_add_format`, `_add_input`) instead of `parents=[...]` parent parsers.
    Parent-parser arguments are always added before a leaf's own arguments,
    which would put `-f` after `-v` in the usage line; helpers keep the planned
    order. Each option's full help text is shown everywhere (the "(as for
    convert)" shorthand in the old plan help targets was dropped).
  - A small `_HelpFormatter` (`RawDescriptionHelpFormatter` + keeps `\n` in
    option help) so the `-f` help lists one format per line.
  - `-v/--verbose` help says it also shows tracebacks for errors.
  - `--copy` without pyperclip prints `error: --copy needs pyperclip: pip
    install "midi2mkcd[optional]"` **after** writing the output, exit 1.
  - **New, not in the old plan:** when stdout is not a console and is a
    `TextIOWrapper`, `_emit` reconfigures it to UTF-8 before writing. On
    Windows, redirected stdout defaulted to cp1252, which garbled `café`
    and would crash on lyrics outside cp1252. `-o` files are always UTF-8.
  - `params check` logs `No problems found` at INFO when clean.
- `__main__.py` is `from midi2mkcd.cli import main` + `raise
  SystemExit(main())`. `[project.scripts]` is `midi2mkcd =
  "midi2mkcd.cli:main"` (checked in the built wheel's `entry_points.txt`).
- Deleted `src/midi2mkcd/main.py` and `tests/integration/test_golden_cli.py`.
- Library renames: `OutputFormat = Literal["hex", "code", "code_extra"]`
  (`api.py`, `render.py` branches and docstrings). Goldens `git mv`ed:
  `play.ts` → `code.ts`, `visualizer.ts` → `code_extra.ts` (contents
  unchanged); `test_golden.py` and `test_render.py` updated.
- `git mv examples/blank_instrument_params.yaml
  src/midi2mkcd/resources/blank_instrument_params.yaml`.
  `utils/resources.py` now has `blank_instrument_params_text()` next to
  `default_instrument_params_text()` (shared `_resource_text(name)`); it is
  re-exported from `midi2mkcd.tuning` (in its `__all__`), not the root.
  `test_tuning.py` no longer has `BLANK_PARAMS`; it uses
  `tuning.blank_instrument_params_text()`. The wheel contains both YAML files.
- **New (user request): General MIDI names in `tune report`.**
  `src/midi2mkcd/utils/gm_names.py` has `GM1_MELODIC_INSTRUMENT_NAMES` (tuple,
  programs 0–127), `GM_DRUM_NOTE_NAMES` (dict, notes 27–87, GM2 ones suffixed
  `(GM2)`), both from soundprogramming.net (URLs in the file), and
  `melodic_instrument_name(n)` / `drum_note_name(n)`, which return `"?"`
  outside those ranges. `render.usage_report` has a new `name` column after
  `number`. `InstrumentUsage` is unchanged (the name is a display concern).
  These names could also label `tune sample` songs later; not done.
- Tests: `tests/integration/test_cli.py` (37 tests, in-process `main(argv)`):
  every golden via `-o` (file = golden + `"\n"`), stdout = code + `"\n"` with
  log lines only on stderr, `-v` adds DEBUG lines, `-p` file,
  `--allow-incomplete` with the blank file on `tune report`, `tune report`
  table, `tune sample --drums`, `params show [--blank]`, `params check`
  (bundled → 0, blank → 1 with one line per problem), garbage MIDI and
  missing input → exit 1 with `error:` and no traceback, `-v` re-raises,
  `-h` for every command exits 0, `--version`, and exit 2 for every usage
  error in "Tests this adds to `test_cli.py`". `test_render.py` gained a
  name-column test (`?` for unknown numbers). 88 tests pass.
- Checks: `uv run pytest`, `uv run ruff check .`, `uv run ruff format
  --check src tests`, `uv run mypy src tests` and `uv run pre-commit run
  --all-files` all pass. Checked by hand:
  every help screen, `uv run midi2mkcd ...` (the installed script), the
  error messages, `--copy` without pyperclip (exit 1), and the UTF-8 lyric
  through a pipe.
- **Fixed a pre-existing pre-commit failure:** the mypy hook passes
  filenames in batches, so mypy couldn't resolve `from helpers import ...` in
  `tests/integration/*.py` (`Cannot find implementation or library stub for
  module named "helpers"`). `[tool.mypy]` in `pyproject.toml` now sets
  `mypy_path = "tests"`. `uv run pre-commit run --all-files` passes.
- CLAUDE.md: Commands, the testing paragraph, the `-p` sentence and the
  Architecture paragraph about `main.py` / `cli.py` are updated.

**Next: step 7** (README, version bump, housekeeping). Pointers:

- README: replace every old-flag command (`-i`, `--test-*`, `--debug`,
  `--generate-extra-code`) with the new commands (see "Mapping from old
  flags"); replace its help-text section (currently `usage: main.py ...`,
  around line 157) with the new `midi2mkcd -h` / subcommand help; mention
  `tune report` (with GM names), `params show/check`, that `-p` is optional,
  and the Windows note under "CLI" → "Behaviour" (prefer `-o` in PowerShell
  5.1). Add a short "Use as a library" section showing `convert()`. Fix links
  to `examples/blank_instrument_params.yaml` (now in `resources/`) if any.
- Bump `version` in `pyproject.toml` to `0.2.0` and add `__version__` to the
  package root (then `cli._version()` may switch to it).
- `requirements.txt`: delete if nothing references it, or regenerate.
- Re-run all checks and tick the acceptance checklist.

## Target layout

```
src/midi2mkcd/
  __init__.py          # re-exports the public API, defines __all__ and __version__
  api.py               # convert(), load_instrument_params(), ConversionResult
  tuning.py            # instrument-tuning tools (convert_replaced, sample_melodics); not re-exported
  render.py            # ALL TypeScript/text output generation
  errors.py            # exception hierarchy
  cli.py               # argparse, file I/O, logging setup, clipboard. Nothing else. (DONE, step 6)
  __main__.py          # from midi2mkcd.cli import main; raise SystemExit(main()) (DONE)
  resources/           # no __init__.py (DONE in 306633b)
    instrument_params.yaml   # moved from examples/instrument_params.yaml
    blank_instrument_params.yaml   # moved from examples/ (DONE, step 6; for `params show --blank`)
  midi_to_song/        # internals, kept in place
  arcade/              # internals, kept in place
  utils/
    strings.py         # parse_range no longer depends on argparse
    format.py
    resources.py       # default_instrument_params_text(), blank_instrument_params_text() (both DONE)
    gm_names.py        # General MIDI instrument / drum note names for `tune report` (DONE, step 6)
```

`main.py` and `utils/logger.py` are deleted. `[project.scripts]` is
`midi2mkcd = "midi2mkcd.cli:main"` (DONE, step 6).

`instrument_params_2.yaml` stays in `examples/`.
`blank_instrument_params.yaml` is now in `resources/` (step 6).

## Public API

Everything below is importable from the package root (`from midi2mkcd import
...`). Nothing else is considered public. The instrument-tuning tools live in
`midi2mkcd.tuning` (see "Tuning tools") and are deliberately **not**
re-exported from the root.

```python
# api.py

OutputFormat = Literal["hex", "code", "code_extra"]   # was "play" / "visualizer" before step 6

def convert(
    midi: bytes | bytearray | memoryview | BinaryIO | os.PathLike[str] | str,
    params: str | InstrumentParameterMapping | None = None,
) -> ConversionResult: ...

def load_instrument_params(
    yaml_text: str, *, allow_incomplete: bool = False   # was --test-force-instrument-param-load
) -> InstrumentParameterMapping: ...
def default_instrument_params_text() -> str: ...   # reads resources/instrument_params.yaml via importlib.resources
```

Rules:

- `midi`: bytes-like or a binary file object is parsed with
  `mido.MidiFile(file=io.BytesIO(...))`. A `str`/`PathLike` is treated as a
  path. `mido` types never appear in the public signatures.
- `params`: `str` is **YAML text, not a path**. `None` means the bundled
  default. An already-loaded `InstrumentParameterMapping` is accepted so
  callers can parse once and convert many times.
- `allow_incomplete=True` skips bad/incomplete entries instead of raising
  (top-level structure checks still apply). Accepting a partial file is a
  loading concern, so it lives here, not on `convert()`. Converting a song
  that uses an instrument the mapping lacks still raises
  `InstrumentParamsError`.
- **No `ConvertOptions` yet.** There are no real conversion options today;
  everything the old testing options did was instrument tuning, which moves to
  `midi2mkcd.tuning`. When genuine conversion options appear (e.g. adjusting
  instrument design during conversion), add them as a keyword-only
  `options: ConvertOptions | None = None` on `convert()`. That is
  non-breaking, so don't add an empty placeholder class now.
- `convert()` never mutates its arguments.
- Importing `midi2mkcd` must not import `argparse` or `pyperclip`, must not
  read or write files other than the bundled YAML, must not print, and must
  not add logging handlers.

```python
@dataclass(frozen=True)
class ConversionResult:
    song: Song
    drum_idx_to_midi_drum: list[int]
    track_idx_to_midi_instrument: list[int]
    lyric_ticks: list[int]
    lyric_texts: list[str]
    note_event_count: int          # total NoteEvents across all tracks
    warnings: list[str]            # human-readable, for front ends to display
    comments: tuple[str, ...] = () # emitted by render.py as `// ...` header lines

    def to_hex(self) -> str: ...                              # raw hex digits only
    def to_typescript(self, format: OutputFormat = "hex") -> str: ...
```

`to_hex` and `to_typescript` delegate to `render.py`. This replaces
`ConvertMIDIToSongResult`; the internal `convert_midi_to_song` can keep
returning its own structure or be folded into `api.py`, whichever is less
churn.

`comments` is generic on purpose: `convert()` leaves it empty, `tuning` fills
it with "melodics/drums replaced with ..." lines, and future conversion
options can add their own. `render.py` never needs to know about tuning.
There is no `options` field on the result.

### Tuning tools (`midi2mkcd.tuning`)

Developer tools for writing and auditioning instrument parameter files. A
submodule, not re-exported from the package root, so the public API stays
small and these can change freely.

```python
# tuning.py

def convert_replaced(
    midi: <same as convert>,
    params: str | InstrumentParameterMapping | None = None,
    *,
    melodics: int | None = None,   # replace every melodic instrument with this MIDI program
    drums: int | None = None,      # replace every drum note with this MIDI drum note
) -> ConversionResult: ...

def sample_melodics(
    midi: <same as convert>,
    instruments: Iterable[int],
    params: str | InstrumentParameterMapping | None = None,
) -> str: ...   # one Arcade program that plays the song once per instrument

def sample_drums(
    midi: <same as convert>,
    notes: Iterable[int],
    params: str | InstrumentParameterMapping | None = None,
) -> str: ...   # drum counterpart: plays the song once per drum note, all drums replaced with it

@dataclass(frozen=True)
class InstrumentUsage:
    kind: Literal["melodic", "drum"]
    number: int                    # MIDI program (melodic) or MIDI drum note (drum)
    note_count: int
    lowest_note: int               # MIDI note numbers, before any pitch shift
    highest_note: int
    min_velocity: int
    max_velocity: int
    mapped: bool                   # the given params have an entry for it

def report(
    midi: <same as convert>,
    params: str | InstrumentParameterMapping | None = None,
) -> list[InstrumentUsage]: ...   # sorted: melodics by program, then drums by note

def check_params(params: str | InstrumentParameterMapping) -> list[str]: ...
```

- Both convert functions call a **private** internal convert (e.g. `api._convert(midi, params,
  *, replace_melodics=None, replace_drums=None)`) that does the same
  note-level replacement as today (instruments are replaced before grouping,
  so all melodic tracks merge). Behaviour and `replace.ts` stay the same.
- `convert_replaced` sets `result.comments` to the replacement lines.
  `sample_melodics` builds one `convert_replaced` result per instrument and
  hands them to the `render.py` sampler.
- `sample_drums` mirrors `sample_melodics` with `convert_replaced(...,
  drums=n)` per note, reusing the same `render.py` sampler (generalise it so
  the per-song label says "MIDI drum note N" instead of "MIDI instrument N").
  No golden file for it; it is new output.
- `report` answers "what does this song need tuned, and in what range?". It
  runs only the parser stage (`timeline/parser.py`: complete notes after
  program changes and drum switching, before the pitch shift), so it
  **never raises** for unmapped instruments; `mapped` just reports coverage.
  `params=None` checks against the bundled default. Bad MIDI still raises
  `InvalidMidiError`.
- `check_params` lints a params file without playing anything and returns
  human-readable problems (empty list means clean). It flags:
  - amplitudes/volumes outside 0–1024 and negative durations or frequencies;
  - envelope sanity issues (e.g. sustain above the start amplitude, zero-length
    steps);
  - GM1 programs 0–127 and drum notes 27–87 with no entry (one summarised
    line each, e.g. `No entry for melodic instruments 3, 7-9`);
  - duplicate `instrument:`/`note:` entries. The loader keeps the last
    duplicate silently, so this check needs the YAML **text**; when given an
    already-loaded mapping it skips the duplicate check.

  When given text, it loads with `allow_incomplete=True` and reports each
  skipped bad entry (the `_entry_error` message) as a problem instead of
  raising, so it works on half-written files. Only unparseable YAML or a bad
  top-level structure raises `InstrumentParamsError`. Use the existing `utils/strings.py` range
  formatting if there is one for the summarised lists, otherwise add a small
  helper.
- Rejected alternative: implementing replacement by rewriting the params
  mapping so every program points at instrument N's settings. That keeps
  each program as its own track instead of merging them, changes the output
  (would need `replace.ts` regenerated) and can hit the track limit on big
  songs.

### `render.py`

Move all output construction here, as pure functions of a `ConversionResult`:

| `format`       | Output                                                              | Was                          |
| -------------- | ------------------------------------------------------------------- | ---------------------------- |
| `"hex"`        | ``hex`...` `` literal                                               | default                      |
| `"code"`       | `music.play(music.createSong(hex`...`), music.PlaybackMode.UntilDone);` | `--test-generate-code`   |
| `"code_extra"` | `songHex`, `midiDrumNoteMap`, `trackInstrumentMap`, `lyricTicks`, `lyricTexts` consts | `--generate-extra-code` |

Also here: the "Generated by ..." header, the `result.comments` lines (which
carry the "melodics/drums replaced with" text from `tuning`), and the
multi-song sampler output used by `tuning.sample_melodics()`.

Output must match the golden files byte for byte, with two deliberate fixes:

1. The existing header check uses truthiness, so replacing with instrument or
   drum note `0` omits the header. In `tuning`, use `is not None` when
   deciding whether to add the replacement comment.
2. The "melodics replaced with MIDI instrument N" comment has a trailing
   space. Remove it.

If either fix changes a golden file, regenerate that file and say so in the
commit message.

### `errors.py`

```python
class Midi2MkcdError(Exception): ...
class InvalidMidiError(Midi2MkcdError): ...        # mido could not parse the input
class InstrumentParamsError(Midi2MkcdError): ...   # bad YAML, missing/invalid fields, unmapped instrument
class SongConstraintError(Midi2MkcdError): ...     # song cannot fit Arcade's format (too many tracks, etc.)
```

- Replace the `ValueError`s in `timeline/validation.py`,
  `midi_to_song/__init__.py` and `instruments.py` with the appropriate
  subclass. Keep the messages.
- Replace `raise Warning("Empty track in the timeline!")` with
  `SongConstraintError`.
- Checks whose message says "(please report)" are internal invariants. Leave
  those as `AssertionError` or a plain `RuntimeError` so they are
  distinguishable from user-input problems.
- Wrap mido parse failures in `InvalidMidiError` (`raise ... from e`).

### Logging

- Every module: `logger = logging.getLogger(__name__)`. Nothing else.
- Delete `utils/logger.py` including `set_all_stdout_logger_levels`.
- Add `logging.getLogger("midi2mkcd").addHandler(logging.NullHandler())` in
  the package `__init__.py`.
- Only `cli.py` configures logging: one `StreamHandler` on **stderr**, level
  from `-v/--verbose`. stdout carries only the generated code.

### Warnings on the result

- Remove the unconditional simulator-crash warning from `main()`.
- In `api.py`, define `SIMULATOR_NOTE_WARNING_THRESHOLD = 15_000`. When
  `note_event_count` meets it, append a short crash-risk message to
  `result.warnings` and also `logger.warning` it. (README says 24k+ notes
  crashed on first play and that ~15k played twice may also crash.)
- The CLI prints `result.warnings` to stderr via the logger. A web page can
  render them however it likes.

## CLI

Rewrite `cli.py` from scratch as roughly: parse args, read files, call the
API, write output. Target well under 250 lines (the help strings are most of
it).

### Shape

Every mode is its own subcommand. The only option that chooses between
output variants is `-f`.

```
midi2mkcd [-h] [--version] {convert,tune,params} ...

midi2mkcd convert INPUT [-f {hex,code,code_extra}] [-p PARAMS] [-o OUT] [--copy] [-v]

midi2mkcd tune replace INPUT [--melodics N] [--drums N] [-f {hex,code,code_extra}] COMMON
midi2mkcd tune sample  INPUT (--melodics RANGE | --drums RANGE)                    COMMON
midi2mkcd tune report  INPUT                                                       COMMON
    COMMON: [-p PARAMS] [--allow-incomplete] [-o OUT] [--copy] [-v]

midi2mkcd params show  [--blank] [-o OUT] [-v]
midi2mkcd params check [-p PARAMS] [-v]
```

- A subcommand is required at every level (`add_subparsers(required=True)`
  for the top level, `tune` and `params`). Bare `midi2mkcd`, `midi2mkcd tune`
  and `midi2mkcd params` print usage and exit 2. There is no implicit
  `convert` (`midi2mkcd song.mid` is a usage error); keeping argparse plain
  matters more than saving one word.
- `--version` prints `midi2mkcd <version>` from
  `importlib.metadata.version("midi2mkcd")` (works before step 7 adds
  `__version__`; switch to `__version__` then if preferred).
- Shared options are defined once in helper functions (`_add_params`,
  `_add_output`, `_add_verbose`, ...) called on each leaf subparser that
  takes them: `-v/--verbose`, `-p/--params`, `-o/--output` + `--copy`,
  `--allow-incomplete`. (Parent parsers were the original plan, but they add
  their options before the leaf's own, which breaks the usage-line order.)
  So all options go **after the last subcommand** (`midi2mkcd tune replace x.mid --melodics 5 -v`).
  Options before the first subcommand are only `-h` and `--version`.
- `-f` is the short form of `--format`. CLI and library use the same names:
  - `hex`: a ``hex`...` `` literal.
  - `code`: a statement that plays the song in Arcade (was `play`).
  - `code_extra`: constants for the Song-Visualizer project (was
    `visualizer`).
- `-o` always means "write this command's output here". Every leaf has it
  except `params check`, which only reports problems.
- `--copy` is on `convert` and every `tune` subcommand (for `tune report` it
  copies the table). Not on `params`.
- `--allow-incomplete` is on every `tune` subcommand. With no `-p` it does
  nothing (the bundled file is complete) and is allowed.

### `--help` text

These were the targets for the help strings. The implemented help (step 6)
matches them in wording, except: argparse wraps usage lines at 80 columns,
lists `-h, --help` and the `{convert,tune,params}` choice line, every option
shows its full help instead of "(as for convert)", `-f` help ends with
`Default: hex.` / `Default: code.`, `-v` says "Show debug logging on stderr,
and tracebacks for errors.", and `tune report`'s description mentions the
General MIDI names. Run `uv run midi2mkcd ... -h` for the real text.

```
$ midi2mkcd -h
usage: midi2mkcd [-h] [--version] {convert,tune,params} ...

Convert a MIDI file to a MakeCode Arcade song.

commands:
  convert   Convert a MIDI file to Arcade code.
  tune      Tools for auditioning instrument parameters against a song.
  params    Print or check instrument parameter files.

Run 'midi2mkcd COMMAND -h' for a command's options.
```

```
$ midi2mkcd convert -h
usage: midi2mkcd convert [-h] [-f {hex,code,code_extra}] [-p PARAMS] [-o OUT] [--copy] [-v] INPUT

Convert a MIDI file to a MakeCode Arcade song.

positional arguments:
  INPUT                 MIDI file to convert.

options:
  -f, --format {hex,code,code_extra}
                        hex: a hex`...` literal (default).
                        code: a statement that plays the song in Arcade.
                        code_extra: constants for the Song-Visualizer project.
  -p, --params PARAMS   Instrument parameter YAML file. Default: the bundled
                        parameters (see 'midi2mkcd params show').
  -o, --output OUT      Write the output to this file (UTF-8, overwritten if
                        it exists). Default: stdout.
  --copy                Also copy the output to the clipboard (needs the
                        'optional' extra: pip install "midi2mkcd[optional]").
  -v, --verbose         Show debug logging on stderr.
```

```
$ midi2mkcd tune -h
usage: midi2mkcd tune [-h] {replace,sample,report} ...

Tools for auditioning instrument parameters against a song.

commands:
  replace   Convert the song with every melodic and/or drum instrument replaced.
  sample    Play the song once per instrument in a range.
  report    List the instruments and drum notes the song uses.
```

```
$ midi2mkcd tune replace -h
usage: midi2mkcd tune replace [-h] [--melodics N] [--drums N] [-f {hex,code,code_extra}]
                              [-p PARAMS] [--allow-incomplete] [-o OUT] [--copy] [-v] INPUT

Convert INPUT with every melodic note played by one program and/or every drum
note played as one drum note. Give at least one of --melodics and --drums.

positional arguments:
  INPUT                 MIDI file to tune against.

options:
  --melodics N          Play every melodic note with MIDI program N.
  --drums N             Play every drum note as MIDI drum note N.
  -f, --format {hex,code,code_extra}
                        As for convert. Default: code.
  -p, --params PARAMS   (as for convert)
  --allow-incomplete    Skip incomplete or invalid entries in the params file
                        instead of failing. Songs that use a skipped
                        instrument still fail.
  -o, --output OUT      (as for convert)
  --copy, -v            (as for convert)
```

```
$ midi2mkcd tune sample -h
usage: midi2mkcd tune sample [-h] (--melodics RANGE | --drums RANGE)
                             [-p PARAMS] [--allow-incomplete] [-o OUT] [--copy] [-v] INPUT

Output one Arcade program that plays INPUT once per instrument in RANGE, each
time with every melodic note (or every drum note) replaced by that instrument.

positional arguments:
  INPUT                 MIDI file to tune against.

options:
  --melodics RANGE      MIDI programs to sample, e.g. "0,2,4-10".
  --drums RANGE         MIDI drum notes to sample, e.g. "35-40".
  -p, --allow-incomplete, -o, --copy, -v
                        (as for tune replace)
```

```
$ midi2mkcd tune report -h
usage: midi2mkcd tune report [-h] [-p PARAMS] [--allow-incomplete] [-o OUT] [--copy] [-v] INPUT

Print a table of the instruments and drum notes INPUT uses, their note and
velocity ranges, and whether PARAMS has an entry for each.

positional arguments:
  INPUT                 MIDI file to report on.

options:
  -p, --allow-incomplete, -o, --copy, -v
                        (as for tune replace)
```

```
$ midi2mkcd params -h
usage: midi2mkcd params [-h] {show,check} ...

Print or check instrument parameter files.

commands:
  show      Print the bundled instrument parameters (or a blank template).
  check     Lint an instrument parameter file.
```

```
$ midi2mkcd params show -h
usage: midi2mkcd params show [-h] [--blank] [-o OUT] [-v]

Print the bundled instrument parameters, as a starting point for your own
file.

options:
  --blank               Print a blank template instead.
  -o, --output OUT      Write the YAML here instead of stdout.
  -v, --verbose         Show debug logging on stderr.
```

```
$ midi2mkcd params check -h
usage: midi2mkcd params check [-h] [-p PARAMS] [-v]

Lint an instrument parameter file and print one problem per line. Exit
status 1 if there are any.

options:
  -p, --params PARAMS   File to check. Default: the bundled parameters.
  -v, --verbose         Show debug logging on stderr.
```

### Option rules

Almost everything is expressed in argparse itself:

- `tune sample`: `--melodics` / `--drums` are an
  `add_mutually_exclusive_group(required=True)`, both with
  `type=range_arg`.
- `tune replace`: needs at least one of `--melodics` / `--drums` (both
  together is fine, as today). argparse can't express "at least one", so check
  after `parse_args` and call **that subparser's** `error(...)` (exit 2, its
  usage line, then `midi2mkcd tune replace: error: give --melodics, --drums
  or both`). Store the subparser with `set_defaults(parser=...)` so `main()`
  can reach it. `-f` defaults to `"code"` here.
- No other cross-option checks are needed.

### Behaviour

- **Params loading.** `-p PATH` → `Path.read_text(encoding="utf-8")` and pass
  the text as `params` (so `check_params` sees duplicates and errors say
  "YAML" not "file"). With `--allow-incomplete`, call
  `load_instrument_params(text, allow_incomplete=True)` and pass the mapping.
  No `-p` → `params=None`.
- **Input.** Pass the `Path` straight to the library (it reads the bytes and
  logs "Reading MIDI file from ...").
- **`params show`.** Emits `default_instrument_params_text()`, or with
  `--blank`, `blank_instrument_params_text()`. For that, step 6 moves
  `examples/blank_instrument_params.yaml` to
  `src/midi2mkcd/resources/blank_instrument_params.yaml` and adds
  `blank_instrument_params_text()` next to the default one in
  `utils/resources.py`. Re-export it from `midi2mkcd.tuning` (not the package
  root), since it is a tuning aid.
- **`params check`.** Reads `-p` as text (or uses the bundled text), prints
  each line of `tuning.check_params(...)` to stdout, exits 1 if there are
  any.
- **Output.** One helper, `_emit(text, output, copy)`, used by every leaf
  except `params check`:
  - `-o OUT`: `OUT.write_text(text + "\n", encoding="utf-8")`, then log
    `Wrote OUT` at INFO. Parent directories must exist (an `OSError`
    otherwise; see errors). Adding the final newline makes files end like
    normal text files; the golden tests compare with `.rstrip("\n")` or read
    the goldens the same way, whichever keeps the goldens untouched.
  - no `-o`: `sys.stdout.write(text + "\n")`. Nothing else ever goes to
    stdout. If stdout is redirected (not a console), it is first reconfigured
    to UTF-8 (step 6; Windows otherwise uses cp1252 for pipes and files).
  - `--copy`: lazy `import pyperclip` (after writing the output, so a missing
    pyperclip doesn't lose the result), copy `text` without the trailing
    newline, log `Copied to clipboard` at INFO. If the import fails:
    `error: --copy needs pyperclip: pip install "midi2mkcd[optional]"`, exit 1.
- **Windows note for the README (step 7).** In Windows PowerShell 5.1, `>`
  writes UTF-16, which Arcade won't accept as pasted text and other tools
  misread. Recommend `-o` over redirection; that's also why `params show` has
  `-o`.
- **Logging.** `configure_logging(logging.DEBUG if args.verbose else
  logging.INFO)`, idempotent as today. **Keep the current format,
  `"%(asctime)s - %(name)s - %(levelname)s - %(message)s"`, for every run**
  (it matches the user's other Python projects). `-v` changes only the level.
  The 15k-note warning arrives as a WARNING line from the library's logger;
  the CLI does not print `result.warnings` itself.
- **Errors and exit status.** `main(argv: list[str] | None = None) -> int`.
  `[project.scripts]` wraps it in `sys.exit(main())`; `__main__.py` does
  `raise SystemExit(main())`.

  | Status | When |
  | ------ | ---- |
  | 0 | success; `params check` found nothing |
  | 1 | `Midi2MkcdError` or `OSError` (missing input/params file, unwritable `-o`): `error: <message>` on stderr, no traceback; `params check` found problems; `--copy` without pyperclip |
  | 2 | usage error (argparse) |
  | traceback | anything else, including the `RuntimeError` "(please report)" invariants |

  Catching `OSError` is a change from the earlier note ("let anything else
  traceback"): a mistyped path is user error and shouldn't print a stack.
  The `OSError` message already names the file (`[Errno 2] No such file or
  directory: 'sogn.mid'`). With `-v`, re-raise instead of printing, so the
  full traceback is available when debugging.

### Example sessions

Normal conversion (stdout is only the code, stderr only log lines; logger
names and timestamps are illustrative):

```
$ midi2mkcd convert song.mid -o song.ts
2026-11-20 14:03:12,345 - midi2mkcd.api - INFO - Reading MIDI file from song.mid
2026-11-20 14:03:12,512 - midi2mkcd.cli - INFO - Wrote song.ts

$ midi2mkcd convert song.mid -f code --copy 2>/dev/null
// Generated by https://github.com/UnsignedArduino/MIDI-to-MakeCode-Arcade
music.play(music.createSong(
  hex`0078000418020300...`
...
```

Tuning loop: what does the song need, audition one instrument, compare a few:

```
$ midi2mkcd tune report song.mid -p mine.yaml
kind     number  name                  notes  range  velocity  mapped
melodic  0       Acoustic Grand Piano  14     53-67  60-100    yes
melodic  48      String Ensemble 1     3      48-55  80-80     NO
drum     36      Bass Drum 1           4      36-36  100-100   yes
drum     38      Snare Drum 1          4      38-38  100-100   yes
drum     42      Closed Hi-hat         16     42-42  70-70     yes

$ midi2mkcd tune replace song.mid -p mine.yaml --melodics 48 --copy -o tune.ts
$ midi2mkcd tune replace song.mid -p mine.yaml --melodics 48 --drums 36 -f hex
$ midi2mkcd tune sample song.mid -p mine.yaml --melodics 40-47 --copy
$ midi2mkcd tune sample song.mid -p mine.yaml --drums 35,36 --copy
```

(This is `render.usage_report`'s real output for `tests/fixtures/fixture.mid`
with program 48 marked unmapped. The `name` column comes from
`utils/gm_names.py`; numbers outside GM1 programs 0–127 or drum notes 27–87
show `?`.)

Params files:

```
$ midi2mkcd params show -o mine.yaml
$ midi2mkcd params show --blank -o blank.yaml
$ midi2mkcd params check -p mine.yaml
Invalid melodic instrument entry (instrument 1): missing field 'waveform'
No entry for melodic instruments 3, 7-9
$ echo $?
1
```

Errors:

```
$ midi2mkcd convert notes.txt
2026-11-20 14:05:01,002 - midi2mkcd.api - INFO - Reading MIDI file from notes.txt
error: Could not parse MIDI file notes.txt: EOFError()
$ midi2mkcd convert song.mid -p broken.yaml
error: Invalid melodic instrument entry (instrument 3): missing field 'amp_envelope'
$ midi2mkcd tune replace song.mid
usage: midi2mkcd tune replace [-h] ...
midi2mkcd tune replace: error: give --melodics, --drums or both
$ midi2mkcd tune sample song.mid --melodics 0-2 --drums 36
usage: midi2mkcd tune sample [-h] ...
midi2mkcd tune sample: error: argument --drums: not allowed with argument --melodics
```

### Code layout

```python
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.handler is _run_tune_replace and args.melodics is None and args.drums is None:
        args.parser.error("give --melodics, --drums or both")
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return args.handler(args)            # set_defaults(handler=..., parser=...) per leaf
    except (Midi2MkcdError, OSError) as e:
        if args.verbose:
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1

def build_parser() -> ArgumentParser: ...    # all add_argument calls, parent parsers
def _run_convert(args: Namespace) -> int: ...
def _run_tune_replace(args: Namespace) -> int: ...
def _run_tune_sample(args: Namespace) -> int: ...
def _run_tune_report(args: Namespace) -> int: ...
def _run_params_show(args: Namespace) -> int: ...
def _run_params_check(args: Namespace) -> int: ...
def _read_params(path: Path | None, *, allow_incomplete: bool) -> str | InstrumentParameterMapping | None: ...
def _emit(text: str, output: Path | None, copy: bool) -> None: ...
# kept from the current cli.py: range_arg, configure_logging (format unchanged)
```

Every `_run_*` is a few lines: resolve params, call one library function,
`_emit`. No TypeScript or table formatting in `cli.py`.

### Mapping from old flags

| Old                                         | New                                  |
| ------------------------------------------- | ------------------------------------ |
| `--input/-i` (required)                     | positional `INPUT`                   |
| `--input-instrument-params/-p` (required)   | `-p/--params`, optional, default bundled |
| `--output/-o`                               | unchanged                            |
| `--generate-extra-code`                     | `convert -f code_extra`              |
| `--test-generate-code`                      | `-f code` (the default for `tune replace`) |
| `--test-replace-all-melodics-with N`        | `tune replace --melodics N`          |
| `--test-replace-all-drums-with N`           | `tune replace --drums N`             |
| `--test-sample-melodic-instruments R`       | `tune sample --melodics R`           |
| `--test-ask-to-replace-all-*-with`          | removed (use the flags above)        |
| `--test-force-instrument-param-load`        | `tune ... --allow-incomplete`        |
| `--test-copy-result-to-clipboard`           | `--copy`                             |
| `--debug`                                   | `-v/--verbose`                       |
| (new)                                       | `tune sample --drums`, `tune report`, `params show [--blank]`, `params check`, `--version` |

### Tests this adds to `test_cli.py`

Beyond the bullet in "Tests" and the step 6 pointers above: `-h` for every
command and subcommand exits 0; bare `midi2mkcd`, `midi2mkcd tune` and
`midi2mkcd params` exit 2; `tune replace` with neither `--melodics` nor
`--drums` exits 2; `tune sample` with both `--melodics` and `--drums` exits
2; `params check -o x` exits 2 (no such option); a missing input file exits 1
with `error:` and no `Traceback` on stderr; `params show` stdout equals
`default_instrument_params_text()` plus `"\n"`; `params show --blank` stdout
equals `blank_instrument_params_text()` plus `"\n"`; `params check` on the
blank file exits 1 and prints one line per problem; `tune report` stdout
matches `render.usage_report(tuning.report(...))`.

## Tests

Put every new test file under `tests/unit/` or `tests/integration/` (the
conftest rejects anything else) and use the paths from `tests/helpers.py`.

- `tests/integration/test_golden.py`: for each golden file, call the new API
  (not the CLI) with `tests/fixtures/fixture.mid` as **bytes** and the bundled
  default params, and compare to the golden text exactly. `hex.ts`,
  `code.ts` and `code_extra.ts` (renamed from `play.ts` / `visualizer.ts` in
  step 6, contents unchanged) come from `convert()`; `replace.ts` from
  `tuning.convert_replaced(..., melodics=5).to_typescript("code")`;
  `sample_0-2.ts` from `tuning.sample_melodics(..., [0, 1, 2])`.
- `tests/integration/test_api.py`:
  - `convert()` accepts bytes, `BytesIO`, and a path, with identical results.
  - `params=None`, `params=<yaml text>`, and
    `params=load_instrument_params(...)` give identical results.
  - Garbage bytes raise `InvalidMidiError`; malformed YAML raises
    `InstrumentParamsError`; an instrument missing from the mapping raises
    `InstrumentParamsError`.
- `tests/integration/test_tuning.py`:
  - `convert_replaced(..., melodics=0)` includes the replacement comment
    (the `is not None` fix).
  - `load_instrument_params(..., allow_incomplete=True)` on the blank params
    file loads; without it, it raises `InstrumentParamsError`. (`BLANK_PARAMS`
    points at `examples/` today; step 6 switches it to
    `blank_instrument_params_text()` / the `resources/` path.)
  - `sample_drums(..., [36, 38])` returns a program with two songs, each
    labelled with its drum note (structure checks, no golden).
  - `report()` on the fixture lists exactly melodic programs 0 and 48 and
    drum notes 36, 38 and 42 (kick, snare, closed hat), with the right note
    counts and ranges, all `mapped=True`; with a params mapping that lacks
    program 48 it marks that one `mapped=False` and does not raise.
  - `check_params(default_instrument_params_text())` returns `[]` (if the
    shipped file has real problems, fix the file or note them).
    `check_params` flags a duplicate `instrument:` entry, an out-of-range
    amplitude, and missing coverage, using small inline YAML strings.
- `tests/unit/test_import_hygiene.py`: in a subprocess, `import midi2mkcd`
  and assert that `argparse` and `pyperclip` are not in `sys.modules`, that
  `midi2mkcd.tuning` is not imported by the root package, and that the
  `midi2mkcd` logger has only a `NullHandler`.
- `tests/integration/test_cli.py` (DONE, step 6): `convert` with `-o` writes
  the same text as `hex.ts`; without `-o`, stdout is exactly the generated
  code plus a trailing newline and contains no log lines. Replaces
  `test_golden_cli.py`.
- Unit tests for small pure pieces (e.g. `parse_range` raising `ValueError`,
  `render.py` header/comment logic) go in `tests/unit/`.

## Other housekeeping

- Bump version to `0.2.0`.
- Delete `requirements.txt` if nothing references it (`pyproject.toml` and
  `uv.lock` are the source of truth), or regenerate it from the lock file.
- Update `README.md`: new CLI syntax in every example command and the help
  text section, a short "Use as a library" section showing `convert()`, and
  fix links to the moved YAML.
- `uv run ruff check`, `uv run ruff format --check` and `uv run mypy` (strict)
  must pass, matching `.pre-commit-config.yaml`.

## Suggested commit order

1. ~~Step 0: pytest, fixture, golden files (old code untouched).~~ Done in
   `a0d0aa3`.
2. ~~Logging cleanup (`getLogger` everywhere, delete `utils/logger.py`; CLI
   configures stderr handler). Goldens still pass through the old CLI
   (`tests/integration/test_golden_cli.py`).~~ Done in `19634e1`.
3. ~~`errors.py` and exception replacement.~~ Done in `6abb26a`.
4. ~~Move default YAML into `resources/`; add `default_instrument_params_text()`;
   update `tests/helpers.py::INSTRUMENT_PARAMS`.~~ Done in `306633b`.
5. ~~`api.py` + `tuning.py` + `render.py` + `ConversionResult`; golden tests
   switch to the API (and `tuning` for `replace.ts` / `sample_0-2.ts`).~~
   Done in `04060f5`.
6. ~~New `cli.py`, delete `main.py` and `test_golden_cli.py`, update entry point.~~
   Done in "Rewrite the CLI as subcommands on top of the library".
7. README, version bump, remaining tests.

## Acceptance checklist

- [x] `from midi2mkcd import convert; convert(open("x.mid","rb").read()).to_typescript("code")` works with no other imports. (checked by hand, `04060f5`; renamed from `"play"` in step 6)
- [x] Golden tests pass (allowing only the two documented fixes). (`test_golden.py`, `04060f5`)
- [x] `import midi2mkcd` pulls in neither `argparse` nor `pyperclip` and installs no log handlers. (`test_import_hygiene.py`, `04060f5`)
- [x] `midi2mkcd convert x.mid > out.ts` produces a file with no log lines in it. (`test_cli.py::test_stdout_is_only_the_code`, step 6)
- [x] No `mido` type appears in any public signature. (`04060f5`)
- [x] `convert()` takes no tuning arguments; replacement and sampling are only reachable through `midi2mkcd.tuning`, which the root package does not import. (`04060f5`)
- [x] `cli.py` contains no f-strings that build TypeScript. (step 6)
- [x] The built wheel is `py3-none-any` and contains `midi2mkcd/resources/instrument_params.yaml` (already true as of `306633b`).
- [ ] ruff, ruff format and mypy strict are clean. (Clean after step 6, including `uv run pre-commit run --all-files`; recheck at the end.)

## Planned features (after this refactor)

Not part of this task. Step 6 left room for them (see "What step 6 should
do now" at the end of this section; all of it was followed). Suggested order:
`recover-midi`, then the synthesizer with its validation harness, then
`render` / `tune ... --wav`, then `params soundfont`, then `params fit`.

### Target CLI

```
midi2mkcd convert      INPUT.mid [-f {hex,code,code_extra}] [-p PARAMS] [-o OUT] [--copy]
midi2mkcd render       INPUT [-p PARAMS] -o OUT.wav [--rate 44100]   # INPUT: .mid, or .ts/.txt with one hex`...`
midi2mkcd recover-midi INPUT [-p PARAMS] -o OUT.mid                  # Arcade song -> MIDI (lossy)

midi2mkcd tune replace INPUT.mid ... [--wav OUT.wav]                 # audition without opening Arcade
midi2mkcd tune sample  INPUT.mid ... [--wav OUT.wav]
midi2mkcd tune report  INPUT.mid ...

midi2mkcd params show      [--blank] [-o OUT]
midi2mkcd params check     [-p PARAMS]
midi2mkcd params soundfont [-p PARAMS] -o OUT.sf2 [--notes RANGE] [--step N] [--rate N]
midi2mkcd params fit       TARGET.sf2 --program N [-p BASE] -o OUT.yaml [--layers 1|2]   # stretch goal
```

- `-f` stays text formats only. Binary outputs (WAV, MIDI, SF2) get their
  own subcommands or flags; never infer a format from the `-o` extension.
- Binary outputs always require `-o` (never written to stdout).
- Hex input (`render`, `recover-midi`): a helper that reads a `.ts`/`.txt`
  file and pulls out the `hex\`...\`` literal. **Single song only for now:**
  if the file has no literal or more than one (e.g. a `tune sample` output),
  raise a `Midi2MkcdError` that says how many it found. No `--index`. A
  `code_extra` file is recognised by its `songHex` / `trackInstrumentMap`
  consts (see `recover-midi`).
- `tune replace ... --wav OUT.wav` / `tune sample ... --wav OUT.wav` render
  what the subcommand would have produced (a replaced song, or the sampler's
  songs one after another with a short gap) so the tuning loop no longer
  needs a paste into Arcade. The sampler case renders the `ConversionResult`s
  directly, so the single-song rule for hex input doesn't apply. Probably the
  biggest win of all these features.

### Library layout

| Module             | Contents                                                                 | Dependencies          |
| ------------------ | ------------------------------------------------------------------------ | --------------------- |
| `recover_midi.py`  | `song_to_midi(...) -> bytes`, hex/`code_extra` text extraction            | mido only             |
| `audio.py`         | `instructions(song)` (port of the TS song → instruction stage), `synthesize(...) -> np.ndarray`, `to_wav_bytes(...) -> bytes` | numpy |
| `soundfont.py`     | SF2 (RIFF) writer, per-note rendering via `convert()` + `audio`           | numpy                 |
| `tuning/fit.py`    | the parameter optimizer (`tuning.py` becomes a package)                  | numpy + `cma` or scipy |

- numpy goes in an `audio` extra, `cma`/scipy in a `fit` extra. Import them
  lazily. Extend `test_import_hygiene.py`: `import midi2mkcd` must not import
  numpy (keeps the web page's convert-only path light; Pyodide does ship
  numpy for pages that want audio).
- Return bytes (`to_wav_bytes`, `song_to_midi`) so the web page can wrap them
  in a `Blob` directly. WAV writing uses the stdlib `wave` module.

### `recover-midi`: Arcade song → MIDI

An escape hatch, explicitly lossy, not something to round-trip through (the
name says so; the shorter alternative was `to-midi`).
`arcade/music.py::decode_song_from_hex` already parses the buffer; the work
is undoing the pipeline. It takes either a plain hex literal or a
**`code_extra`** file, and the `code_extra` file makes it much better:

| Information        | Plain hex + exact params file                                  | `code_extra` file                                 |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------- |
| Melodic program    | Match each track's embedded `Instrument` against the params entries. Identical entries are ambiguous: pick the lowest program, log it. | `trackInstrumentMap[track]` (exact; `-1` = drum track) |
| Drum notes         | Match each embedded drum against the params' drum entries (same ambiguity rule). | `midiDrumNoteMap[drumIdx]` (exact; the drum index is global across drum tracks) |
| Lyrics             | Lost                                                           | `lyricTicks` / `lyricTexts` → MIDI lyric meta events |
| Velocity           | Undo pitch compensation using the params' `melodic_pitch_comp_k` | Same; needs `-p` for k (see below)                |

- With a `code_extra` file, `-p` is optional. Pitch compensation scales
  **velocity** (not pitch) by note: `velocity * 2 ** (-(note - ref) / 12 * k)`
  with `ref = 60 - 11 - 12`, then rounded and clamped to 1–127. Undo it by
  dividing when k is known (from `-p`, or the bundled default's k if `-p` is
  omitted; log which). Rounding and clamping make it approximate either way.
- Pitch: undo the track's octave offset, the MIDI 60 → Arcade C4 shift and the
  extra octave down (`midi_to_song/__init__.py`). Needs no params.
- Merge tracks that were split for the same instrument (range splits,
  note-byte splits) back into one MIDI track/channel.
- Timing: 120 BPM, 24 ticks/beat, written at e.g. 480 MIDI ticks/beat with
  one tempo event. Note lengths come back as the quantized/gate-fixed ones.
- Velocity comes from the trailing per-chord velocity section when present,
  else a fixed default.
- Channels: drums on channel 10; melodic programs on the other 15, reusing
  channels with program changes (or a second port) if there are more.
- Tests: `song_to_midi(convert(fixture).to_typescript("code_extra"))`
  recovers programs 0 and 48, drum notes 36/38/42 and the lyrics; the
  plain-hex path recovers the same programs with the bundled params; note
  starts match the fixture within one song tick; a `tune sample` output is
  rejected as multi-song.

### Synthesizer and `render` (WAV preview)

How Arcade plays a song (from memory of pxt / pxt-common-packages; **read the
source before starting** and record the file paths here):

1. **Song → instructions (TypeScript, `libs/mixer`).** Each note becomes a
   list of fixed-size records (waveform, start/end frequency, start/end
   volume, duration), each one linear ramp. ADSR, pitch envelope and LFO are
   approximated as chains of ramps; drums are already step lists. A straight
   port.
2. **Instructions → samples.** On hardware: C++ (`melody.cpp`): naive
   waveforms, duty-cycle squares, LFSR-style noise. In the simulator: pxsim's
   audio code on Web Audio (likely band-limited, so smoother). Pick the
   **simulator** as the reference (it is what people hear while editing) and
   aim for perceptually equal, not sample-exact.

- numpy: per instruction, `phase = cumsum(freq / sr)` over a linear frequency
  ramp, waveform lookup, linear gain ramp; sum tracks into one buffer; clip
  like the mixer does.
- **Validation is the real cost.** Build a small set of test songs (one per
  waveform, envelope/LFO cases, each drum type), record them from the
  simulator by hand into `testing/` (gitignored), and compare spectrograms
  and RMS envelopes. Expect several "slightly off" rounds.
- `render` accepts a MIDI file (converts first, with `-p`) or a file with
  exactly one Arcade hex literal (see "Target CLI").

### `params soundfont`: params → SF2

Lets the user hear Arcade-ish instruments in a MIDI editor. Lives under
`params` because it exports a params file, not a song.

- Render each note by building a one-note song and running it through the
  real `convert()` pipeline, then `audio`, so the pitch shift, pitch
  compensation and gate fixes match a real conversion.
- Size: 128 programs × ~60 notes × 4 velocity layers × 1–2 s at 44.1 kHz is
  several GB. If Arcade velocity is plain amplitude scaling (check), use one
  velocity layer and SF2's velocity → attenuation. One mono sample per note at
  22.05 kHz is a few hundred MB; one every 2–3 semitones (`--step`) about
  100 MB. `--notes RANGE` limits the note range.
- Note length: a pre-rendered sample doesn't know when the note ends. Render
  attack/decay into a sustain loop and set the SF2 volume-envelope release to
  the instrument's release. Drums are one-shots.
- No maintained Python SF2 writer to rely on; the RIFF layout is a few
  hundred lines to write by hand.

### `params fit`: optimize params toward a real instrument (stretch goal)

- Target: samples from a GM SoundFont at a few notes, velocities and
  durations (read from the SF2, or rendered with FluidSynth).
- Loss: multi-resolution log-spectrogram distance plus an RMS-envelope term.
- Search: mixed discrete/continuous and not differentiable. Loop over the
  ~20 waveforms; for each, CMA-ES (`cma`) or scipy `differential_evolution`
  over ADSR, amplitude, pitch envelope and LFO. Evaluations are short numpy
  renders, so thousands are fine.
- Expect envelope shape, brightness and attack to match; timbre stays
  chiptune.
- **Two layers** (detuned pair for chorus, a noise layer for attacks, for
  melodic and drum instruments): params schema change (an entry becomes a
  list of layers) and the converter emits one track per layer. Costs: doubles
  track count (hard limit), doubles `note_event_count` (toward the 15k
  simulator warning), clipping risk. Fit single-layer first and only add a
  second layer if the loss improvement is audible.

### What step 6 should do now

- Keep `-f` text-only and `_emit` text-only; add a separate bytes writer
  (`_emit_bytes(data, output)`) only when the first binary command lands.
- Keep `INPUT` handling per subcommand (don't assume every command takes
  MIDI), so `render` / `recover-midi` can accept hex input.
- Build `tune` and `params` as subcommand groups so `tune ... --wav`,
  `params soundfont` and `params fit` slot in without reshaping anything.
- Nothing else; don't add placeholder subcommands.

## Later (not part of this task)

- Publish the wheel to PyPI (trusted publishing from a tag) so the web page
  can `micropip.install("midi2mkcd==<version>")`. Alternative if not
  publishing: a `web/` folder in this repo with a GitHub Action that builds
  the wheel and deploys it next to the page on GitHub Pages. Do not rely on
  GitHub Release asset URLs for `micropip`; they are not served with CORS
  headers.
- `requires-python >=3.14` means the web page must use Pyodide 314 or newer.

### Possible later additions to `midi2mkcd.tuning`

Ideas for speeding up instrument tuning that are **not** part of this
refactor. Each fits the module as designed above (functions returning MIDI
bytes, a `ConversionResult`, or TypeScript text) and needs no change to the
public API.

- **Generated test phrases.** `tuning.test_phrase(instrument, *, low, high)
  -> bytes` builds a MIDI file with mido: a scale across the instrument's real
  range, a velocity ramp, a duration ladder (very short / medium / held notes,
  to exercise the envelope, gate and the attack < gate + release case), and a
  chord. Feeds straight into `convert()` / `convert_replaced()`. Removes the
  need to tune against whatever song is to hand (the YAML header's
  bass-on-high-notes problem) and makes tuning `melodic_pitch_comp_k` easier
  (same phrase two octaves apart). A drum version (each note at soft and hard
  velocities, labelled) would improve `sample_drums`.
- **A/B variants and sweeps.** `tuning.sample_variants(midi, instrument,
  variants: list[Instrument]) -> str` plays the same passage once per variant
  of one instrument's parameters, plus a helper like `sweep(base,
  "amp_envelope.attack", [5, 20, 50])` to build the variants. CLI:
  something like `tune variants INPUT --melodics N alt.yaml`.
- **Solo.** `convert_replaced(..., only=[40])` (or a separate `solo()`) drops
  every other track so one instrument's real part can be heard on its own
  without the rest of the mix.
- **Watch mode (CLI only).** `midi2mkcd tune replace ... --watch --copy` re-runs the
  conversion whenever the params YAML is saved and copies the result to the
  clipboard, so the loop is edit → save → paste into Arcade. A polling loop in
  `cli.py`; no library change.
