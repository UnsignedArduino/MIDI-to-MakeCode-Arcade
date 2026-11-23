# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CLI tool (`midi2mkcd`) that converts a MIDI file into a MakeCode Arcade song: a hex-encoded buffer you paste into Arcade TypeScript as `hex\`...\``. It supports GM1 melodic instruments (0-127), the GM1 standard drum kit, the GM2 extended drum range (27-87, standard kit only), and Roland GS/Yamaha XG SysEx drum-track switching. Requires Python >= 3.14 and `uv`.

## Commands

```bash
uv sync                                   # create venv + install deps (incl. dev group)
uv run midi2mkcd convert song.mid                      # print the hex literal to stdout (bundled params)
uv run midi2mkcd convert song.mid -p my.yaml -o out.ts # custom params; write (overwrites)
uv run midi2mkcd convert song.mid -f code              # -f {hex,code,code_extra}; code_extra = Song-Visualizer consts
uv run midi2mkcd recover-midi song.ts -o song.mid      # Arcade song text -> MIDI (lossy; -o required)
uv run midi2mkcd render song.mid -o song.wav           # MIDI or song text -> WAV, like the simulator (needs numpy)
uv run midi2mkcd tune report song.mid                  # instruments/drums used (GM names), ranges, params coverage
uv run midi2mkcd tune replace song.mid --melodics 48   # every melodic note as program 48 (and/or --drums N)
uv run midi2mkcd tune sample song.mid --melodics 40-47 # one program playing the song once per instrument
uv run midi2mkcd params show [--blank] -o mine.yaml    # bundled params (or blank template)
uv run midi2mkcd params check -p mine.yaml             # lint a params file; exit 1 on problems
uv run midi2mkcd ... -v                                # debug logging (and tracebacks for errors)
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src tests                     # strict mode
uv run pre-commit run --all-files         # ruff check, ruff format --check, mypy
```

GitHub Actions (`.github/workflows/`) runs on pushes to `main` and on every PR: `lint.yaml` (ruff check, ruff format --check, mypy, all on `.`), and `unit.yaml` / `integration.yaml` (`pytest -m unit` / `-m integration` on Ubuntu, Windows and macOS). A PR's last commit message can skip a test tier with `[skip-unit-tests]`, `[no-integration]`, `[skip-tests]` and similar (see `check-skip.yaml`); pushes to `main` always run everything.

`tests/` (pytest, `unit/` and `integration/` only) compares output against golden files in `tests/fixtures/golden/`, generated from `tests/fixtures/fixture.mid`. The simulator ports are checked against `golden/sim_instructions.json`, produced by the real pxt code: `uv run python tests/fixtures/make_sim_cases.py` writes the inputs (`sim_cases.json`), then `node tests/fixtures/make_sim_goldens.mjs <pxt clone>` (Node ≥ 22.13, not in CI) writes the outputs. For listening, `render` writes a WAV that sounds like the simulator; the final check is still playing the output in MakeCode Arcade. The synth was checked against a simulator recording with the (gitignored) scripts in `testing/calibration/analysis/` (`compare.py`; see `RENDER_FEATURES_PLAN.md`, section 4). The `testing/` directory (gitignored) holds local sample MIDI files. The `tune` subcommands help with tuning instruments; `--allow-incomplete` loads a partial params file and `--copy` copies the result to the clipboard (needs the optional `pyperclip`).

`-p` (instrument parameter YAML) is optional. `src/midi2mkcd/resources/instrument_params.yaml` is the hand-tuned reference and the bundled default (read via `utils/resources.py::default_instrument_params_text`, which uses `importlib.resources`); its header comment documents units (ms, amplitude 0-1024, Hz) and the waveform name → Arcade ID mapping.

## Architecture

The package is a library with a thin CLI. `api.py::convert` (MIDI bytes/stream/path + params YAML text or mapping → `ConversionResult`) is the public entry point, re-exported from the package root; `tuning.py` holds the instrument-tuning tools (replace, sample, report, check_params) and is not re-exported; `song_to_midi/__init__.py::song_to_midi` (song text → MIDI bytes, the reverse of `midi_to_song`, not re-exported) reads its input with `utils/song_text.py::parse_song_text` (the single ``hex`...` `` literal plus `code_extra` consts) and `decode_song_hex`, as `render` does for text input; `render.py` builds all TypeScript and report text; `audio.py` (not re-exported) turns a song into the simulator's play instructions (`note_instructions`, `drum_instructions`, `song_events`, over the `arcade/sim_*` ports) and then into samples the way `pxtsim/sound/playInstructions.ts` plays them in Chrome (`synthesize`, `render_song`, `to_wav_bytes`). numpy is the optional `audio` extra (also in the dev group): `audio.py` imports it only inside the Stage 2 functions, and a missing numpy raises `MissingDependencyError`. `synthesize`/`render_song` report progress through a `progress(done, total)` callback (the library never prints); the CLI turns it into a tqdm bar on stderr (`cli._ProgressBar`; tqdm is also in the `audio` extra, and without it there is no bar); `errors.py` has the `Midi2MkcdError` hierarchy. `cli.py` only parses arguments (subcommands `convert`, `recover-midi`, `render`, `tune {replace,sample,report}`, `params {show,check}`; binary outputs require `-o` and go through `_emit_bytes`, never stdout), reads files, configures logging and calls one library function per command; `__main__.py` and the `midi2mkcd` script both run `cli.main`. `utils/gm_names.py` has the General MIDI instrument and drum note names.

**`midi_to_song/__init__.py::convert_midi_to_song`** is the core pipeline. It runs a chain of `timeline_*` stages, each producing a new list of typed dataclasses from `midi_to_song/models.py` (the variables are named `global_timeline_0..8`):

1. `timeline/parser.py`: builds an absolute-time message list across all tracks; resolves per-port/channel instrument state (program changes, CC and SysEx drum switching, channel 10); pairs note on/off into complete notes; extracts lyrics.
2. A pitch shift for melodic notes (MIDI 60 → Arcade's different C4 numbering, plus an empirical extra octave down). Drums aren't shifted because they map to per-track drum indices.
3. `timeline/processor.py`: pitch compensation; gate-length fixes (keeps attack < gate + release to avoid a pxt playback bug); quantizing to song ticks; grouping by instrument; splitting tracks whose note range exceeds the 64-note window per octave offset; grouping notes with identical start/end/velocity into chords; splitting tracks whose encoded note bytes would overflow the 16-bit length field.
4. `timeline/validation.py`: hard checks that raise on violations of Arcade format constraints.
5. Final mapping into `arcade/music_types.py` dataclasses. It picks each melodic track's octave offset, and for drum tracks it builds the per-track drum list plus the MIDI-drum → index mapping.

The song is fixed at 120 BPM, 4 beats/measure, 24 ticks/beat (1 tick = 1/48 s). All MIDI timing is converted to absolute seconds and then quantized to this grid; MIDI tempo is not carried into the song header.

**`arcade/`** is a Python port of pxt's `pxtlib/music.ts` (encode/decode) and `localtypings/pxtmusic.d.ts` (types), plus the simulator's `pxtsim/sound/song.ts` (`sim_song.py`: note/drum → 12-byte play instructions, reproducing JS number semantics) and `sequencer.ts` (`sim_sequencer.py`: frequency table, tick timing, volumes, what each tick plays), pinned to the pxt commit named in their headers. The byte layout is documented in a comment at the top of `music_types.py`. Keep this code faithful to upstream pxt. Per-chord velocity is encoded as an extra trailing section, which stable Arcade now reads (older versions ignore it).

Format limits that drive most of the pipeline's complexity: each note is stored as 6 bits relative to the track's octave (a 64-note range), track/note-byte lengths are 16-bit, and polyphony is per-chord with no overlap within a track. Breaking any of these splits tracks rather than dropping notes.

## Conventions

- mypy runs in `strict` mode (`mido` is untyped and ignored). Ruff targets py314. Note that Python 3.14 syntax such as unparenthesized `except A, B:` is used.
- Library modules only do `logger = logging.getLogger(__name__)`; the package root adds a `NullHandler`. Only the CLI configures logging (`cli.configure_logging`: one stderr handler on the `midi2mkcd` logger), so stdout carries only generated code. The default level is INFO; `-v`/`--verbose` sets it to DEBUG.
